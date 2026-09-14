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

## 2. Tablolar ve sorumluluklar (v1) — **✅ M10–M13 UYGULANDI (2026-09-11)**

| Tablo | Sorumluluk | Tenant izolasyonu |
|---|---|---|
| `departments` | Birim ağacı (`parent_id`), kod, ad, birim sorumlusu, durum | `tenant_id` + RLS + FORCE + guard (`parent_id`, `manager_employee_id`) |
| `positions` | Unvan/kadro tanımı (kod, ad, seviye) — **birime bağlı değil** | `tenant_id` + RLS + FORCE |
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
- **Önbellek (v1'de YOK):** kapsam sonucu her çözümlemede veritabanından hesaplanır;
  sahte önbellek kurulmadı. Geçersiz kılma **sınırı** hazırdır:
  `apps/rbac/services/scope.py::invalidate_org_scope_cache()` — organizasyonu
  değiştiren servisler (`move_department`, `set_primary_manager`, `move_employee`,
  birim silme/geri alma) bunu **çağırır**; ileride eklenecek önbellek (K17) yalnızca
  burayı doldurur, çağıranlar değişmez.
- **Geri dönüş tetikleyicileri:** derinlik > 20, kiracı başına birim > 5.000 veya
  kapsam sorgusu p95 > 50 ms → closure tablosu ayrı bir kararla değerlendirilir.
- **Taşıma ve kapsam (OD5 kapalı):** dept_manager kapsamı **güncel alt ağaçtır**;
  birim taşındığında kapsam yeni yapıya göre yeniden hesaplanır (büyüyebilir/
  küçülebilir). Bu nedenle taşıma (a) önbelleği **geçersiz kılar** ve (b) **yüksek
  etkili denetim olayı** olarak kaydedilir (aktör, eski/yeni `parent_id`, gerekçe).
  Döngü/derinlik ihlali veritabanında reddedilir (bölüm 4).

## 4. Döngü ve tutarlılık kuralları (veritabanı garantileri) — **✅ UYGULANDI**

| Kural | Nasıl zorlanır |
|---|---|
| Departman kendi atası olamaz (derin döngü) | `trg_departments_tree_guard` (`BEFORE INSERT/UPDATE`) ata zincirini yürür ve zincirde kendi kaydını bulursa **hata verir**; yürüyüş 20 adımla sınırlıdır ✅ |
| Departman kendi ebeveyni olamaz | `CHECK (parent_id IS DISTINCT FROM id)` (`departments_parent_not_self`) ✅ |
| Çalışan kendisinin yöneticisi olamaz | `CHECK (employee_id <> manager_employee_id)` (`employee_manager_not_self`) ✅ |
| Kiracı dışı ilişki kurulamaz | Mevcut `hr.ensure_same_tenant_ref()` guard'ı 11 tabloda 19 tetikleyiciyle (`parent_id`, `manager_employee_id`, `department_id`, `position_id`, `user_id`, `scope_org_unit_id`) ✅ |
| Bir çalışanın **tek birincil** yöneticisi olur | Kısmi benzersiz indeks: `(employee_id) WHERE relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL` ✅ |
| Maksimum derinlik | Tetikleyici + CTE sınırı: **20 seviye** ✅ |
| Referans bütünlüğü | `employees.department_id` zorunlu (v1: her çalışan bir birimde); birim boşaltılmadan **arşivlenmez** (servis guard'ı: alt birim/aktif çalışan varsa red) ✅ |
| Silinmiş çalışanla yeni aktif raporlama ilişkisi | `trg_employee_manager_active_guard` ✅ |
| Aktif olmayan birim kapsam olarak atanamaz | `hr.user_role_scope_guard` (M9 tamamlama; devre dışı bırakma serbesttir) ✅ |

Döngü kontrolü **yalnızca uygulama katmanına bırakılmaz**: döngü, kapsam
çözümlemesini bozarak yetki sınırlarını kaydırabileceği için veritabanında
zorunludur. Uygulama katmanındaki kontrol **ikinci savunma**dır (daha anlaşılır hata
mesajı için).

## 5. Kapsam ve yetki

- **Ağaç yetki mirası üretmez.** Bir üst birimin yöneticisi olmak, alt birimlerin
  yetkilerini otomatik **vermez**; kapsam yalnızca *hangi kayıtları görebileceğini*
  belirler. Yetki kararı **her zaman** RBAC'ten gelir (izin kodu + kapsam + koşul).
- **Çalışan listesi görünürlüğü:** kapsamı yalnız `SELF` olan kullanıcı liste ve detayda
  **yalnız kendi kaydını** görür; başka çalışanın kaydı/numarası dönmez, kapsam dışı
  detay `404`'tür. Birim yöneticisi kapsamındaki (birim + alt ağaç) çalışanları,
  kiracı yöneticisi tüm kiracıyı görür. Organizasyon şeması (birim ağacı) ayrı uçtur ve
  çalışana açıktır.
- **Kişisel veri (K2/K3) görünürlüğü:** kapsam **tek başına yetmez**; kişisel veri
  `employee.sensitive.read` izni **ve** kapsam birlikte gerekir (`can_access_employee_personal_data()`).
  Departman yöneticisi kapsamındaki çalışanın **organizasyon kimliğini** görür, kişisel
  verisini **görmez** (`403`); kapsam dışı kayıtta `404`. Şirket yöneticisi izni,
  Yetki Yönetimi ekranından başka bir role de verebilir — veriyi yalnızca yetki verilenler görür.
  Kişisel veri formu **bütün alanları** toplar (ad, soyad, kimlik no, doğum tarihi,
  kan grubu, iletişim, adres, yakın kişi, vatandaşlık/izin, banka + IBAN); kan grubu
  K3 olduğu için veritabanında şifreli durur (OD31).
- Kapsam kodları: `TENANT` (tüm kiracı), `UNIT` (kendi birimi ve alt ağacı),
  `UNIT_DIRECT` (yalnızca kendi birimi), `ASSIGNED` (atanan kayıt), `SELF`.
- Rol bazlı görünürlük (güncel rol kodlarıyla):

| Rol | Organizasyon görünürlüğü |
|---|---|
| `tenant_admin` | Kiracının tamamı |
| `tenant_officer` | Yetkilendirildiği modüller (şirket geneli, izin bazlı) |
| `dept_manager` | Kendi birimi **ve alt birimleri** (kapsam: `UNIT`) ✅ **OD5 kapalı (2026-09-11)** — kapsam kaynağı `user_roles.scope_org_unit_id`; NULL ise **fail-closed**. **M8/M9 ✅ UYGULANDI:** rol `scope_type = UNIT` (DB `CHECK`), atama kapsamı guard tetikleyicisiyle **zorunlu**; alt ağaç çözümlemesi (M10) gelene kadar bu atamalar **izin üretmez** |
| `employee` | Kendi kaydı + kendi biriminin **genel** bilgileri (ad, unvan, birim) |

- Organizasyon şeması görünümü (ağaç) **kişisel veri içermez**: ad yerine **genel
  kullanıcı kimliği** (OD2/OD25), unvan ve birim gösterilir.
- **Şirket dizini (2026-09-11, ürün sahibi kararı — test arayüzü):** `employee`
  kapsamlı kullanıcı, organizasyon şemasının yanında **şirket geneli çalışan
  dizinini** görebilir; ancak bu görünüm yukarıdaki ilkeye uyar: **ad, e-posta ve
  izin/bakiye bilgisi gösterilmez**, yalnız çalışan numarası + genel kullanıcı
  kimliği + unvan + birim + durum görünür. Kullanıcı **kendi** kaydını ve kendi izin
  bakiyesini tam görür. Kapsamı `UNIT`/`TENANT` olan roller (yönetici/yetkili) kapsam
  içindeki çalışanları izin özetiyle görür (`employee.read` ∧ kapsam).
- Onay yönlendirmesi: `approval_steps` adımları ya bir **kişiye** (`approver_user_id`)
  ya da bir **role/kapsama** (`approver_role_code` + `UNIT` kapsamı) bağlanır; onaylayan
  kendi talebini onaylayamaz (`SEPARATE_ACTOR`).

### 5.1 Kapsam kararı (OD5 — kapalı) ✅ — **M8/M9 ✅ UYGULANDI (2026-09-11)**

- **Dept_manager kapsamı: atandığı birim + tüm alt ağaç (`UNIT`)**; kapsam kaynağı
  `hr.user_roles.scope_org_unit_id`. `scope_org_unit_id IS NULL` ise atama **hiçbir
  kapsam üretmez** (fail-closed; NULL "tüm tenant" **değildir** — kiracı geneli yetki
  `TENANT` tipli rolle, `tenant_admin`/`tenant_officer`).
- **Uygulama durumu (M10–M13 ✅, 2026-09-11):** tablolar **kuruldu**; kapsam çözümleyici
  (`apps/rbac/services/scope.py`) artık **gerçek alt ağacı** hesaplar (recursive CTE,
  derinlik ≤ 20): `UNIT` = atanan birim + alt ağaç, `UNIT_DIRECT` = yalnız atanan birim,
  `TENANT` = kiracı geneli, `SELF` = kendi kaydı, çoklu atama = **birleşim** (tek kiracı
  içinde). `user_roles.scope_org_unit_id` **gerçek FK**'dir (M9 tamamlandı). Fail-closed:
  silinmiş/silinmiş ata altındaki birim, çözülemeyen kapsam ve kiracı uyuşmazlığı
  **kapsam üretmez**.
- **Karar kuralı:** `izinli = (izin kodu var) ∧ (kayıt etkin kapsamda)`. Kapsam izin
  üretmez; izin de kapsam üretmez. Biri yoksa **reddet**. Merkezi uygulama noktası:
  `apps/organization/services.py::can_access_employee()` (çalışan hedefi) —
  görünümler bu fonksiyonu kullanır, kontrolü kopyalamaz.
- **Çoklu atama:** aynı kiracı içinde birden fazla atama **birleşir (union)**;
  kiracılar arası birleşim **yok**. Soft-delete edilmiş, süresi geçmiş atama veya
  pasif kullanıcı **kapsam üretmez**.
- **Kapsam = güncel alt ağaç:** birim taşınırsa kapsam yeni yapıya göre yeniden
  hesaplanır (önbellek geçersiz kılınır) ve taşıma **yüksek etkili denetim olayı**
  olarak yazılır.
- **Kişisel veri:** "çalışanı yönetebilir" ≠ "K2/K3'ü görebilir". `employees` PII
  içermez; kişisel veri ayrı izin + koşul (maskeleme, `MFA_OK`, `PURPOSE_GIVEN`,
  gerekirse `GRANT_VALID`) gerektirir — kapsam bu kuralları **gevşetmez**.
- **Soft-delete/restore (✅ UYGULANDI):** birim silinirse ona bağlı **kapsam atamaları da
  devre dışı bırakılır** (`user_roles.deleted_at`) → atama kapsam üretmez; birim
  **restore edilse bile** atama **sessizce** yeniden etkinleşmez (OD21) — yetki için
  **açık**, denetimli yeni atama gerekir. Birim ancak **boşken** silinir (aktif alt birim
  veya aktif çalışan varsa servis reddeder).

## 6. Birincil yönetici ve ek ilişkiler

- `employee_manager.relation_type` ∈ {`primary`, `secondary`, `delegate`}.
- **Birincil** yönetici zorunlu değildir (ör. genel müdürün yöneticisi yoktur) ama
  varsa **tektir** (kısmi benzersizlik).
- İlişkiler tarihlidir (`effective_from`/`effective_to`): yönetici değişikliği eski
  satırı **kapatır**, yenisini açar → geçmiş korunur (OD21).
- Yetki devri (delege) ayrı bir türdür ve **sürelidir**; süre bitince ilişki
  kendiliğinden geçersiz olur.
- Yönetici ataması **yetki etkisi taşıdığı için** denetim kaydına yazılır.

**`employee_manager` ≠ `dept_manager` (OD5 ile kesinleşti ✅):**

| | `employee_manager` | `dept_manager` |
|---|---|---|
| Ne tanımlar | **Çalışan bazlı** raporlama/onay ilişkisi (`primary`/`secondary`/`delegate`, tarihli) | **RBAC rolünün organizasyon birimi kapsamı** (`UNIT`) |
| Yetki üretir mi | **Hayır** — yalnızca ilişki/onay yönlendirmesi | **Evet**, yalnızca RBAC izin setiyle birlikte (izin ∧ kapsam) |
| Kaynağı | `employee_manager` satırı | `user_roles` + `scope_org_unit_id` |

- Bir çalışanın **birincil yöneticisi olmak `dept_manager` izin seti KAZANDIRMAZ**;
  **`dept_manager` rolü olmak `employee_manager` satırı ÜRETMEZ**.
- `departments.manager_employee_id` (birim sorumlusu) da **yetki üretmez**.
- İki bilgi onay akışında **birlikte** kullanılabilir (ör. `ASSIGNED` koşulu +
  `UNIT` kapsamı) ama **authorization kaynakları ayrıdır**.

## 7. Çalışanın yaşam döngüsü

| Durum | Anlamı | Silme davranışı |
|---|---|---|
| Aktif (`active`) | Çalışıyor | — |
| Askıda (`suspended`) | İdari bekleme; **giriş engelli**, kayıt ve geçmiş korunur | — |
| Ayrıldı (`terminated`) | İlişki bitti (`termination_date` + `termination_reason_code` dolu) | Kayıt **arşivde**, kişisel veri saklama politikasına tabi |
| Arşiv (`archived`) | Uzun süreli saklama, raporlama için okunabilir; **listelerde görünmez** | — |

**Durum makinesi — ✅ KESİNLEŞTİ (OD41, 2026-09-13; ayrıntı: `docs/HR_ROADMAP.md` §28.2):**

| Geçiş | İzin | Zorunlu | Yan etki |
|---|---|---|---|
| `active → suspended` | `employee.suspend` | gerekçe | `users.status='inactive'`, token iptali, `auth_version++` |
| `suspended → active` | `employee.restore` | gerekçe | erişim yeniden açılır (yeni giriş gerekir) |
| `active/suspended → terminated` | `employee.terminate` | `termination_date` + `termination_reason_code` + gerekçe | erişim kesilir (yukarıdaki üç adım) |
| `terminated → archived` | `employee.delete` (**arşivleme**) | gerekçe | listelerde görünmez |
| `terminated → active` | `employee.restore` | gerekçe (**hata düzeltme**) | geçmiş korunur |
| `archived → active` | — | — | **YASAK** (yalnız P9 KVKK/denetimli akış) |
| `terminated → suspended` | — | — | **YASAK** |

- **İhbar dönemi (`notice_period`) ayrı bir durum DEĞİLDİR** (OD41): `status ∈ {active, suspended}`
  ∧ `termination_date > bugün` ise UI'da **"ihbar döneminde"** olarak **türetilir**; yeni kolon
  `notice_end_date` yalnız ihbarın **bitiş** tarihini tutar.
- İzinsiz geçiş **409** (fail-closed) + deneme audit'e yazılır; aynı duruma yazma **no-op** (200).
- `suspended` çalışan **izin talebi açamaz** (OD42); askıdan önce açılmış talepler iptal edilmez.

**Uygulama (P1-B ✅, 2026-09-14):** geçişler yalnız merkezi servis
(`apps/organization/lifecycle.py`: `suspend_employee`, `restore_employee`,
`terminate_employee`, `archive_employee`, `change_manager`) üzerinden yapılır;
view/model `save()` yolu yoktur.

- **Erişim kesme (OD59):** suspend/terminate/archive'da bağlı kullanıcı
  `users.status='inactive'` yapılır, API token'ları iptal edilir
  (mevcut `revoke_tokens_for_inactive_user`) ve `users.auth_version` artırılır;
  restore'da kullanıcı tekrar `active` olur ama `auth_version` yine artar →
  **eski oturum/token asla dirilmez**, yeni giriş zorunludur. Oturum açılışında
  snapshot (`users.auth_version` / `api_tokens.auth_version_snapshot`)
  `apps/accounts/auth_version.py` ile her istekte karşılaştırılır; uyuşmazlık
  → oturum flush + `auth.session_revoked` audit + 401.
- **Planlı çıkış (OD43):** `termination_date > bugün` ise durum korunur
  (`employee.termination.schedule` audit'i yazılır, erişim kesilmez);
  vadesi gelenleri terminated yapan idempotent komut:
  `python manage.py process_employee_terminations` (çift çalıştırma duplicate
  audit/token üretmez; scheduler bağlama P2/ops turunda).
- **Aynı duruma yazma no-op** (audit/token üretilmez); eşzamanlı geçişler
  `select_for_update` ile sıraya alınır.
- `suspended`/`terminated` çalışan **yeni izin VE fazla mesai talebi açamaz**
  (OD42; `apps/leave/services/*` + `apps/overtime/services/authorization.py`
  kümesi yalnız `active`'e daraltıldı); geçmiş talepler okunabilir kalır.

Kurallar (OD21): çalışan kaydı **fiziksel silinmez**; `deleted_at` + `status`
kullanılır. Soft-delete edilmiş kaydın yeniden açılması açık bir `*.restore` izni ve
denetim kaydı gerektirir (**sessiz yeniden etkinleştirme yasak**). Benzersizlikler
kısmidir: `(tenant_id, employee_no) WHERE deleted_at IS NULL`; ayrıca **bir kullanıcıya
en fazla bir aktif çalışan kaydı** (`UNIQUE(user_id) WHERE deleted_at IS NULL AND
status <> 'archived'` — OD59).

**Numara üretimi (M12/0013):** `employee_no` **sunucuda** üretilir (`EMP-000001`
biçimi, `hr.employee_no_seq` sequence). Arayüz kullanıcıdan numara istemez ve
istemcide numara hesaplanmaz; **birim değişikliği numarayı değiştirmez** (numara
işe alımda bir kez üretilir ve saklanır). Sicil numarası **kimlik değildir** →
`public_id` ile karıştırılmaz.

**Uygulama (M12 ✅):** durum kümesi DB `CHECK` ile dayatılır
(`employees_status_valid`: `active`/`suspended`/`terminated`/`archived`);
`termination_date` boş ya da `hire_date`'ten sonra olmalıdır; çalışan kaydında
**kişisel veri kolonu yoktur** (ad/iletişim/kimlik/sağlık verisi
`employee_personal_data` içinde, K3 alanları şifreli: `health_notes`,
`disability_status`, `biometric_ref` ve **kan grubu** — `blood_type`, migration
0012; sağlık verisi sayıldığı için şifreli, kapalı küme A+/A-/B+/B-/AB+/AB-/0+/0-).

### 7.1 İstihdam koşulları, işyeri ve geçmiş — ✅ **P1-A UYGULANDI (2026-09-13)**

| Kavram | Nerede | Not |
|---|---|---|
| **İşyeri / lokasyon** | `hr.workplaces` (YENİ) — `code`, `name`, `type` (`office\|branch\|remote\|client_site`), **`country_code`**, `timezone`, adres | **Departman ile karıştırılmaz:** departman = organizasyon ekseni ("kim nereye bağlı"), işyeri = fiziksel/mali eksen (ülke, saat dilimi, resmî tatil, bordro) |
| **İstihdam koşulları** | `hr.employee_employment_terms` (YENİ, **etkili tarihli**, tek açık satır): `employment_type`, `working_model`, `workplace_id`, `contract_type`, `contract_start_date`, `contract_end_date`, `probation_end_date` | Sözleşme yenileme ve model değişikliği **üzerine yazılmaz**; yeni satır açılır (OD60) |
| **Departman / pozisyon geçmişi** | `hr.employee_assignments` (YENİ, etkili tarihli, tek açık satır) | `move_employee` eski satırı kapatır, yenisini açar; `employees.department_id/position_id` "güncel değer" olarak **korunur** ve aynı servis yolundan yazılır |
| **Yönetici geçmişi** | mevcut `hr.employee_manager` (etkili tarihli) | **Yeni sistem kurulmaz**; değişimde eski ilişki kapanır, yenisi açılır |
| **Maaş geçmişi** | P6 `hr.employee_compensation` | P1'de ücret alanı **yok** |
| **Ülkeye özgü alanlar** | kural setleri (P7) + koşullu form görünürlüğü + belge türleri (P3) | `employees` tablosuna ülke bazlı kolon **eklenmez** |

**Uygulama (P1-A ✅):** tablolar `organization/0014_p1a_people_lifecycle` ile oluşturuldu;
RLS+FORCE (`0016_rls_p1a`), kiracılar arası guard'lar (`0017`), "soft-delete edilmiş
birim/pozisyon/işyeri yeni **açık** satırda kullanılamaz" guard'ı (`0018`) ve mevcut
çalışanlar için atama backfill'i (`0015`) uygulandı. **Servisler ve arayüz P1-B ile
uygulandı (2026-09-14):** `apps/organization/services.py` içinde `change_assignment`
(eski açık satır kapanır, yenisi açılır; `employees.department_id/position_id`
snapshot'ı aynı işlemde senkron) ve `change_employment_terms` (yeni koşul satırı;
üzerine yazım yok); yönetici değişimi mevcut `set_primary_manager` altyapısı +
`change_manager` (audit: `employee.manager.change`) ile. Birleşik **iş geçmişi**
(`apps/organization/history.py::employee_history`) bu tabloları business-readable
olaylara eşler (`Görev yeri değişti`, `İstihdam koşulları güncellendi`,
`Yönetici değişti/atandı`, durum olayları) — PII değeri döndürülmez.

Kesin şema, durum makinesi, form politikası ve izin seti: `docs/HR_ROADMAP.md` **§28**;
karar kayıtları: `docs/AUTHORIZATION.md` **§16**.


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
| **OD5** | Departman yöneticisinin kapsamı: alt ağaç mı, yalnızca doğrudan birim mi? | **Kapalı** (2026-09-11) — **(A) alt ağaç** (`UNIT`): atandığı birim + tüm alt birimler; kapsam kaynağı `user_roles.scope_org_unit_id`, NULL **fail-closed** (asla "tüm tenant" değil); ayrıntı `docs/AUTHORIZATION.md` OD5 §5.1–5.11 |
| **OD27** | Kiracı içinde birden fazla tüzel şirket (`companies`) | **Kapalı** (2026-09-11) — v1 tek tüzel kişilik; gelecek yolu ve tetikleyiciler bölüm 9'da |
| **OD24** | Ağaç mekanizması ve döngü garantileri | **Kapalı** (2026-09-11) |
| — | Organizasyon ağacı için gerçek ölçüm (derinlik, birim sayısı, kapsam sorgu süresi) | Uygulama sonrası ölçülecek (geri dönüş tetikleyicileri bölüm 3'te) |
