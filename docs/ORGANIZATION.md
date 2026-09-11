# Organizasyon Yapısı — Persopyro

Şirket içi hiyerarşinin ve çalışan yapısının modellenmesi. Bu belge **hedef şemayı**
tanımlar; bağlayıcı kararlar `docs/AUTHORIZATION.md` (OD5, OD24, OD27) ve
`docs/DATABASE.md` (bölüm B.4, M10–M13) içindedir.

**İşaretler:** ✅ mevcut ve doğrulandı · 🔜 tasarlandı, uygulanmadı · ❓ OPEN DECISION

**Kapsam notu:** `~/hrsaas_db/` referans tasarımı (13 SQL dosyası, ~90 tablo) girdi
olarak kullanılmıştır ama **birebir uygulanmaz**. Aşağıdaki "Referans tasarımdan
farklar" bölümü hangi tabloların v1 kapsamına girmediğini açıkça listeler.

---

## 1. Nesne hiyerarşisi (v1)

```
tenant (kiracı = müşteri şirket)                ← hr.tenants (canonical)
└── department (birim ağacı, parent_id)         ← hr.departments
    ├── position (unvan/kadro)                  ← hr.positions
    └── employee (çalışan)                      ← hr.employees
        ├── employee_personal_data (K2/K3)      ← ayrı tablo, maskeli/şifreli
        └── employee_manager (yönetici ilişkisi) ← hr.employee_manager
```

**Hiyerarşi iki katmanda ifade edilir:**

| Katman | Tablo | Ne anlatır |
|---|---|---|
| **Birim ağacı** | `departments.parent_id` | Organizasyon şeması (GM → Deputy GM → Departman → Alt departman) |
| **Kişi zinciri** | `employee_manager` | Kim kime bağlı (çalışan → birincil yönetici; ikincil/delege ilişkiler) |

Birim sorumlusu ayrıca `departments.manager_employee_id` ile tutulur (onay
yönlendirmesi için); bu, `employee_manager` ile **aynı şey değildir** ve çift kayıt
oluşturmaz (biri "birimin sorumlusu", diğeri "kim kime raporluyor").

## 2. Tablolar ve sorumluluklar (hedef, v1) 🔜

| Tablo | Sorumluluk | Tenant izolasyonu |
|---|---|---|
| `departments` | Birim ağacı (`parent_id`), kod, ad, birim sorumlusu, durum | `tenant_id` + RLS + FORCE + guard (`parent_id`, `manager_employee_id`) |
| `positions` | Unvan/kadro tanımı (kod, ad, seviye) | `tenant_id` + RLS + FORCE |
| `employees` | Çalışanın **iş** kaydı (birim, unvan, giriş/çıkış, durum, çalışan no) | `tenant_id` + RLS + FORCE + guard (`department_id`, `position_id`, `user_id`) |
| `employee_personal_data` | Kişisel veri (K2) ve özel nitelikli veri (K3, şifreli) — 1:1 | `tenant_id` + RLS + FORCE + guard |
| `employee_manager` | Yönetici/raporlama ilişkisi (birincil/ikincil/delege, tarihli) | `tenant_id` + RLS + FORCE + guard |
| `leave_*`, `approval_steps` | İzin ve onay akışı (birim/yönetici zincirini kullanır) | `tenant_id` + RLS + FORCE |
| `shifts`, `shift_assignments`, `attendance_records` | Vardiya ve devam kaydı | `tenant_id` + RLS + FORCE |
| `documents`, `document_access` | Evrak ve erişim politikası | `tenant_id` + RLS + FORCE |

**Kişisel veri ayrımı (OD21 + AUTHORIZATION bölüm 7):** `employees` tablosu PII
**içermez**; ad, iletişim, kimlik ve sağlık verisi `employee_personal_data` içinde
tutulur. Böylece "kim bu birimde çalışıyor" sorusu (K0/K1) ile "bu kişinin TCKN'si
ne" sorusu (K2/K3) **farklı izinlerle** yanıtlanır.

## 3. Hiyerarşi modeli — adjacency list + özyinelemeli CTE ✅ KARAR (OD24 kapalı)

- **Model:** `departments.parent_id` ile **adjacency list**; ağaç **PostgreSQL
  özyinelemeli CTE** (`WITH RECURSIVE`) ile çözülür.
- **v1'de eklenmeyecek:** materialized path, nested set, closure tablosu.
  Gerekçe: birim taşıma tek satırlık güncellemedir; türetilmiş yapıların bakımı ve
  tutarsız kalma riski bu ölçekte gereksizdir.
- **Sorgu deseni:** her zaman `(tenant_id, parent_id)` indeksiyle başlar; CTE
  **derinlik sınırlıdır** (bozuk/veri dışı döngüye karşı son savunma).
- **Önbellek:** bir kullanıcının kapsam sonucu (birim alt ağacı) DB cache'te kısa
  TTL ile tutulur; birim taşıma veya yönetici değişiminde **geçersiz kılınır**.
- **Geri dönüş tetikleyicileri:** derinlik > 20, kiracı başına birim > 5.000 veya
  kapsam sorgusu p95 > 50 ms → closure tablosu ayrı bir kararla değerlendirilir.

## 4. Döngü ve tutarlılık kuralları (veritabanı garantileri)

| Kural | Nasıl zorlanır |
|---|---|
| Departman kendi atası olamaz (derin döngü) | `BEFORE INSERT OR UPDATE` tetikleyicisi ata zincirini yürür ve döngüde **hata verir** (fail-closed); yürüyüş 20 adımla sınırlıdır |
| Departman kendi ebeveyni olamaz | `CHECK (parent_id IS DISTINCT FROM id)` |
| Çalışan kendisinin yöneticisi olamaz | `CHECK (employee_id <> manager_employee_id)` + `employee_manager` için döngü tetikleyicisi |
| Kiracı dışı ilişki kurulamaz | Mevcut `hr.ensure_same_tenant_ref()` guard'ı (`parent_id`, `manager_employee_id`, `department_id`, `position_id`, `user_id`) |
| Bir çalışanın **tek birincil** yöneticisi olur | Kısmi benzersiz indeks: `(employee_id) WHERE relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL` |
| Maksimum derinlik | Tetikleyici + CTE sınırı: **20 seviye** |
| Referans bütünlüğü | `employees.department_id` zorunlu (v1: her çalışan bir birimde); birim boşaltılmadan arşivlenmez |

Döngü kontrolü **yalnızca uygulama katmanına bırakılmaz**: döngü, kapsam
çözümlemesini bozarak yetki sınırlarını kaydırabileceği için veritabanında
zorunludur. Uygulama katmanındaki kontrol **ikinci savunma**dır (daha anlaşılır hata
mesajı için).

## 5. Kapsam ve yetki

- **Ağaç yetki mirası üretmez.** Bir üst birimin yöneticisi olmak, alt birimlerin
  yetkilerini otomatik **vermez**; kapsam yalnızca *hangi kayıtları görebileceğini*
  belirler. Yetki kararı **her zaman** RBAC'ten gelir (izin kodu + kapsam + koşul).
- Kapsam kodları: `TENANT` (tüm kiracı), `UNIT` (kendi birimi ve alt ağacı),
  `UNIT_DIRECT` (yalnızca kendi birimi), `ASSIGNED` (atanan kayıt), `SELF`.
- Rol bazlı görünürlük (güncel rol kodlarıyla):

| Rol | Organizasyon görünürlüğü |
|---|---|
| `tenant_admin` | Kiracının tamamı |
| `tenant_officer` | Yetkilendirildiği modüller (şirket geneli, izin bazlı) |
| `dept_manager` | Kendi birimi **ve alt birimleri** (kapsam: `UNIT`) ❓OD5 |
| `employee` | Kendi kaydı + kendi biriminin **genel** bilgileri (ad, unvan, birim) |

- Organizasyon şeması görünümü (ağaç) **kişisel veri içermez**: ad yerine **genel
  kullanıcı kimliği** (OD2/OD25), unvan ve birim gösterilir.
- Onay yönlendirmesi: `approval_steps` adımları ya bir **kişiye** (`approver_user_id`)
  ya da bir **role/kapsama** (`approver_role_code` + `UNIT` kapsamı) bağlanır; onaylayan
  kendi talebini onaylayamaz (`SEPARATE_ACTOR`).

## 6. Birincil yönetici ve ek ilişkiler

- `employee_manager.relation_type` ∈ {`primary`, `secondary`, `delegate`}.
- **Birincil** yönetici zorunlu değildir (ör. genel müdürün yöneticisi yoktur) ama
  varsa **tektir** (kısmi benzersizlik).
- İlişkiler tarihlidir (`effective_from`/`effective_to`): yönetici değişikliği eski
  satırı **kapatır**, yenisini açar → geçmiş korunur (OD21).
- Yetki devri (delege) ayrı bir türdür ve **sürelidir**; süre bitince ilişki
  kendiliğinden geçersiz olur.
- Yönetici ataması **yetki etkisi taşıdığı için** denetim kaydına yazılır.

## 7. Çalışanın yaşam döngüsü

| Durum | Anlamı | Silme davranışı |
|---|---|---|
| Aktif (`active`) | Çalışıyor | — |
| Askıda (`suspended`) | İzinli/ayrılış sürecinde, kısıtlı erişim | — |
| Ayrıldı (`terminated`) | İlişki bitti (`termination_date` dolu) | Kayıt **arşivde**, kişisel veri saklama politikasına tabi |
| Arşiv (`archived`) | Uzun süreli saklama, raporlama için okunabilir | — |

Kurallar (OD21): çalışan kaydı **fiziksel silinmez**; `deleted_at` + `status`
kullanılır. Soft-delete edilmiş kaydın yeniden açılması açık bir `*.restore` izni ve
denetim kaydı gerektirir (**sessiz yeniden etkinleştirme yasak**). Benzersizlikler
kısmidir: `(tenant_id, employee_no) WHERE deleted_at IS NULL`.

## 8. Vardiya, devam ve evrak (hedef şema)

- `shifts` + `shift_assignments`: vardiya tanımı ve ataması (kapsam: `UNIT`).
- `attendance_records`: fiili çalışma; düzeltmeler **gerekçeli** ve onaylı
  (`attendance.correct.approve`), her düzeltme denetim kaydına yazılır.
- `documents` + `document_access`: evrak sınıflandırması (`classification`) ve
  **"kişisel veri içeriyor mu?"** işareti (`contains_personal_data`) zorunludur;
  "emin değilim" = **en kısıtlayıcı** politika (AUTHORIZATION bölüm 9).
- "Birim ağacının yetki mirası üretmemesi" bu modüllerde de geçerlidir: vardiya/evrak
  yetkisi izin kodundan gelir, unvandan değil.

## 9. Çok tüzel kişilik durumu ✅ KARAR (OD27 kapalı)

**v1: `1 tenant = 1 tüzel şirket / işveren organizasyonu`.** Ayrı `companies` veya
`legal_entities` tablosu **eklenmez**; `departments`, `positions`, `employees` ve tüm
ilişkili kayıtlar **doğrudan `tenant_id`'ye** bağlıdır. `company_id` hiçbir tabloda
yer almaz ve RBAC scope'a şirket katmanı eklenmez. Tüzel kişilik bilgisi zaten
`hr.tenants` satırındadır (`legal_name`, `tax_number`, adres/iletişim).

**Geleceğe açıklık:** ihtiyaç doğarsa ayrı bir `legal_entities` katmanı eklenir;
`departments`/`employees` ona bağlanır (nullable → backfill → zorunlu). Ancak
**tenant güvenlik sınırı olmaya devam eder** ve tüzel kişilik **hiçbir zaman** tenant
izolasyon sınırının yerine geçmez (RLS `tenant_id` üzerinde kalır). Kapsam katmanı
eklenirse yeni bir kapsam kodu (ör. `COMPANY`) **kararla** tanımlanır.

**Yeniden değerlendirme tetikleyicileri** (herhangi biri): aktif kiracıların ≥ %10'u
veya ≥ 3 kiracı ikinci işveren şirket istiyorsa; bordro/izin politikalarının tüzel
kişilik bazında ayrılması sözleşmesel zorunluluk olursa (≥ 1 imzalı sözleşme);
enterprise satışlarda ardışık ≥ 2 anlaşma bu nedenle bloke olursa; tek kiracıda
≥ 2 ayrı işveren sicil numarası gerekirse.

## 10. Referans tasarımdan farklar (bilinçli)

| Referans tasarım | v1 kararı | Gerekçe |
|---|---|---|
| `companies` (çok tüzel kişilik) | **OD27 kapalı: v1'de yok** (tenant = tüzel kişilik) | KOBİ hedefi; kapsam çözümlemesini ve RLS'i tek boyutta tutar |
| `employment_history` | v1'de yok; geçmiş `employee_manager` tarihleri + denetim kaydıyla tutulur | Ayrı tarihçe tablosu, henüz tanımlı olmayan raporlama ihtiyacı için erken maliyet |
| `emergency_contacts` | v1'de `employee_personal_data` kapsamında değerlendirilir (ayrı tablo ertelendi) | K2 sınıfı; erişim politikası aynı |
| `work_schedules` | v1'de `shifts` ile karşılanır | Aynı işlevin iki tabloya bölünmesi gereksiz |
| `projects` | v1 kapsamı dışında | Yetkilendirme modelini etkilemiyor |
| Enum tipleri (16 adet) | `CHECK` kısıtlı metin alanları (Django `TextChoices`) | Taşıma ve raporlama kolaylığı (ARCHITECTURE 5.4) |
| Departman hiyerarşisi için nested set/materialized path önerileri | **Adjacency list + recursive CTE** (OD24) | Bakımı en sade, bozulma riski en düşük |

## 11. Açık noktalar

| # | Konu | Durum |
|---|---|---|
| **OD5** | Departman yöneticisinin kapsamı: alt ağaç mı, yalnızca doğrudan birim mi? | Açık (öneri: alt ağaç) |
| **OD27** | Kiracı içinde birden fazla tüzel şirket (`companies`) | **Kapalı** (2026-09-11) — v1 tek tüzel kişilik; gelecek yolu ve tetikleyiciler bölüm 9'da |
| **OD24** | Ağaç mekanizması ve döngü garantileri | **Kapalı** (2026-09-11) |
| — | Organizasyon ağacı için gerçek ölçüm (derinlik, birim sayısı, kapsam sorgu süresi) | Uygulama sonrası ölçülecek (geri dönüş tetikleyicileri bölüm 3'te) |
