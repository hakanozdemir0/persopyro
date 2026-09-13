# Persopyro — Uçtan Uca HR Ürün Mimarisi ve Geliştirme Yol Haritası

**Durum:** `designed-not-coded` (bu doküman **plan** dokümanıdır; bu turda **hiçbir kod,
migration, DB değişikliği veya yeni uç eklenmemiştir**).
**Son güncelleme:** 2026-09-13 · **Ölçüm temeli:** `6b59ebd` kodu (doküman commit'i `4e417d8`; kod değişmedi)
· 473 test yeşil · ölçümler **bağımsız envanter ajanı + doğrudan kod/DB okuması** ile çapraz kontrol edildi (2026-09-13).
**Kapsam:** işe girişten işten ayrılışa kadar İK operasyonunun tamamı; çok ülkeli (TR/AB/ABD)
genişleyebilir mimari.
**Tek doğruluk kaynağı:** bu doküman. Diğer dokümanlar aynı planı tekrar etmez, buraya **referans
verir** (§29).

> Bu sürüm, dokümanın önceki taslak sürümündeki (fazla mesai + dinamik onboarding formu)
> ayrıntıları **§4** ve **§11** altında bütünleştirir; o sürümde önerilen **OD32–OD40**
> numaraları **korunmuştur** (§26.1).

---

## 0. Nasıl okunur

| Bölüm | Ne için |
|---|---|
| §1 | **Ölçüm:** mevcut sistem ne durumda (4 sınıflı olgunluk tablosu + kanıtlar) |
| §2 | **Modül haritası** (bounded context'ler ve bugünkü karşılıkları) |
| §3–§21 | Modül bazlı **tasarım** (people, form, onboarding, offboarding, bildirim, belge, izin, devam, fazla mesai, ücret, bordro, ülke kuralları, TR hesapları, raporlama, panel, ESS, MSS, görev matrisi, sınırlar) |
| §22 | **Fazlandırma** (P0–P9; her fazda amaç, tablolar, servisler, izinler, audit, job, kabul kriterleri, OD blocker, migration riski) |
| §23 | **Açık OD6–OD18 analizi** (hangisi hangi fazı bloklar) |
| §24–§25 | Fonksiyonel olmayan gereksinimler ve **ölçek sınırları** |
| §26–§29 | Önerilen açık kararlar, bağımlılık grafiği, sıradaki paket, doküman haritası |

**Yazım kuralı:** "✅ VAR" yalnız **koddan doğrulanmış** demektir; kanıt (modül/tablo/fonksiyon
adı) verilir. Doğrulanmayan hiçbir şey "var" sayılmaz.

### 0.1 Zaten verilmiş kararlar (bu doküman **yeniden karar vermez**)

Aşağıdaki konularda karar **mevcuttur**; roadmap bunları **uygulama işi** olarak planlar:

| Konu | Karar yeri | Durum |
|---|---|---|
| Görev kuyruğu / worker | `docs/ARCHITECTURE.md` §10 — django-q2 + ORM broker. **Ölçüldü:** paket **kurulu** (`pyproject.toml:20` `django-q2>=1.10`), `Q_CLUSTER` **yapılandırılmış** (`config/settings.py:274-285`: `max_attempts` 3, `retry` 600 s, `save_limit` 500, `catch_up` False, **`sync: DEBUG`** → geliştirmede senkron) | kuyruk var, **hiçbir görev kuyruğa atılmıyor** (kod yok) |
| Dosya saklama | `docs/ARCHITECTURE.md` §9 (+ K7) — **S3 uyumlu özel nesne depolama + boto3**, ön imzalı URL | karar var, kod yok |
| E-posta | `docs/ARCHITECTURE.md` §11 — Django SMTP + sağlayıcı bağımsız relay, **kuyruk üzerinden** | ayarlar var, kod yok |
| Redis / cache | `docs/ARCHITECTURE.md` §6.1 — **Redis yok**, DB cache + DRF throttling | uygulandı |
| Denetim kaydı | `docs/ARCHITECTURE.md` §12 — DB tetikleyicisi + `log_action`, uygulama rolüne yalnız INSERT/SELECT | uygulandı |
| Vardiya + devam tabloları | `docs/DATABASE.md` — **M15** (`hr.shifts`, `hr.shift_assignments`, `hr.attendance_records`) | tasarlandı, kod yok |
| Belge tabloları | `docs/DATABASE.md` — **M16** (`hr.documents` + `hr.document_access`, içerik **K4**) | tasarlandı, kod yok |
| MFA | `docs/ARCHITECTURE.md` §7 — TOTP `tenant_admin` + `super_admin` için zorunlu | ⚠️ karar var ama **OD10 hâlâ AÇIK** (§23) |

> Bu nedenle §8 belge bölümü **yeni depolama kararı önermez**; §7 bildirim bölümü **yeni kuyruk
> altyapısı önermez**; §10 devam bölümü **M15 şemasını yeniden yazmaz, genişletir**.

---

## 1. Ölçüm: mevcut sistemin olgunluk durumu

### 1.1 Ölçüm yöntemi (tekrarlanabilir)

```bash
# kod ölçeği ve migration sayısı
for d in apps/*/; do find "$d" -name '*.py' -not -path '*/migrations/*' | xargs wc -l | tail -1; done
ls apps/*/migrations/*.py | grep -v __init__ | wc -l

# izin kataloğu (kod biçimi: <kaynak>.<işlem>)
python -c "from apps.rbac.permission_catalog import PERMISSIONS; print(len(PERMISSIONS))"

# modül envanteri
grep -rn "class .*(TenantScopedModel)\|class .*(UUIDModel)" apps/*/models.py

# test ölçeği
grep -rc "^def test_" tests/*.py
bash scripts/run_tests.sh            # 473 passed (baseline)
bash scripts/verify_fresh_install.sh # VERIFY_EXIT=0
```

### 1.2 Ölçülen büyüklükler

| Ölçüm | Değer |
|---|---|
| Uygulama paketi | 9 (`accounts`, `audit`, `common`, `leave`, `organization`, `platform`, `rbac`, `tenancy`, `testui`) |
| Kod satırı (migration hariç) | **11.896** |
| Kod satırı (migration dahil) | **17.307** |
| Test satırı | **10.813** (19 test dosyası, **462** `def test_` fonksiyonu → pytest sonucu **473 test**; parametreli testler dahil) |
| Migration | **49** |
| Model sınıfı | **43** `apps/*/models.py` sınıf tanımı → bunlardan **21'i kayıtlı somut proje modeli** (leave 7, organization 5, rbac 5, accounts 2, audit 1, tenancy 1), **4'ü soyut taban** (`apps/common`: UUIDModel, TimeStampedModel, SoftDeleteModel + 1; kayıtlı değil), kalanı model dışı sınıf. Çerçeve modeli 9 (django_q 5, auth 2, contenttypes 1, sessions 1) |
| Tablo | **28** benzersiz `db_table` adı; canlı DB'de `hr`+`public` şemasında **33 tablo** (migration'la oluşan dizin/M2M tabloları dahil) |
| İzin kodu | **39** / 12 modül — **33 kiracı** (`leave` 8, `employee` 5, `document` 4, `org` 3, `shift` 3, `tenant` 2, `user` 2, `role` 2, `attendance` 2, `audit` 1, `billing` 1) + **6 platform** (`platform.*`); canlı DB'den doğrulandı: `hr.permissions` = 39 satır (33 kiracı + 6 platform) |
| Varsayılan rol | **5** (`tenant_admin`, `tenant_officer`, `hr_manager`, `dept_manager`, `employee`) |
| Doküman | 11 dosya, ~9.400 satır (`AUTHORIZATION` 2.086, `CHANGELOG` 3.158, `DATABASE` 952, `SECURITY` 749, `ARCHITECTURE` 666 …) |

> **Dikkat (ölçüm bulgusu):** `shift`, `attendance`, `document`, `billing` modüllerinin **izin
> kodları vardır ama modülleri yoktur**. `hr_manager` rolü bugün bu izinleri taşır
> (`shift.assign`, `attendance.manage`, `document.upload` …) — yani **yetki kataloğu
> uygulamadan ileridedir**. Bu bir hata değil, planlı bir hazırlıktır; ancak rol izin
> ekranında "kullanılamayan yetki" görüntüsü yaratır ve **OD16 (katalog evrimi)** kararını
> önemli kılar.

### 1.3 Olgunluk sınıflandırması (koddan doğrulanmış)

**Sınıf 1 — Tamamen uygulanmış (çalışıyor + testli)**

| Yetenek | Kanıt |
|---|---|
| Kiracı izolasyonu + PostgreSQL RLS (FORCE) | Ölçüldü (canlı DB): `hr`+`public` şemasında **33 tablo**, bunların **19**'unda `relrowsecurity` açık, tek politika `tenant_isolation` (`USING`/`WITH CHECK` = `hr.current_tenant_id()`); `hr.*` tablolar `tenant_id` + politika; `verify_fresh_install.sh` RLS adımları |
| Veritabanı rolleri ayrımı (owner/app/platform) | `scripts/bootstrap.sh`, `scripts/02_grant_app_privileges.sql`, `tests/test_database_roles.py` |
| Kimlik doğrulama (oturum + API token) | `apps/accounts`, `apps/platform`; `tests/test_api_auth.py`, `test_api_tokens_db_guards.py` |
| RBAC + organizasyon kapsamı + self-elevation yasağı | `apps/rbac/services/roles.py`, `tests/test_rbac_scope_m7_m9.py` |
| Denetim izi (audit) | `apps/audit/services.py` `log_action`, `tests/test_audit_integrity.py` |
| Organizasyon ağacı (adjacency list, döngü guard'ı, soft delete) | `apps/organization`, `tests/test_organization_m10_m13.py` |
| Çalışan kaydı (iş kaydı, PII ayrı tablo) + otomatik çalışan no | `hr.employees`, `hr.employee_no_seq` (migration 0013) |
| Kişisel veri K2 (açık) + K3 (Fernet şifreli) + yetki kapısı | `hr.employee_personal_data`, `apps/common/fields.py`, `apps/common/crypto.py` |
| Birincil yönetici ilişkisi (etkili tarihli, tek aktif) | `hr.employee_manager` |
| İzin türü + politika + kıdem kuralı | `hr.leave_types`, `hr.leave_policies`, `hr.leave_policy_rules` |
| Hakediş (idempotent) + yıl dönümü yenilemesi | `accrue_leave_entitlements`, `next_accrual_date`, `manage.py accrue_leave_entitlements` |
| Bakiye + append-only defter + manuel düzeltme (gerekçeli, audit'li) | `hr.leave_balances`, `hr.leave_balance_entries`, `adjust_leave_balance` |
| İzin talebi akışı (talep, çok adımlı onay, red, iptal, iade, override) | `hr.leave_requests`, `hr.approval_steps`, `tests/test_leave_m14_*.py` |
| Geliştirme/test arayüzü (kapsam görünürlüğü, çıkışın oturumu kapatması) | `apps/testui`, `tests/test_testui.py` (104 test) |
| Sıfırdan kurulum doğrulaması | `scripts/verify_fresh_install.sh` (6c-1…6c-10) |

**Sınıf 2 — Kısmen uygulanmış (parçası var, tamamlanmadı)**

| Yetenek | Var olan | Eksik |
|---|---|---|
| Çalışan durumu / lifecycle | `hr.employees.status` kapalı kümesi (`active/suspended/terminated/archived`) + `termination_date` | **geçiş servisi yok** (hiçbir servis `terminated` yazmıyor), ihbar dönemi, çıkış süreci |
| İzin belge zorunluluğu | `leave_types.requires_document` + `leave_requests.document_ref` (UUID) | **belge modülü yok** → dosya yüklenemez/doğrulanamaz |
| Devir (carry-forward) | `leave_balances.carried_forward_days` kolonu (kalan hesabına giriyor) | **onu yazan servis yok** (yıl sonu devir kuralı yok) |
| İzin gün hesabı | `calculate_requested_days` (takvim günü, yarım gün) | hafta sonu/resmi tatil/çalışma takvimi yok |
| Self service (çalışan) | izin talebi + bakiye + kendi kaydı | fazla mesai, belge, bordro belgesi, bilgi değişiklik talebi |
| Manager self service | ekip görünürlüğü + izin onayı + ekip bakiyesi | ekip takvimi, fazla mesai onayı, vardiya, değişiklik talepleri |
| Dashboard | panel kartları (toplam/izinde/yaklaşan/bekleyen/kritik bakiye) | görev, belge, deneme süresi, sözleşme, bordro kartları |
| Raporlama | ekran içi tablolar + filtreler | merkezî export servisi, XLSX, asınk üretim, güvenli indirme |
| Yetki kataloğu ↔ modül | **39** izin kodu (33 kiracı + 6 platform; `shift`/`attendance`/`document` izinleri var) | ilgili modüller yok (bkz. §1.2 notu) |
| Uluslararasılaştırma | `USE_TZ`, tek TZ; Türkçe metinler | i18n altyapısı, çoklu dil, çoklu para birimi |

**Sınıf 3 — Yalnız planlanmış (tasarım var/başlanmadı)**

Fazla mesai · yapılandırılabilir onboarding formu · onboarding/offboarding görev listeleri ·
bildirim/e-posta (outbox) · belge yönetimi · ücret/maaş geçmişi · bordro hazırlığı · ülke kural
motoru · TR hesaplamaları · merkezî raporlama/Excel · devam/vardiya + çalışma takvimi ·
görev merkezi (tam hâli).
**Kanıt:** ilgili modüller için `apps/` altında paket, `models.py` veya `services/` **yok**;
yalnız izin kodları ve doküman taslakları var (§1.2 notu).

**Sınıf 4 — Hiç başlanmamış (kapsam dışı veya V1 dışı)**

Performans yönetimi · işe alım/aday takibi (ATS) · resmi bordro beyanı (e-beyan/SGK bildirgesi) ·
eğitim/LMS · turnike/kart cihaz entegrasyonu (yalnız içe aktarım yolu planlanır) · MFA ·
nesne depolama (S3) · kur dönüşümü · planlı/paylaşımlı raporlar · mikroservis/dağıtık mimari
(**bilinçli olarak yapılmayacak**, §25).

### 1.4 En büyük fonksiyonel eksikler (öncelik sırasıyla)

1. **İstihdam koşulları ve sözleşme** — bugün çalışanın "ne zaman, hangi koşullarda" çalıştığı
   modellenmiyor (P1).
2. **Bildirim/e-posta** — izin akışı tamamen arayüz bağımlı; e-posta ile çalışmıyor (P2).
3. **Belge** — İK'nın ana işi olan belge takibi yok; izin "belge gerekir" bayrağı işlevsiz (P3).
4. **Devam/vardiya + çalışma takvimi** — izin gün hesabı bu yüzden yanlış (takvim günü) (P4).
5. **Fazla mesai, ücret geçmişi, bordro hazırlığı** — İK operasyonunun ikinci yarısı (P5–P6).
6. **Uluslararasılaşma altyapısı** — i18n/çoklu para birimi/ülke kuralları: ürün bugün
   fiilen tek ülkeli (P7).
7. **Çıkış süreci** — `terminated` durumu **hiç yazılmıyor**; erişim kapatma akışı yok (P9).

---

**Ölçülen altyapı doğrulamaları (2026-09-13, canlı DB + kod okuması)**

| Ölçüm | Sonuç | Kanıt |
|---|---|---|
| `hr.permissions` satırı | **39** (33 kiracı + 6 platform) | `SELECT count(*) …` (canlı DB) |
| RLS açık tablo | **19** / 33 tablo | `pg_class.relrowsecurity` (canlı DB) |
| Varsayılan kiracı rolleri | 5 (`tenant_admin` 33, `hr_manager` 22, `tenant_officer` 21, `dept_manager` 16 — UNIT, `employee` 8 — SELF) | `permission_catalog.py:118-228` |
| Platform rolleri | 2 kapalı küme (`superadmin` 6, `platform_admin` 4) | `apps/platform/constants.py` |
| Django Admin | **yok** (OD19 kapalı; `/admin/` route yok) | `tests/test_od19_django_admin_kapali.py` |
| Throttle oranları | `auth` **10/dk**, `user` 1000/sa, `anon` 60/dk | `config/settings.py:256-260` |
| Kuyruk | django-q2 kurulu + `Q_CLUSTER` yapılandırılmış, **görev yok** | `settings.py:274-285`, `pyproject.toml:20` |
| Dosya deposu | `STORAGE_S3_*` ayarlı, storage backend/boto3 **yok** | `settings.py:300-309` |
| E-posta | SMTP ayarlı, `send_mail`/`EmailMessage` çağrısı **yok** | grep → 0 |
| i18n | `LANGUAGE_CODE="tr"`, `USE_I18N=True`, `locale/` **yok** | `settings.py:329-334` |
| Yedekleme betiği | `scripts/` içinde pg_dump/pg_restore **yok** (yalnız plan) | `docs/ARCHITECTURE.md` yedekleme bölümü |
| Para birimi ayarı | **YOK** (`billing.manage` izni var, currency alanı/ayarı yok) | grep → 0 |

### 1.4 Ölçüm kanıt işaretçileri (dosya:satır)

| Bulgu | Kanıt (kod) |
|---|---|
| İzin gün hesabı **takvim günü**; çalışma takvimi yokluğu kodda açıkça yazılı | `apps/leave/services/requests.py:7-9`, `:90-92`; `apps/leave/models.py:512-513` ("M15, `work_schedules` gelene kadar") |
| Vardiya/evrak modülleri arayüzde "gelecek" olarak işaretli | `apps/testui/labels.py:87-89` ("M15/M16 ile gelecek") |
| `approval_steps` tablosu **vardiya düzeltmesi** gibi ileriki onayları taşımak üzere genel tasarlanmış | `apps/leave/models.py:23-25` |
| Belge erişim izinleri var, **model/tablo yok** | `apps/rbac/permission_catalog.py:51-54` (`document.*`); `LeaveRequest.document_ref` (FK değil, `UUIDField`) `apps/leave/models.py:560-567` |
| Maaş/ücret verisi yok: tek ücret sinyali izin türü tarafı | `LeaveType.is_paid` (`apps/leave/models.py:67`), `LeaveCategory.UNPAID` (`apps/leave/leave_type_catalog.py:30`) |
| **Raporlama izin kodu bile yok** (`report.*` / `export.*`) → yeni izin kodları gerekecek | `apps/rbac/permission_catalog.py` (`report` yalnız etiket sözlüğünde) |
| Ülke bilgisi yalnız veri alanı düzeyinde | `Tenant.country` (`apps/tenancy/models.py:50`), `EmployeePersonalData.citizenship`/`work_permit*` |
| Fazla mesai / bordro / onboarding / sözleşme / deneme süresi / lokasyon / çalışma modeli: kodda **0 eşleşme** | `apps/` + `config/` anahtar kelime taraması (ölçüm ajanı raporu) |

## 2. Modül haritası (bounded context'ler)

| # | Bağlam | Bugünkü paket | Durum | Hedef faz |
|---|---|---|---|---|
| 1 | People (çalışan + istihdam koşulları) | `apps/organization` (kısmen) | 🟡 kısmi | P1 |
| 2 | Organization (ağaç, birim, unvan) | `apps/organization` | ✅ tam | — |
| 3 | Identity/RBAC/Tenancy | `apps/accounts`, `apps/rbac`, `apps/platform`, `apps/tenancy` | ✅ tam | — |
| 4 | Leave | `apps/leave` | ✅ tam (takvim hariç) | P4 (takvim) |
| 5 | Attendance/Shift | — | ❌ yok | P4 |
| 6 | Overtime | — | ❌ yok | P5a/P5b |
| 7 | Documents | — | ❌ yok | P3 |
| 8 | Notifications | — | ❌ yok | P2 |
| 9 | Compensation | — | ❌ yok | P6 |
| 10 | Payroll (hazırlık) | — | ❌ yok | P6 |
| 11 | Calculations (ülke kuralları + TR) | — | ❌ yok | P7 |
| 12 | Reporting/Export | — (ekran içi tablolar) | 🟡 kısmi | P8 |
| 13 | Onboarding | — | ❌ yok | P1 |
| 14 | Offboarding | — (durum alanı) | 🟡 kısmi | P9 |
| 15 | Audit | `apps/audit` | ✅ tam | — |
| 16 | Dev/Test arayüzü | `apps/testui` | ✅ tam (sürekli gelişir) | — |

**Karar (öneri):** tek Django projesi, **modular monolith** — her bağlam kendi `apps/<ad>`
paketi + servis katmanı. Mikroservis bölünmesi **yapılmaz** (§21'de sınır kuralları ve
gerekçe).

## 3. Kişi / çalışan yaşam döngüsü (People lifecycle)

Bu bölüm **tasarımdır** (kod yazılmadı). Mevcut durum: `hr.employees` (iş kaydı, PII yok) +
`hr.employee_personal_data` (K2 açık / K3 şifreli) + `hr.employee_manager` (birincil yönetici,
etkili tarihli) uygulanmıştır (M12/M13). Aşağıdaki tablo hangi işe-giriş alanının **bugün
karşılandığını**, hangisinin **eksik** olduğunu koddan ölçülmüş hâliyle verir.

### 3.1 İşe giriş (onboarding) veri alanları — mevcut karşılama

| Gereksinim | Bugün nerede | Durum |
|---|---|---|
| Temel kimlik (ad, soyad) | `hr.users.first_name/last_name` + K2 `employee_personal_data.first_name/last_name` | ✅ VAR |
| Kurumsal kullanıcı hesabı | `hr.users` + kiracı/e-posta/parola; çalışan oluşturmada **zorunlu** ve **atomik** (test UI akışı) | ✅ VAR (UI akışı), API'de servis ayrımı sadeleştirilmeli |
| Kişisel bilgiler (doğum tarihi, kimlik no) | `employee_personal_data.birth_date`, `.national_id` (K2) | ✅ VAR |
| İletişim (kişisel e-posta/telefon) | `.personal_email`, `.personal_phone` (K2) | ✅ VAR |
| Adres | `.address` (K2) | ✅ VAR |
| Acil durum kişisi | `.emergency_contact_phone`, `.emergency_contact_relation` (K2) | ✅ VAR (isim/adres alanı yok) |
| Vatandaşlık | `.citizenship` (K2) | ✅ VAR |
| Çalışma/oturum izni | `.work_permit_type`, `.work_permit_valid_until` (K2) | ✅ VAR |
| Banka / IBAN | `.bank_name`, `.iban` (K2) | ✅ VAR |
| İşe giriş tarihi | `hr.employees.hire_date` | ✅ VAR |
| Çalışan numarası | `hr.employees.employee_no`, sunucu üretir (`hr.employee_no_seq`) | ✅ VAR |
| Departman | `hr.employees.department_id` (NOT NULL) | ✅ VAR |
| Pozisyon/unvan | `hr.employees.position_id` → `hr.positions` | ✅ VAR |
| Birincil yönetici | `hr.employee_manager` (etkili tarihli, tek aktif birincil) | ✅ VAR |
| Durum | `hr.employees.status` (kapalı küme; aşağıda) | ✅ VAR (kısmi işlev) |
| **Çalışma modeli** (ofis/hibrit/uzaktan, kısmi süre) | yok | ❌ YOK |
| **İstihdam tipi** (tam/yarı zamanlı, sözleşmeli, stajyer) | yok | ❌ YOK |
| **Lokasyon / işyeri** | yok (yalnız organizasyon ağacı) | ❌ YOK |
| **Sözleşme tipi + başlangıç/bitiş** | yok | ❌ YOK |
| **Deneme süresi (probation)** | yok | ❌ YOK |
| **Ücret bilgisi** | yok | ❌ YOK (§12) |
| **Bordro bilgileri** (vergi no, SGK no, bordro profili) | yok | ❌ YOK (§13) |
| **İzin başlangıç/hakediş bilgisi** | dolaylı: `hr.leave_policies` + `hr.leave_balances`; çalışan bazlı politika bağı yok | 🟡 KISMİ |
| **Belge checklist** | yok | ❌ YOK (§8, §5) |

> **Sonuç:** kişisel veri ve organizasyon ekseni **büyük ölçüde tamam**; **istihdam koşulları**
> (çalışma modeli, istihdam tipi, lokasyon, sözleşme, deneme süresi, ücret, bordro) **hiç yok**.
> Yeni alanların tamamı **etkili tarihli** modellerle gelmeli (aşağıdaki §3.2), `hr.employees`
> tablosuna tek tek kolon eklenmemelidir.

### 3.2 Tarihçeli (historical) veri kuralı — **yazma/üzerine yazma politikası**

| Alan / varlık | Kural | Gerekçe |
|---|---|---|
| Departman (`department_id`) | **Değiştirilebilir tek kolon** + `audit_logs` (`employee.department_change`). Geçmiş gerekirse `hr.employee_assignments` ile sonradan modellenir. | Organizasyon geçmişi zaten audit'te; erken aşamada ayrı tablo gereksiz maliyet. |
| Pozisyon/unvan | Aynı: tek kolon + audit (`employee.update`). Terfi geçmişi gerektiğinde `hr.employee_assignments` (effective_from/until) eklenir. | |
| Birincil yönetici | **Zaten tarihçeli**: `hr.employee_manager.effective_from/effective_to`, yeni ilişki açılır, eskisi kapatılır. | Mevcut tasarım doğru; bozulmamalı. |
| **Maaş / ücret** | **Üzerine YAZILMAZ**: `hr.employee_compensation` (effective_from/until, para birimi, periyot, gerekçe). | Yanlış yazım geri alınamaz; bordro/tazminat hesabı geçmişe bağlı. |
| **Çalışma modeli / istihdam tipi / lokasyon / sözleşme / deneme süresi** | **Etkili tarihli** `hr.employee_employment_terms` (veya alan bazlı effective tablo). | Sözleşme yenileme ve model değişikliği yasal olarak izlenebilir olmalı. |
| Rol/yetki ataması | Zaten etkili + soft-delete (`hr.user_roles`, `role.assign`/`role.revoke` audit). | Bozulmamalı. |
| İzin hakları | `hr.leave_balances` (dönem bazlı) + `hr.leave_balance_entries` **append-only** defter. | Mevcut tasarım doğru. |
| K2/K3 kişisel veri | **Tek satır, tarihçesiz** (yanlışsa düzeltme); değişiklikler audit'e **alan adı** olarak yazılır, **değer yazılmaz**. | KVKK: değişiklik geçmişinde eski PII saklamak riski büyütür. |

**Kural (özet):** *para ve koşullar tarihçeli; kimlik/PII tarihçesiz; değişiklik ize audit ile.*

### 3.3 Çalışan durumu (lifecycle) — mevcut kapalı küme kullanılır

Kodda **var olan** kapalı küme: `hr.employees.status ∈ {active, suspended, terminated, archived}`
(DB `CHECK` kısıtı + `EmployeeStatus`). **Yeni enum uydurulmaz.**

Önerilen hedef akış (mevcut küme **genişletilmeden**, gerekirse yalnız iki geçiş eklenerek):

```
                 işe giriş
                     │
                     ▼
   active ──────────────────────────────► suspended ──(dönüş)──► active
     │   ▲                                    │
     │   └──── notice_period (yeni) ◄──────┘  │
     │            │                           │
     └────────────┴────────► terminated ◄─────┘
                                │
                                ▼
                             archived
```

- **`notice_period` (ihbar süreci):** bugünkü kümede **yok**. Karar gerekiyor → **OD41 (öneri)**.
  Öneri: `status` kümesini büyütmek yerine `termination_date` + yeni `notice_end_date` alanı
  kullanmak ve çalışan `active` kalırken "ihbar döneminde" olarak **türetmek** (durum makinesini
  karmaşıklaştırmamak).
- **Geçiş yetkisi:** `employee.update` (mevcut) + yüksek etkili olduğu için işten çıkışta
  **`employee.terminate` (yeni izin, öneri)** ve iki adımlı onay → §6 Offboarding.
- **Askıya alma** bugün **yalnız durum alanı** olarak var; `suspended` çalışan
  - hakediş almaya devam eder (mevcut `accrue_leave_entitlements` kuralı),
  - yeni izin talebi açabilir mi? → **OD42 (öneri): hayır** (izni HR açar) veya evet.
- Her geçiş `audit_logs`'a yazılır: `employee.status_change` (yeni olay adı, öneri).

### 3.4 Çalışan güncellemeleri — akış ve onay ihtiyacı

| Güncelleme | Kim başlatır | Onay | Tarihçe | Mevcut durum |
|---|---|---|---|---|
| Departman değişikliği | HR | tek adım (mevcut) | audit + istenirse atama geçmişi | ✅ çalışıyor (`move_employee`) |
| Terfi/unvan | HR | tek adım | audit | 🟡 kısmi (`position` yazımı var; terfi akışı yok) |
| Yönetici değişikliği | HR | tek adım | etkili tarihli tablo | ✅ çalışıyor |
| Maaş değişikliği | HR | **iki adım** (HR + onaylayıcı) | `employee_compensation` | ❌ yok (§12) |
| Çalışma modeli değişikliği | HR/çalışan talebi | tek adım | etkili tarihli | ❌ yok |
| Sözleşme yenileme | HR | tek adım | etkili tarihli | ❌ yok |
| Rol/yetki | HR (`role.manage`) | mevcut guard'lar | audit + soft-delete | ✅ çalışıyor |
| Kişisel bilgi (K2) | çalışan **talebi** → HR onayı (§18) | HR onayı | audit (değer yok) | ❌ yok |

### 3.5 Kabul kriterleri (P1)

1. Yeni çalışan: istihdam koşulları (model/tip/lokasyon/sözleşme/deneme) **tek işlemde** ve
   **etkili tarihli** yazılır; alan zorunlulukları tenant yapılandırmasından gelir (§4).
2. Aynı çalışan için **iki farklı tarihte iki farklı departman/maaş** kaydı DB'de **birlikte**
   bulunur; geçmiş sorgulanabilir.
3. Durum geçişleri yalnız izin verilen geçişleri kabul eder; izin verilmeyen geçiş **409**
   döner (fail-closed) ve audit'e yazılır.
4. İşten ayrılmış çalışanın `audit_logs` kayıtları **silinmez**, görüntülenebilir kalır.
5. `archived` çalışan hiçbir listede varsayılan olarak görünmez; KVKK gereği ayrı politikaya
   bağlanır (§24 retention).

---

## 4. Yapılandırılabilir çalışan kayıt / onboarding formu

**Yaklaşım kararı (öneri):** ağır "form builder" **yazılmaz**; **metadata + config** yaklaşımı
kullanılır. Form yapısı koddaki alan tanımından **türetilir**, tenant yalnızca *görünürlük /
zorunluluk / sıra / etiket* override eder. Böylece yeni alan eklemek migration + kod işi olur,
tenant yapılandırması ise **veri** olur.

### 4.1 Alan kilit seviyeleri (üç seviye)

| Seviye | Tenant ne yapabilir | Örnek alanlar |
|---|---|---|
| **A. Sistem-kilitli** | Yalnız **etiket/sıra** değiştirebilir; **gizleyemez, opsiyonel yapamaz** | `tenant` (kiracı bağı), çalışan kimliği (`employee_no` — sunucu üretir), `ad`, `soyad`, `hire_date`, giriş hesabı alanları (`email`, `password`), `department` |
| **B. Görünürlük yapılandırılabilir** | Görünür/gizli + zorunlu/opsiyonel + sıra | `birth_date`, `national_id`, `personal_email`, `personal_phone`, `address`, acil durum kişisi, `citizenship`, `work_permit_*`, `bank_name`, `iban`, `position`, `manager`, istihdam koşulları |
| **C. Ülkeye/tipe bağlı koşullu** | Yukarıdakiler + **ülke / istihdam tipi / çalışma modeli** bazlı görünürlük | `work_permit_type`/`work_permit_valid_until` (yalnız `citizenship != TR`), IBAN (yalnız SEPA/TR), `national_id` (ülke başına format) |

**Kural:** A seviyesi alanlar **hiçbir koşulda** kapatılamaz (kullanıcı bildirimi). K3 alanlar
(ör. kan grubu) **zorunlu yapılamaz** — gerekçe: özel nitelikli veriyi zorunlu toplamak KVKK
açısından aşırı veri toplama riski (öneri → OD37, mevcut öneri listesinde).

### 4.2 Şema (plan — uygulanmayacak)

| Tablo | Kolonlar (özet) | Not |
|---|---|---|
| `hr.form_field_definitions` | `id`, `tenant_id`, `form_code` (`employee_onboarding`), `field_code` (kod içi kanonik alan), `section_code`, `section_label`, `label_override`, `helper_text`, `is_visible`, `is_required`, `sort_order`, `applicability` (JSON: `country_in`, `employment_type_in`, `work_model_in`), `lock_level` (A/B/C), `created_by`, audit kolonları | `field_code` **kodda tanımlı** olmalı (allowlist); bilinmeyen kod reddedilir (fail-closed) |
| `hr.form_field_visibility` | Alternatif normalizasyon: `field_definition_id`, `dimension` (`country`\|`employment_type`\|`work_model`), `value`, `is_visible`, `is_required` | Ayrı tablo **şart değil**; ilk sürümde `applicability` JSON yeterli → **OD35** (mevcut öneri) kararı |
| `hr.employee_form_responses` (gerekirse) | Yanıt **saklanmaz**; form yalnız **girdi** üretir, veri asıl tablolara yazılır | Ayrı yanıt tablosu **önerilmez** (çift doğruluk kaynağı yaratır) |

### 4.3 Doğrulama ve güvenlik kuralları

1. **Zorunluluk sunucuda**: form konfigürasyonu **istemciden** gelmez; API, aktif konfigürasyonu
   okuyup zorunlu alanları **sunucuda** dayatır (mevcut `employee + user atomik` akışı korunur).
2. **Bilinmeyen alan reddi**: payload'da tanımlı olmayan alan gelirse **400** (mevcut K3
   fail-closed yaklaşımının genelleştirilmesi).
3. **K2/K3 sınıflandırması bozulmaz**: sınıf **kodda** (alan tanımı) durur; tenant yalnız
   görünürlük/zorunluluk değiştirir. K3 yazımı hâlâ `employee.sensitive.read` + kapsam kapısından
   geçer ve şifreli saklanır.
4. **Değişiklik denetimi**: konfigürasyon değişikliği `audit_logs`'a
   `employee_form.config.update` olarak yazılır (kim, hangi alan, eski→yeni görünürlük).
5. **Sürümleme**: mevcut çalışanların zorunlu alanları sonradan zorunlu yapılırsa **mevcut
   kayıtlar geçersiz sayılmaz**; "eksik alan" görevi üretilir (§5 onboarding). Kesin davranış →
   **OD38** (mevcut öneri).

### 4.4 Kabul kriterleri (P1)

1. Tenant yöneticisi B seviyesi bir alanı gizlediğinde form **o alanı istemez** ve kayıt başarılı
   olur; A seviyesi bir alanı gizlemeye çalıştığında **403/400** alır (kanıt: test).
2. Aynı alan İK (TR) kiracısında zorunlu, başka kiracıda opsiyonel olabilir.
3. Konfigürasyon değişikliği **audit** kaydı üretir ve kim tarafından yapıldığı görülür.
4. K3 bir alan zorunlu yapılamaz (sunucu reddi).

---

## 5. Onboarding (görev/checklist akışı)

**Karar (öneri):** V1'de BPMN/workflow engine **kurulmaz**; mevcut `hr.approval_steps` benzeri
**görev listesi + atama + son tarih + durum** modeli yeterlidir.

### 5.1 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.onboarding_templates` | `id`, `tenant_id`, `code`, `name`, `applies_to` (JSON: ülke/istihdam tipi/birim), `is_active`, audit | Kiracı şablonu; sistem varsayılanı seed |
| `hr.onboarding_template_tasks` | `template_id`, `task_code`, `title`, `description`, `owner_role_code`, `due_offset_days` (işe giriş ±), `is_required`, `sort_order` | Görev tanımı |
| `hr.employee_tasks` (genel görev tablosu) | `id`, `tenant_id`, `employee_id`, `kind` (`onboarding`\|`offboarding`\|`document`\|`contract`\|`probation`), `task_code`, `title`, `assignee_user_id`, `due_date`, `status` (`open`\|`done`\|`canceled`\|`overdue`), `completed_at`, `completed_by`, `reference_kind`/`reference_id` (serbest bağ), audit | **Tek tablo, iki kullanım** (onboarding/offboarding) → mikroservis gibi bölünmez |
| `hr.employee_task_events` | `task_id`, `event`, `actor`, `note`, `created_at` | Görev geçmişi (append-only) |

### 5.2 Varsayılan görev seti (işe giriş)

| # | Görev | Sahip (role_code) | Vade | Zorunlu |
|---|---|---|---|---|
| 1 | Sözleşme imzalandı (belge yüklendi) | `hr_manager` | giriş −7 | ✅ |
| 2 | Kimlik/izin belgeleri alındı | `hr_manager` | giriş −3 | ✅ |
| 3 | Kullanıcı hesabı açıldı (rol atandı) | `hr_manager` | giriş günü | ✅ |
| 4 | Departman + unvan atandı | `hr_manager` | giriş −3 | ✅ |
| 5 | Yönetici atandı | `hr_manager` | giriş −3 | ✅ |
| 6 | Bordro bilgileri tamamlandı (vergi/SGK/banka) | `payroll_prep` (yeni) | giriş +5 | ✅ (P6) |
| 7 | Banka bilgisi doğrulandı | `hr_manager` | giriş +5 | 🟡 |
| 8 | İzin hakkı/hakediş tanımlandı | `hr_manager` | giriş +10 | ✅ |
| 9 | Ekipman/erişim (zimmet) | `hr_manager` | giriş günü | ❌ opsiyonel |
| 10 | Eğitim/oryantasyon | `hr_manager` | giriş +30 | ❌ opsiyonel |
| 11 | Şirket politikaları onayı | `hr_manager` | giriş +30 | ❌ opsiyonel |
| 12 | Çalışma modeli/işyeri teyidi | `hr_manager` | giriş günü | 🟡 |

Görevler **otomatik üretilir**: çalışan oluşturma işlemi başarıyla commit olduktan sonra aynı
transaction içinde `hr.employee_tasks` satırları yazılır (mail/HTTP **gönderilmez**, §7).

### 5.3 Kabul kriterleri

1. Şablonsuz kiracıda **sistem varsayılanı** uygulanır (boş görev listesi ile çalışan açılmaz).
2. Görev tamamlandığında denetim kaydı ve tamamlayan kullanıcı görünür; **geri alma** denetimli.
3. Vadesi geçen görevler `overdue` olarak işaretlenir (hesaplanır, ayrı kolon zorunlu değil) ve
   panelde "bekleyen görev" olarak sayılır (§17).
4. İşe giriş tarihi **geriye alınırsa** görev vadeleri yeniden hesaplanır (mevcut görevlerin
   tamamlanmışları korunur).

---

## 6. Offboarding (işten çıkış)

**İlke:** offboarding, **tek bir servis çağrısı** değil **kontrollü bir süreçtir**; her adım
denetlenir ve geri alınabilirliği açıkça belirtilir. Mevcut kodda **çıkış işlemi yapan servis
YOKTUR** (yalnız `status` alanı ve `termination_date` kolonu var) — bu turda yalnız planlanır.

### 6.1 Süreç adımları

| # | Adım | Girdi | Etki | Geri alınabilir? |
|---|---|---|---|---|
| 1 | Çıkış kararı / neden | `termination_date`, `reason_code`, `notice_end_date` | `hr.employees` alanları + audit `employee.terminate` | ✅ (karar iptal edilebilir) |
| 2 | İhbar süresi | `notice_end_date` | Türetilmiş etiket "ihbar döneminde" (OD41) | ✅ |
| 3 | Kullanılmamış izin hesabı | `hr.leave_balances` (kalan) | **Salt okunur hesap** + kalem dökümü; ödeme/gün aktarımı **payroll** kararına bağlı | ✅ |
| 4 | Bordro kapanışı | son bordro dönemi | `payroll` finalization (P6) | ❌ (dönem kapanınca düzeltme kaydı) |
| 5 | Zimmet/ekipman | `hr.employee_tasks` (offboarding) | Görev listesi | ✅ |
| 6 | Belge teslimi | §8 belge modülü | Görev + belge durumu | ✅ |
| 7 | Kullanıcı/token revoke | `hr.users`, `hr.api_tokens` | Token iptali (mevcut token altyapısı) + oturum kapatma | ✅ (yeni token üretilebilir) |
| 8 | Rol kaldırma | `hr.user_roles` | Soft-delete + `role.revoke` audit | ✅ |
| 9 | Şirket erişimleri | SSO/hesap kapatma (kapsam dışı: dış sistemler) | — | — |
| 10 | Çıkış evrakları | §8 | Belge üretimi/yükleme | ✅ |
| 11 | Arşiv | `status = archived` (mevcut kapalı küme) | KVKK retention politikası (§24) | 🟡 (süper yönetici onaylı) |

### 6.2 Değişmez kurallar

1. **Audit bozulmaz:** işten ayrılan kullanıcının `audit_logs` kayıtları **silinemez**;
   `audit_logs.user_id` FK'si `SET_NULL` ise **aktör kimliği** ayrıca saklanır (ör.
   `actor_label`/`actor_email_snapshot`) — mevcut şema kontrol edilip gerekiyorsa alan eklenir.
2. **Çift doğruluk kaynağı yok:** çıkış tarihi yalnız `hr.employees`'te tutulur; izin/maaş
   hesapları **türetilir**, kopya kolon açılmaz.
3. **Fail-closed:** çıkışı **başlatan** ile **onaylayan** aynı kişi olamaz (iki adım); kendi
   kendini çıkarma engellenir.
4. **Yetki:** yeni izin kodu önerisi `employee.terminate` (mevcut `employee.update`'ten ayrı,
   çünkü yıkıcı ve yüksek etkili). Onay gereksinimi → **OD43 (öneri)**.
5. **Idempotent:** aynı çıkış iki kez tetiklenirse ikincisi **409** döner, kısmi yan etki
   üretmez.

### 6.3 Kabul kriterleri

1. Çıkış işlemi tamamlanınca: çalışan `terminated` (sonra `archived`), token'lar iptal,
   roller kaldırılmış, offboarding görevleri açılmış, denetim kayıtları yazılmış olur —
   **ya hepsi ya hiçbiri** (tek transaction; dış sistem çağrıları **görev** olarak).
2. Kullanılmamış izin günü ve (varsa) tazminat hesabı **eksik veri listesiyle** birlikte
   gösterilir (§15); eksik veri varken hesaplama **başlatılmaz**.
3. İşten ayrılmış kullanıcı artık giriş yapamaz ve **hiçbir** kiracı verisini göremez
   (mevcut token/oturum altyapısı ile).
4. Arşivlenmiş çalışan listede görünmez ama audit ve belge kayıtları korunur.

## 7. Bildirim / e-posta (notification & mail)

**Durum: hiç başlanmamış** (kodda `notification`/`mail` gönderimi yok; ayarlar hazır).
**Mevcut kararlar (tekrar edilmez, referans):** e-posta altyapısı ve **kuyruk** kararı verilmiştir —
`docs/ARCHITECTURE.md` §10 (background jobs: **django-q2, ORM broker**) ve §11 (e-posta: Django
SMTP backend + sağlayıcı bağımsız relay, gönderim **kuyruk üzerinden**, şablonlar kodda, TR/EN
hedeflenir, e-postaya **kişisel veri yazılmaz**). `config/settings.py` bu ayarları zaten içerir
(`EMAIL_BACKEND` konsol/SMTP, `EMAIL_HOST*`, `DEFAULT_FROM_EMAIL`).
**Ölçüldü (2026-09-13):** `django-q2>=1.10` **kurulu** ve `Q_CLUSTER` yapılandırılmış
(`config/settings.py:274-285`; `max_attempts` 3, `retry` 600 s, `save_limit` 500,
`catch_up` False), ancak **hiçbir görev kuyruğa atılmıyor**. `sync: DEBUG` olduğu için
geliştirmede görevler istek içinde **senkron** çalışır (test edilebilir), üretimde worker
süreci çalıştırılmalıdır (`manage.py qcluster`) — bu ayrım testlerde ve `verify_fresh_install.sh`
adımlarında dikkate alınmalıdır.

### 7.1 Mimari: **outbox** deseni (zorunlu)

```
business transaction ──► notification_events (OUTBOX, aynı transaction)
                                   │  (commit olduktan sonra)
                                   ▼
                       django-q2 görevi: notify.dispatch(event_id)
                                   │
                                   ├─► notification_deliveries (her kanal/deneme için 1 satır)
                                   └─► SMTP relay (veya console, dev)
```

**Neden outbox:** kullanıcı bildirimi "işin yan etkisi"dir; mail sağlayıcısı çökse bile
**izin onayı geri alınmamalıdır**. Bu yüzden:
1. HTTP isteği içinde **mail gönderilmez** (yavaş SMTP yanıt süresini bozar).
2. Olay **aynı DB transaction'ında** kuyruğa yazılır (atomiklik: iş yoksa olay da yok).
3. Gönderim ayrı süreçte (worker) yapılır; başarısızlık **yeniden denenir** ve kayda geçer.

### 7.2 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.notification_templates` | `id`, `tenant_id` (null = sistem varsayılanı), `code`, `channel` (`email`\|`inapp`), `locale` (`tr`\|`en`), `subject`, `body`, `is_system`, `status`, audit | Kiracı sistem şablonunu **ezebilir**, silemez |
| `hr.notification_events` (outbox) | `id`, `tenant_id`, `event_code`, `payload` (JSONB), `dedup_key` (**UNIQUE**), `created_at`, `created_by`, `status` (`pending`\|`dispatched`\|`failed`) | Olay = "ne oldu" (izin talebi gönderildi). `payload` **kişisel veri içermez**: kimlikler ve render için gereken minimum alanlar |
| `hr.notification_deliveries` | `id`, `tenant_id`, `event_id`, `channel`, `recipient_user_id`/`recipient_email`, `template_code`, `status` (`queued`\|`sent`\|`failed`\|`skipped`), `attempts`, `next_retry_at`, `sent_at`, `error_code`, `error_detail` (maskeli), `provider_message_id` | Her deneme ayrı satır **veya** tek satır + `attempts` (öneri: tek satır + deneme geçmişi JSON) |
| `hr.notification_preferences` (opsiyonel) | `user_id`, `event_code`, `channel`, `enabled` | Kullanıcı bazlı sessize alma (işlemsel bildirimler **kapatılamaz**) |

**Idempotans:** `dedup_key` = `f"{event_code}:{nesne_id}:{hedef_user_id}:{adım}"` — aynı olay
iki kez kuyruğa girse bile **tek** e-posta gider (mevcut `leave_balance_entries` idempotans
deseniyle aynı yaklaşım).

### 7.3 Olay kataloğu (V1 + hedef)

| Olay | Tetikleyici | Alıcı | Faz |
|---|---|---|---|
| `leave.request.submitted` | talep gönderildi | sıradaki onaylayıcı(lar) | P2 |
| `leave.request.decided` | onay/red | talep sahibi | P2 |
| `leave.request.step_advanced` | adım onaylandı | sonraki onaylayıcı | P2 |
| `leave.request.canceled` | iptal | onaylayıcılar | P2 |
| `leave.starting_soon` | 1 gün önce (job) | çalışan + yönetici | P2 |
| `leave.balance.low` | eşik altı (job) | çalışan + İK | P4 |
| `onboarding.task.due` | vade −1 gün (job) | görev sahibi | P1/P2 |
| `offboarding.task.due` | vade −1 gün | görev sahibi | P9 |
| `document.missing` / `document.expiring` | job | çalışan + İK | P3 |
| `contract.expiring` / `probation.ending` | job (30/7 gün) | İK | P1/P9 |
| `attendance.shift_changed` | vardiya atandı/değişti | çalışan | P4 |
| `overtime.request.submitted` / `.decided` | talep/karar | yönetici/çalışan | P5a |
| `payroll.period.ready` | dönem girdileri hazır | payroll hazırlık rolü | P6 |
| `report.ready` | asınk rapor bitti | isteyen kullanıcı | P8 |
| `auth.password_reset` / `auth.invite` | mevcut akış | kullanıcı | P2 |
| `anniversary` / `birthday` | job (opt-in) | çalışan/İK | P9 |

### 7.4 Kabul kriterleri (P2)

1. **Mail sağlayıcısı çöktüğünde**: izin talebi kaydı **başarılı** kalır (201), `deliveries`
   satırı `failed` olur ve `next_retry_at` ile yeniden denenir (kanıt: test).
2. **Aynı olay iki kez** işlense bile **tek** e-posta üretilir (`dedup_key` UNIQUE).
3. İzin talebi gönderildiğinde sıradaki onaylayıcı **adı ve unvanı** konu/gövdede değil,
   **bağlantı** ile görülür (kişisel veri e-postada taşınmaz — ARCHITECTURE §11 kuralı).
4. Çok adımlı akışta her adım **yalnız kendi onaylayıcısına** bildirim üretir.
5. Kiracı, sistem şablonunu **ezebilir**; silemez ve `event_code` ekleyemez (katalog kodda).

---

## 8. Belge yönetimi (document management)

**Durum: hiç başlanmamış** (kod yok), ancak **veri modeli ve depolama kararı mevcut**:
`docs/DATABASE.md` **M16** (`hr.documents` + `hr.document_access`, içerik sınıfı **K4**,
indirme kaydı zorunlu) ve `docs/ARCHITECTURE.md` §9 (**S3 uyumlu özel nesne depolama + boto3**,
ön imzalı URL, `tenant/<tenant_id>/<kategori>/<uuid>` yol düzeni, `checksum_sha256`,
`is_confidential` için ek şifreleme, yükleme doğrulama; geliştirmede yerel dosya sistemi).
`config/settings.py` içindeki `STORAGE_S3_*` ve `STORAGE_SIGNED_URL_TTL_SECONDS` ayarları bu
kararın karşılığıdır.

> **Bu bölüm yeni depolama kararı önermez.** Yalnızca M16 şemasını **genişleten deltalar**,
> güvenlik kuralları ve kabul kriterleri planlanır.

### 8.1 Belge türleri kataloğu (delta)

Mevcut M16 `hr.documents.category` serbest metin/alan düzeyindedir; hedef: **kapalı küme +
kiracı özel türleri**.

| Tablo (yeni, delta) | Kolonlar | Not |
|---|---|---|
| `hr.document_types` | `tenant_id` (null = sistem), `code`, `name`, `category` (`contract`\|`identity`\|`passport`\|`work_permit`\|`residence_permit`\|`diploma`\|`certificate`\|`health_report`\|`leave_report`\|`payslip`\|`handover`\|`termination`\|`custom`), `is_required_by_default`, `has_expiry`, `default_validity_months`, `classification_floor` (`public`\|`internal`\|`confidential`\|`restricted`), `retention_months`, `is_system`, `status` | Sistem kodları silinemez/çakışamaz |
| `hr.document_requirements` | `tenant_id`, `document_type_id`, `applies_to` (JSON: ülke/istihdam tipi/birim), `is_required`, `due_offset_days`, `is_active` | Hangi çalışandan hangi belge zorunlu → onboarding görevini üretir (§5) |
| `hr.tenant_quotas` | `tenant_id`, `storage_bytes_limit`, `document_count_limit`, `max_file_bytes` (tenant), audit | Sistem tavanı **aşılamaz** (§8.3) |

Mevcut M16 tablolarına eklenecek (delta) kolonlar: `document_type_id` (FK), `expiry_date`,
`upload_status` (`pending`\|`stored`\|`rejected`\|`quarantined`), `scan_status`
(`not_scanned`\|`clean`\|`infected`\|`error`), `download_count`, `superseded_by_id`,
`retention_policy_code`.

### 8.2 Sürümleme

- M16'da `version` kolonu **vardır**; sürümler **aynı belge kimliği altında** yeni
  `storage_key` + artan `version` ile saklanır (ayrı `document_versions` tablosu **gerekmez**,
  ilk sürümde). Eski sürümler silinmez, `superseded_by_id` ile işaretlenir.
- Yeni sürüm yüklendiğinde **denetim** ve (gerekiyorsa) **yeniden onay** gerektiren tür
  işaretlenir (ör. sözleşme).

### 8.3 Yükleme güvenliği (değişmez kurallar)

| Kural | Uygulama |
|---|---|
| **Sistem tavanı** | Sistem geneli **hard cap** (öneri **OD46**: 25 MB); hiçbir tenant aşamaz |
| **Tenant limiti** | `hr.tenant_quotas.max_file_bytes` **≤ sistem tavanı** (servis reddi + DB `CHECK`); kota dolduysa yükleme **reddedilir** |
| **MIME allowlist** | Yalnız izinli MIME türleri (PDF, JPEG/PNG, DOCX/XLSX, …); **içerik sniffing** (ilk baytlar) ile doğrulanır |
| **Uzantı tek başına yetmez** | `filename` **sanitize** edilir; uzantı ↔ MIME uyuşmazlığı **reddedilir**; saklanan ad **UUID**'dir (kullanıcı adı asla yol olmaz) |
| **Hash** | `checksum_sha256` yüklenirken hesaplanır (bütünlük + tekrar tespiti) |
| **Storage key** | `tenant/<tenant_id>/<category>/<uuid>` (ARCHITECTURE §9) — **erişim yolu ile paylaşılmaz** |
| **Zararlı yazılım** | `scan_status` alanı; tarayıcı bağlanana kadar `not_scanned` + `download` **politikası** (öneri: `confidential/restricted` indirilemez, karantina) |
| **Yükleme durumu** | `pending → stored` **atomik**; yarıda kalırsa nesne deposundan yetim dosya **temizlenir** (job) |
| **İndirme kaydı** | Her indirme `hr.document_access` + `audit_logs` (M16 zorunlu kılıyor) |
| **Retention / expiry** | `retention_until`, `expiry_date`; süresi geçen belge **otomatik silinmez**, `expiring` olayı üretir (§7) |
| **Erişim yetkisi** | `document.read` / `document.confidential.read` (**ayrı izin**) + organizasyon kapsamı; `document_access` süreli paylaşım (platform aktörü dahil) |
| **Kota muhasebesi** | Silme/sürümleme kotayı **düşürür**; kota aşımı **otomatik silme** yapmaz (yalnız uyarı) |

### 8.4 Belge ↔ hassas veri: metadata/blob ayrımı

| Katman | Nerede | Sınıf |
|---|---|---|
| Metadata (tür, sahip, tarih, hash, sınıflandırma) | PostgreSQL `hr.documents` | K2/K4-işaretli |
| İçerik (blob) | S3 uyumlu nesne deposu, **özel** (public değil), ön imzalı URL | **K4** |
| Erişim izi | `hr.document_access` + `audit_logs` | K1 |

**Karar (mevcut, tekrar edilmez):** büyük binary **DB'ye gömülmez** — nesne deposunda tutulur,
DB yalnız üst veri saklar (ARCHITECTURE §9; VPS disk sınırı 12 GB bu yüzden belirleyici oldu).

### 8.5 Background jobs (django-q2, §7 ile aynı altyapı)

`check_document_expiry` (günlük) · `notify_missing_documents` (haftalık) · `purge_orphan_objects`
· `scan_pending_documents` (tarayıcı bağlanınca) · `apply_retention` (önizleme modu varsayılan).

### 8.6 Kabul kriterleri (P3)

1. Kota aşımı → **413/400**, dosya **kısmen yazılmaz**, yetim nesne kalmaz.
2. MIME allowlist dışı içerik (ör. `.pdf` adlı çalıştırılabilir) **reddedilir**.
3. Kapsam dışı kullanıcı belgeyi **listeleyemez**, indiremez (404 — varlık sızdırılmaz).
4. `confidential`/`restricted` belge indirmesi **ayrı izin** ister ve **audit** yazar
   (denetim kaydı: kim, hangi belge, ne zaman, IP).
5. Zorunlu belge eksikse onboarding görevi + bildirim üretilir (§5, §7).
6. Belge süresi dolduğunda çalışan ve İK bilgilendirilir; belge **kendiliğinden silinmez**.
7. Yeniden yükleme (yeni sürüm) eski sürümü **korumalı** bırakır (`superseded_by_id`).

## 9. İzin (leave) — mevcut durum değerlendirmesi ve takvim boşluğu

### 7.1 Lifecycle kontrol listesi

| Adım | Kodda durum | Kanıt |
|---|---|---|
| İzin türü (leave type) | ✅ VAR | `hr.leave_types` (`uses_balance`, `requires_document`, `requires_approval`, `is_paid`, `category`, `max_consecutive_days`) |
| Politika (policy) | ✅ VAR (tek aktif politika) | `hr.leave_policies` (`accrual_method`, `fixed_days`, `allow_negative_balance`, `allow_refund_after_approval`) |
| Kıdeme bağlı kural | ✅ VAR | `hr.leave_policy_rules` (`min_service_months`, `max_service_months`, `entitlement_days`) |
| Hakediş (accrual) | ✅ VAR, **idempotent** | `accrue_leave_entitlements` + `manage.py accrue_leave_entitlements` |
| Entitlement | ✅ VAR | `hr.leave_balances.entitled_days` |
| Bakiye defteri (ledger) | ✅ VAR, **append-only** | `hr.leave_balance_entries` (`entry_kind`, `delta_days`, `reason`, `actor`) |
| Talep | ✅ VAR | `hr.leave_requests` (+ `is_half_day`, `days_requested` sunucuda) |
| Onay (çok adımlı) | ✅ VAR | `hr.approval_steps` (adım sırası zorunlu, `override` denetimli) |
| Red | ✅ VAR | `decide_approval_step(decision=rejected)` |
| İptal | ✅ VAR | `cancel_leave_request` (+ `allow_refund_after_approval`) |
| Belge zorunluluğu | 🟡 KISMİ | tür bayrağı + `document_ref` (UUID) var; **belge modülü yok** → dosya yüklenemiyor (§6) |
| Geri iade (refund) | ✅ VAR | `allow_refund_after_approval` + defter kaydı |
| Manuel düzeltme | ✅ VAR | `adjust_leave_balance` (gerekçe zorunlu, audit) |
| Yıl dönümü yenilemesi | ✅ VAR (görünüm + accrual) | `next_accrual_date` (işe giriş yıl dönümü) + idempotent accrual |
| **Yıl sonu devri (carry-forward)** | 🟡 **TASARLANDI, KOD YOK** | `leave_balance_entries.entry_kind` kapalı kümesinde **`carryover` zaten tanımlı** (`docs/DATABASE.md` M14) ve `carried_forward_days` kolonu `available_days` üretilen kolonuna giriyor; ancak **onu yazan hiçbir servis yok** → devir kuralı + idempotent servis eksik |
| **Çalışma takvimi / resmi tatil** | ❌ **YOK** | `calculate_requested_days` **takvim günü** sayar; hafta sonu/tatil hesaba katılmaz (kod yorumu: "çalışma takvimi modülü M15 gelene kadar") |
| **Yarı zamanlı / kısmi süre** | ❌ YOK | gün hesabı 1 gün = 1 gün; FTE/pro-rata yok |
| **Ücretsiz izin → bordro** | ❌ YOK | tür kategorisi var, payroll bağı yok (§13) |
| **Ekip izin takvimi** | 🟡 KISMI | panelde "yaklaşan izinler" var; ekip takvimi görünümü yok (§19) |

### 7.2 Zorunlu düzeltme: gün hesabı **takvim günü** değil **iş günü** olmalı

Bugünkü hesap (`requests.py:82-100`): `(end_date - start_date).days + 1`, yarım gün = 0,5.
Bu **bilinçli bir geçici basitleştirme**; gerçek HR operasyonunda hatalı sonuç verir
(hafta sonuna denk gelen 3 günlük talep 3 gün düşer).

**Plan:**

| Aşama | Ne değişir | Ne zaman |
|---|---|---|
| A0 (bugün) | Takvim günü; arayüz "takvim günü" diye **açıkça yazar** | ✅ yapıldı |
| A1 (M15) | `hr.working_calendars` + `hr.calendar_days` (hafta tatili, resmi tatil, yarım gün çalışma); gün hesabı **calendar-aware** olur; talep **iş günü** düşer | §10 ile birlikte |
| A2 | Çalışan bazlı **çalışma programı** (`hr.employee_work_schedules`, FTE, kısmi süre) → pro-rata düşüm | §12 (istihdam koşulları) sonrası |
| A3 | Ülke bazlı resmi tatil setleri (country rules, §14) | P7 |

**Geçiş kuralı (kritik):** A1'e geçerken **geçmiş talepler yeniden hesaplanmaz**; her talebin
`days_requested` değeri **yazıldığı gibi kalır** (mevcut veri değişmez). Yeni hesap yalnız
yeni taleplerde uygulanır; fark varsa arayüz "eski hesap" bilgisini gösterir. Gün hesabı
sürümü talebe yazılır: `calculation_version` (öneri).

### 7.3 Kabul kriterleri

1. A1'de: cumayı takip eden pazartesiyi kapsayan bir talep, hafta sonu için **gün düşmez**
   (kanıt: test), resmi tatile denk gelen gün düşmez.
2. Yıl sonu devri: politika `carry_over` kuralına göre kalan gün bir sonraki döneme **idempotent**
   aktarılır; iki kez çalıştırma çift devir üretmez.
3. Mevcut tüm leave testleri (M14) değişmeden geçer.

---

## 10. Devam / vardiya (attendance & shift)

**Durum: hiç başlanmamış** (kodda `attendance` yalnız izin kodu olarak var: `attendance.read`,
`attendance.manage` — panelde "M15 ile gelecek" notu düşülmüş).

### 8.1 Model (plan)

| Tablo | Kolonlar (özet) | Not |
|---|---|---|
| `hr.working_calendars` | `tenant_id`, `code`, `name`, `timezone`, `week_start`, `is_default` | İzin gün hesabının da kaynağı (§9.2); **yeni tablo** (mevcut tasarımda yok) |
| `hr.calendar_days` | `calendar_id`, `date`, `day_type` (`workday`\|`weekend`\|`holiday`\|`half_day`), `name`, `country_code` | Resmi tatil + hafta tatili; ülke seti §14'den beslenir |
| `hr.shifts` | `tenant_id`, `code`, `name`, `start_time`, `end_time`, `break_minutes`, `crosses_midnight`, `work_model` | Vardiya tanımı |
| `hr.shift_assignments` | `employee_id`, `shift_id`, `location_id`, `effective_from`, `effective_until`, `work_model` (`office`\|`hybrid`\|`remote`\|`flex`) | Etkili tarihli atama (tek aktif kural) |
| `hr.attendance_records` | **M15'te tasarlandı** (`docs/DATABASE.md` §"hr.shifts, hr.shift_assignments, hr.attendance_records"): gün kolonu **`work_date`** (indeks `(tenant_id, employee_id, work_date)`); eklenecek kolonlar: `shift_id`, `check_in`, `check_out`, `worked_minutes`, `break_minutes`, `late_minutes`, `early_leave_minutes`, `source`, `status`, `overtime_minutes` | **Şema sıfırdan uydurulmaz**: M15 tasarımı genişletilir; gün başına tek satır (kısmi benzersiz) |
| `hr.attendance_events` | `record_id`, `event` (`in`\|`out`\|`break_start`…), `at`, `source`, `actor` | Ayrıntılı giriş/çıkış (opsiyonel ayrıntı düzeyi) |

### 8.2 Çelişki yasağı: izin ↔ devam **tek doğruluk kaynağı**

Kural: **bir gün için ya `leave` ya `present`** olur.

1. `hr.attendance_records.status = 'leave'` satırı **yalnız** onaylı `hr.leave_requests`
   aralığından **türetilir** (kopya değil; türetilmiş görünüm veya idempotent üretim).
2. Onaylanan izin, ilgili günlerde devam kaydını `leave`'e çeker; izin iptal edilirse kayıt
   yeniden hesaplanır (idempotent `recompute_day`).
3. Devam kaydı elle `present` yazılırsa ve o gün onaylı izin varsa **409/uyarı** (çift kayıt
   yasağı DB kısmi benzersizliği + servis guard'ı ile).
4. Fazla mesai `overtime_minutes` alanı **öneri**: hesaplanan ve **onay bekleyen** dakika;
   onaylanınca `hr.overtime_requests`'e dönüşür (§11) — iki yerde onay durumu tutulmaz.

### 8.3 Kabul kriterleri (M15)

1. Aynı çalışan + gün için ikinci `attendance_records` satırı **oluşturulamaz**.
2. Onaylı izinli günde devam kaydı `leave`; gelmediği için `absent` yazılamaz (guard).
3. Vardiya ataması etkili tarihli: geçmiş bir gün için geçmiş vardiya geçerli olur (tarihçe).
4. Geç kalma/erken çıkma dakikaları **sunucuda** hesaplanır (arayüz hesap yapmaz).
5. Remote/flex çalışma modeli vardiya ile çelişmez: `work_model='remote'` iken konum zorunlu
   değildir ve devamsızlık üretmez.

---

## 11. Fazla mesai (overtime) — ayrıntılı tasarım

**Durum: hiç başlanmamış.** Ayrıntılı şema/kural modeli **`docs/HR_ROADMAP.md` §2**'de
üretilmişti; burada tam kapsam ve **karar noktaları** ile bütünleştirilir.

### 9.1 Talep akışı

1. Çalışan girer: `date`, `start_time`, `end_time` **veya** `total_minutes`, `description`,
   `reason_code` (izin türü gibi kapalı küme), `project_code`/`department` (opsiyonel).
2. Sunucu: `total_minutes` **çakışma/duplicate** kontrolünden geçer (aynı gün aynı saat aralığı
   iki kez sayılamaz), çalışma takvimi ve vardiya ile tutarlılık denetlenir (§10).
3. Onay: **manager adımı (zorunlu)** → (opsiyonel) İK/ikinci adım; mevcut `hr.approval_steps`
   altyapısı **yeniden kullanılır** (yeni onay motoru yazılmaz) → **OD39** (mevcut öneri).
4. Onay anında **katsayı ve karşılık tipi sabitlenir** (`pay_multiplier_snapshot`,
   `compensation_type_snapshot`) → sonradan kural değişse geçmiş bozulmaz → **OD34** (mevcut öneri).

### 9.2 Kural modeli (kod içine gömülmez)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.overtime_rules` | `tenant_id`, `code`, `name`, `min_minutes`, `max_minutes` (null = üst sınır yok), `multiplier`, `compensation_type` (`pay`\|`time_off`\|`employee_choice`), `day_type` (`any`\|`weekday`\|`weekend`\|`holiday` — başlangıçta `any`), `effective_from`, `effective_until`, `created_by`, audit | Kurallar **yarı açık aralık** `[min, max)` — izin politika kurallarıyla aynı kural (mevcut tasarım tutarlılığı) |
| `hr.overtime_requests` | `employee_id`, `date`, `start_time`, `end_time`, `total_minutes` (sunucu hesaplar/doğrular), `reason_code`, `description`, `status` (`draft`\|`pending`\|`approved`\|`rejected`\|`canceled`), `compensation_type_snapshot`, `pay_multiplier_snapshot`, `time_off_minutes_snapshot`, `approved_at`, `decided_by`, `leave_balance_id` (TIME_OFF karşılığı yazıldıysa), audit | `attendance_record_id` NULL FK (§10) |
| `hr.overtime_entries` (opsiyonel) | `request_id`, `segment_no`, `minutes`, `multiplier`, `compensation_type`, `amount`/`credit_minutes` | Katsayı dilimlerine bölünmüş döküm (0–60 → 1.0, 61+ → 1.5 gibi) |

### 9.3 Katsayı ve karşılık

- **Kural seti örneği (tenant tanımlar, kodda yok):**
  ```
  [0,  60)  → multiplier 1.0   (pay)
  [60, ∞)   → multiplier 1.5   (pay)
  ```
  veya müşteri isterse `[0,120) → 1.5, [120,∞) → 2.0`.
- **Dilimleme:** toplam süre kural aralıklarına göre **dilimlenir**; her dilim kendi katsayısıyla
  hesaplanır (ör. 90 dk: 60×1.0 + 30×1.5). Sonuç `hr.overtime_entries` dökümünde saklanır →
  hesap **açıklanabilir** ve sonradan denetlenebilir.
- **PAY (ücret):**
  ```
  saatlik_ücret = f(employee_compensation, employment_hours, country_rule_set)
  tutar        = Σ_dilim (dakika / 60) × saatlik_ücret × multiplier
  ```
  `saatlik_ücret` türetimi **ülke/bordro kural motoruna** bağlanır (§14) — TR'ye özel formül
  global koda **yazılmaz**. **Payroll hazırlığı (§13) yokken PAY hesabı yazılmaz** (izin kodu
  ve şema hazır olsa da hesaplama motoru P6 sonrasına bağlıdır).
- **TIME_OFF (izin karşılığı):** onaylı fazla mesai için **leave ledger'a güvenli olay** yazılır:
  - Yeni izin türü (sistem kodu, ör. `overtime_comp`) **veya** mevcut tür → **OD32** (mevcut öneri).
  - Yazım yolu: `hr.leave_balance_entries` (`entry_kind='overtime_credit'` yeni tür, `delta_days`
    = dakika/60/… gün, `reason` zorunlu) — mevcut `adjust_leave_balance` benzeri **ayrı servis
    fonksiyonu**; doğrudan bakiye update **yasak**.
  - **Yuvarlama politikası (öneri):** kredi **dakika** cinsinden hesaplanır, bakiyeye **yarım
    gün adımıyla aşağı yuvarlanır** (`floor(credit/240)*0.5` gün); artan dakika
    `hr.overtime_requests`'te saklanır ve sonraki krediyle birleşir. Kesin kural → **OD44 (yeni öneri)**.
- **İptal/geri alma:** onaylanan fazla mesai iptal edilirse PAY tarafı **payroll düzeltmesi**,
  TIME_OFF tarafı **ters defter kaydı** üretir (silme yok).

### 9.4 Kabul kriterleri (P5)

1. 90 dakika, `[0,60)=1.0` + `[60,∞)=1.5` kuralında → **döküm** 60×1.0 + 30×1.5 (kanıt: test).
2. Kural sonradan değiştirilse bile **eski kaydın** snapshot'ı ve tutarı değişmez.
3. TIME_OFF karşılığı bakiyeye yazıldığında **ledger kaydı + audit** oluşur; bakiye ledger
   toplamıyla **birebir** uyuşur (mutabakat testi).
4. Aynı gün için çakışan iki fazla mesai kaydı **reddedilir**.
5. Yalnız `overtime.request.create` sahibi kendi kaydını açar; onay `overtime.request.approve`
   + kapsam ister (kendi kaydını onaylayamaz).

---

## 12. Ücret / maaş geçmişi (compensation history)

**Durum: hiç başlanmamış.** Bugün maaş **hiçbir** yerde tutulmuyor (ne `hr.employees`'te ne
başka tabloda) → **tek kolon yazma riski yok ama veri de yok.**

### 10.1 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.employee_compensation` | `id`, `tenant_id`, `employee_id`, `effective_from`, `effective_until` (null = güncel), `basis` (`gross`\|`net`), `currency` (ISO-4217), `pay_frequency` (`monthly`\|`biweekly`\|`weekly`\|`hourly`), `base_amount`, `employment_hours` (haftalık/aylık sözleşme saati), `variable_pay` (JSON, opsiyonel), `reason_code`, `created_by`, audit | **Üzerine yazılmaz**; yeni satır açılır, eskisinin `effective_until` değeri kapatılır |
| Kısıtlar | Tek **açık** kayıt/çalışan (kısmi benzersiz: `effective_until IS NULL`), `effective_until > effective_from` | Mevcut `employee_manager` deseniyle aynı yaklaşım (tutarlılık) |
| **Veri sınıfı** | **K2** (kişisel + finansal; sağlık/özel nitelikli değil) → açık kolon + `employee.sensitive.read` kapısı; ek olarak **ayrı izin** önerisi `compensation.read` / `compensation.manage` (öneri → **OD45**) | Tutarın arayüzde maskelenmesi (yalnız HR + gerektiğinde kendi kaydı) |
| Ülkeye özel kırılım | `hr.employee_payroll_profile` (§13) | SGK/vergi no gibi ülkeye özel alanlar ayrı tutulur |

### 10.2 Kurallar

1. **Tarihçe korunur:** retro ücret değişikliği **yeni satır** + `reason_code`
   ("toplu zam", "terfi", "yasal artış"); geçmiş satır **değiştirilemez**, yalnız kapalı
   aralık düzeltmesi yeni kayıtla yapılır.
2. **Aynı gün iki kayıt** reddedilir (kısmi benzersiz).
3. Değişiklik **iki adımlı onay** (HR girer → ikinci kişi onaylar) ve audit; toplu değişiklik
   için içe aktarma (import) ayrı **report/job** altyapısıyla (§16).
4. `effective_from` **gelecekte** olabilir (planlı zam); maaş hesabı **o tarihte** geçerli
   kaydı kullanır (geçmiş sorgularda tarih parametresi zorunlu).
5. Maaş alanları **export** iznine tabi (§16 K2/K3 bölümü): ekranı göremeyen Excel'de de göremez.

### 10.3 Kabul kriterleri (P6)

1. İki ardışık zam sonrası **üç** kayıt vardır; en eskisi silinmemiştir.
2. Belirli bir tarih için "o gün geçerli maaş" sorgusu **tek** kayıt döner.
3. Yetkisiz kullanıcı maaş alanını ne API'de ne arayüzde ne de Excel'de görür.

---

## 13. Bordro hazırlığı (payroll preparation)

### 11.1 Karar önerisi: **V1 = bordro HAZIRLIĞI, motor değil**

| Seçenek | Artı | Eksi | Öneri |
|---|---|---|---|
| A. Tam resmi bordro motoru (TR/US/EU hesaplama + beyan) | Ürün tam olur | Mevzuat takibi, beyan sorumluluğu, sertifikasyon, sürekli bakım; **tek VPS hedefiyle uyumsuz** | ❌ V1 için |
| **B. Bordro hazırlığı + dışa aktarım** | Muhasebe/bordro sağlayıcısına **veri** üretir; mevzuat sorumluluğu taşımaz; TR/ABD/AB'de satılabilir | Tam otomatik bordro yok | ✅ **Önerilen** |
| C. Hiçbiri | — | İK operasyonu yarım kalır | ❌ |

**Gerekçe:** ürünün farkı İK operasyonu; bordro mevzuatı ülke bazlı ve hızla değişen bir
sorumluluk alanıdır. Bordro **girdileri** üretmek (izin, devamsızlık, fazla mesai, prim, kesinti,
yeni işe giren/çıkan listesi) hem bugün değer üretir hem de ileride tam motora geçiş kapısını açar.

### 11.2 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.payroll_periods` | `tenant_id`, `code` (2026-09), `start_date`, `end_date`, `pay_date`, `status` (`open`\|`calculating`\|`review`\|`approved`\|`finalized`\|`exported`), `approved_by`, `finalized_at`, audit | Dönem kapandıktan sonra **imutabl** |
| `hr.payroll_inputs` | `period_id`, `employee_id`, `input_code` (salary, overtime_pay, overtime_timeoff, unpaid_leave_days, absence_days, bonus, premium, benefit, deduction, tax_base_input, manual_adjustment), `quantity`, `amount`, `currency`, `source_kind`/`source_id` (hangi modülden), `note`, audit | **Kaynak izlenebilir**: her girdi hangi izin/fazla mesai/devamsızlık kaydından geldiğini taşır |
| `hr.payroll_runs` | `period_id`, `run_no`, `status`, `started_at`, `finished_at`, `warnings` (JSON) | Çalıştırma kaydı (export/raporla aynı job altyapısı) |
| `hr.payroll_exports` | `run_id`, `template_code`, `file_id` (belge modülü referansı), `created_by`, `expires_at` | Dışa aktarım dosyası **belge modülünde** saklanır (§6) |
| `hr.employee_payroll_profile` | `employee_id`, `country_code`, `tax_id`/`social_security_no` (ülkeye özel, K2), `bank_account` (mevcut `iban`), `cost_center`, `pay_group` | Ülkeye özel alanlar **JSON/ayrı tablo**; global tabloya ülke kolonu yığmak yasak |

### 11.3 Akış

```
dönem aç ─► girdileri topla (toplama servisleri, her modülün kendi servisinden)
        ─► uyarılar (eksik banka/vergi no, kapanmamış devamsızlık, onaysız fazla mesai)
        ─► İK gözden geçirir (manuel düzeltme = ayrı input satırı + gerekçe)
        ─► onay (iki adım) ─► FINALIZE (imutabl) ─► EXPORT (XLSX/CSV/bordro sağlayıcı şablonu)
```

### 11.4 Dönem kapandıktan sonra geriye dönük değişiklik

**Kural:** finalize edilmiş dönem **değiştirilmez**. Yeni bilgi çıkarsa:
1. **Düzeltme girdisi** bir **sonraki** döneme yazılır (`manual_adjustment`, `reason_code`
   zorunlu, `source_id` eski dönemi işaret eder) — "retro düzeltme".
2. Gerekirse **ek dönem çalıştırması** (`run_no` artar) ve dışa aktarım yeniden üretilir; eski
   çıktı dosyası **silinmez**, `superseded_by` ile işaretlenir.
3. Denetim: `payroll.period.finalize`, `payroll.adjustment.create`, `payroll.export.create`.

### 11.5 Kabul kriterleri (P6)

1. Finalize edilmiş dönemde `payroll_inputs` **update edilemez** (servis + DB guard).
2. Her satırın `source_kind/source_id` değeri vardır; "bu tutar nereden geldi?" sorusu
   tek sorguyla yanıtlanır (izlenebilirlik testi).
3. Eksik veri (ör. banka hesabı olmayan çalışan) **uyarı** olarak listelenir, çalıştırma
   **çökmez** ve o çalışan için satır "eksik" işaretlenir.
4. Dışa aktarım dosyası yalnız yetkili kullanıcı tarafından, **süresi dolmadan** indirilebilir
   ve indirme **denetim kaydı** üretir.

## 14. Ülke kural motoru (country rule engine)

**İlke:** ülkeye özel iş hukuku/bordro kuralları **global koda gömülmez**. Kod yalnız
*hesaplama sözleşmesini* bilir; *değerler ve formüller* versiyonlanmış kural kayıtlarından gelir.

### 12.1 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.country_rule_sets` | `country_code` (ISO-3166-1 alpha-2), `rule_code` (ör. `severance_pay`, `overtime_hourly_rate`, `income_tax_brackets`, `annual_leave_minimum`), `version` (int), `effective_from`, `effective_until`, `parameters` (JSONB), `source_reference` (kanun/resmi gazete metni + URL), `implementation_key` (kod içi **hesaplama fonksiyonu** kimliği), `status`, audit | Aynı `rule_code` + `country` için **dönem çakışması yasak** (kısmi benzersiz) |
| `hr.country_rule_calculations` (hesap izi) | `rule_code`, `rule_set_id`, `rule_version`, `country_code`, `input_snapshot` (JSONB), `parameters_snapshot` (JSONB), `breakdown` (JSONB), `result` (JSONB), `calculated_at`, `calculated_by`, `related_employee_id`, `related_period_id` | **İmutable**; eski hesap yeni mevzuatla **değişmez** |
| (opsiyonel) `hr.tax_brackets` / `hr.social_security_params` | ülke kırılımı | Ayrı normalizasyon gerekirse |

### 12.2 Sözleşme (kod ↔ kural)

1. Kod, `implementation_key` → saf fonksiyon eşlemesi tutar: `(inputs, parameters) -> breakdown`.
2. Fonksiyon **mevzuat metni içermez**; tavan/oran/tarih **parametreden** gelir. Yeni mevzuat =
   yeni kural **versiyonu** + (gerekirse) yeni fonksiyon; eski versiyon **korunur**.
3. Hesap çıktısı §14.1'deki **calculations** tablosuna yazılır: kullanılan input snapshot,
   kural sürümü, parametreler, kalem kırılımı, tarih, aktör.
4. **Fail-closed:** ilgili tarih için geçerli kural versiyonu yoksa hesap **başlatılmaz**;
   arayüz "bu ülke/tarih için kural tanımlı değil" der (uydurma varsayılan **yok**).
5. Kural tanımlama izni: platform seviyesi (`platform.country_rule.manage`) — **kiracı kendi
   ülke kuralını değiştiremez**, yalnız **parametre override**'ı (ör. sektör tavanı) izinli ise
   kiracı seviyesinde ayrı tablo ile.

### 12.3 Kabul kriterleri (P7)

1. Aynı hesap iki farklı kural sürümüyle iki farklı sonuç verir; **eski hesap kaydı değişmez**.
2. Yeni versiyon eklendiğinde geçmiş hesaplar **yeniden hesaplanmaz** (opsiyonel "yeniden
   hesapla" işlemi **yeni** calculation satırı üretir).
3. Parametresiz (kural yok) durumda hesaplama **başlamaz**, eksik bilgi gösterilir.
4. TR/SG/DE/FR için aynı `rule_code` farklı parametrelerle tanımlanabilir; global kod satırı
   **değişmez**.

---

## 15. Türkiye hesaplamaları (plan — bu turda formül/kod yok)

**Modül sınırı:** `hr` çekirdeği + `country_rule_sets` (`country_code='TR'`); TR'ye özel
hesaplar **ayrı servis paketi** olarak yazılır (`apps/calculations/tr/…` benzeri), global kod
`if country == "TR"` içermez.

| Hesap | Gereken veriler (mevcut ✅ / eksik ❌) | Not |
|---|---|---|
| **Kıdem tazminatı** | `hire_date` ✅, `termination_date` ✅, ücret geçmişi ❌ (§12), giydirilmiş ücret bileşenleri ❌, kıdem tavanı (kural parametresi) ❌, işe giriş/çıkış arası boşta geçen süre ❌ | Tavan ve katsayı **kural versiyonundan** |
| **İhbar tazminatı** | `notice_end_date` ❌ (OD41), ihbar süresi kıdı → kural, ücret geçmişi ❌ | İhbar süresi hesabı kural motorunda |
| **Evlilik nedeniyle ayrılma / tazminat** | ayrılış nedeni kodu ❌ (`reason_code` + `termination_reason`), evlilik tarihi ❌, `hire_date` ✅ | Neden kodu altyapısı §6 ile gelir |
| **Kullanılmamış izin karşılığı** | kalan izin ✅ (`hr.leave_balances.available_days`), ücret ❌ (§12), günlük ücret türetimi → kural | Bugün "kaç gün" **verilebilir**; "kaç TL" **verilemez** |
| **Brüt/net bordro yardımcıları** | bordro girdileri ❌ (§13), vergi dilimleri/SGK parametreleri ❌ (§14) | V1 kapsamı: **girdi üretmek**, hesap değil |
| **Vergi/matrah girdileri** | `payroll_inputs` ❌, ülke parametreleri ❌ | Kural motoru ile |

**Eksik veri gösterimi (zorunlu UX kuralı):** hesap **başlatılmadan önce** sistem gerekli
alanları kontrol eder; eksikse **hesap çalışmaz** ve liste hâlinde gösterir:
> "Kıdem tazminatı hesaplanamıyor — eksik: (1) ücret geçmişi kaydı yok, (2) çıkış nedeni
> seçilmedi, (3) 2026 kıdem tavanı kuralı tanımlı değil."

**Sonuç ekranı (her hesap için ortak):** toplam → kalem dökümü → kullanılan değerler →
parametre/tavan (kural sürümü ile) → hesap tarihi → uyarılar → **Excel/PDF** çıktısı (§16).
Sonuç **saklanır** (`country_rule_calculations`) — yeniden hesaplama yeni kayıt üretir.

---

## 16. Raporlama / Excel (merkezî servis)

**Kural:** her ekran kendi XLSX'ini üretmez. Tek servis, tek güvenlik kapısı.

### 14.1 Model (plan)

| Tablo | Kolonlar | Not |
|---|---|---|
| `hr.report_definitions` | `code`, `name`, `module`, `query_kind` (`orm`\|`sql`), `parameter_schema` (JSON), `columns` (JSON: kod, etiket, sınıf K0–K3, format), `required_permissions` (liste), `max_rows`, `is_async`, audit | Rapor **tanımı koddadır**; tenant yalnızca parametre/kayıt seçer |
| `hr.report_jobs` | `definition_id`, `parameters` (JSON), `requested_by`, `status` (`queued`\|`running`\|`done`\|`failed`), `started_at`, `finished_at`, `row_count`, `error_code`, `progress` | **Asenkron** üretim (kuyruk/worker §24) |
| `hr.report_files` | `job_id`, `file_id` (belge modülü §6), `sha256`, `row_count`, `expires_at`, `download_count`, audit | Dosya **belge modülünde**; `expires_at` zorunlu |

### 14.2 Zorunlu raporlar (hedef liste)

Çalışan listesi · organizasyon şeması · işe giriş/çıkış hareketleri · izin bakiyeleri · izin
talepleri/onay geçmişi · devamsızlık · vardiya planı · fazla mesai · belge eksikleri/süresi
dolan · maaş geçmişi · bordro hazırlık girdileri · tazminat hesapları · onboarding/offboarding
görev durumu · audit dışa aktarımı (yetkiliye).

### 14.3 Güvenlik (değişmez kurallar)

1. **Yetki ayrımı yok:** rapor, kullanıcının **aynı** kapsam kapılarından geçer
   (`kapsamdaki_calisanlar` benzeri tek çözümleyici). Ekranda görünmeyen satır/alan Excel'de de
   **görünmez** — rapor sorgusu `SELECT *` yapamaz, kolon listesi tanımdan gelir.
2. **K2/K3:** ayrı izin kodu (`report.sensitive.export` öneri), **maskeleme** (ör. IBAN/kimlik
   son 4 hane) ve **audit**; K3 alanlar varsayılan **hiç** dahil edilmez.
3. **XLSX güvenliği:**
   - **Formula injection:** `=`, `+`, `-`, `@`, TAB, CR ile başlayan hücre değerleri
     **metin olarak** yazılır (önek/escape) — kütüphane seçiminden bağımsız kural.
   - **max_rows:** sistem üst sınırı (öneri: 50.000 satır) + tenant alt sınırı; aşarsa iş
     **parçalanır** veya reddedilir.
   - **async:** eşik üstü raporlar kuyruğa gider; kullanıcıya "hazır olunca bildirim" (§9).
   - **dosya süresi:** `expires_at` sonrası indirilemez ve fiziksel dosya **temizlenir**.
   - **secure download:** imzalı, tek kullanımlık bağlantı veya yetki kontrolü + audit; dosya
     yolu/publik URL **asla** açığa çıkmaz.
4. **Rate limit:** export uçları ayrı ve daha sıkı limitli (kötüye kullanım maliyeti yüksek).

### 14.4 Kabul kriterleri (P8)

1. Kapsamı yalnız Finans olan kullanıcının çalışan listesi raporu **İK çalışanını içermez**
   (kanıt: test).
2. `=cmd|' /C calc'!A0` benzeri bir değer Excel'de **formül olarak çalışmaz**.
3. Süresi dolmuş rapor dosyası indirilemez (410/404) ve diskten temizlenmiştir.
4. Her indirme `report.download` denetim kaydı üretir.

---

## 17. Dashboard / görev merkezi

**İlke:** tek dashboard sayfası değil, **role/kapsama göre değişen** bir "yapılacaklar" merkezi.

### 15.1 Kart seti (hedef)

| Kart | Kaynak | Durum | Rol |
|---|---|---|---|
| Toplam çalışan | `hr.employees` | ✅ VAR (test UI) | HR/manager |
| Bugün izinde | onaylı izinler | ✅ VAR | HR/manager |
| 7 gün içinde izin | onaylı izinler | ✅ VAR | HR/manager |
| Bekleyen onay (izin) | `hr.approval_steps` | ✅ VAR | yönetici/HR |
| Bakiyesi kritik çalışanlar | `hr.leave_balances` | ✅ VAR (kalan ≤ 3) | HR |
| **Onboarding görevleri** | §5 | ❌ | HR |
| **Offboarding görevleri** | §6 | ❌ | HR |
| **Belge eksikleri** | §6 | ❌ | HR |
| **Süresi dolacak belgeler** | §6 (expiry) | ❌ | HR |
| **Deneme süresi bitenler** | §3 (probation) | ❌ | HR/manager |
| **Sözleşmesi bitenler** | §3 (contract) | ❌ | HR |
| **Fazla mesai bekleyen** | §11 | ❌ | manager/HR |
| **Bordro dönemi hazırlığı** | §13 | ❌ | payroll_prep |
| **İzin başlangıcı (bugün/yarın)** | izin | 🟡 KISMİ (yaklaşan) | manager |

### 15.2 Kurallar

- Kartlar `hr.employee_tasks` + izin onay kuyruğu + belge durumundan **türetilir**; ayrı sayaç
  tablosu tutulmaz (tutarlılık maliyeti).
- Sorgular **kapsam kapısından** geçer: manager yalnız ekibini, HR kapsamını görür; sayılar
  asla kapsam dışı veriyi toplamaz (sayaç sızıntısı testi).
- Boş durum anlamlı metinle gösterilir ("Bugün izinde kimse yok").

---

## 18. Employee self service (ESS)

| Yetenek | İzin kodu (mevcut/öneri) | Düzenlenebilir alanlar |
|---|---|---|
| Profilini gör | `employee.read` (kendi kaydı) ✅ | — |
| İzin talebi | `leave.request.create` ✅ | — |
| İzin bakiyesi | `leave.balance.read` (kendi) ✅ | — |
| İzin geçmişi | ✅ | — |
| **Fazla mesai talebi** | `overtime.request.create` (yeni, §11) | — |
| **Belge yükle** (kendi) | `document.upload` (yeni, §6) | Tanımlı belge türleri |
| **Belgelerini gör** | `document.read.self` (öneri) | — |
| **Bordro/maaş belgesini gör** | `payslip.read.self` (öneri) | — |
| **Kişisel bilgi değişiklik talebi** | `profile.change.request` (yeni) | Adres, telefon, e-posta, banka/IBAN, acil durum kişisi; **onay gerektirir** |
| **Şirket duyuruları** | `announcement.read` (yeni, §9 bildirim ile aynı modül) | — |

**Zorunlu kural:** ESS yeni bir yetki yolu **açmaz**; mevcut `SELF` kapsamı + izin kodları
kullanılır. Çalışan **kendi** K2 alanlarını doğrudan **yazamaz** (talep açar → HR onaylar →
yazım HR tarafından, audit'li). Kimlik no, IBAN gibi alanların değişimi **iki adımlı** ve
denetimli; K3 alanlar ESS'te **hiç** düzenlenemez.

---

## 19. Manager self service (MSS)

| Yetenek | İzin/kapsam | Durum |
|---|---|---|
| Ekibini gör | `employee.read` + `UNIT` kapsamı | ✅ VAR (kapsam testi var) |
| İzin onayla/reddet | `leave.request.approve` + kapsam + adım sahipliği | ✅ VAR |
| Ekip izin bakiyesi | `leave.balance.read` + kapsam | ✅ VAR (test UI) |
| **Ekip izin takvimi** | aynı kapı | 🟡 KISMİ (yaklaşan izinler listesi; takvim görünümü yok) |
| **Fazla mesai onayı** | `overtime.request.approve` (yeni) | ❌ |
| **Vardiya görüntüleme/değişiklik talebi** | `attendance.read` + talep | ❌ |
| **Çalışan değişiklik talebi** (terfi/model/maaş önerisi) | yeni `employee.change.request` | ❌ |
| **Onboarding görevleri** (kendi ekibi) | `hr.employee_tasks` atanan | ❌ |
| **Performans** | — | **Kapsam dışı (V1)**; ayrı ürün kararı |

**Kural:** manager, **kendi** kaydına ilişkin işlem yapamaz (self-approval yasağı — mevcut
`kendi talebini onaylayamaz` kuralı tüm yeni akışlara **genişletilir**).

---

## 20. HR görev matrisi

Sınıflandırma **koddan ölçülmüştür** (Mevcut = çalışıyor ve testli; Kısmi = parçası var;
Planlandı = tasarım var, kod yok; Kapsam dışı = V1'de yapılmayacak).

| HR görevi | Mevcut | Kısmi | Planlandı | Kapsam dışı | Hedef faz |
|---|---|---|---|---|---|
| Çalışan açma (iş kaydı) | ✅ | | | | — |
| Kullanıcı hesabı açma + rol atama | ✅ | | | | — |
| Özlük dosyası (K2/K3 kişisel veri) | ✅ | | | | — |
| Departman/yönetici atama | ✅ | | | | — |
| Organizasyon ağacı yönetimi | ✅ | | | | — |
| İzin talebi + çok adımlı onay | ✅ | | | | — |
| İzin bakiyesi/hakediş + manuel düzeltme | ✅ | | | | — |
| İzin türü/politika yönetimi | ✅ | | | | — |
| Yetki/rol yönetimi (RBAC) | ✅ | | | | — |
| Denetim kaydı (audit) | ✅ | | | | — |
| Çalışan listesi/filtre/görünümler | ✅ | | | | — |
| Sözleşme takibi (tip/süre/belge) | | | ✅ | | P1 |
| İstihdam koşulları (model/tip/lokasyon/deneme) | | 🟡 (organizasyon kısmı) | ✅ | | P1 |
| Yapılandırılabilir kayıt formu | | | ✅ | | P1 |
| Onboarding checklist | | | ✅ | | P1 |
| Offboarding süreci (çıkış, zimmet, erişim kapatma) | | 🟡 (`status` alanı) | ✅ | | P1/P9 |
| Bildirim/e-posta (izin onay akışı) | | | ✅ | | P2 |
| Belge yönetimi (sözleşme/kimlik/izin/sağlık) | | 🟡 (`document_ref` alanı, izin kodu) | ✅ | | P3 |
| Vardiya/çalışma takvimi | | | ✅ | | P4 |
| Puantaj/devam (giriş-çıkış, devamsızlık) | | | ✅ | | P4 |
| Fazla mesai (talep + kural + karşılık) | | | ✅ | | P5 |
| Maaş değişikliği / ücret geçmişi | | | ✅ | | P6 |
| Bordro hazırlığı + dışa aktarım | | | ✅ | | P6 |
| Ülke kural motoru | | | ✅ | | P7 |
| TR hesaplamaları (kıdem/ihbar/izin karşılığı) | | | ✅ | | P7 |
| Raporlama/Excel (merkezî) | | | ✅ | | P8 |
| Görev merkezi/dashboard (tam) | | 🟡 (izin kartları) | ✅ | | P9 |
| Çalışan self service (tam) | | 🟡 (izin kısmı) | ✅ | | P9 |
| Manager self service (tam) | | 🟡 (izin onayı) | ✅ | | P9 |
| Performans yönetimi | | | | ✅ | — |
| Resmi bordro beyanı (e-beyan, SGK bildirgesi) | | | | ✅ | — |
| İşe alım / aday takibi (ATS) | | | | ✅ | — |
| Eğitim/LMS yönetimi | | | | ✅ | — |
| Zaman/cihaz entegrasyonları (turnike, kart) | | | ✅ (import yolu) | cihaz SDK | P4 |

---

## 21. Modül sınırları (bounded contexts)

**Karar (öneri): modular monolith** — tek Django projesi, **tek deployment**, modül sınırları
kod içinde `apps/` paketleri + **servis katmanı** ile korunur. Mikroservis **yapılmaz**.

| Bağlam | Paket | Sorumluluk | Konuştuğu modüller |
|---|---|---|---|
| people | `apps/organization` (+ `apps/people` yeni) | Çalışan, istihdam koşulları, sözleşme, görevler | organization, documents, notifications |
| organization | `apps/organization` | Ağaç, birim, unvan, yönetici | people |
| identity/rbac | `apps/accounts`, `apps/rbac`, `apps/platform`, `apps/tenancy` | Kullanıcı, rol, izin, kapsam, kiracı | tümü (kapı) |
| leave | `apps/leave` | Tür, politika, hakediş, defter, talep, onay | people, attendance, payroll |
| attendance | `apps/attendance` (yeni) | Takvim, vardiya, devam | leave, overtime, payroll |
| overtime | `apps/overtime` (yeni) | Kural, talep, karşılık | attendance, leave, payroll, calculations |
| documents | `apps/documents` (yeni) | Tür, dosya, sürüm, erişim (**M16 tasarımı**: `hr.documents` + `hr.document_access`, içerik **K4**) | people, notifications, reporting |
| notifications | `apps/notifications` (yeni) | Şablon, olay, teslim, outbox | tümü (tetikleyici) |
| payroll | `apps/payroll` (yeni) | Dönem, girdi, dışa aktarım | leave, attendance, overtime, people |
| calculations | `apps/calculations` (yeni) | Ülke kural motoru + TR paketi | payroll, people |
| reporting | `apps/reporting` (yeni) | Tanım, iş, dosya | tümü (okuma) |
| audit | `apps/audit` | Denetim izi | tümü (yazma) |

**Sınır kuralları:**
1. Modüller birbirinin **tablolarına doğrudan yazmaz**; **servis fonksiyonu** çağırır
   (mevcut `apps/leave/services` deseni genelleştirilir).
2. Döngüsel bağımlılık yasak: `notifications` ve `audit` **kimseyi import etmez** (olay
   payload'ı ile beslenir); `reporting` **yalnız okur**.
3. Her modül kendi `permissions` modül kataloğunu tanımlar; izin kodları `<modül>.<kaynak>.<işlem>`.
4. Yeni modül = yeni `apps/<ad>` + `services/` + `migrations/` + test dosyası; **mevcut
   modülün içine yeni sorumluluk gömülmez**.

## 22. Fazlandırma (gerçek bağımlılıklara göre düzeltilmiş)

> Kullanıcının önerdiği sıra temel alındı; **iki düzeltme** yapıldı:
> (1) **P5 ikiye ayrıldı** — `PAY` karşılığı, ücret geçmişi (P6) ve saatlik ücret kuralı (P7)
> olmadan **hesaplanamaz**; bu yüzden P5a = talep+kural+TIME_OFF, P5b = PAY (P6/P7 sonrası).
> (2) **P8'e "erken dilim"** eklendi — çalışan listesi ve izin bakiyesi raporları P1/P3
> sonrasında hemen üretilebilir (HR'ın en sık istediği çıktı), tam rapor altyapısı P8'de kalır.

---
### P0 — Mevcut modüllerin stabilizasyonu (devam eden)
- **Amaç:** arayüz/izin/çalışan/organizasyon akışlarında kalan pürüzler; regresyon yok.
- **Kullanıcıya görünen sonuç:** kesintisiz çalışan test arayüzü; tutarlı hata mesajları.
- **DB:** yok (yalnız mevcut tablolar). **Servis:** mevcut.
- **Permissions:** mevcut. **Audit:** mevcut olaylar.
- **Job:** yok. **Test:** mevcut 473 test + test UI smoke.
- **OD blocker:** OD29/OD30/OD31 (açık kararlar) — **P0'ı bloklamaz**, ama izin/bakiye
  yetkilerinin son hâli P1 öncesi netleşmeli.
- **Migration riski:** yok.

---
### P1 — People lifecycle + yapılandırılabilir onboarding formu
- **Amaç:** istihdam koşullarının (model/tip/lokasyon/sözleşme/deneme) ve form
  yapılandırmasının gelmesi; işe girişin **tek akışta** tamamlanması.
- **Kullanıcıya görünen sonuç:** HR, çalışanı açarken ülke/tipe göre değişen form görür; alanları
  kiracı yöneticisi açar/kapatır; eksik alanlar için görev üretilir.
- **DB (yeni):** `hr.employee_employment_terms`, `hr.employee_contracts`,
  `hr.employee_assignments` (terfi geçmişi — gerekiyorsa), `hr.form_field_definitions`,
  `hr.employee_tasks`, `hr.employee_task_events`, `hr.onboarding_templates`,
  `hr.onboarding_template_tasks`. **Değişen:** `hr.employees` (+`termination_reason_code`,
  `notice_end_date` — OD41 kararına bağlı).
- **Servisler:** `employee.create` (genişletilmiş, atomik), `employment_terms.set`,
  `contract.create/renew`, `form_config.upsert`, `tasks.generate_from_template`,
  `tasks.complete`.
- **Permissions (öneri):** `employee.terminate`, `employee.form.manage`,
  `employee.task.read|manage`, `contract.manage`.
- **Audit events:** `employee.create`, `employee.terms_change`, `contract.create`,
  `contract.renew`, `employee_form.config.update`, `employee_task.create|complete`.
- **Background jobs:** yok (görev üretimi senkron, mail P2).
- **Test kabul:** §3.5, §4.4 ve §5.3'teki maddelerin tamamı.
- **OD blocker:** **OD41** (notice_period temsili), **OD35/OD36/OD38** (form modeli/izin/sürüm),
  **OD37** (K3 zorunlu olabilir mi). → P1 **bu kararlar olmadan başlamamalı** (özellikle OD41 ve
  OD38).
- **Migration riski:** orta — `hr.employees`'e nullable alan eklemek güvenli; **zorunlu** hâle
  getirmek mevcut satırları kırar → yalnız uygulama katmanında zorunluluk.

---
### P2 — Bildirim / e-posta (outbox)
- **Amaç:** onay akışının e-posta ile yürümesi; mail hatasının ana işlemi bozmaması.
- **Kullanıcıya görünen sonuç:** izin talebi düşünce approver'a mail; onay/red sonucu çalışana
  mail; mail gitmese bile işlem başarılı kalır, "gönderilemedi" yeniden denenir.
- **DB (yeni):** `hr.notification_templates`, `hr.notification_events`,
  `hr.notification_deliveries` (+ `dedup_key`, `attempts`, `next_retry_at`, `error_code`).
- **Servisler:** `notify.enqueue(event)` (outbox yazımı, **transaction içinde**),
  `notify.dispatch()` (worker), `notify.retry`.
- **Permissions:** `notification.template.manage`, `notification.read`.
- **Audit events:** `notification.template.update`, `notification.event.enqueued`,
  `notification.delivery.failed`.
- **Job:** worker (`run_worker`) + `send_notifications` komutu; SMTP bağımsız.
- **Test kabul:** §5.4 (mail sağlayıcı çökerse ana işlem rollback olmaz; aynı olay iki kez
  mail atmaz — dedup; başarısız teslim yeniden denenir).
- **OD blocker:** yok (SMTP yapılandırması operasyonel).
- **Migration riski:** düşük (yalnız yeni tablolar).

---
### P3 — Belge yönetimi
- **Amaç:** merkezî belge deposu + yükleme güvenliği + belge zorunluluğu/süre takibi.
- **Kullanıcıya görünen sonuç:** çalışan/İK belge yükler, eksik/süresi dolan belgeler için görev
  ve bildirim üretilir; izin türü "belge gerekir" gerçekten belge ister.
- **DB (yeni):** `hr.document_types`, `hr.documents`, `hr.document_versions`,
  `hr.document_access_log`; `hr.tenant_quotas` (depolama/kota). **Değişen:**
  `hr.leave_requests.document_ref` → `hr.documents.id` FK'si (nullable, geçiş güvenli).
- **Servisler:** `document.upload` (MIME sniff + hash + sanitize), `document.download`
  (yetki + audit), `document.expire_check`, `quota.consume`.
- **Permissions:** `document.type.manage`, `document.upload`, `document.read`,
  `document.confidential.read`, `document.delete`.
- **Audit:** `document.upload`, `document.download`, `document.delete`,
  `document.type.update`.
- **Job:** `check_document_expiry` (günlük), `purge_expired_files`, `scan_pending`
  (malware tarayıcı bağlanırsa).
- **Test kabul:** §6.7 (kota aşımı, MIME reddi, yetkisiz indirme 403, süre bitimi, K3 belge
  ayrı izin, indirme audit'i).
- **OD blocker:** **OD46** (system hard cap ve tenant üst sınırı kaç MB), **OD47** (blob DB'de mi
  dosya sisteminde mi — gerekçeli karar), **OD48** (retention süreleri).
- **Migration riski:** düşük-orta (`leave_requests.document_ref` geçişi nullable FK).

---
### P4 — Devam / vardiya + çalışma takvimi
- **Amaç:** takvim, vardiya, giriş/çıkış, devamsızlık; izin gün hesabı iş gününe geçer.
- **Kullanıcıya görünen sonuç:** izin talebi hafta sonu/tatil için gün düşmez; vardiya ataması
  ve devamsızlık görünür.
- **DB (yeni):** `hr.working_calendars`, `hr.calendar_days`, `hr.shifts`,
  `hr.shift_assignments`, `hr.attendance_records`, `hr.attendance_events`.
- **Servisler:** `calendar.resolve_day_type`, `attendance.recompute_day` (idempotent),
  `shift.assign`, `attendance.import`.
- **Permissions:** `attendance.read`, `attendance.manage` (mevcut), `shift.manage`,
  `calendar.manage`.
- **Audit:** `shift.assign`, `attendance.manual_entry`, `calendar.update`.
- **Job:** `mark_absenteeism` (günlük), `publish_shift_plan`.
- **Test kabul:** §10.3 + §9.3 (A1 maddeleri).
- **OD blocker:** **OD49** (izin gün hesabı geçişinde geçmiş talepler: yazıldığı gibi mi kalır —
  öneri: kalır). Ayrıca resmi tatil kaynağı (ülke kuralı mı, elle mi) → OD50.
- **Migration riski:** **yüksek** — gün hesabı davranışı değişiyor; mevcut izin testlerinin
  gözden geçirilmesi ve `days_requested` yazımının sürümlenmesi gerekir.

---
### P5a — Fazla mesai: talep + kural + izin karşılığı (TIME_OFF)
- **Amaç:** fazla mesai kaydı ve izin karşılığı; ücret hesabı **yok**.
- **Kullanıcıya görünen sonuç:** çalışan girer, yönetici onaylar, izin kredisi bakiyeye düşer.
- **DB (yeni):** `hr.overtime_rules`, `hr.overtime_requests`, `hr.overtime_entries`.
- **Servisler:** `overtime.create`, `overtime.decide`, `overtime.split_segments`,
  `overtime.credit_timeoff` (leave ledger'a yazar), `overtime.cancel`.
- **Permissions:** `overtime.rule.manage`, `overtime.request.create|read|approve|cancel`,
  `overtime.balance.read`.
- **Audit:** `overtime.rule.update`, `overtime.request.create|approve|reject|cancel`,
  `overtime.timeoff.credit`.
- **Job:** `recompute_overtime` (kural değişince yalnız **yeni** talepler; geçmiş değişmez).
- **Test kabul:** §11.4/1,2,4,5 + ledger mutabakatı.
- **OD blocker:** **OD32** (leave ledger'a yazım biçimi), **OD44** (yuvarlama), **OD39**
  (onay adım sırası).
- **Migration riski:** düşük (yeni tablolar).

---
### P5b — Fazla mesai: ücret karşılığı (PAY)
- **Amaç:** onaylı fazla mesainin tutara çevrilmesi.
- **Bağımlılık:** **P6 (ücret geçmişi)** + **P7 (saatlik ücret kuralı)** — bu ikisi yoksa
  hesaplama **yazılmaz**.
- **Kullanıcıya görünen sonuç:** bordro girdisine "fazla mesai tutarı" kalemi düşer; kalem dökümü
  görülebilir.
- **DB:** mevcut `hr.overtime_*` + `hr.payroll_inputs` (P6).
- **Servisler:** `overtime.calculate_pay` (kural snapshot'lı), `payroll.input_from_overtime`.
- **Permissions:** `overtime.pay.read` (**OD33**), `payroll.input.write`.
- **Audit:** `overtime.pay.calculated`, `payroll.input.create`.
- **Job:** `recompute_open_period_inputs` (yalnız **açık** dönem).
- **Test kabul:** §11.4/1,2,3 + tutarın kural sürümüyle yeniden üretilebilirliği.
- **OD blocker:** **OD33**, **OD34** (snapshot anı).
- **Migration riski:** düşük.

---
### P6 — Ücret geçmişi + bordro hazırlığı
- **Amaç:** maaş tarihçesi + dönem bazlı bordro girdisi üretimi ve dışa aktarım.
- **Kullanıcıya görünen sonuç:** HR maaş değişikliğini tarihli kaydeder; dönem sonunda girdiler
  toplanır, onaylanır, Excel/bordro şablonuna aktarılır.
- **DB (yeni):** `hr.employee_compensation`, `hr.employee_payroll_profile`,
  `hr.payroll_periods`, `hr.payroll_inputs`, `hr.payroll_runs`, `hr.payroll_exports`.
- **Servisler:** `compensation.set` (yeni satır + kapatma), `payroll.open_period`,
  `payroll.collect_inputs`, `payroll.adjust`, `payroll.approve`, `payroll.finalize`,
  `payroll.export`.
- **Permissions:** `compensation.read|manage` (öneri), `payroll.period.manage`,
  `payroll.input.write`, `payroll.approve`, `payroll.export`.
- **Audit:** `compensation.create`, `payroll.period.open|finalize`,
  `payroll.adjustment.create`, `payroll.export.create`.
- **Job:** `collect_payroll_inputs` (dönem kapanışı), `generate_export` (asınk).
- **Test kabul:** §12.3 + §13.5.
- **OD blocker:** **OD45** (ayrı maaş izni), **OD51** (bordro çıktı formatı/şablonu — sağlayıcı
  bazlı), **OD52** (retro düzeltmede ek dönem mi, manuel düzeltme mi — öneri: manuel düzeltme).
- **Migration riski:** düşük-orta (yeni tablolar; `payroll_inputs` hacmi).

---
### P7 — Ülke kural motoru + TR hesaplamaları
- **Amaç:** mevzuatı versiyonlanmış kural + saf fonksiyon ile modellemek; TR hesaplarını
  global koda gömmemek.
- **Kullanıcıya görünen sonuç:** çıktı farkı olmayan, denetlenebilir hesaplar; "eksik veri"
  listesiyle uyarı; Excel/PDF sonuç.
- **DB (yeni):** `hr.country_rule_sets`, `hr.country_rule_calculations` (+ gerekirse
  `hr.tax_brackets`, `hr.social_security_params`).
- **Servisler:** `rules.resolve(country, code, at)`, `calculations.run(rule_code, inputs)`,
  TR paketi (`severance`, `notice`, `unused_leave_pay`, `payroll_helpers`).
- **Permissions:** `platform.country_rule.manage` (platform), `calculation.run`,
  `calculation.read`, `calculation.export`.
- **Audit:** `country_rule.create|update`, `calculation.run`.
- **Job:** `verify_rule_coverage` (yaklaşan `effective_until` uyarısı).
- **Test kabul:** §14.3 + §15 (eksik veri varken hesap başlamaz; eski hesap değişmez; tavan/kural
  sürümü saklanır).
- **OD blocker:** **OD53** (kural verisi platform mı dosya mı), **OD54** (kiracı parametre
  override'ı izinli mi).
- **Migration riski:** düşük (yeni tablolar) + **hukuki doğruluk riski** (parametrelerin
  kaynağı/resmi referansı olmalı).

---
### P8 — Raporlama / Excel (merkezî) — *erken dilim P1/P3 sonrası*
- **Amaç:** tek rapor servisi, tek güvenlik kapısı, güvenli XLSX.
- **Kullanıcıya görünen sonuç:** HR ihtiyacı olan listeleri tek yerden, yetkisi kadar indirir.
- **DB (yeni):** `hr.report_definitions`, `hr.report_jobs`, `hr.report_files`.
- **Servisler:** `report.run`, `report.job_status`, `report.download`,
  `xlsx.safe_write` (formula injection koruması), `report.schedule` (opsiyonel).
- **Permissions:** `report.run`, `report.sensitive.export`, `report.definition.read`.
- **Audit:** `report.run`, `report.download`, `report.failed`.
- **Job:** `run_report_job` (worker), `purge_expired_reports`.
- **Test kabul:** §16.4.
- **OD blocker:** **OD55** (satır üst sınırı/parçalama), **OD56** (paylaşılan/planlı rapor izni).
- **Migration riski:** düşük.

---
### P9 — Offboarding + ileri otomasyon + görev merkezi + self service
- **Amaç:** çıkış sürecinin uçtan uca yürütülmesi, görev merkezinin tamamlanması, ESS/MSS.
- **Kullanıcıya görünen sonuç:** çıkışta erişimler kapanır, görevler açılır, hesaplar üretilir;
  panel "bugün ne yapmalıyım" ekranı olur.
- **DB:** mevcut `hr.employee_tasks` genişletilir (`kind=offboarding`), `hr.announcements`.
- **Servisler:** `employee.terminate` (atomik süreç), `access.revoke_all`,
  `offboarding.generate_tasks`, `dashboard.cards`, `profile_change.request`.
- **Permissions:** `employee.terminate`, `announcement.manage`, `profile.change.request`.
- **Audit:** `employee.terminate`, `access.revoke`, `employee.archived`.
- **Job:** `check_contract_expiry`, `check_probation_end`, `anniversary_reminder`.
- **Test kabul:** §6.3 + §17 + §18/§19 kural maddeleri.
- **OD blocker:** **OD41** (notice), **OD43** (çıkış onayı kaç adım), **OD57** (archived veri
  saklama).
- **Migration riski:** orta (durum geçişleri + erişim kapatma yan etkileri).

---

## 23. Açık OD6–OD18 analizi (roadmap blocker görünürlüğü)

Bu kararlar **AUTHORIZATION.md**'de açıktır; burada roadmap'e etkisi ölçülür.
(Karar bu turda **verilmez**.)

| OD | Konu | Blokladığı faz | Koddan önce kapanmalı mı | Durum değerlendirmesi |
|---|---|---|---|---|
| **OD6** | Silme mi, arşivleme mi | **P1/P9** (çalışan çıkışı/arşiv), P3 (belge silme) | **Evet** | Kısmen fiilen çözülmüş: kod **soft-delete** (OD21 kapalı) + `archived` durumu kullanıyor. Kalan soru: **kalıcı silme (KVKK "silme hakkı")** nasıl yapılacak → P3/P9 retention kararıyla birleşmeli |
| **OD7** | Şirket yöneticisinin kendi çalışan verisini silme yetkisi | P1 (people), P3 | **Evet** | Fiilen: yönetici **soft-delete** yapabiliyor; kalıcı silme yok. Karar, `employee.delete` izninin kapsamını belirler |
| **OD8** | ADMIN'in yönetim talebi kapsamı | P2/P8 (platform→kiracı veri erişimi, rapor) | Hayır (P0 değil) | Platform tarafı M17–M19 gelmeden etkisiz; **P7+** için gerekli |
| **OD9** | ŞİRKET YETKİLİSİ'nin yetki sınırı | **P1/P6** (kim maaş/bordro görebilir) | **Evet** | Özellikle `compensation.read`, `payroll.*` izinlerinin hangi rollerde olacağını doğrudan etkiler |
| **OD10** | MFA zorunluluğu hangi rollerde | P0 (güvenlik), P6 (bordro/maaş) | **Evet (güvenlik)** | **⚠️ Doküman tutarsızlığı:** `docs/ARCHITECTURE.md` §7 kararı **vermiş görünüyor** (TOTP `tenant_admin` + `super_admin` için zorunlu) ancak **kod yok** ve OD10 hâlâ **AÇIK**. Karar fiilen verilmiş: OD kaydı kapatılmalı veya karar revize edilmeli |
| **OD11** | Bir kişi hem platform hem kiracı rolü taşıyabilir mi | P7 (platform kural yönetimi ↔ kiracı) | Evet (P7 öncesi) | Bugün **fiilen yasak** (mevcut tasarım). Ülke kuralı yönetimi platform tarafında olduğundan bu kural korunmalı → **obsolete değil** |
| **OD12** | Kişiye özel izin istisnası (override) kullanılmalı mı | P1/P6 (özel yetkilendirme) | Hayır | Kodda `hr.user_permission_overrides` **var** ve test UI'da salt-okunur görünüyor → fiilen kullanılıyor; karar yalnız **yönetim akışı** için gerekli |
| **OD13** | Denetim kaydının görünürlüğü, saklaması, bütünlüğü | **P8** (audit raporu), P3 (belge indirme izi), P9 (arşiv) | **Evet** | Audit **hacmi** ve saklama süresi raporlama/retention kararlarını bloklar; ayrıca "kim audit görebilir" sorusu P8'i etkiler |
| **OD14** | Genel kimlik ile arama | P1/P8 (çalışan arama, rapor) | Hayır | Mevcut arama `employee_no` + ad üzerinden; uluslararası `national_id` aramasının PII riski kararı P7/P8 ile gelir |
| **OD15** | Kiracı, platform erişimini görsün mü | P7 (kural güncellemeleri şeffaflığı) | Hayır | P7 öncesi netleşmeli ki mevzuat güncellemesi kiracıya nasıl bildirilecek bilinsin |
| **OD16** | Özel rol sınırı ve katalog evrimi | **P1/P6** (yeni izin kodları: `overtime.*`, `payroll.*`, `document.*`) | **Evet** | **En kritik blocker:** her yeni modül izin kodu ekliyor; katalog/özel rol sınırı netleşmezse yetki modeli dağılır |
| **OD17** | Platform çift onayının uygulanabilirliği | Hayır (platform), dolaylı P7 | Hayır | Tek yöneticili kiracı senaryosunda operasyonel; P7 için "acil durum" yolu tanımlı olmalı |
| **OD18** | Platform kişisel veri erişiminde kiracının rolü | P8 (platform raporu), P9 | Hayır | Kişisel veriye platform erişimi raporlama geldiğinde kritik olur |

**Blocker özeti (koddan önce kapanması gerekenler):** OD6, OD7, OD9, OD10, OD13, OD16
(+ yeni öneriler OD41, OD38, OD46, OD47, OD49, OD51).

---

## 24. Fonksiyonel olmayan gereksinimler (NFR)

| Alan | Bugün (ölçülmüş) | Hedef/kural |
|---|---|---|
| **Audit** | DB **tetikleyicisi** (`hr.audit_trigger_fn`) + servis `log_action(...)`; uygulama rolüne yalnız **INSERT/SELECT** (UPDATE/DELETE yok); `audit.read` + görüntüleme kaydı (`ARCHITECTURE.md` §12) | Yeni modüller aynı sözleşmeyi kullanır; **PII değeri yazılmaz**, alan adı yazılır; ağır raporlama için ayrı salt-okunur görünüm |
| **MFA (TOTP)** | **Karar VAR, kod yok**: `docs/ARCHITECTURE.md` §7 = `tenant_admin` ve `super_admin` için **zorunlu** TOTP | Uygulanırken OD10 kaydı kapatılmalı (bkz. §23) |
| **Tenant isolation** | `tenant_id` + RLS + FORCE + middleware bağlamı | Yeni tablolar **istisnasız** `tenant_id` + RLS politikası + FORCE alır; cross-tenant testi her yeni modülde zorunlu |
| **RLS** | Politikalar + `FORCE ROW LEVEL SECURITY`; app rolü ile erişim | Yeni tablolar migration'da politika + grant ile birlikte gelir (grant script'i güncellenir) |
| **Şifreleme** | K3 alanlar Fernet (`apps/common/fields.py`), anahtar `.env` | K3 kapsamı genişlerse anahtar rotasyonu prosedürü yazılır; **belge içeriği** ayrıca disk/depolama seviyesinde şifreli (OD47 kararına bağlı) |
| **Secrets** | `.env` (dosya) | V1: tek VPS, dosya izinleri 600 + yedekte hariç; ileride secret manager (yeniden değerlendirme eşiği §25) |
| **Dosya depolama** | **Karar VAR, kod yok**: `docs/ARCHITECTURE.md` §9 = **S3 uyumlu özel nesne depolama + boto3**, ön imzalı URL, `tenant/<tenant_id>/<kategori>/<uuid>`; geliştirmede yerel FS. `config/settings.py` içinde `STORAGE_S3_*` + `STORAGE_SIGNED_URL_TTL_SECONDS` mevcut (ölçüldü) | P3: bu karar uygulanır; path traversal/SSRF koruması, dosya adı sanitize, sha256, kota |
| **Yedek/geri yükleme** | `docs/ARCHITECTURE.md`: **pgBackRest veya `pg_dump` → nesne depolama, şifreli**; "geri yükleme denenmeden yedek yok sayılır"; belge deposunda **sürümleme + yaşam döngüsü** | Günlük tam yedek + WAL arşivi; **geri yükleme tatbikatı** çeyrek yılda bir |
| **Observability** | Django log; üretim hedefi `docs/ARCHITECTURE.md` §12: stdout → **journald** (+ Sentry); audit'ten **ayrı** | Yapılandırılmış log, `request_id`, yavaş sorgu uyarısı; **PII loglanmaz** |
| **Queue/worker** | **Kurulu ve yapılandırılmış, kullanılmıyor**: `django-q2>=1.10` bağımlılıkta (`pyproject.toml:20`), `Q_CLUSTER` `config/settings.py:274-285` (ORM broker, `max_attempts` 3, `retry` 600 s, `save_limit` 500, `catch_up` False, `sync: DEBUG`); **enqueue edilen görev yok** (ölçüldü). `sync: DEBUG` sayesinde geliştirmede görevler istek içinde senkron çalışır → test edilebilir, üretimde worker süreci gerekir | P2: outbox + mevcut django-q2 yapılandırması kullanılır; ayrı kuyruk altyapısı **kurulmaz** |
| **Rate limits** | **DRF throttling + DB cache** (`ARCHITECTURE.md` §6.1); `/auth/login` ve token uçlarında `throttle_scope='auth'` (kanıt: `apps/accounts/views.py`) | Export/belge yükleme/mail tetikleme uçlarına ayrı scope; tenant bazlı kota (P3) |
| **API pagination** | Test UI uçları sabit limitli liste | Genel API standardı: cursor/sayfa tabanlı; rapor uçları asınk |
| **Async exports** | **YOK** | P8: eşik üstü raporlar worker'a; ilerleme durumu ve bildirim |
| **Data retention** | **YOK** | P3/P9: belge/audit/çalışan arşiv saklama süreleri + otomatik temizlik + KVKK silme talebi akışı (OD6/OD48) |
| **GDPR/KVKK** | K1–K3 sınıflandırması + erişim kapıları var | Veri sahibi başvurusu (erişim/silme/düzeltme) akışı; işleme amacı kaydı; **yurt dışı aktarım** notu (SaaS, AB barındırma kararı) |
| **Localization (i18n)** | Ölçüldü: `USE_I18N = True`, `LANGUAGE_CODE = "tr"`, `USE_TZ = True`, `TIME_ZONE = "UTC"`; **`LOCALE_PATHS` ve çeviri dosyaları yok**, arayüz metinleri TR sabit | P7: çoklu dil (UI + e-posta şablonları, `ARCHITECTURE.md` §11 TR/EN hedefi); ülke bazlı tarih/para biçimi |
| **Timezone** | Tek TZ (proje ayarı) | Vardiya/devam modülü **kiracı ve lokasyon** bazlı TZ ister (§10 `working_calendars.timezone`) |
| **Currency** | **YOK** | P6: `currency` (ISO-4217) zorunlu; çoklu para birimi; kur dönüşümü **kapsam dışı** (raporlama tek para biriminde) |
| **Country-specific rules** | **YOK** (kod ve tasarım yok; `hr.country_rule*` `DATABASE.md`'de geçmiyor — ölçüldü) | §14: versiyonlanmış kural seti; global kod ülke `if`'i içermez |

---

## 25. Performans / ölçek sınırları

**İlke:** V1 hedefi **küçük/orta işletme** (10–250 çalışan/kiracı). Dağıtık sistem
**kurulmaz**; tek VPS, tek veritabanı, tek worker yeterlidir. Aşağıdaki eşikler
**yeniden değerlendirme tetikleyicileridir** (bu turda bir şey kurulmaz).

| Boyut | V1 hedefi | Yeniden değerlendirme eşiği | O eşikte yapılacak |
|---|---|---|---|
| Çalışan/kiracı | ≤ 250 | > 1.000 çalışan **veya** > 50 kiracı | Listelerde cursor pagination zorunlu, ağır raporlar için okuma replikası |
| Denetim (audit) hacmi | ~50–200 olay/çalışan/yıl | `hr.audit_logs` > ~50 M satır **veya** sorgu p95 > 300 ms | Tablo bölümleme (partition by month) + arşiv tablosu; audit yazımını kuyruğa alma |
| Belgeler | ≤ 10 GB/kiracı | > 100 GB toplam **veya** > 200 k belge | Nesne depolama (S3-uyumlu) + CDN'siz imzalı indirme; metadata arama indeksi |
| Raporlar | ≤ 50 k satır/dosya | Düzenli > 50 k satır **veya** rapor süresi > 2 dk | Asınk üretim zorunlu (P8), parça parça indirme, önbellek |
| Bildirim kuyruğu | ≤ 5 k olay/gün | > 50 k/gün **veya** gecikme > 5 dk | Ayrı kuyruk (harici broker) + paralel worker |
| Devam kaydı | ≤ 250 × 250 iş günü = ~60 k satır/yıl/kiracı | > 5 M satır **veya** günlük toplu işlem > 5 dk | Aylık bölümleme, toplu `bulk_create`, rapor için özet tablo |
| Fazla mesai/payroll girdisi | ≤ 10 k satır/dönem | > 500 k satır/dönem | Dönem bazlı arşiv + asınk toplama |
| Eşzamanlı kullanıcı | ≤ 50 | > 300 | Uygulama sunucusu ölçekleme (2+ worker), bağlantı havuzu |
| Veritabanı boyutu | ≤ 20 GB | > 100 GB | Yönetilen PostgreSQL + yedek/geri yükleme otomasyonu |

**Ek kurallar:**
1. **N+1 sorgu yasağı**: her liste/rapor ucu `select_related`/`prefetch_related` kullanır;
   testlerde sorgu sayısı ölçümü (mevcut test UI deseni genişletilir).
2. **Index politikası**: `tenant_id` + sık filtrelenen kolonlar; kısmi indeksler (soft-delete
   koşullu) mevcut deseniyle aynı.
3. **Ağır iş senkron HTTP'de yapılmaz** (rapor, toplu mail, toplu bordro toplama) — worker.
4. **Bellek**: dosya yükleme **stream** edilir (tümü belleğe alınmaz); max boyut kontrolü
   stream sırasında keser.

## 26. Açık kararlar (öneri)

> **Bu turda hiçbir karar verilmedi.** Aşağıdakiler **öneri**dir; `docs/AUTHORIZATION.md`
> içindeki OD listesine **açılmamıştır**. OD32–OD40 numaraları bu dokümanın ilk sürümünde
> (fazla mesai + dinamik form bölümleri) önerilmişti ve **korunmuştur**; OD41+ bu turda eklenen
> önerilerdir. En yüksek **kapanmış** karar OD31'dir.

### 24.1 Mevcut öneriler (ilk sürümden korunanlar)

| # | Soru | Önerilen seçenek |
|---|---|---|
| OD32 | Fazla mesainin TIME_OFF karşılığı leave ledger'a nasıl yazılsın? | Sistem kodlu izin türü (`overtime_comp`) + `entry_kind='overtime_credit'` defter kaydı |
| OD33 | Ücret karşılığı için ayrı `overtime.pay.read` gerekli mi? | Gerekli değil; mevcut `overtime.request.read` + `compensation.read` yeterli |
| OD34 | Katsayı hangi anda sabitlenir? | **Onay anında** snapshot (`pay_multiplier_snapshot`) |
| OD35 | Tenant serbest (custom) alan tanımlayabilsin mi? | V1: hayır (yalnız tanımlı alanların görünürlüğü); custom alan P9+ |
| OD36 | Form yapılandırma izni: `tenant_form.*` mı, `role.manage` mı? | Yeni `employee.form.manage` kodu |
| OD37 | K3 alanlar dinamik formda zorunlu yapılabilir mi? | **Hayır** (aşırı veri toplama riski) |
| OD38 | Form yapılandırması sürümlenmeli mi? | Evet: değişiklik audit + mevcut kayıtlar geçersiz sayılmaz, "eksik alan" görevi üretilir |
| OD39 | Fazla mesai onay sırası leave ile aynı mı? | Evet, aynı altyapı (önce birincil yönetici, sonra İK) |
| OD40 | `attendance_records.overtime_minutes` ile `overtime_requests` ilişkisi | Devam kaydı **hesaplar**, talep **onaylar**; onay tek yerde (`overtime_requests.status`) |

### 24.2 Bu turda eklenen öneriler

| # | Soru | Önerilen seçenek | Blokladığı faz |
|---|---|---|---|
| **OD41** | `notice_period` durum olarak mı, türetilmiş etiket mi? | Durum kümesi büyütülmez; `notice_end_date` alanı + türetilmiş "ihbar döneminde" etiketi | P1/P9 |
| **OD42** | `suspended` çalışan izin talebi açabilir mi? | Hayır; yalnız HR açar (talep formu gizlenir, sunucu da reddeder) | P1 |
| **OD43** | İşten çıkış onayı kaç adım? | İki adım (başlatan ≠ onaylayan), `employee.terminate` izni | P1/P9 |
| **OD44** | TIME_OFF yuvarlama politikası | Dakika hesaplanır, bakiyeye **yarım gün aşağı** yuvarlanır; artan dakika birikir | P5a |
| **OD45** | Maaş için ayrı izin kodu? | Evet: `compensation.read` / `compensation.manage` (K2 + ayrı denetim) | P6 |
| **OD46** | Belge boyutu limitleri | Sistem tavanı **25 MB**, tenant alt sınırı ≤ 25 MB (dosya türü bazlı daha düşük olabilir) | P3 |
| **OD47** | ~~Belge blob'u nerede?~~ | **⚠️ KARAR ZATEN VERİLMİŞ** (`docs/ARCHITECTURE.md` §9 + K7: S3 uyumlu nesne depolama, metadata DB'de, dev'de yerel FS) → **karar değil, uygulama işi**; roadmap blocker'ı değildir | P3 (uygulama) |
| **OD48** | Saklama (retention) süreleri | Belge: iş ilişkisi + yasal süre; audit: 10 yıl; rapor dosyası: 7 gün | P3/P8/P9 |
| **OD49** | İzin gün hesabı değişince geçmiş talepler? | Yazıldığı gibi kalır; `calculation_version` ile işaretlenir | P4 |
| **OD50** | Resmi tatil kaynağı | Ülke kural setinden beslenir + kiracı **ek** gün ekleyebilir (silme yok) | P4/P7 |
| **OD51** | Bordro çıktı formatı | V1: XLSX/CSV **şablon** (sağlayıcı bazlı şablon P6'da seçilir); otomatik gönderim yok | P6 |
| **OD52** | Kapalı dönem düzeltmesi | Sonraki döneme **manuel düzeltme girdisi** (ek dönem değil) | P6 |
| **OD53** | Ülke kural verisi nerede tutulur? | DB (`hr.country_rule_sets`) + kodda saf fonksiyon; referans metin zorunlu | P7 |
| **OD54** | Kiracı ülke kurallarını override edebilir mi? | Kuralları **hayır**; yalnız izinli parametrelerde kiracı override'ı | P7 |
| **OD55** | Rapor satır üst sınırı | 50.000 satır; aşarsa parçalı üretim veya reddet + yönlendir | P8 |
| **OD56** | Planlı/paylaşımlı rapor | V1: yalnız elle çalıştırma (planlı rapor P9+) | P8 |
| **OD57** | Arşivlenmiş çalışan verisi | Kişisel veri **maskelenir**, hesap kayıtları (audit/bordro) korunur; kalıcı silme yalnız KVKK başvurusuyla ve denetimli | P9 |

---

## 27. Bağımlılık grafiği

```
                    ┌───────────────────────────────────────────┐
                    │ P0  mevcut modüllerin stabilizasyonu      │
                    └───────────────┬───────────────────────────┘
                                    │ (OD6/OD7/OD9/OD10/OD16 netleşmeli)
                                    ▼
        ┌───────────────────────────────────────────────────────────┐
        │ P1  people lifecycle + configurable onboarding formu      │
        │     (employment terms, contract, probation, tasks, form)   │
        └───────┬───────────────────────────────────┬───────────────┘
                │                                   │
                ▼                                   ▼
   ┌────────────────────────┐          ┌────────────────────────────┐
   │ P2  notifications      │◄─────────│  tüm modüller olay yayar    │
   │     (outbox + worker)  │          └────────────────────────────┘
   └───────┬────────────────┘
           │ (belge olayları: eksik/süre)
           ▼
   ┌────────────────────────┐
   │ P3  documents          │──── izin "belge gerekir" gerçek olur (§9)
   └───────┬────────────────┘
           │
           ▼
   ┌─────────────────────────────────────────────┐
   │ P4  attendance + çalışma takvimi            │──► izin gün hesabı iş günü (A1)
   └───────┬─────────────────────────────────────┘
           │
           ▼
   ┌─────────────────────────────────────────────┐
   │ P5a overtime: talep + kural + TIME_OFF      │──► leave ledger (P0 mevcut)
   └───────┬─────────────────────────────────────┘
           │
           ▼
   ┌────────────────────────┐        ┌───────────────────────────────┐
   │ P6  compensation +     │◄───────│ P7  country rule engine       │
   │     payroll hazırlığı  │        │     + TR hesaplamaları        │
   └───────┬────────────────┘        └───────────┬───────────────────┘
           │                                     │
           └──────────────┬──────────────────────┘
                          ▼
              ┌───────────────────────────┐
              │ P5b overtime PAY          │  (P6 + P7 olmadan YAZILMAZ)
              └───────────┬───────────────┘
                          ▼
              ┌───────────────────────────┐
              │ P8  reporting/Excel       │  (erken dilim: P1/P3 sonrası liste+bakiye)
              └───────────┬───────────────┘
                          ▼
              ┌───────────────────────────┐
              │ P9  offboarding + görev   │
              │     merkezi + ESS/MSS     │
              └───────────────────────────┘
```

**Kritik kenarlar (ihlal edilemez):**
1. `P5b → P6,P7` : ücret karşılığı, maaş geçmişi ve saatlik ücret kuralı olmadan yazılmaz.
2. `P6 → P4,P5a` : bordro girdisi devamsızlık ve fazla mesai verisine bağlıdır.
3. `P3 → P2` : belge olayları (eksik/süre) bildirim altyapısı ister.
4. `P4 → P1` : çalışma takvimi, istihdam koşullarındaki çalışma modeline bağlıdır.
5. `P9 → P3,P5a,P6` : çıkışta izin karşılığı ve bordro kapanışı bu modüllere bağlıdır.

---

## 28. Sıradaki uygulama paketi (P1.0 — öneri)

**Amaç:** çalışan açma akışının "istihdam koşulları + yapılandırılabilir form + görev listesi"
ile tamamlanması. Kod yazılmadan önce **OD16, OD38, OD41** kapanmalı veya açıkça ertelenmelidir.

| Adım | İş | Dosya (öneri) | Test |
|---|---|---|---|
| 1 | İstihdam koşulları modeli (etkili tarihli) + RLS + audit | `apps/people/models.py`, `migrations/0001_*.py` | tarihçe + tek açık kayıt testi |
| 2 | Sözleşme modeli (tip, süre, belge bağı) | aynı | sözleşme yenileme testi |
| 3 | Görev modeli (`hr.employee_tasks`) + şablonlar | aynı | şablondan üretim + idempotans |
| 4 | Form yapılandırma modeli + allowlist doğrulama | aynı | A seviyesi kilitli alan reddi |
| 5 | Çalışan oluşturma servisi: form konfigürasyonuna göre zorunluluk | `apps/organization/services.py` (genişletme) | eksik zorunlu alan → 400 |
| 6 | Test arayüzü: alt sekme + dinamik alan gösterimi | `apps/testui/…` | canlı doğrulama |
| 7 | Panel: onboarding görev kartı | `apps/testui/api.py` | kart sayısı testi |

**Kabul (paket sonu):** §3.5 + §4.4 + §5.3 maddeleri; tüm mevcut testler değişmeden geçer;
`verify_fresh_install.sh` yeni adımlarla genişletilir.

---

## 29. Doküman haritası

| Doküman | Rolü |
|---|---|
| **`docs/HR_ROADMAP.md` (bu doküman)** | **Tek doğruluk kaynağı**: modül mimarisi, faz sırası, önerilen kararlar, ölçüm sonuçları |
| `docs/PROJECT.md` | Ürün kapsamı ve hedef pazar (roadmap'e referans) |
| `docs/FEATURES.md` | Uygulanmış özellikler (durum etiketli); roadmap'e referans |
| `docs/ARCHITECTURE.md` | Teknik mimari kararları (K-numaralı); modül sınırları için bu doküman §21'a referans |
| `docs/DATABASE.md` | Uygulanmış şema (M-numaralı); roadmap tabloları **burada yazılmaz**, uygulanınca eklenir |
| `docs/AUTHORIZATION.md` | İzin/RBAC/kapsam kararları ve OD kaydı (KAPALI/AÇIK) |
| `docs/SECURITY.md` | Güvenlik ilkeleri (RLS, şifreleme, erişim); bu doküman §24'ye referans |
| `docs/ORGANIZATION.md` | Organizasyon modeli ve yaşam döngüsü kuralları |
| `docs/API.md` | Uygulanmış uçlar (üretim sözleşmesi); roadmap uçları **yazılmaz** |
| `docs/DEVELOPMENT_RULES.md` | Geliştirme/doğrulama kuralları (kapılar, elle test adımları) |
| `docs/CHANGELOG.md` | Sürüm bazlı değişiklik kaydı |

**Yazım kuralı:** bu doküman **plan** içerir; uygulanan hiçbir şey buraya "yapıldı" diye
yazılmaz. Uygulama tamamlandığında ilgili bölüm `docs/FEATURES.md`/`DATABASE.md`'ye taşınır ve
burada **Durum: implemented** etiketiyle işaretlenir (mevcut doküman lejantı).
