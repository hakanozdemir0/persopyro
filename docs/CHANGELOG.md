# Değişiklik Kaydı — Persopyro

Kayıt biçimi: en yeni sürüm en üstte. Her giriş tarih, sürüm, eklenen /
değişen / doğrulanan / kalan başlıklarını içerir.

## [0.37.0] — 2026-09-13 — P1-A: People lifecycle veri modeli + izin altyapısı (KOD)

**Kapsam:** P1'in **ilk uygulama paketi** (P1-A). Şema, izin kataloğu, RLS/guard altyapısı ve
mevcut veri backfill'i uygulandı. **P1-B/P1-C (lifecycle servisleri/uçları, form konfigürasyon
uçları, geçmiş ekranı) bu turda yazılmadı**; terminate/suspend UI, bildirim, belge, bordro,
offboarding kapsam dışıdır.
**Baseline:** `c688ddd`, çalışma ağacı temiz, **473 passed**.

### EKLENEN

- **İzin kataloğu (+8 kod):** `employee.sensitive.write`, `employee.suspend`, `employee.terminate`,
  `employee.restore`, `employee.history.read`, `employee.form.manage`, `org.workplace.read`,
  `org.workplace.manage`. Katalog: **47** kod (41 kiracı + 6 platform).
  Rol hedefleri: `tenant_admin` 41, `hr_manager` 30, `tenant_officer` 23, `dept_manager` 17,
  `employee` 8. **`NON_DELEGABLE_PERMISSION_CODES`** (OD9) katalogda tanımlandı.
  Migration: `rbac/0012_p1a_permissions_roles` (izin kayıtları + **sistem rol senkronu**).
- **`hr.workplaces`** (OD58): `code`, `name`, `type` (`office|branch|remote|client_site`),
  `country_code` (ISO alpha-2, CHECK), `city`, `address`, `timezone` (**IANA**, model validator +
  CHECK), `status`; kısmi benzersizlik `(tenant, code) WHERE deleted_at IS NULL`.
- **`hr.employee_employment_terms`** (OD60): etkili tarihli istihdam koşulları
  (`employment_type`, `working_model`, `workplace_id`, `contract_type`, `contract_start_date`,
  `contract_end_date`, `probation_end_date`, `reason`, `created_by`); **tek açık satır**
  (kısmi benzersizlik) + tarih/ küme CHECK'leri. **Ücret alanı yok** (P6).
- **`hr.employee_assignments`** (OD60): departman/pozisyon atama geçmişi; tek açık satır;
  tarih CHECK'i; `(tenant, employee, effective_from)` / `(tenant, department)` /
  `(tenant, position)` indeksleri.
- **`hr.form_field_definitions`** (OD35/OD36/OD37/OD38): tenant override satırları;
  `lock_level` + `data_class` **sunucu hesaplar**; DB `CHECK`'leri: bilinmeyen `field_code`
  reddi, **A** seviyesi gizlenemez/opsiyonel yapılamaz, **K3** zorunlu yapılamaz.
  Alan kataloğu koda eklendi (`apps/organization/employee_form_catalog.py`, 33 alan).
- **`hr.employees` yaşam döngüsü kolonları** (OD41): `notice_end_date` (CHECK ≥ `termination_date`),
  `termination_reason_code` (kapalı küme), `termination_note`; `status='terminated'` iken tarih +
  neden **zorunlu** (DB CHECK); **`UNIQUE(user_id) WHERE deleted_at IS NULL AND status <> 'archived'`**
  (OD59). `notice_period` **durum olarak eklenmedi** (türetilmiş etiket).
- **`users.auth_version`** (OD59): `PositiveIntegerField` (negatif olamaz), varsayılan 0 —
  erişim kesme altyapısı (uygulama P1-B'de).
- **RLS + FORCE** (`organization/0016_rls_p1a`) yeni 4 tabloda; **kiracılar arası referans
  guard'ları** (`0017_p1a_same_tenant_guards`) ve **"soft-delete edilmiş hedefe yeni açık kayıt
  yazılamaz" guard'ı** (`0018_p1a_open_ref_guard`).
- **Backfill** (`organization/0015_p1a_backfill`): mevcut (silinmemiş) her çalışan için
  `department_id`/`position_id` + `hire_date` üzerinden **tek açık atama**; her kiracı için
  **33 alan** form konfigürasyonu. Idempotent (kısmi indeks + `ON CONFLICT … WHERE`).
  **`employee_employment_terms` için satır üretilmedi** (mevcut şemada istihdam tipi/modeli yok;
  uydurma değer yazmak yanlış iş verisi olurdu) ve legacy çalışanlara **kullanıcı uydurulmadı**.
- **Yetkiler:** `scripts/02_grant_app_privileges.sql` — yeni tablolarda `SELECT, INSERT, UPDATE`
  (DELETE/TRUNCATE geri alındı, OD21) + platform rolü için açık `REVOKE ALL`.
- **Testler:** `tests/test_p1a_people_lifecycle.py` (38 test): izin kataloğu/rol setleri,
  devredilemez liste, işyeri izolasyonu + kısmi benzersizlik + timezone/ülke doğrulaması,
  istihdam koşulları (tek açık kayıt, tarih aralığı, geçmiş korunumu, cross-tenant reddi),
  atamalar (tek açık, cross-tenant, soft-delete hedef guard'ı, backfill idempotansı),
  form alan kuralları (bilinmeyen kod, A kilidi, K3 zorunluluk reddi, tenant izolasyonu),
  lifecycle CHECK'leri, auth_version, RLS/FORCE, guard trigger'ları, uygulama rolü yetkileri.
- **`scripts/verify_fresh_install.sh` 6c-11:** yeni tablolar, kolonlar, kısıtlar, index'ler,
  RLS+FORCE sayısı (23), izin sayısı (47), rol izin sayıları, form alanı (33), çoklu açık atama (0),
  `auth_version`, platform erişim yasağı, uygulama rolünde DELETE yasağı.

### DÜZELTİLEN (uygulama sırasında ölçülen hatalar)

0. **`null=True` yerine boş dize (DJ001) + migration sırası:** `termination_reason_code` ve
   `label_override` alanları proje kuralı gereği `blank=True, default=""` yapıldı
   (`organization/0019_p1a_reason_not_null`). Kolonu `NOT NULL` yapmadan önce mevcut NULL'ların
   doldurulması ve **eski CHECK'lerin önce kaldırılması** gerekti (aksi halde `''` yazımı eski
   kısıtı ihlal eder); doldurma da FORCE RLS nedeniyle kiracı bağlamında yapılır. Üç adım da
   ölçülerek düzeltildi.

1. **Backfill sessizce no-op oluyordu:** kaynak tablo `hr.employees` FORCE RLS olduğu için kiracı
   bağlamı kurulmadan `SELECT` boş dönüyordu → migration `set_config('app.tenant_id', …)` ile
   kiracı bazında çalışacak şekilde düzeltildi (0 → 9 atama).
2. **`ON CONFLICT DO NOTHING` kısmi benzersizlikte idempotans sağlamıyor:** PostgreSQL kısmi
   indeksi hedef belirtilmeden arbiter olarak kullanmaz → `ON CONFLICT (…) WHERE …` biçimine
   geçildi (tekrar çalıştırma testi eklendi).
3. **`rbac/0012` geri alma FK ihlaliyle düşüyordu:** `hr.role_permissions` FORCE RLS olduğu için
   kiracı bağlamı olmadan DELETE hiçbir satır görmüyordu → geri alma da kiracı bazında çalışıyor.
4. **`0018` guard fonksiyonu `acik uuid` bildirimi** tarih alanını okuyunca
   `InvalidTextRepresentation` veriyordu → `text` yapıldı.
5. **Dev veritabanında yetki sapması bulundu ve düzeltildi:** `demo` kiracısında `hr_manager`
   şablondan sapmıştı (33 izin; `tenant.manage`, `role.manage`, `user.manage`, `employee.delete`,
   `billing.manage`, `leave.balance.manage`, `document.delete` dahil). OD16 senkronu ile **30**'a
   indirildi; özel roller dokunulmadı.
6. **İki mevcut test yeni şemaya uyarlandı:** `test_m14_deleted_ve_pasif_calisan…` (terminated
   çalışan artık tarih + neden taşır) ve `test_m7_katalog_ile_donmus_kopya_ayrismaz`
   (P1-A deltası bilinçli delta olarak eklendi). Test yardımcısı `calisan(...)` ek alan kabul eder.

### ÖLÇÜM (canlı dev DB; migration öncesi → sonrası)

| Ölçüm | Önce | Sonra |
|---|---|---|
| Tablo (`hr`+`public`) | 33 | **37** |
| RLS + FORCE tablo | 19 | **23** |
| İzin kataloğu | 39 | **47** |
| Yeni tablo | 0 | **4** |
| `demo` rol izinleri | admin 33, hr_manager **33**, officer 21, dept_manager 16, employee 8 | admin **41**, hr_manager **30**, officer **23**, dept_manager **17**, employee 8 |
| `demo` atama / form alanı | — | **9** / **33** |
| Çalışan / kullanıcı | 9 / 6 | 9 / 6 (**veri kaybı yok**) |

## [0.36.0] — 2026-09-13 — P1 blocker kararları kapatıldı: People Lifecycle + Configurable Onboarding

**Kapsam:** **yalnız doküman** — kod, migration, DB değişikliği ve yeni uç **yok**
(`makemigrations --check` temiz; `git diff` yalnız `docs/`; DB yalnız **okundu**).
**Baseline:** `03871fd`, 473 test yeşil. **Sonuç:** P1 **`implementation-ready` ✅**.

### KAPATILAN KARARLAR (17)

- **P1 blocker (OD6–OD18 analizi):** **OD6** (silme→arşivleme; `employee.delete` = arşivleme,
  fiziksel silme P9, saklama 10 yıl), **OD7** (kiracı arşivler, silme platform onayıyla P9),
  **OD9** (devredilemez/devredilebilir izin listesi), **OD16** (özel rol sınırı 40 + katalog
  evrimi: sistem rolleri data migration ile, özel roller otomatik güncellenmez).
- **Doğrudan ilgili:** **OD29** (`leave.balance.manage` → `tenant_admin` + `hr_manager`),
  **OD30** (`employee.sensitive.write` **eklenir**; "yazma ⇒ okuma" kuralı), **OD31** (kan grubu
  K3/şifreli kalır, formda toplanır ama **zorunlu olamaz**).
- **Yol haritası önerilerinden:** **OD35** (custom alan v1'de **yok**), **OD36**
  (`employee.form.manage`), **OD37** (K3 zorunlu yapılamaz → 400), **OD38** (sürüm tablosu yok;
  audit + türetilmiş "eksik alan"), **OD41** (`notice_period` **türetilmiş etiket** +
  `notice_end_date`), **OD42** (`suspended` izin talebi açamaz + girişi engellenir), **OD43**
  (P1'de tek adım; iki adımlı onay P9).
- **Bu turda açılıp kapatılan yeni kararlar:** **OD58** (`hr.workplaces` ayrı entity; departmanla
  karışmaz), **OD59** (1 kullanıcı ↔ en fazla 1 aktif çalışan; erişim kesme: `inactive` + token
  iptali + `users.auth_version` ile oturum düşürme), **OD60** (iki katmanlı geçmiş:
  `employee_employment_terms` + `employee_assignments`; generic JSON history **reddedildi**),
  **OD61** (8 yeni izin kodu + adlandırma standardı + rol hedefleri).

### LATER / OBSOLETE

- **LATER (P1'i bloklamaz):** OD8, OD10 (MFA — ayrı güvenlik turu), OD12, OD13, OD14, OD15, OD17, OD18.
- **OBSOLETE:** **OD11** — platform ve kiracı rol uzayları yapısal olarak ayrık (OD3 kapalı).

### KESİNLEŞEN TASARIM (kod turuna girdi)

- **Veri modeli:** +4 tablo (`hr.workplaces`, `hr.employee_employment_terms`,
  `hr.employee_assignments`, `hr.form_field_definitions`); `hr.employees`
  (+`notice_end_date`, +`termination_reason_code`, +`termination_note`, +`user_id` kısmi
  benzersizlik), `hr.users` (+`auth_version`).
- **Durum makinesi:** 4 durum korunur; 7 geçiş tanımlı (2'si **yasak**), izinsiz geçiş **409**,
  aynı duruma yazma no-op.
- **Form politikası:** A (sistem-kilitli: `department`, `hire_date`, e-posta/parola, ad/soyad,
  `employee_no`) / B (yapılandırılabilir) / C (koşullu) seviyeleri; bilinmeyen alan kodu **400**;
  A seviyesi gizleme/opsiyonel yapma **400**; zorunluluk **sunucuda**.
- **İzin seti:** `employee.suspend`, `employee.terminate`, `employee.restore`,
  `employee.history.read`, `employee.form.manage`, `employee.sensitive.write`,
  `org.workplace.read`, `org.workplace.manage` → kiracı kodu 33→41, genel 39→47;
  rol hedefleri `tenant_admin` 41, `hr_manager` 30, `tenant_officer` 23, `dept_manager` 17.
- **P1 kapsamı dışı:** ücret (P6), belge kolonu (P3), bildirim gönderimi (P2), offboarding
  checklist + self-service (P9), custom alan (P9), MFA (ayrı tur).

### ÖLÇÜM (canlı DB, yalnız okuma)

- P1 kardinalite kısıtı için risk ölçümü: `demo` kiracısında **9 çalışan, 5'i kullanıcıya bağlı,
  çoklu bağlı kullanıcı = 0** → `UNIQUE(user_id) WHERE deleted_at IS NULL AND status <> 'archived'`
  kısıtı bugünkü veriyle uygulanabilir. (`askida` kiracısında çalışan yok.)
- RLS nedeniyle ölçümler `set_config('app.tenant_id', …)` ile kiracı bağlamı kurularak yapıldı
  (bağlamsız sorgu tüm satırları gizler — bu davranış doğru çalışıyor).

### DEĞİŞEN DOKÜMANLAR

`docs/AUTHORIZATION.md` (§16 karar kayıtları + §14 durum), `docs/HR_ROADMAP.md` (§28 kesin tasarım,
§22 P1 = implementation-ready, §26 kapatılan öneriler), `docs/DATABASE.md` (P1 tabloları),
`docs/ORGANIZATION.md` (§7 durum makinesi + §7.1 istihdam koşulları/işyeri/geçmiş),
`docs/SECURITY.md` (§14 P1 güvenlik kararları), `docs/ARCHITECTURE.md` (P1 modül sınırı),
`docs/FEATURES.md` (P1 durumu), `docs/CHANGELOG.md`.

## [0.35.2] — 2026-09-13 — Doküman düzeltmesi: model/tablo sayımı (kod değişmedi)

**Kapsam:** yine **yalnız doküman** (`docs/`). İkinci envanter ajanının (veri modeli + migration)
raporuyla yapılan çapraz kontrol, §1'deki model sayımının **yanlış etiketlendiğini** gösterdi.

### DÜZELTİLDİ

- **"43 model" → doğru ayrım:** 43 sayısı `apps/*/models.py` içindeki **tüm sınıf tanımlarıdır**.
  Django uygulama kaydına göre (`manage.py shell` → `apps.get_models()`) **kayıtlı somut proje
  modeli 21** (leave 7, organization 5, rbac 5, accounts 2, audit 1, tenancy 1); ayrıca **4 soyut
  taban** (`apps/common`: UUIDModel, TimeStampedModel, SoftDeleteModel + 1 — kayıtlı değil) ve
  **9 çerçeve modeli** (django_q 5, auth 2, contenttypes 1, sessions 1).
- **Tablo sayımı eklendi:** **28** benzersiz `db_table` adı; canlı DB'de `hr`+`public` şemasında
  **33 tablo** (migration'la oluşan dizin/M2M tabloları dahil). Önceki tek satırlık "43 model"
  ifadesi bu ikisini karıştırıyordu.
- **Migration dağılımı doğrulandı:** tenancy 9, accounts 5, rbac 11, organization 13, leave 4,
  platform 5, audit 2 → **49** (envanter ajanının uygulama bazlı sayımıyla birebir uyuştu).
- **0.35.0 girdisindeki** ölçüm satırı da (43 model / 38 izin) düzeltildi — aynı oturumda
  yazıldığı ve yayımlanmadığı için yerinde düzeltildi.

### DOĞRULANAN

- `manage.py shell` + canlı DB okuması (`pg_class`) ile üç bağımsız kaynak uyuştu.
- Kod/migration/DB değişmedi: `makemigrations --check --dry-run` → `No changes detected`;
  DB yalnız **okundu**.

## [0.35.1] — 2026-09-13 — Doküman düzeltmesi: ölçüm sayıları (kod değişmedi)

**Kapsam:** yine **yalnız doküman**. Bağımsız envanter ajanının raporu ile yapılan çapraz
kontrol, `docs/HR_ROADMAP.md` §1'deki üç ölçüm değerini düzeltmeyi gerektirdi. Kod, migration,
DB ve uç **değişmedi** (`makemigrations --check` temiz; `git diff --stat` yalnız `docs/` + `README.md`).

### DÜZELTİLDİ

- **İzin kodu sayısı 38 → 39.** Önceki sayım katalog dosyasındaki regex taramasına dayanıyordu;
  canlı DB doğrulaması `hr.permissions` = **39** (33 kiracı + 6 platform) gösterdi. Modül
  kırılımı ve `platform.*` listesi güncellendi.
- **Kuyruk durumu "kurulu değil" → "kurulu ve yapılandırılmış, kullanılmıyor".** `django-q2>=1.10`
  bağımlılıkta (`pyproject.toml:20`) ve `Q_CLUSTER` ayarlı (`config/settings.py:274-285`;
  `max_attempts` 3, `retry` 600 s, `save_limit` 500, `catch_up` False, **`sync: DEBUG`**);
  **hiçbir görev enqueue edilmiyor**. §0.1, §7.1 ve §24 buna göre düzeltildi; `sync: DEBUG`
  nedeniyle geliştirmede görevlerin senkron çalıştığı ve üretimde `qcluster` gerektiği not edildi.
- **Test ölçeği netleştirildi:** 19 test dosyası, **462** `def test_` fonksiyonu → pytest sonucu
  **473 test** (parametreli testler dahil). Önceki "20 test dosyası" ifadesi yanlıştı.
- **§1.3'e ölçülen altyapı tablosu eklendi:** 19/33 tabloda RLS açık (`pg_class.relrowsecurity`,
  canlı DB), 5 varsayılan kiracı rolü (+2 platform rolü), Django Admin yok (OD19), throttle
  oranları (`auth` 10/dk, `user` 1000/sa, `anon` 60/dk), dosya deposu/e-posta/i18n/yedekleme/para
  birimi boşlukları.
- **`README.md`:** "97 test yeşil" ifadesi **bayattı** → "473 test yeşil (2026-09-13 ölçümü)".

### DOĞRULANAN

- Ölçümler bağımsız bir envanter ajanı raporuyla çapraz kontrol edildi; uyuşmayan üç nokta
  (izin sayısı, kuyruk durumu, test dosyası sayısı) **koddan ve canlı DB'den** yeniden ölçülüp
  düzeltildi. Canlı DB yalnız **okundu** (`SELECT count(*)`); hiçbir yazma yapılmadı.
- `makemigrations --check --dry-run` → `No changes detected`; `scripts/run_tests.sh` → 473 passed.

## [0.35.0] — 2026-09-12 — Dokümantasyon: uçtan uca HR ürün mimarisi ve geliştirme yol haritası

**Kapsam:** **yalnız doküman.** Bu sürümde **hiçbir kod, migration, veritabanı değişikliği veya
yeni uç eklenmedi** (kanıt: `git diff --stat` → yalnız `docs/`; `makemigrations --check` temiz).
**Baseline:** commit `6b59ebd`, çalışma ağacı temiz, tam test suite **473 passed**,
`verify_fresh_install.sh` VERIFY_EXIT=0.

### UYGULANDI (doküman)

- **`docs/HR_ROADMAP.md` yeniden yazıldı ve genişletildi (1542 satır, 30 bölüm):** ürün mimarisi,
  modül tasarımları, fazlandırma ve açık karar önerileri için **tek source-of-truth**. Önceki
  taslak sürümün fazla mesai + dinamik form içeriği §4 ve §11 altında bütünleştirildi.
- **Ölçüm bölümü (kod kaynaklı):** 9 uygulama / **43 model sınıfı → 21 kayıtlı somut proje modeli** / 49 migration / **39 izin** (33 kiracı + 6 platform; 12 modül) /
  5 varsayılan kiracı rolü + 2 platform rolü / 11.896 satır uygulama kodu + 10.813 satır test / 473 test; her yetenek
  *tamamen uygulanmış · kısmen · yalnız tasarlanmış · başlanmamış* olarak sınıflandırıldı.
- **Zaten verilmiş kararların tekrarı önlendi:** django-q2 kuyruk (ARCHITECTURE §10), S3 nesne
  depolama (§9/K7), SMTP e-posta (§11), DB cache + DRF throttling (§6.1), DB tetikleyicili audit
  (§12), vardiya/devam M15 ve belge M16 şemaları referans alındı; bunlar **yeni karar olarak
  önerilmedi**.
- **Yeni modül tasarımları:** bildirim outbox (§7), belge yönetimi + yükleme güvenliği (§8),
  izin takvimi boşluğu (§9), devam/vardiya M15 genişletmesi (§10), fazla mesai (§11), ücret
  geçmişi (§12), bordro hazırlığı (§13), ülke kural motoru (§14), Türkiye hesaplamaları (§15),
  raporlama/Excel (§16), dashboard (§17), çalışan/yönetici self service (§18–§19).
- **HR görev matrisi (§20)** ve **fazlandırma P0–P9 (§22)**; faz sırası kod bağımlılıklarına göre
  düzeltildi (fazla mesai `PAY` karşılığı P6+P7'ye bağlandı).
- **OD analizi (§23):** OD6–OD18 için "hangi fazı bloklar / koddan önce kapanmalı mı / obsolete mi
  / fiilen çözülmüş mü" tablosu. **OD32–OD57** önerileri listelendi (AÇILMADI).
- **Doküman tutarsızlığı kaydı:** OD10 (MFA) kararı ARCHITECTURE §7'de verilmiş görünüyor ama
  AUTHORIZATION.md'de hâlâ AÇIK; belge modülüne dair SECURITY.md §9'daki ✅ işaretleri tasarımı
  gösteriyor, kod yok. İkisi de ilgili dosyalarda notlandı.
- **Diğer dokümanlara kısa referanslar:** `PROJECT.md` (doküman dizini), `FEATURES.md` (§13
  güncel sıra + ölçüm notu), `DATABASE.md` (B.3 plan notu), `AUTHORIZATION.md` (§14 OD notu),
  `ARCHITECTURE.md` (faz tablosu → roadmap), `SECURITY.md` (§9 tasarım/kod ayrımı).

### DOĞRULANAN

- `git diff --stat` **yalnız `docs/`** dosyalarını içerir; `apps/`, `tests/`, `scripts/`,
  `config/` altında değişiklik **yok** → kod/migration/DB değişmedi.
- `python3 manage.py makemigrations --check --dry-run` → **model değişikliği yok** ("No changes
  detected"), yani planlanan tablolar koda sızmadı.
- Yeni uç eklenmedi: `git diff --name-only` içinde `urls.py`/`views.py`/`api.py` **yok**.
- Veritabanı **değiştirilmedi**: migration çalıştırılmadı, veri yazılmadı (bu turda `migrate.sh`
  kullanılmadı).

### KALAN (doküman olarak da açık)

- OD32–OD57 **öneri** durumundadır; hiçbiri karara bağlanmadı.
- OD29–OD31 ve OD6–OD18 hâlâ açık; hangi fazı blokladıkları §23'te listelendi.
- Employee lifecycle'ın `notice_period` / `suspended` / `archived` geçişleri için güvenli servis
  **yok**; karar bekliyor (§3.3).

## [0.34.0] — 2026-09-12 — Test arayüzü: izin hakları, hakediş yenileme, zorunlu giriş hesabı, bakiye ekranı

**Baseline (son commit `9851cb5`):** çalışma ağacı temiz, tam test suite **460 passed**,
`verify_fresh_install.sh` VERIFY_EXIT=0. Kullanıcı bildirimi: *test arayüzünü gerçek kullanıcı
geri bildirimine göre iyileştir; mevcut akışları kullanılabilir hale getir; payroll / overtime
engine / documents / notifications modüllerini **henüz uygulama**.*

### UYGULANDI

- **Çalışan detayında ham izin listesi kaldırıldı:** `effective_permissions` artık API'den de
  arayüzden de dönmez (detayda Rol / Kapsam / Kapsam birimi / Verilme tarihi kalır).
- **İzin Hakları bölümü (çalışan detayı):** izin türü · yıllık hak · kullanılan · bekleyen ·
  kalan · **sonraki hakediş**; yetkili kullanıcı için *Politikadan hakediş tanımla* (idempotent
  `accrue_leave_entitlements`) ve *Bakiye düzelt* (`adjust_leave_balance`; **gerekçe zorunlu**,
  defter + denetim kaydı). Arayüz doğrudan bakiye yazmaz.
- **Yıllık hakediş yenilemesi:** yeni saf fonksiyon `next_accrual_date(hire_date, as_of)` —
  işe giriş yıl dönümü (ör. `12.09.2026` → sonraki hakediş `12.09.2027`; 29 Şubat → 28 Şubat).
  Aynı dönemde tekrar çalıştırma **yeni hak yazmaz** (idempotent; test ölçüyor). UI'da
  "Sonraki yıllık hakediş: gg.aa.yyyy" gösterilir.
- **Zorunlu giriş hesabı:** çalışan oluşturmada `email`, `password`, `first_name`, `last_name`,
  `department_id`, `hire_date` zorunlu; çalışan + kullanıcı **tek işlemde** yazılır (hata
  durumunda **yarım kayıt kalmaz** — iç `transaction.atomic()` savepoint). Parola hiçbir
  yanıtta/log'da görünmez.
- **Çalışan numarası** (önceki tur): sunucu üretir, birim taşımasında değişmez — korundu.
- **Çalışanlar ekranı alt bölümlere ayrıldı:** *Çalışan Ara / Liste* ve *Yeni Çalışan*
  (tek sayfada form + liste + detay yığılmıyor).
- **Detay çekmecesi bölümleri:** İş Bilgileri → İzin Hakları → Rol ve Yetkiler → Kişisel
  Bilgiler; aynı `Detay` tıklaması (Escape/Kapat) kapatır.
- **Onay ekranı "Onay beklenen" kolonu:** sıradaki onaylayıcı **adı + unvanı** (çok adımlıysa
  `1. … — Bekliyor / 2. … — Sonraki adım`); kaynak `approval_steps`, arayüz tahmin etmez.
- **Yaklaşan izin durumu:** bugün izinde → **yeşil «İzinde»**; 0 < gün ≤ 7 → **sarı «N gün
  sonra»**; daha ileri → nötr «N gün sonra». Renk tek bilgi kaynağı değil (metin de var).
- **Yeni "İzin Bakiyeleri" ekranı** (`/test-ui/api/balances`): çalışan · birim · izin türü ·
  hakediş · kullanılan · bekleyen · kalan · sonraki hakediş · işlem; filtreler (çalışan ara,
  birim, izin türü, **düşük/tükendi**) **sunucuda** uygulanır; düzenleme modalı yalnızca
  seçili/hak ekle/hak düş/politikadan hakediş + gerekçe ile çalışır (`leave.balance.manage`).
- **Panel operasyonel hâle geldi:** 4 özet kartı (toplam çalışan, bugün izinde, 7 gün içinde,
  bekleyen onay), altında bekleyen talepler / yaklaşan izinler (durum rozetleri) ve
  **bakiyesi kritik çalışanlar** (kalan ≤ 3 gün) geniş tablosu.
- **Rol izinleri kusuru düzeltildi:** onay kutusu artık yalnız *"sizde olmayan VE seçili
  olmayan"* izin için kilitli. Önceki kural (`duzenlenebilir && aktor_sahip`) aktörde olmayan
  ama rolde seçili izinlerin **kaldırılmasını da** engelliyordu. Kilit nedeni API'de açıkça
  döner (`ekleme_kilitli`, `kaldirma_kilitli`, `kilit_nedeni`, `can_edit_reason`).
- **Teknik jargon UI'dan temizlendi** (backend/kapsam açıklamaları, "Unvan ≠ rol", ham izin
  kodları); `Kapsam ve yetki` özeti de artık kod listesi basmıyor.

### PLANLANDI (kod yazılmadı) — `docs/HR_ROADMAP.md`

- **Fazla mesai (overtime) modülü:** `hr.overtime_rules` / `overtime_requests` / `overtime_entries`,
  PAY / TIME_OFF karşılığı, mevcut leave ledger'a güvenli aktarım, `overtime.*` izin planı,
  onay akışı; ücret hesabı **payroll hazır olmadan yazılmaz**.
- **Yapılandırılabilir çalışan kayıt/onboarding formu:** `hr.form_field_definitions` +
  `hr.form_field_visibility`, sistem-kilitli alanlar kapatılamaz, K2/K3 sınıflandırması korunur,
  `tenant_form.*` izin planı.
- Dokümanda **OD32–OD40 (öneri)** üretildi; bu turda **açılmadı** (karar gerektiğinde açılacak).

### Doğrulama

- `tests/test_testui.py`: **+13 test** (ham izin listesi yok, izin hakları görünür, hakediş
  idempotent, `next_accrual_date` kuralları, hesapsız kayıt reddi + atomiklik, alt sekmeler,
  rol izin ekle/kaldır + kilit kuralları, sıradaki onaylayıcı, üç yaklaşan-izin durumu, bakiye
  ekranı kapsam/filtre, düzeltme gerekçe+denetim, SELF ve departman kapsam sınırları).
  Tam suite: **460 → 473 passed**.
- Kapılar: `RUFF_EXIT=0`, `BANDIT_EXIT=0`, `CHECK_EXIT=0`, `MAKEMIG_EXIT=0`, `TEST_EXIT=0`.
- Canlı tarayıcı (gerçek `runserver`, %100 zoom): Panel özet kartları, İzin Bakiyeleri ekranı
  (filtre + düzenleme modalı: gerekçesiz reddedildi, gerekçeli uygulandı), Çalışanlar alt
  sekmeleri, zorunlu hesap alanları, çekmece bölümleri, Onaylar "Onay beklenen" zinciri
  (`Finans Müdürü — Yönetici — Bekliyor` / `İK Yöneticisi — Bekliyor`), yatay taşma yok.

### Notlar

- **Migration gerekmedi** (model değişikliği yok); `hr.employee_no_seq` (0013) değişmedi.
- Bu tur **payroll / overtime engine / documents / notifications** modüllerini **uygulamadı**.
- M15 (vardiya/devam) ve M16 (evrak) hâlâ sırada.

## [0.33.0] — 2026-09-12 — Test arayüzü UX turu: geniş yerleşim, detay çekmecesi, otomatik çalışan no

**Baseline (son commit `1a10e8c`):** çalışma ağacı temiz, tam test suite **450 passed**,
`verify_fresh_install.sh` **VERIFY_EXIT=0**. Kullanıcı bildirimi: *gerçek kullanıcı testinde
tespit edilen UX ve fonksiyon sorunlarını düzelt; yeni büyük HR modülü ekleme, mevcut
backend/RBAC/RLS/tenant isolation/leave/organization/employee/role-permission mimarisini koru.*

### Yerleşim ve tasarım standardı

- `base.html` içinde ortak yerleşim katmanı: `--page-max: 1400px`, `.page-container`,
  `.grid-2`, `.grid-3`, `.split`, `.form-grid`, `.data-table`, `.detail-panel`,
  `.action-bar`, `.badge/.teknik`, `.option-card`, `.rol-kart`, `.card.ic`.
  Ana içerik masaüstünde **1400 px**'e kadar genişler; `≤980px`'de bloklar alt alta
  düşer. **Yeni bağımlılık yok** (npm/Vite/Tailwind/React/Bootstrap eklenmedi).
- Sayfa başına yerleşim: Panel = 3 kart üst sıra + geniş tablolar; Departmanlar =
  ağaç/detay **split-view**; Çalışanlar = filtre + geniş tablo + sağ çekmece;
  Onaylar = geniş tablo; Yetki Yönetimi = rol listesi/permission **split-view**.

### Fonksiyonel düzeltmeler

- **Çalışan detayı** artık **sağ çekmece** (`#detail-panel`): satır altı açılan panel
  sayfa yerleşimini bozmuyor. Aynı çalışanın `Detay`ı tekrar tıklanınca **kapanıyor**;
  başka çalışanda önceki kapanıp yenisi açılıyor (tek çekmece); buton metni
  `Detay` ⇄ `Detayı kapat`; Escape/Kapat da kapatıyor.
- **Çalışan numarası sunucuda otomatik üretiliyor** (`EMP-000001`; `hr.employee_no_seq`,
  migration **0013**). Form numara istemiyor (salt-okunur bilgi alanı), istemci numara
  hesaplamıyor; `nextval` atomik olduğu için eşzamanlı kayıtlarda çakışma yok ve numara
  **birim değişince değişmiyor**. `public_id` ile karıştırılmıyor (OD2/OD25).
- **Çalışan listesi** kolonları: Çalışan (ad + e-posta), Çalışan no, Unvan, Birim,
  Yönetici, Rol (yetki), Kalan izin (backend bakiyesi), Durum, İşlem + üstte
  ad/no/birim/unvan/e-posta **filtresi** (`n / m kayıt`).
- **İzin bakiyesi görünümü:** Panel'de kapsam bilançosu (hak/kullanılan/bekleyen/kalan)
  backend'den geliyor; çalışan kaydı olmayan kullanıcı boş panel yerine kapsamındaki
  çalışanların bakiyesini görüyor, çalışan yalnız kendi bakiyesini görüyor.
- **İzin türü özellikleri** 2×2 kart düzeni (onay kutusu başlıkla hizalı, açıklama
  altında, eşit yükseklik); izin türü tablosu başlıkları kullanıcı dostu
  (Kod/Ad/Kategori/Ücretli/Bakiye/Belge/Onay/Durum/İşlem) ve tablo tam genişlik.
- **İzin Talebi** sekmesi doğrudan açıldığında çalışan listesi boş kalmıyor (liste
  sekme açılışında çekiliyor).
- **Unvan ≠ rol** ayrımı arayüzde açık: çalışan detayında ve Yetki Yönetimi'nde
  unvanın işi, rolün yetkiyi tanımladığı yazıyor.

### Sonuç ve doğrulama

- `tests/test_testui.py`: **+10 test** (otomatik çalışan no, numara çakışmaması, taşıma
  sonrası numaranın sabit kalması, çekmece toggle kuralları, liste kolonları, yerleşim
  sözleşmesi, izin türü özellikleri, yetki ekranı rol listesi, kalan izin kolonu,
  şablon yorumu sızması regresyonu). Tam suite: **450 → 460 passed** (EXIT=0).
- Kapılar: `RUFF_EXIT=0`, `BANDIT_EXIT=0`, `CHECK_EXIT=0`, `MAKEMIG_EXIT=0`,
  `TEST_EXIT=0`.
- Canlı tarayıcı (gerçek `runserver`, %100 zoom): giriş, Panel (3 kart + bakiye
  tablosu), Departmanlar split-view + taşıma modalı, Çalışanlar kolonları + filtre +
  çekmece aç/kapat/çoklu-tık/başka çalışan/Escape, numarasız çalışan oluşturma
  (`EMP-000004`), Yetki Yönetimi split-view + gruplar, İzin Talebi + 2×2 özellik
  kartları, çıkışın sunucu tarafında oturumu kapatması doğrulandı.

### Notlar / açık noktalar

- Eşzamanlı üretim doğrulaması: atomik `nextval` + ardışık üretim testi
  (`tests/test_testui.py`) ve `verify_fresh_install.sh` adım **6c-9**'da paralel HTTP
  oluşturma ölçümü (iki eşzamanlı istek → farklı numaralar).
- `hr.employee_no_seq` **kiracıdan bağımsızdır** (global sıra): kiracı içi benzersizlik
  bunun alt kümesidir; numara hassas veri değildir (OD31 kapsamı dışı).
- Bu tur **yeni HR modülü eklemedi**; M15 (vardiya/devam) ve M16 (evrak) hâlâ sırada.

### Değişen dosyalar (bu sürüm)

- `apps/testui/templates/testui/base.html` — yerleşim/tasarım standardı (genişlik,
  grid'ler, çekmece, tablo, rozet, kart içi bölüm, dar ekran kuralları)
- `apps/testui/templates/testui/app.html` — sayfa yerleşimleri, çalışan listesi +
  filtre + çekmece, numarasız kayıt formu, rol/izin ekranları, izin türü kartları
- `apps/organization/services.py` — `next_employee_no()` (sequence) +
  `create_employee(employee_no=None)`
- `apps/organization/migrations/0013_m12_employee_no_sequence.py` — **YENİ**
- `apps/testui/api.py` — çalışan oluşturmada numara istemciden alınmaz; rol atama
  özetine `scope_org_unit_name`
- `tests/test_testui.py` — +10 test
- `scripts/verify_fresh_install.sh` — adım 6c-9
- `docs/CHANGELOG.md`, `docs/DEVELOPMENT_RULES.md`, `docs/FEATURES.md`,
  `docs/API.md`, `docs/DATABASE.md`, `docs/ORGANIZATION.md`

## [0.32.0] — 2026-09-12 — Çalışan kaydı bütün bilgileri ister: **kan grubu** (K3, şifreli) dahil

**Baseline (son commit `911bb42`):** çalışma ağacı temiz, tam test suite **444 passed**.
Kullanıcı bildirimi: *"çalışanların kayıt esnasında bütün bilgilerini iste, kan grubu dahil"*.

### Veri modeli

- `hr.employee_personal_data.blood_type` eklendi (migration **0012**, eklemeli ve
  `nullable`): `EncryptedCharField` — **K3 (özel nitelikli: sağlık)** sayıldığı için
  **Fernet ile şifreli**. Kapalı küme (`BloodType`): `A+/A-/B+/B-/AB+/AB-/0+/0-`
  (Türkiye yazımı: `0` = O); boş değer "bilinmiyor"dur.
- Mevcut kolonlar, kısıtlar, RLS politikaları, yetkiler ve K3 şifreleme altyapısı
  **değişmedi**; uygulanmış migration'lar elle düzenlenmedi.

### Arayüz (kayıt formu)

- **Yeni çalışan** formunun "Kişisel veriler" bölümü artık **bütün alanları** sorar:
  ad, soyad, **kimlik no (TCKN/uyruk no)**, doğum tarihi, **kan grubu**, kişisel
  e-posta/telefon, adres, yakın kişi telefonu + yakınlık derecesi, vatandaşlık,
  oturma/çalışma izni + geçerlilik, banka, IBAN. Zorunlu olan yalnız **ad/soyad**
  olarak işaretlendi; kayıt **tek istekte** hem çalışanı hem kişisel veriyi yazar.
- **Kan grubu** açılır listesi sunucudan gelir (`/test-ui/api/lookups →
  blood_types`; tek kaynak model `BloodType`) — arayüzde sabit liste tutulmaz.
- Kişisel veri güncelleme formunda da kimlik no + kan grubu vardır; eskimiş
  "kimlik numarası bu formda düzenlenmez" notu gerçek davranışla değiştirildi.
- Çalışan detayında kan grubu **etiketle** gösterilir (`0 Rh+`) ve alanın K3/şifreli
  olduğu belirtilir.

### Yetki ve güvenlik

- Okuma **ve** yazma aynı kapıdan: `employee.sensitive.read` ∧ organizasyon kapsamı
  (OD5 §5.7). İzinsiz okuma/yazma `403`, kapsam dışı `404` — davranış değişmedi.
- Kan grubu **kapalı küme** doğrulaması servis katmanında (şifreli kolonda DB
  `CHECK` yazılamaz): geçersiz değer `400`. Bilinmeyen yeni bir K3 alanı yazılmaya
  çalışılırsa istek **fail-closed** reddedilir.
- Denetim kaydı: değer yazılmaz; K2 alanları `alanlar`, K3 alanları `k3_alanlar`
  anahtarıyla (ör. `["blood_type"]`) kaydedilir — sınıf farkı denetimde görünür.
- Şifreli alan okunamazsa (anahtar değişmiş/bozuk satır) yanıt 500'e düşmez; alan
  "bilinmiyor" (`null`) döner, yazma yolu fail-closed kalır.
- **OD31 açıldı:** kan grubunun sınıfı (K3 mi K2 mi) ve K3 alanların arayüzden
  toplanması kararı ürün/uyum sahibine bırakıldı; uygulanan seçenek (A) — K3/şifreli
  ve arayüzde K2 kapısıyla toplanır — olarak kaydedildi. **Güvenlik açığı değildir**,
  iki uçta da fail-closed'dur.

### Hata düzeltmeleri (canlı doğrulamada bulundu — hesabı bağlı olmayan çalışan)

- **`/test-ui/api/lookups` 500:** yönetici seçimi için üretilen etiket doğrudan
  `employee.user.first_name` okuyordu; `user_id` NULL olan bir çalışan oluşturulunca
  `AttributeError` fırlıyor ve **arayüzün tamamı** (panel, çalışanlar) boş kalıyordu.
  Etiket artık tek yerde ve güvenli sırayla üretilir (`_calisan_etiketi()`:
  ad-soyad → e-posta → çalışan no).
- **Çalışan detayı açılmıyordu:** `yetki_gorunur` true iken rol alanları
  (`revoked_assignments`, `permission_overrides`, `effective_permissions`,
  `assignable_roles`) yalnız kullanıcısı bağlı çalışanda dönüyordu; arayüz `.map`
  çağırıp çöküyordu. Bu alanlar artık **her yanıtta** boş liste olarak döner (şekil
  sözleşmesi, `is_self` ile aynı ilke) ve arayüz `|| []` ile savunmalıdır.
- İki hata da **canlı tarayıcı doğrulamasında** yakalandı (arayüzden hesapsız çalışan
  oluşturulunca panel boş kaldı); düzeltmeler 2 regresyon testiyle kilitlendi.

### Demo verisi

- Demo çalışanlara kan grubu yazıldı (`DEMO-001` `A+`, `DEMO-002` `0+`, `DEMO-003`
  `AB-`) — kurgusal kişiler; idempotenttir. Kimlik no/IBAN gibi gerçek hayatta
  anlamlı alanlar **bilinçli olarak boş** bırakılmaya devam eder.

### Doğrulama

- Tam test suite: **444 → 450 passed** (6 yeni regresyon testi: form bütün alanları
  sorar, kayıt tek istekte yazar, kan grubu **veritabanında şifreli** + denetimde
  yalnız alan adı, geçersiz değer `400` / yetkisiz `403` / boşaltma `null`,
  kullanıcısız çalışanda `/lookups` kırılmaz, kullanıcısız çalışanın detayı boş
  listelerle `200` döner).
- `verify_fresh_install.sh` yeni adım **6c-8** (sıfırdan kurulum): form alanları
  (`emp-kan`, `emp-kimlik`, `emp-iban`) sunucudan gelir; çalışan + bütün kişisel veri
  tek istekte oluşturulur; kan grubu kod + etiket olarak okunur; **ham kolon
  `gAAAAA…` (şifreli, düz metin değil)**; geçersiz değer `400`.
- Canlı tarayıcı: kayıt formunda kimlik no + kan grubu alanları görünür, kayıt
  sonrası detayda kan grubu etiketi okunur; `node --check` ile gömülü betik denetlendi.

### Kapsam dışı (bilinçli)

- Diğer K3 alanları (`health_notes`, `disability_status`, `biometric_ref`) hâlâ
  **arayüzden** toplanmaz/yazılmaz (özel nitelikli; operasyonel gereksinim yok).
- K3 için ayrı izin kodu ve "onaylı süreli erişim" akışı bu turda **eklenmedi**
  (OD30/OD31 açık; katalog değişikliği ayrı tur ister).
- Kan grubu üzerinden **arama/filtreleme yok** (şifreli kolon; tasarım gereği).

## [0.31.0] — 2026-09-12 — Test arayüzü: görsel iyileştirme turu (tasarım belirteçleri, tablolar, boş durumlar, iki hata düzeltmesi)

**Baseline (son commit `0fb2c49`):** çalışma ağacı temiz, tam test suite **444 passed**.
Kapsam: yalnızca geliştirme amaçlı manuel test arayüzü (`/test-ui/`) — **gömülü
CSS/JS**, yeni bağımlılık veya frontend zinciri **yok**. Backend, veri modeli,
yetki kararları ve API sözleşmesi **değişmedi**.

Kullanıcı bildirimi: *"persopyro projesine görsel olarak iyileştirmeler yap"*.

### Tasarım dili (base.html)

- Renk/ölçü/gölge **tasarım belirteçleri** (`:root`): tek yerden yönetilen palet,
  yarıçap ölçeği, üç gölge seviyesi, odak halkası, monospace değişkeni.
- Tipografi: başlık hiyerarşisi (`h1`–`h4`), `h3` = büyük harf etiket, `h4` = alt
  çizgili bölüm başlığı; gövde satır yüksekliği ve harf aralığı düzeltildi.
- Üst bar: yarı saydam + bulanık zemin, marka için kare logo bloğu (`P`), yumuşak
  alt gölge; sekmeler **dolu mavi** seçili durum ve dar ekranda yatay kaydırma.
- Formlar: etiketler yarı kalın ve koyu; alanlar `grid auto-fit` ile 2-4 sütun;
  odakta mavi çerçeve + halka; `.form-alt` ile düğme satırı ayraçlı.
- Düğmeler: `primary` (dolu mavi), `danger` (kırmızı çerçeve), `ghost`, `kucuk`;
  hover/active/focus-visible durumları ve gölge geçişleri.
- Tablolar: açık gri büyük harf başlık, satır hover vurgusu, `.sayi` ile hizalı
  sayılar, `.tablo-kaydir` ile **kart içinde yatay kaydırma** (geniş izin türü ve
  çalışan tabloları sayfayı taşırmıyor).
- Durum bileşenleri: `pill` renkleri (onaylandı/bekliyor/reddedildi), sol kenarlı
  renkli mesaj şeridi, modal giriş animasyonu, `.bos` kesikli **boş durum** kutusu,
  `.liste-satir` onay satırı düzeni, `.ipucu` bilgi şeridi, `.kv` etiket/değer
  tablosu, `.rozet` sayaç etiketi; `prefers-reduced-motion` ve dar ekran kuralları.
- Giriş ekranı (`login.html` çıktısı) yeni kart gölgesi/yarıçapıyla aynı dilde.

### Arayüz düzeltmeleri (test arayüzü JS)

- **Kapsam açıklaması düzeltildi:** çalışan listesi `meta.self_only` alanını
  okuyor; önceki sürümde kaldırılmış `meta.directory_only` adı okunduğu için
  SELF kapsamlı kullanıcıya **yanlış** ("şirket dizini") açıklama görünüyordu.
- **Yinelenen IBAN satırı kaldırıldı:** çalışan detayındaki kişisel veri tablosunda
  IBAN iki kez basılıyordu; artık bir kez, kimlik numarasıyla birlikte.
- **Ölü kod temizlendi:** kaldırılan "şirket dizini" görünümünün (`c.dizin`,
  `c.public_id`) kullanılmayan dalı ve `directory_only` metni silindi.
- Kart başlıkları sayaç rozeti + sağ tarafta kapsam notu; liste boşsa kesikli boş
  durum kutusu (yalnızca kayıt yoksa görünür).
- Satır içi `style="..."` kullanımları sınıflara taşındı (`liste-satir`,
  `form-alt`, `ipucu`); geniş tablolar `tablo-kaydir` ile sarıldı.

### Doğrulama

- Tam test suite: **444 passed** (bu turda yeni davranış eklenmedi; mevcut
  regresyonlar şablon değişikliğinden etkilenmedi).
- `node --check` ile gömülü arayüz betiği denetlendi. **Bu denetim bir hata
  yakaladı:** çalışan listesindeki yeni koşullu blokta kapanmayan şablon dizesi
  (template literal) tüm betiğin çalışmamasına ve panelin boş kalmasına yol açıyordu;
  düzeltildi ve `verify_fresh_install.sh` adım **6c-7** ile kalıcı denetime bağlandı.
- Canlı tarayıcı (gerçek sunucu, `demo@persopyro.test`): Panel, Departmanlar,
  Çalışanlar (liste + detay + kişisel veriler), İzin Talebi sekmeleri tarandı;
  konsol hatası yok, tasarım belirteçleri ve boş durum kutusu görünür.

### Not (geliştirme ortamı)

- Django şablon yükleyicisi bu sürümde her zaman `cached.Loader` ile sarmalanır;
  `runserver --noreload` altında şablon değişiklikleri **sunucu yeniden
  başlatılmadan** görünmez (bu turda panelin "eski" görünmesinin nedeni buydu).
  Kural `docs/DEVELOPMENT_RULES.md` "DİKKAT — şablon önbelleği" notuna eklendi.

### Kapsam dışı (bilinçli)

- Üretim kullanıcı arayüzü, tema/tasarım sistemi, JS çerçevesi ve yeni npm
  bağımlılığı yok; üretim varsayılanında `/test-ui/*` yine **404**.
- Çalışan kayıt formunun alanları bu turda **değiştirilmedi** (sonraki turda
  kan grubu dahil tüm alanlar eklendi — bkz. 0.32.0).

## [0.30.0] — 2026-09-11 — Test arayüzü: görünürlük ve izin akışı düzeltmeleri (kendi talebini onaylama, izin durumu noktası, banka bilgileri, tarih biçimi)

**Baseline (son commit `9c81671`):** çalışma ağacı temiz, tam test suite **435 passed**.

Kullanıcı bildirimi: *"yetkisi olmayan çalışanlar yeni çalışan/pozisyon/departman ekle
gibi yerleri görmesin — 'yetkiniz yok' yazması yerine hiç görmemeli"*, *"izin bakiyesi 0
olsa bile izin talebi oluşturulabilsin"*, *"onaylayan kişi talep sahibinin mevcut izin
bakiyesini onaylama ekranında görebilmeli"*, *"yetki yönetimindeki 'Oturumum' kısmı boş
yere görünüyor"*, *"yeni ünvan ekle ile yeni çalışan ekle iç içe olmamalı"*, *"IBAN
görünmüyor; banka bilgileri olarak IBAN ve Banka sorulmalı"*, *"tüm tarihler gg.aa.yyyy
biçiminde"*, *"izin talep eden çalışan kendi iznini onaylamasın ama iptal/düzenleyebilsin"*,
*"yalnız yetki verilenler izin tanımlaması yapabilsin"*, *"yaklaşan izinlerde tarihi geçmiş
gösterilmesin"*, *"izinde olanların yanında yeşil nokta, 7 günden az kalanlarda sarı"*,
*"normal çalışan yalnız kendini görsün; yönetici kendine bağlı çalışanları görsün"*,
*"her çalışan kaydında bağlı olduğu yönetici seçilebilmeli"*.

### Görünürlük (yetkisi olmayan hiç görmesin)

- Departmanlar, çalışanlar, pozisyon, izin türü ve politika formları artık yetkisiz
  kullanıcıda **hiç render edilmez** (önceden "Yetkiniz yok: …" notu veya boş kart
  kabuğu görünüyordu). Backend davranışı değişmedi: yetkisiz istek yine `403`.
- **Yetki Yönetimi** sekmesi zaten yalnız `role.manage` sahibinde görünür; sekmedeki
  boş "Oturumum" kartı kaldırıldı.

### İzin akışı

- **Bakiye yetersiz (0) iken talep açılabilir:** anahtar politika bayrağıdır
  (`allow_negative_balance`). Demo/seed politikası açık; `leave.type.manage` sahibi
  İzin Talebi sekmesindeki **İzin politikası** kartından açıp kapatabilir. Kapalıyken
  aşırı talep **409** + "Yetersiz izin bakiyesi" mesajı verir (fail-closed).
- **Onaylayan, talep sahibinin güncel bakiyesini görür:** onay kuyruğu (panel kartı ve
  Onaylar sekmesi) satırlarında `talep_sahibi_bakiye` döner (hak/kullanılan/bekleyen/kalan).
- **Kendi talebini onaylama yasağı:** servis zaten `SEPARATE_ACTOR` ile reddediyordu
  (`can_approve_step` + adım sahibi kontrolü → `403`); arayüz artık kendi talebinde
  Onayla/Reddet yerine **Düzenle** ve **İptal** gösteriyor.
- **Düzenleme akışı:** "Düzenle" talebin tarih/tür/gerekçesini forma taşır; kaydetme
  eski talebi **iptal** edip yenisini onaya sunar (onay adımları ve rezervasyon yeniden
  kurulur; geçmiş ve denetim korunur). Arayüz iki adımı tek düğmede yapar ve sonucu
  açıkça söyler.
- **İzin durumu noktası:** çalışan listesinde bugün onaylı izin aralığında olan kayıtta
  **yeşil**, bitişe **7 günden az** kaldıysa **sarı** nokta; örtüşen izinlerde en yakın
  bitiş belirleyicidir. Veri onaylı taleplerden gelir (kişisel veri içermez).
- **Yaklaşan izinler:** aralığı tamamen geçmiş izinler listelenmez (`end_date >= bugün`);
  devam eden izin görünür.

### Çalışan yönetimi

- **Yalnız kendi kaydı:** normal çalışan (`SELF` kapsamı) çalışan listesinde artık
  YALNIZ kendi kaydını görür; başka çalışanların kaydı ve numarası dönmez, kapsam dışı
  çalışan detayı **404**. (Bu, önceki "PII'siz şirket dizini" kararını yürürlükten
  kaldırır — kullanıcı bildirimi: "diğer çalışanları ve nolarını görmesine gerek yok".)
  Organizasyon şeması (birimler) görünür kalır.
- **Yönetici seçimi:** yeni çalışan formunda **Bağlı olduğu yönetici** seçilir;
  `employee.update` + kapsam doğrulanır ve atama yazılmadan **önce** yapılır
  (`set_primary_manager`, denetimli). Onay akışının ilk adımı bu ilişkiye göre kurulur.

### Kişisel veriler

- **Banka bilgileri:** `hr.employee_personal_data` tablosuna `bank_name` kolonu eklendi
  (migration **0011**, eklemeli). Form ve detay artık **Banka** + **IBAN** alanlarını
  birlikte gösterir/sorar; IBAN daha önce kaydedilse de arayüzde gösterilmiyordu.

### Tarih biçimi

- Tüm tarih gösterimleri **gg.aa.yyyy** (`11.09.2026`): biçimlendirme tek bir `tarih()`
  yardımcısında; ISO değerler saklanır, biçim yalnız sunumda değişir. `html lang="tr"`
  zaten ayarlı olduğundan tarih girişleri de TR biçiminde görünür.

### Doğrulama ve yeni denetim adımı

- 9 yeni regresyon testi (75 toplam): politika ile eksi bakiye talebi, politika yetkisi,
  izin durumu işaretleri (yeşil/sarı), onay kuyruğunda bakiye + kendi talebi, kendi
  talebini onaylayamama, yaklaşan izin süzgeci, yönetici seçimi, banka bilgileri.
- `verify_fresh_install.sh` **6c-7**: gömülü arayüz JS'i `node --check` ile denetlenir
  (bozuk şablon dizesi panelin sessizce boş kalmasına yol açıyordu; node yoksa atlanır).
- Canlı tarayıcı: formlar yetkisizde hiç görünmüyor, çalışan yalnız kendi kaydını görüyor,
  kendi talebinde Düzenle/İptal var, düzenleme gerçekten iptal+yeni talep üretiyor,
  onay satırında bakiye görünüyor, tarihler gg.aa.yyyy, sarı/yeşil noktalar çalışıyor.

### Kapsam dışı (bilinçli)

- Talep düzenleme, ayrı bir "güncelle" servisi yerine **iptal + yeni talep** olarak
  uygulandı (onay adımları/rezervasyon tutarlılığı ve denetim izi için).
- Rol silme/pasifleştirme yok; impersonation yok; hafta tatili/resmî tatil gün hesabı M15.

## [0.29.0] — 2026-09-11 — Test arayüzü: yetki yönetimi kapısı, hesap açma, kişisel veriler, rol oluşturma

**Baseline (son commit `f1bd7cf`):** çalışma ağacı temiz, tam test suite **428 passed**.

Kullanıcı bildirimi: *"hangi kullanıcı olursa olsun kendi panelinde kendi bilgilerini
görmesi gerekir (ad soyad, unvan, birim, kayıtlı e-posta, şirket, izin bakiyesi)"*,
*"çalışan eklerken hesap bağlama seçeneği olmasın; e-posta ve şifreyi biz verelim,
şifre oluşturucu olsun"*, *"izin türü ekleme ekranı çok karmaşık"*, *"yetki yönetimi
bölümü yalnızca yetki açıp kapatabilenlerde görünsün, yetkiler daha açıklayıcı olsun"*,
*"rol oluşturma ve düzenleme seçeneği olmalı"*, *"yeni çalışan girişinde kişisel veriler
sorulsun (ad, soyad, e-posta, adres, yakın telefonu ve yakınlık derecesi, vatandaşlık —
Türk değilse oturma/çalışma izni—, doğum tarihi); bu verileri yalnızca şirket yöneticisi
ve rol ile yetkilendirdiği kişiler görebilmeli"*.

### Şema (ilk kez bu turda migration)

- `hr.employee_personal_data` tablosuna **5 K2 kolonu** eklendi
  (`apps/organization/migrations/0010_m12_personal_data_k2_genisletme.py`):
  `emergency_contact_phone`, `emergency_contact_relation`, `citizenship`,
  `work_permit_type`, `work_permit_valid_until` (nullable).
  Eklemeli (additive); mevcut kolon ve kısıtlar değişmedi, K3 şifreli kolonlar sabit.
  Şifreleme **gerekmez** (K2 — K3 değil). Canlı geliştirme DB'sine `scripts/migrate.sh`
  ile uygulandı.
- Vatandaşlık kuralı: `citizenship` **TR değilse** oturma/çalışma izni türü **zorunlu**;
  kural servis/UI katmanında uygulanır (DB `CHECK` bilinçli olarak eklenmedi — bilgi
  sonradan tamamlanabilir, kayıt bloke edilmez). Gerekçe ölçümle sabitlendi.

### Panel — "Bilgilerim"

- Her kullanıcı (rol/izin farkı olmadan) kendi panelinde **Ad Soyad, Kayıtlı e-posta,
  Şirket, Unvan, Birim, Çalışan no, Yıllık izin (hak/kullanılan/bekleyen/kalan), Roller**
  satırlarını görür. Çalışan kaydı yoksa alan boş, bakiye yerine
  "Bu kullanıcıya bağlı çalışan kaydı yok." yazılır. Veriler backend'den gelir.

### Çalışan ekleme — hesap açma ve kişisel veriler

- **Kişisel veri boşlukları:** `employee.sensitive.read` izni + kapsamı olan kullanıcı
  yeni çalışan formunda kişisel veri alanlarını görür; izni olmayan kullanıcı forma
  bunları **hiç görmez** (backend de yazmayı reddeder). Kişisel veri **okuma** ucu
  `GET /test-ui/api/employees/<uuid>/personal`; izin yoksa **403**, kapsam dışıysa **404**.
- **Hesap açma:** "Hesap bağlama" listesi kaldırıldı. Yönetici `user.manage` izniyle
  e-posta + **parola** verir; parola için **"Parola üret"** düğmesi (15 karakter,
  tarayıcıda üretilir, sunucu parolayı **geri döndürmez**). Zayıf parola Django
  doğrulayıcılarıyla **400**; e-posta çakışması **400**. Hesap açma denetim kaydı
  `user.create` yazar ve **yalnızca e-postayı** içerir (parola asla — regresyon testi).
- Denetim: kişisel veri yazımı `employee.personal_data.create|update` olarak **alan
  adlarını** yazar, değerleri yazmaz (PII denetim kaydına düşmez — regresyon testi).

### İzin türleri — sadeleştirme

- Form **hazır şablonlar** kazandı (Yıllık izin, Hastalık/rapor, Ücretsiz izin, Doğum
  izni) → kod/ad/kategori ve dört özellik (ücretli, bakiye kullanır, onay, belge)
  otomatik dolar; alanlar açıklama metinleriyle ayrıldı.

### Yetki Yönetimi — görünürlük, açıklamalar, rol oluşturma

- Sekme artık yalnızca **`role.manage`** sahibinde görünür (`/api/me` → `menu.roles_manage`);
  çalışan ve departman yöneticisi sekneyi hiç görmez (ölçüm: `(GİZLİ)`).
- İzinler modül başlıkları + **modül açıklaması** ile listelenir; her izin Türkçe adıyla
  ("Devam kaydı düzeltme"), kod yalnızca detayda. Kapsam tipleri Türkçe etiketli.
- **Yeni servisler:** `create_custom_role` + `update_role_meta`
  (`apps/rbac/services/roles.py`). Kurallar: yalnız `role.manage` ∧ aynı kiracı;
  SYS/platform kapsamı **reddedilir**; varsayılan/sistem rol kodu ve var olan kod
  **400**; kendi rolünü düzenleme yasağı; **etkin ataması olan rolde kapsam değişikliği
  400** (atamalar kapsamsız kalmasın); her değişiklik `role.create`/`role.update`
  denetim kaydı yazar. Uçlar: `POST /test-ui/api/roles/create`,
  `POST /test-ui/api/roles/<uuid>/update`.

### Doğrulama özeti

- İzin/servis kuralları hedef testlerle; 7 yeni regresyon testi
  (`tests/test_testui.py`: panel kartı, hesap açma + gerçek giriş, zayıf parola,
  kapsamsız PII 403/404, PII zorunlu alan, rol oluşturma/yetkisiz, kapsam değişikliği
  kilidi, izin türü şablonları).
- `verify_fresh_install.sh` **6c-3d** adımı: yönetici kişisel veriyi görür, departman
  yöneticisi **403**, yönetici rol oluşturur **201**, departman yöneticisi **403**,
  çalışanın menüsünde `roles_manage=false` + `pii_gorunur=false`.
- Canlı tarayıcı (gerçek `runserver`): panel "Bilgilerim" kartı, 3 bölümlü çalışan
  formu, "Parola üret" (15 karakter), kişisel veri bölümü + 11 alanlı form, izin türü
  şablonları, **gerçek rol oluşturma** ("Vardiya Sorumlusu" göründü), çalışan görünümünde
  Yetki Yönetimi sekmesi gizli.

### Açık karar

- **OD30 AÇILDI:** kişisel veri **yazma** için ayrı izin kodu yok; arayüz okuma iznini
  (`employee.sensitive.read`) yazma için de kullanıyor (katalog sabit tutuldu). Seçenekler
  `docs/AUTHORIZATION.md` OD30.

### Kapsam dışı (bilinçli)

- Rol **silme/pasifleştirme** yok (istenmedi). Rol izin (override) yazma yok — karar yok.
- Impersonation yok; platform rolleri kiracı arayüzünden yönetilmez.
- Hafta tatili/resmî tatil gün hesabı **M15** (M14'ten devralınan kısıt).

## [0.28.0] — 2026-09-11 — Test arayüzü: yetki sınırları sıkılaştırıldı (çalışan yetki düzenleyemez), PII'siz şirket dizini, yetki ekranı sadeleştirildi

**Baseline (son commit `60b4500`):** çalışma ağacı temiz, tam test suite **424 passed**.

Kullanıcı bildirimi: *"çalışan statüsündeki bir kişi kendisi dahil herkesin yetkisini
düzenleyebiliyor"*, *"çalışan diğer kişileri ve organizasyon şemasını görsün ama
bakiye gibi detayları görmesin"*, *"yetki ekranı çok karışık"*.

### Bulgu ve düzeltme (yetki sınırları)

- **Backend zaten reddediyordu** (`update_role_permissions` guard'ları; çalışanın
  POST'u **403**), ancak arayüz **rol kataloğunu ve izin kutularını herkese**
  gösteriyordu → kullanıcı düzenleyebildiğini sanıyordu. İki katmanlı düzeltme:
  1. **`role.read` kapısı:** `/test-ui/api/roles` ve `/api/roles/<id>/permissions`
     artık `role.read` ister → `employee` (ve `dept_manager`) **403** alır; rol
     listesini dahi göremez. `tenant_officer` listeyi görür, **düzenleyemez**.
  2. **Arayüz:** yetkisi olmayanda düzenleyici **salt okunur** (tüm kutular ve
     "Tümünü seç" kapalı, açık uyarı bandı), düğme etiketi "Yetkileri görüntüle".
- Kapsam **SELF** olan kullanıcı için çalışan listesi artık **şirket dizini**: çalışan
  no + genel kullanıcı kimliği + unvan + birim + durum; **ad/e-posta yok, izin/bakiye
  yok** (docs/ORGANIZATION.md §5 "kişisel veri içermez" ilkesi). Kullanıcı **kendi**
  kaydını ve bakiyesini tam görür. Kapsam dışı çalışanın detayı ve bakiye ucu **404**.
- Onay kuyruğu, yaklaşan ve karara bağlanan izinler kapsam/yetki süzgecinden geçmeye
  devam eder (çalışan yalnız kendi kayıtlarını görür — regresyon testiyle sabitlendi).

### Yetki ekranı (kullanılabilirlik)

- Rol tablosu: rol adı (kod altında), kapsam, izin sayısı, atama sayısı ve tek düğme
  ("Yetkileri düzenle" / "Yetkileri görüntüle").
- Düzenleyici: **modül modül katlanır gruplar** ("İzin 3/8 seçili" sayaçlarıyla),
  **izin arama** kutusu, **Tümünü seç / Hiçbirini seçme / Değişiklikleri geri al**,
  kaydetmeden önce **"N izin eklenecek, M izin kaldırılacak"** özeti, gerekçe alanı ve
  **Kaydet**.

### Ölçüm

| Ölçüm | Önce | Sonrası |
|---|---|---|
| Test sayısı | 424 | **428** (+4: yetki sınırları, dizin, çalışan izin görünürlüğü) |
| Yeni migration / bağımlılık | — | **0** |
| Yinelenen doğrulama | — | `verify_fresh_install.sh` **6c-5**: çalışan → `/api/roles` 403, dizin PII'siz |
| Güvenlik modeli | — | **değişmedi** (izin kataloğu/RLS aynı; yalnız test arayüzü kapıları sıkılaştı) |

### Doğrulama

- `tests/test_testui.py` **59 test**; tam suite **428 passed**.
- Gerçek tarayıcı: çalışan → Yetki Yönetimi'nde rol **tablosu boş** (403), düzenleme
  düğmesi yok; Çalışanlar sekmesinde **dizin** satırları (public_id ile, "gizli"
  bakiye) + kendi kaydı tam; demo yöneticide düzenleyici katlanır gruplar, arama,
  sayaçlar ve değişiklik özetiyle görünür.
- `verify_fresh_install.sh` 6c-5 adımı ve `ruff`/`bandit`/`check`/`makemigrations --check`.

## [0.27.0] — 2026-09-11 — Test arayüzü: departman düzenleme, rol izin düzenleme (yetki yönetimi), yaklaşan izinler, sabit üst bar

**Baseline (son commit `5b2e844`):** çalışma ağacı temiz, tam test suite **416 passed**.

Kapsam dışı (bu turda **dokunulmadı**): izin kataloğu (kodlar değişmedi), RLS,
kimlik doğrulama, üretim API sözleşmesi (`/api/v1/*`), kullanıcı hesabı oluşturma,
platform yetkilendirmesi (OD3 — kiracı arayüzünden yönetilmez).

### Eklenen

- **Rol izin düzenleme (yeni servis `update_role_permissions`)** — şirket yöneticisi
  bir rolün izin kümesini manuel düzenler. Guard'lar: yalnız `role.manage`; kendi
  rolü düzenlenemez (self-elevation/demotion yasağı); **sahip olunmayan izin
  dağıtılamaz** (kaldırma serbest); **platform izinleri (OD3) listelenmez, verilemez**;
  bilinmeyen kod ve **boş küme** reddedilir (boş rol sessiz kilitlenme yaratır);
  her değişiklik `audit_logs`'a yazılır (`role.permissions.update`). Değişiklik
  anında geçerlidir (izinler her istekte rol tablosundan çözülür).
- **Yetki Yönetimi sekmesi** — rol tablosu (kapsam tipi, izin sayısı, atama sayısı,
  atanabilirlik) + rol seçilince **modül gruplu, Türkçe etiketli** izin kutuları,
  "Tümünü seç" ve modül başlığı seçimi, gerekçe alanı, kaydet. Kendi rolünüzde ve
  yetkisiz kullanıcıda düzenleyici salt okunur; sahip olmadığınız izinler işaretsiz
  gösterilir. Rol **atama** yeri: Çalışanlar → Detay → "Rol ve Yetkiler".
- **Departman düzenleme** — "Departmanlar" ağacında **Düzenle**: ad ve kod değişir
  (`update_department` servisi, denetim `org.unit.update`); boş ad/kod `400`; taşıma
  ayrı işlem olarak kalır.
- **Panelde "Yaklaşan izinler"** — kapsamınızdaki çalışanların **onaylı ve
  gelecekteki** izinleri (çalışan, birim, tür, başlangıç, bitiş, gün). Bekleyen ve
  kapsam dışı talepler listelenmez.
- **Sabit üst bar** — başlık (Persopyro / kullanıcı / Çıkış) ve sekmeler tek bir
  `sticky` üst bar içinde; sayfa kaydırılırken görünür kalır.

### Ölçüm

| Ölçüm | Önce | Sonrası |
|---|---|---|
| Test sayısı | 416 | **424** (+8: departman düzenleme, rol izin guard'ları, yaklaşan izinler, üst bar) |
| Yeni migration | — | **0** |
| Yeni bağımlılık | — | **0** |
| Yeni uç (yalnız `/test-ui/api/*`) | 20 | **22** |
| Yeni servis | — | `update_role_permissions` (rbac) |
| Güvenlik modeli | — | **değişmedi** (izin kataloğu, RLS, kapsam mantığı aynen) |

### Doğrulama

- `tests/test_testui.py` **55 test**; tam suite **424 passed**.
- Gerçek tarayıcı (Chrome/CDP): sabit üst bar (`position: sticky`, sekmeler üst bar
  içinde); panelde "Yaklaşan izinler"; "Departmanlar → Düzenle" ile gerçek yeniden
  adlandırma; "Yetki Yönetimi"nde rol tablosu (izin/atama sayıları), dept_manager
  düzenleyicisi (16 izin, platform izni yok, "Tümünü seç"), bir izni kaldırıp
  kaydetme (sayaç 16→15, sunucuda doğrulandı) ve geri yükleme (16).
- `verify_fresh_install.sh`: departman yeniden adlandırma, rol izin ekranı,
  platform izni `400`, kendi rolü `403`, aynı kümenin yeniden yazılması `200`.
- `ruff`, `bandit`, `manage.py check`, `makemigrations --check`: temiz.

## [0.26.0] — 2026-09-11 — Test arayüzü: çalışan/unvan, izin türü yönetimi, panel karar görünümü

**Baseline (son commit `71399fe`):** çalışma ağacı temiz, tam test suite **408 passed**.

Kullanıcının bildirdiği üç eksik. Kapsam dışı (bu turda **dokunulmadı**):
RBAC/RLS/izin kataloğu, üretim API sözleşmesi (`/api/v1/*`), kimlik doğrulama,
kullanıcı **hesabı oluşturma** (test arayüzünden hesap açılmaz; mevcut hesap seçilir).

### 1) "Bağlanacak kullanıcı bulunamadı" + unvan tanımlanamıyor

- **Kök neden:** çalışan formunda kullanıcı **serbest metin** olarak isteniyordu;
  yazılan e-posta kayıtlı değilse istek 400 dönüyordu (liste satırını kopyalamak da
  bu hatayı üretiyordu).
- Artık kullanıcı **listeden seçilir** (`lookups.users`): her hesabın bağlı çalışanı
  (varsa) etikette gösterilir; seçim yapılmazsa hesap bağlanmaz. Yabancı kiracıya ait
  veya kayıtsız hesap **400** ile ve anlaşılır mesajla reddedilir.
- **Unvan (pozisyon) tanımlama eklendi:** `org.position.manage` sahibi çalışan
  formundan yeni unvan ekler; eklendikten sonra liste yenilenir ve yeni unvan formda
  seçili gelir (`POST /test-ui/api/positions`).

### 2) Panelde onaylı izinler görünmüyordu

- Panelde **"Karara bağlanan izinler"** kartı: durum sayıları (onay bekleyen /
  onaylanan / reddedilen / iptal) ve son karara bağlanan talepler (çalışan, birim,
  tür, tarih, gün, durum, karar tarihi).
- Kapsam süzgeci aynı kuraldan geçer: çalışan yalnız kendi kayıtlarını, yönetici alt
  ağacındakileri görür; kapsam dışı çalışanın kararı listede çıkmaz.

### 3) İzin türü ekleme yoktu

- **İzin türleri** kartı: tüm türler (pasifler dahil) kod/kategori/ücretli/bakiye/
  belge/onay/durum kolonlarıyla; şirket yöneticisi (`leave.type.manage`)
  **yeni tür** ekler (sistem kodları rezerve → 400) ve mevcut türü **pasifleştirir**
  (silinmez; OD21 gereği mevcut talepler korunur). Eklenen tür anında talep
  formundaki listede görünür.
- Yeni uçlar: `POST /test-ui/api/positions`, `GET|POST /test-ui/api/leave-types`,
  `POST /test-ui/api/leave-types/<uuid>/deactivate`.

### Ölçüm

| Ölçüm | Önce | Sonrası |
|---|---|---|
| Test sayısı | 408 | **416** (+8: kullanıcı seçimi/unvan, izin türü CRUD, panel karar listesi) |
| Yeni migration | — | **0** |
| Yeni bağımlılık | — | **0** |
| Yeni uç (yalnız `/test-ui/api/*`) | 17 | **20** |
| Güvenlik modeli | — | **değişmedi** (kapsam/RLS/servis yetkisi aynı) |

### Doğrulama

- `tests/test_testui.py` **47 test**; tam suite **416 passed**.
- Gerçek tarayıcı (Chrome, CDP) ile: kullanıcı seçimi ve "— bağlı: DEMO-001"
  etiketi, unvan ekleme formu, izin türü ekleme (gerçek istek → "İzin türü eklendi."
  → listede ve talep formunda görünür), panelde karar kartı ve durum sayıları.

## [0.25.0] — 2026-09-11 — Test arayüzü: 4 kritik eksik düzeltmesi (çıkış, bakiye, rol/yetki, birim taşıma)

**Baseline (son commit `7c8ad0e`):** çalışma ağacı temiz, tam test suite **388 passed**.

Kapsam: kullanıcının bildirdiği dört eksik. Kapsam dışı (bu turda **dokunulmadı**):
RBAC/RLS/kapsam karar mantığı ve izin kataloğu, kimlik doğrulama mekanizması,
üretim API sözleşmesi (`/api/v1/*`), yeni frontend zinciri, impersonation,
vardiya/devam (M15), evrak (M16), permission override **yazma** (arayüzde salt
okunur: yazma servisi/kararı yok).

### 1) Çıkış gerçekten çalışmıyor (kök neden bulundu)

- **Kök neden:** arayüzün `fetch` yardımcısı CSRF başlığını yalnızca **gövdesi olan**
  isteklerde gönderiyordu; çıkış isteği gövdesiz olduğu için `403` alıyor, hata
  yutuluyor ve kullanıcı giriş ekranına yönlendiriliyordu — oturum sunucuda **açık**
  kalıyordu.
- Düzeltme: CSRF başlığı yazma yöntemlerinin tamamında gönderilir; çıkış artık
  gerçek yanıtı bekler, başarısızlıkta kullanıcıya bildirir (uydurmaz), yerel durumu
  temizler ve `location.replace` ile girişe döner (geri tuşu paneli açmaz).
- Sunucu tarafı: oturum gerçekten kapatılır; oturumsuz istekler sözleşme gereği
  `403` döner ve artık `error.code="not_authenticated"` ile "yetki yok" durumundan
  **ayrılır** (arayüz yalnızca gerçekten oturumu kapanmış kullanıcıyı girişe atar).
- Panel/giriş sayfaları `no-store` ile önbelleğe alınmaz (geri tuşu bayat içerik göstermez).

### 2) Yıllık izin bakiyesi görünmüyordu

- Panelde **"Yıllık izin bakiyem"** kartı: hakediş / kullanılan / bekleyen / kalan
  (değerler `hr.leave_balances` + **üretilen** `available_days` kolonundan).
- Çalışan listesinde her satırda yıllık izin özeti; **Detay** ekranında tüm izin
  türleri. Hakediş yoksa hata değil: "Henüz yıllık izin hakedişi oluşturulmamış."
- Arayüz hiçbir bakiye değerini kendisi hesaplamaz; onay/red/iptal sonrası görünüm
  backend'den yenilenir.
- Yetki: çalışan yalnız kendi bakiyesini, müdür yalnız kapsamındaki çalışanların
  özetini görür; kapsam dışı çalışanın detayı/bakiyesi **404**.

### 3) Unvan / rol / yetki ekranı yoktu

- **Unvan (position) ve birim (department) ayrı** gösterilir (M10/M12 ayrımı).
- Kullanıcının sistem rolü **Türkçe adıyla** listelenir (`hr.roles.name`, ör.
  `dept_manager` → "Departman Yöneticisi") ve kapsamı okunabilir metne çevrilir
  (ör. "Finans + alt birimleri"); teknik kod yalnızca ayrıntıda görünür.
- "Rol ve Yetkiler" bölümü (`role.manage` sahibine): mevcut roller + kapsamlar,
  **rol atama** (UNIT rollerde birim kapsamı zorunlu), **rol kaldırma** (soft delete,
  OD21) ve **geri alma**, izin istisnaları salt okunur, etkin izin listesi.
- **Yeni servisler:** `revoke_role_assignment`, `restore_role_assignment` ve girdi
  doğrulaması için `RoleAssignmentValidationError` (HTTP 400) — yetki/kapsam reddi
  hâlâ `RoleAssignmentError` (HTTP 403). `assign_role_to_user`, OD21 gereği
  kaldırılmış atamanın **sessizce** yeniden oluşturulmasını engeller.
- Self-elevation ve kiracı kuralları arayüzden bağımsız korunur: kullanıcı kendi
  rolünü atayamaz, sahip olmadığı yetkiyi dağıtamaz, `role.manage` yoksa rol
  yönetimi kapalıdır; platform rolleri kiracı arayüzünden yönetilemez.

### 4) Birim taşıma kullanıcı dostu değildi

- "Taşı" artık pencere (modal) açar: taşınacak birim, **şu anki üst birim** ve
  organizasyon ağacından seçilen **yeni üst birim** açılır listede; "En üst seviyeye
  taşı (kök)" seçeneği vardır; taşıma öncesi onay metni gösterilir.
- Hedef listesi **sunucudan** gelir (`?moves_for=<id>` → kendisi ve alt ağacı hariç);
  ham UUID istenmez/gösterilmez. Döngü/derinlik/kiracı garantileri veritabanında
  aynen çalışır (UI yalnızca kolaylık).
- Taşıma sonrası ağaç ve çalışan listesi otomatik yenilenir; kapsam yeniden hesaplanır.

### Ölçüm

| Ölçüm | Önce | Sonrası |
|---|---|---|
| Test sayısı | 388 | **407** (+19: çıkış, bakiye görünürlüğü, unvan/rol/yetki, taşıma) |
| Yeni migration | — | **0** |
| Yeni bağımlılık | — | **0** |
| Yeni servis | — | `revoke_role_assignment`, `restore_role_assignment` (+ doğrulama hata sınıfı) |
| Yeni uç (yalnız `/test-ui/api/*`) | 12 | **17** (`employees/<id>`, `employees/<id>/roles`, `roles`, `role-assignments/<id>/revoke|restore`; `departments?moves_for=`) |
| Güvenlik modeli | — | **değişmedi** (RLS, kapsam, servis yetkisi, self-elevation guards) |

### Doğrulama

- `scripts/run_tests.sh`: **407 passed**; `tests/test_testui.py` **38 test** (CSRF
  doğrulaması **açık** istemci ile — tarayıcı davranışı).
- `verify_fresh_install.sh`: sıfırdan kurulum **0 FAIL**, `SONUÇ: OK`; 6c-3 adımı
  çıkışın oturumu kapatmasını, bakiye/unvan/rol görünürlüğünü, taşıma hedef
  listesini ve rol atama/kaldırmayı gerçek HTTP ile ölçer.
- Gerçek `runserver` ile manuel akış: çalışan girişi → yıllık bakiye → çıkış →
  çıkış sonrası korunan yolun **302** girişe yönlenmesi → müdür girişi → listede
  unvan + rol + bakiye → detay → rol atama/`403` (yetkisiz)/kaldırma → birim taşıma
  (hedef seçenekleri kendisi ve alt ağacı hariç) → ağacın güncellenmesi → `/admin/` 404.
- `ruff`, `bandit`, `manage.py check`, `makemigrations --check`: temiz.

## [0.24.0] — 2026-09-11 — Manuel test arayüzü (`/test-ui/`): organizasyon + izin akışını tarayıcıdan test

**Baseline (son commit `97fe3cb`):** çalışma ağacı temiz, tam test suite **369 passed**.

Bu tur bir **ürün özelliği değildir**: mevcut backend'i tarayıcıdan gerçek kullanıcı
gibi elle denemek için **geliştirme/test arayüzü** eklenir. Kapsam dışı (bu turda
**dokunulmadı**): RBAC/RLS/kapsam karar mantığı, kimlik doğrulama mekanizması,
üretim API sözleşmesi (`/api/v1/*`), vardiya/devam (M15), evrak (M16), dashboard,
onboarding/billing, frontend zinciri (React/Vite/Tailwind/npm), impersonation,
tema/tasarım sistemi.

### Ölçüm

| Ölçüm | Önce | Sonra |
|---|---|---|
| Test sayısı | 369 | **388** (+19: arayüz, kapsam görünürlüğü, yetki reddi, bakiye) |
| Yeni migration | — | **0** (arayüz uygulaması model içermez) |
| Yeni Python bağımlılığı | — | **0** (Django şablonu + gömülü vanilla JS/CSS) |
| `/test-ui/*` (üretim varsayılanı, `DJANGO_DEBUG=0`) | — | **404** (`TEST_UI_ENABLED` kapalı) |
| Değişen güvenlik kuralı | — | **yok** (RLS, kapsam, servis katmanı yetkisi aynen yürürlükte) |
| Demo hesaplar | 1 (`demo@`) | **4** (`demo@` kiracı yöneticisi, `finans.mudur@` dept_manager, `personel@` employee, `ik.calisan@` employee) |

### Eklenen / değişen

- **Yeni uygulama `apps/testui`** — model **yok**, migration **yok**; `views.py`
  (giriş sayfası + panel kabuğu), `api.py` (JSON uçları + tek yerde hata eşlemesi),
  `urls.py`, şablonlar (`base`, `login`, `app`).
- **`/test-ui/` yolu ve kapı** — `config/settings.py`: `TEST_UI_ENABLED`
  (`PERSOPYRO_TEST_UI`, varsayılan `DJANGO_DEBUG`) ve `TEST_UI_DEMO_USERS`;
  `config/urls.py` yolu koşulsuz kaydeder, kapı **çalışma anında** uygulanır:
  kapalıyken bütün `/test-ui/*` yolları **404** döner (davranış testlerde ölçülebilir
  kalır; üretimde varsayılan kapalıdır).
- **Uçlar** (`/test-ui/api/*`, hepsi oturum çerezi + CSRF): `me`, `dashboard`,
  `lookups`, `departments` (GET/POST), `departments/<id>/move|delete`,
  `employees` (GET/POST), `employees/<id>/balances`, `leave-requests` (GET/POST),
  `leave-requests/<id>/decision|cancel`. Yeni kimlik doğrulama yolu **eklenmedi**;
  giriş/çıkış mevcut `/api/v1/auth/login|logout` uçlarıdır.
- **Yetki kararı arayüzde verilmez** — tüm işlemler servis katmanına delege edilir;
  arayüz yalnızca buton görünürlüğünü ayarlar (tek güvenlik kontrolü değildir).
  Yetki/kapsam reddi HTTP **403** ve "Bu işlem için yetkiniz veya organizasyon
  kapsamınız yok." mesajıyla döner; kapsam dışı nesne **404** (RLS nesneyi göstermez).
  Hata gövdeleri yığın izi (stack trace) içermez.
- **`apps/organization/services.py`** — yetki/kapsam redleri için
  `OrganizationAuthorizationError(OrganizationError)` alt sınıfı eklendi; HTTP
  katmanı bunu **403** olarak eşler. Mevcut `pytest.raises(OrganizationError)`
  beklentileri etkilenmez (alt sınıf).
- **Demo verisi** (`scripts/_demo_data.py`) — çalışan kullanıcıları (`personel@`,
  `ik.calisan@`), Finans müdürü kullanıcısının çalışan kaydıyla eşlenmesi, izin
  türleri + yıllık politika (14 gün) + dönem hakedişi ve **iki bekleyen talep**:
  biri kapsam **içi** (Muhasebe, `IZIN_TESTI_MUHASEBE`), biri kapsam **dışı**
  (İnsan Kaynakları, `IZIN_TESTI_IK`). Parolalar yalnızca betiğe verilen
  `DEMO_PASSWORD` ile üretilir; migration'lara/repoya secret konmaz.
- **`scripts/verify_fresh_install.sh`** — HTTP adımına **6c-1…6c-4** eklendi:
  giriş sayfası + oturumsuz 403, gerçek oturumla panel/veri, **kapsam görünürlüğü**
  (Muhasebe görünür, İK görünmez/onaylanamaz), yetkisiz departman oluşturma **403**,
  ve **üretim varsayılanında 404**.

### Doğrulama

- `scripts/run_tests.sh`: **388 passed** (öncesi 369).
- `tests/test_testui.py` (19 test): giriş sayfası, geçerli/hatalı giriş, oturumsuz
  yönlendirme, çalışanın yalnız kendi talepleri, dept_manager alt ağaç görünürlüğü,
  sibling DENY (403), onay → bakiye, red → bakiye tüketilmez, iptal, çıkış,
  `/admin/` 404, askıdaki kiracı, cross-tenant, arayüz kapalıyken 404, departman
  oluşturma yetkisi, kapsamla sınırlı çalışan listesi, kişisel veri sızmaması.
- `verify_fresh_install.sh`: sıfırdan kurulum, **0 FAIL** (bkz. commit raporu).
- `ruff`, `bandit` (0 bulgu), `manage.py check`, `makemigrations --check`: temiz.
- Canlı geliştirme DB'sinde gerçek HTTP ile elle geçiş yapıldı (giriş, kapsam,
  onay/red, 403/404 yolları).

## [0.23.0] — 2026-09-11 — M14: izin (leave) modülü — tür, politika, hakediş, talep, onay

**Baseline (son commit `042dfc7`):** çalışma ağacı temiz, migration'lar tam uygulanmış,
tam test suite **330 passed**; canlı DB ölçümü: 39 izin kodu, FORCE RLS tablo **12**,
izin tablosu **0**. Kapsam dışı (bu turda **dokunulmadı**): vardiya/devam (`shifts`,
`attendance_records`), evrak (`documents`), access request/grant, dashboard,
onboarding/billing, **frontend/API CRUD uçları**, çalışma takvimi ve resmî tatil
hesabı, maskeleme motoru, zamanlayıcı (cron) kurulumu.

### Ölçüm (canlı DB, öncesi → sonrası)

| Ölçüm | Önce | Sonra |
|---|---|---|
| İzin kodu (katalog) | 35 | **39** (`leave.request.cancel`, `leave.request.approve.override`, `leave.balance.read`, `leave.balance.manage`) |
| Kiracı verisi taşıyan FORCE RLS tablosu | 12 | **19** (`leave_types`, `leave_policies`, `leave_policy_rules`, `leave_balances`, `leave_balance_entries`, `leave_requests`, `approval_steps`) |
| Varsayılan rol izin sayıları | 29/19/22/14/6 | **33/21/22/16/8** (tenant_admin/tenant_officer/hr_manager/dept_manager/employee; `platform.*` kodları kiracı rollerine verilmez) |
| Runtime yetkileri (yeni tablolar) | — | `SELECT/INSERT/UPDATE`; **`DELETE`/`TRUNCATE` yok**; defter **append-only** (`SELECT/INSERT`) |
| `persopyro_platform` erişimi | — | **hiçbiri** (7 tabloda 0/0/0/0) |
| DB guard'ı (izin modülü) | — | **4** yeni tetikleyici (+ 14 kiracı-içi FK guard'ı) |
| Demo verisi | — | 7 izin türü, 1 politika, 3 bakiye, onaylanmış sonda talebi |

### Eklenen / değişen

- **Yeni uygulama `apps/leave`** — modeller, servis paketi (`types`, `policies`,
  `balances`, `requests`, `authorization`), `manage.py accrue_leave_entitlements`
  komutu ve sistem izin türü katalogu (`leave_type_catalog.py`).
- **M14 ✅ (`leave/0001_m14_leave_schema`)** — yedi tablo; OD21 kısmi benzersizlikler;
  `leave_balances.available_days` **üretilen (STORED)** kolon; `approval_steps`
  **genel** onay tablosu (`subject_kind`/`subject_id`, FK **yok**); `leave_requests`
  `document_ref` M16 entegrasyon noktası (gerçek FK **eklenmedi**).
- **`leave/0002_m14_leave_guards`** — dört DB guard'ı: kıdem aralığı **çakışma reddi**,
  **negatif bakiye** reddi (politika izin vermiyorsa), **talep durum makinesi**
  (yasak geçişler + karar/iptal zamanı + **belgesiz onay yasağı**), **onay sırası**
  (önceki adım karara bağlanmadan sonraki adım karar veremez).
- **`leave/0003_m14_rls_leave` + `0004_m14_leave_same_tenant_guards`** — RLS + FORCE
  (7 tablo) ve 14 kiracı-içi yabancı anahtar guard'ı.
- **`rbac/0011_m14_leave_permissions`** — matrisin **E. İzin** bölümündeki dört eksik
  kod katalogda; rol bağları matrise göre tamamlandı (idempotent; `hr_manager`
  değiştirilmedi).
- **Hakediş (§2/§4):** `fixed` ve `tenure_based` ([min, max) yarı açık); sabit değerler
  **kod içine gömülmedi**; hakediş **idempotent** (dönem başına tek `accrual` kaydı;
  değer değişirse fark gerekçeli **düzeltme** kaydı olarak yazılır).
- **Bakiye modeli (§3):** güncel durum + **append-only olay defteri** (rezervasyon,
  kullanım, iade, hakediş, düzeltme); manuel düzeltmede **gerekçe zorunlu** ve
  denetimli; **negatif bakiye** yalnız politikayla izin verilirse.
- **Talep + akış (§5–§9, §11–§12):** sunucu tarafı gün hesabı (**takvim günü**; hafta
  sonu/tatil hesabı takvim modülüne bırakıldı — sahte kesinlik yok), istemci
  değeri doğrulanır, ön bildirim/azami süre kuralları; kapalı durum kümesi; onay
  sırası **birincil yönetici → yetkili rol**; `SEPARATE_ACTOR`; adım atlama yalnız
  `leave.request.approve.override` + kayıtlı gerekçeyle; onaya sunumda **rezervasyon**,
  nihai onayda **tek** kullanım, iade politika koşuluna bağlı.
- **Yetkilendirme (§10):** yeni sistem yazılmadı — `apps/leave/services/authorization.py`
  mevcut `izin ∧ kapsam` katmanını genişletir; **aynı kiracı** koşulu açıkça doğrulanır
  (fail-closed); ASSIGNED görünürlüğü yalnız **karar verebilen** adım sahibi için.
- **Yetki turu (`scripts/02_grant_app_privileges.sql`)** — izin tablolarında
  `DELETE`/`TRUNCATE` geri alındı; `leave_balance_entries` **append-only**.

### Doğrulanan

- **Testler:** yeni `tests/test_leave_m14_core.py` (16) + `tests/test_leave_m14_flow.py`
  (23) + `tests/leave_helpers.py` ortak yardımcıları; istek listesindeki 28 senaryo
  kapsandı (tür izolasyonu/benzersizlik, sistem kodu rezervi, fixed/kıdem hakediş,
  çakışan kural reddi, idempotent accrual, talep oluşturma/doğrulama, izin ∧ kapsam
  ALLOW/DENY, sibling/cross-tenant DENY, sıra atlanamaz, override, belge zorunluluğu,
  iptal/iade, çift tüketim engeli, süresi dolmuş rol ataması, denetim kayıtları,
  RLS/FORCE, platform erişim yasağı, runtime yetki turu). **Tam suite: 369 passed**
  (330 → 369; regresyon yok). `tests/test_rbac_scope_m7_m9.py` katalog delta'sına
  (M14) göre güncellendi.
- **Kalite:** `ruff check .` ✅, `bandit` ✅, `manage.py check` ✅,
  `makemigrations --check` ✅, `migrate` ✅ (5 yeni migration).
- **Fresh install (`scripts/verify_fresh_install.sh`)**: yeni adım **`6e/8`** — yedi
  tablo, RLS/FORCE/politika, **FORCE RLS 19**, dört guard tetikleyicisi, kiracı
  guard'ı, kısmi benzersizlik, durum `CHECK`, yetki turu (app `DELETE` yok, defter
  append-only, platform erişemez), katalog kodları ve **rol izin sayıları** ve
  **uçtan uca izin sondası** (`scripts/_leave_probe.py`: tür → politika → hakediş →
  talep → onay → bakiye; **iki kez çalıştırılıp idempotans doğrulanır**). Sıfırdan
  kurulum: **OK**, 369 passed, geçici DB temizlendi.
- **HTTP smoke (`scripts/smoke.sh`):** health/ready 200, `/admin/` 404, token 201 →
  logout 204 → 401, askıya alınmış kiracı 403; açık process/port yok.
- **Eski garantiler:** platform authorization ✅, tenant RLS + FORCE ✅, NOBYPASSRLS ✅,
  `auth_*`/`api_tokens`/`permissions` daraltmaları ✅, `employee_personal_data`
  güvenlik sınırı ✅ (izin kapsamı K2/K3 erişimi **üretmez**).

### Kalan (bilinçli, kapsam dışı)

- Çalışma takvimi / resmî tatil ve hafta sonu hesabı (M15), evrak modülü ve gerçek
  `document_ref` FK'si (M16), ekip çakışma kontrolü, carryover (devir) işi, izin
  gerekçesi maskeleme, bildirimler ve API CRUD uçları.

### Güvenlik

- **Yeni `SECURITY FINDING` yok.** Kapatılan yüzeyler: kapsamsız/izinsiz talep
  oluşturma, sibling birim ve cross-tenant görünürlük/onay, kendi talebini onaylama
  (`SEPARATE_ACTOR`), adım atlama, belgesiz onay, negatif bakiye, çift tüketim
  (rezervasyon/kullanım/iade), süresi dolmuş veya silinmiş rol atamasıyla onay,
  runtime `DELETE` ile izin kaydı silme.
- **`OPEN DECISION` yok** (OD5/OD21/OD24/OD27 kapalı; yeni karar gerekmedi).
  Mevcut açık kararlar: OD6–OD18 (**13**), bu modülü **engelleyen yok**.
- **Bilinçli sınır (raporlandı):** gerçek iki bağlantılı yarış testi (`transactional_db`)
  bu projenin test ortamında **çalıştırılamıyor** — `pytest-django` teardown'ındaki
  `flush`, `hr.permissions`/`hr.role_permissions` TRUNCATE sırasını çözemiyor
  (ayrıca ham SQL tablolar Django tablo listesinde yok). Garanti iki katmanda
  **doğrudan** sınanır: servis (`select_for_update` + durum kontrolü) ve veritabanı
  (`(leave_request_id, entry_kind)` benzersiz kısıtı).

## [0.22.0] — 2026-09-11 — M10–M13: organizasyon ve çalışan temel veri modeli + M9 tamamlama

**Baseline (son commit `1964e0b`):** çalışma ağacı temiz, migration'lar tam uygulanmış,
tam test suite **273 passed**. Kapsam dışı (bu turda **dokunulmadı**): `leave_*`/onay
tabloları, `shifts`/`attendance`, `documents`, access request/grant, dashboard,
onboarding/billing, API CRUD uç noktaları, maskeleme motoru.

### Ölçüm (canlı DB, öncesi → sonrası)

| Ölçüm | Önce | Sonra |
|---|---|---|
| Kiracı verisi taşıyan FORCE RLS tablosu | **7** | **12** (`departments`, `positions`, `employees`, `employee_personal_data`, `employee_manager`) |
| Kiracı-içi FK guard tetikleyicisi | 9 | **19** (11 tablo) |
| `user_roles.scope_org_unit_id` | düz UUID kolonu | **gerçek FK** (`hr.departments.id`) |
| Kapsam çözümlemesi | fail-closed köprü (çözülemez → izin yok) | **gerçek alt ağaç** (recursive CTE ≤ 20) |
| `persopyro_app` yetkileri (yeni tablolar) | (yoktu) | `SELECT/INSERT/UPDATE` ✅, **`DELETE`/`TRUNCATE` YOK** |
| `persopyro_platform` yetkileri (yeni tablolar) | (yoktu) | **hiçbiri** (4 tabloda 0/0/0/0) |
| Org/çalışan verisi (canlı) | 0 | demo: 4 birim, 2 pozisyon, 3 çalışan, 1 raporlama ilişkisi |

### Eklenen / değişen

- **Yeni uygulama `apps/organization`** — `Department`, `Position`, `Employee`,
  `EmployeePersonalData` (1:1, PK `employee_id`), `EmployeeManager` + servis katmanı.
- **M10 ✅ (`organization/0001_od24_departments_positions` + `0009`)** — `hr.departments`
  (`code`/`name`/`status`, `parent_id` **adjacency list**, `manager_employee_id`) ve
  `hr.positions` (`code`/`name`/`level`; **birime bağlı değil**). Kısmi benzersizlikler
  (`WHERE deleted_at IS NULL`), `(tenant_id, parent_id)` indeksi, `status` kapalı küme
  `CHECK`. **OD27: `company_id`/`legal_entity_id` eklenmedi.**
- **M11 ✅ (`organization/0002` + `0008`)** — `hr.departments_tree_guard`: **döngü reddi**,
  **derinlik ≤ 20**, **silinmiş birimin altına yeni aktif birim bağlanamaz**;
  `CHECK (parent_id IS DISTINCT FROM id)`; `hr.employee_manager_active_guard`: silinmiş
  çalışan/yönetici ile **yeni aktif** ilişki kurulamaz. `path`/closure kolonu **eklenmedi**.
- **M12 ✅ (`organization/0003`)** — `hr.employees` (**PII içermez**; `department_id`
  **NOT NULL**, `user_id`/`position_id` nullable, `employee_no` kısmi benzersizliği,
  `status` kapalı küme, `hire_date`/`termination_date`) + `hr.employee_personal_data`
  (K2 açık alanlar, **K3 Fernet şifreli**: `health_notes`, `disability_status`,
  `biometric_ref`; `apps/common/fields.py`).
- **M13 ✅ (`organization/0004`)** — `hr.employee_manager`: `relation_type`
  (`primary`/`secondary`/`delegate`), `effective_from`/`effective_to`, **tek aktif
  birincil yönetici** kısmi benzersiz indeksi, `(employee_id, manager_employee_id)` kısmi
  benzersizliği, self-manager `CHECK`'i. **RBAC değildir — yetki üretmez.**
- **M9 tamamlama (`rbac/0008` + `0009` + `0010`)** — `scope_org_unit_id` **gerçek FK**
  (kolon **düşürülmedi**, `SeparateDatabaseAndState` ile yalnızca kısıt eklendi); kapsam
  guard'ı **aktif birim** denetimi kazandı; devre dışı bırakma serbest bırakıldı (ölçülen
  hata: birim silme servisi atamaları kapatamıyordu).
- **Kapsam çözümleyici ✅ (`apps/rbac/services/scope.py`)** — `UNIT` = atanan birim + alt
  ağaç (recursive CTE ≤ 20), `UNIT_DIRECT` = yalnız atanan birim, `TENANT` = kiracı geneli,
  `SELF` = kendi kaydı; çoklu atama **birleşimi**; fail-closed (silinmiş birim ve alt ağacı,
  kiracı uyuşmazlığı, silinmiş/süresi geçmiş atama, pasif kullanıcı → kapsam yok).
  `invalidate_org_scope_cache()` = önbellek sınırı (v1'de önbellek **yok**, sahte sistem
  kurulmadı).
- **İzin ∧ kapsam ✅ (`apps/organization/services.py`)** — merkezi
  `can_access_employee(user, employee, permission_code)` ve
  `can_access_employee_personal_data(...)`; `object_in_org_scope()` kapsam ekseni.
  **Organizasyon kapsamı K2/K3 erişimi ÜRETMEZ** (izin + `MFA_OK` + `PURPOSE_GIVEN` gerekir).
- **Servisler + denetim** — birim oluştur/güncelle/**taşı**/sil/**geri al**/sorumlu ata,
  pozisyon oluştur, çalışan oluştur/**birim değiştir**, birincil yönetici ata/kapat. Her
  işlem `audit_logs`'a yazılır (`org.unit.*`, `employee.*`); `changes` **PII değeri içermez**.
  Birim silme **boş birim** şartına bağlıdır ve bağlı **kapsam atamalarını devre dışı bırakır**
  (birim restore edilse bile atama sessizce yetki kazanmaz — OD21/OD5 §5.6).
- **Yetki turu (`scripts/02_grant_app_privileges.sql`)** — yeni beş tabloda `DELETE`/`TRUNCATE`
  geri alındı (OD21 soft-delete; K2/K3 fiziksel silinmez).

### Doğrulanan

- **Testler:** yeni `tests/test_organization_m10_m13.py` (**56 test**) — 33 maddelik liste
  (M10 tablo/benzersizlik/cross-tenant parent; M11 self-parent/cycle/derinlik 20/silinmiş
  ata/move; M12 zorunlu birim + cross-tenant department-position + 1:1 + K3 şifreleme;
  M13 self/cross-tenant manager + tek aktif primary + secondary/delegate; M9 gerçek FK;
  kapsam çözümleyici 9 senaryo; izin ∧ kapsam ALLOW/DENY; RLS/FORCE; platform erişim
  yasağı; servis + denetim + soft-delete/restore davranışı; OD27 kolon yokluğu).
  `tests/test_rbac_scope_m7_m9.py` M10 sonrası davranışa güncellendi;
  `tests/test_tenant_isolation.py` yapısal denetimi `organization` uygulamasını kapsayacak
  şekilde genişletildi. **Tam suite: 330 passed** (273 → 330; regresyon yok).
- **Kalite:** `ruff check .` ✅, `bandit` ✅, `manage.py check` ✅,
  `makemigrations --check` ✅ (elle yazılan migration'lar model durumuyla birebir),
  `migrate` ✅ (10 yeni migration uygulandı).
- **Fresh install (`scripts/verify_fresh_install.sh`)**: yeni adım **`6d/8`** — beş tablo
  için RLS+FORCE+politika, **FORCE RLS sayısı 12**, OD27 kolon yokluğu, M9 **gerçek FK**,
  ağaç tetikleyicisi, `(tenant_id, parent_id)` indeksi, OD24 path kolonu yokluğu, M13
  kısmi benzersiz indeks + koşulu, 7 kiracı-içi guard, yetki turu (app `DELETE` yok, platform
  erişemez) ve **kapsam sondası** (`scripts/_scope_probe.py`: alt ağaç erişimi ALLOW, kardeş
  birim DENY, K2/K3 kapsamdan gelmez, TENANT rolü kiracı geneli). Sıfırdan kurulum: **OK**,
  330 passed, geçici DB temizlendi.
- **HTTP smoke (`scripts/smoke.sh`):** health/ready 200, `/admin/` 404, token 201 → logout
  204 → 401, askıya alınmış kiracı 403; açık process/port bırakılmadı.
- **Eski garantiler:** platform authorization ✅, tenant RLS + FORCE ✅, `persopyro_app`
  ve `persopyro_platform` NOBYPASSRLS ✅, `auth_*`/`api_tokens`/`permissions` daraltmaları ✅,
  `tenant_directory` salt-okunur ✅.

### Kalan (bilinçli, kapsam dışı)

- `leave_*`/`shifts`/`attendance`/`documents`/access request-grant modülleri (M14–M21),
  `ASSIGNED` koşulunun nesne düzeyi uygulaması (onay modülü), maskeleme motoru,
  kapsam önbelleği (K17) ve API CRUD uçları.
- Rol yönetimi **API/akış** katmanı (servis guard'ları hazır).

### Güvenlik

- **Yeni `SECURITY FINDING` yok.** Kapatılan yüzeyler: kapsamsız/çözülemeyen UNIT ataması,
  silinmiş birim kapsamı, silinmiş ata altında yeni aktif birim, cross-tenant parent /
  department / position / manager / personal-data, self-manager, çoklu aktif birincil
  yönetici, runtime `DELETE` ile kişisel veri (ve `ON DELETE CASCADE`) silme, platform
  rolünün HR verisine erişimi.
- **`OPEN DECISION` yok** (OD5/OD21/OD24/OD27 zaten kapalı; yeni karar gerekmedi).
  Mevcut açık kararlar: OD6–OD18 (**13**), engelleyici yoktur.

## [0.21.0] — 2026-09-11 — M7 + M8 tam, M9 kısmi: tenant RBAC scope altyapısı (OD5 uygulaması)

**Baseline (son commit `8418be3`):** çalışma ağacı temiz, migration'lar tam uygulanmış,
tam test suite **244 passed**. Kapsam dışı (bu turda **dokunulmadı**): M10–M13
organizasyon tabloları (`departments`/`positions`/`employees`), organization scope
resolver'ın alt ağaç hesabı, leave/shift/onay modülleri, platform authorization yapısı.

### Ölçüm (göç öncesi → sonrası)

| Ölçüm | Önce | Sonra |
|---|---|---|
| `manager` rolü / ataması | 2 kiracıda **1 rol**, izin **7** | `dept_manager` olarak göç etti (izin **14**), kod `manager` **kalmadı** |
| `tenant_officer` | yok | **oluşturuldu** (izin **19**) |
| Kiracı başına rol sayısı | 4 | **5** |
| Kiracı başına rol-izin bağı | 64 | **90** |
| `user_roles` atama sayısı | 1 | **1 (kayıp yok)** |
| `hr.manager` rolü (silinen) | 0 | 0 |

### Eklenen / değişen

- **M7 ✅ (`rbac/0005_od5_rol_katalogu_gocu`)** — `manager → dept_manager` **yerinde**
  göç: rol kimliği, atamaları ve izin bağları **korunur** (yeni rol üretilmez → çift
  kayıt/atama kaybı yok); izin seti §6.2 **DY** kolonuna explicit getirilir (eksiğini
  ekler, hedefte olmayanı **çıkarır**); `tenant_officer` §6.2 **ŞYT** setiyle
  oluşturulur (`◐ ❓OD9` işaretli kodlar **alınmaz**, katalogda olmayan kod →
  **açık hata**). Fail-closed: aynı kiracıda `manager`+`dept_manager` birlikte varsa
  **açık hata**; soft-delete edilmiş `manager` **sessizce canlandırılmaz**; göç
  **idempotent**; kiracı başına rol/rol-izin sayıları **raporlanır**. Geri alma, atama
  varsa **reddeder** (veri kaybı yok).
- **M8 ✅ (`rbac/0006_od5_role_scope_type`)** — `hr.roles.scope_type`: **NOT NULL** +
  DB `CHECK` (`roles_scope_type_valid`) kapalı küme `TENANT`/`UNIT`/`UNIT_DIRECT`/
  `ASSIGNED`/`SELF`. **`SYS` tenant kümesine alınmadı** (§4.4: yalnız platform rolleri).
  Backfill: `tenant_admin`/`tenant_officer`/`hr_manager` = `TENANT`, `dept_manager` =
  `UNIT`, `employee` = `SELF`, custom roller = `TENANT` (§4.1) ve **raporlanır**.
- **M9 ✅ kısmi (`rbac/0007_od5_user_role_scope`)** — `hr.user_roles.scope_org_unit_id`
  (uuid, nullable) + benzersizliğin **`(user_id, role_id, scope_org_unit_id)` `NULLS NOT
  DISTINCT`** olarak güncellenmesi (kapsamsız rollerde çift atama engellenir, aynı rol
  farklı birimlere atanabilir) + **guard tetikleyicisi** `trg_user_role_scope_guard`
  (UNIT/UNIT_DIRECT ⇒ kapsam **zorunlu**; diğer tiplerde kapsam **NULL olmalı**; rol
  kiracı bağlamında görünmüyorsa yazım **reddedilir**). **Bilinçli ertelenen (M10
  sonrası, sahte alan üretilmedi):** `departments(id)` **FK'si**, "aktif birim" guard'ı,
  **alt ağaç çözümlemesi**.
- **Fail-closed köprü (`apps/rbac/services/scope.py`)** — M10 öncesi kapsam
  **çözülemez**; bu yüzden `UNIT`/`UNIT_DIRECT` atamaları **izin üretmez**. Aksi hâlde
  bir `dept_manager` izni **tenant-wide** görünürdü (yetki yükseltme). İki kavram
  ayrıldı: `declared_permission_codes()` (**izin sahipliği** — yetkilendirme kaynağı
  değildir) ve `effective_permission_codes()`/**`authorized_permission_codes()`**
  (kapsama göre **kullanılabilir** — tek yetki kaynağı). `TENANT` ve `SELF` roller
  mevcut davranışını **korur**.
- **Self-elevation guard'ları (`apps/rbac/services/roles.py`)** — `can_assign_role()` +
  `assign_role_to_user()`: kullanıcı **kendi rolünü atayamaz/değiştiremez**; aktör
  atadığı rolün **tüm** izinlerine sahip olmalı (sahip olmadığı yetki dağıtılamaz);
  `role.manage` gerekir; `UNIT`/`UNIT_DIRECT` rolde kapsam **zorunlu**, diğer tiplerde
  kapsam **verilemez**; her atama `audit_logs`'a **`role.assign`** olarak yazılır.
- **Katalog (`apps/rbac/permission_catalog.py`)** — `DEFAULT_ROLE_TEMPLATES` hedef
  adlarla: `tenant_admin` (29), **`tenant_officer`** (19), `hr_manager` (22; şablon
  olarak korunur), **`dept_manager`** (14, `UNIT`), `employee` (6, `SELF`); her şablonda
  `scope_type`; eski `manager` kaldırıldı (`LEGACY_ROLE_CODE_MAP`).

### Doğrulanan

- **Testler:** yeni `tests/test_rbac_scope_m7_m9.py` (**29 test** — 20 maddelik liste:
  göç/atama korunumu, çift kayıt yok, soft-delete, hedef izin setleri, scope_type
  eşlemeleri + DB reddi, kapsamsız UNIT reddi, NULL≠tenant-wide, TENANT/SELF davranışı,
  silinmiş/süresi geçmiş atama, pasif kullanıcı, self-elevation, tenant izolasyonu,
  fresh-install seti) + katalog↔migration donmuş kopya eşitliği. **Tam suite: 273 passed**
  (244 → 273; regresyon yok). `tests/test_api_auth.py` beklenen rol seti güncellendi.
- **Kalite:** `ruff check .` ✅, `bandit -q -r apps config -x tests` ✅, `manage.py check`
  ✅, `makemigrations --check` ✅ (elle yazılan migration'lar model durumuyla birebir),
  `migrate` ✅.
- **Fresh install (`scripts/verify_fresh_install.sh`): yeni adım `6b/8`** — eski `manager`
  rolü **yok**, hedef rol seti + kapsam tipleri doğru, izin sayıları 29/14/19,
  `scope_type` `CHECK`, `scope_org_unit_id` kolonu, `NULLS NOT DISTINCT` benzersizliği,
  guard tetikleyicisi; **M9'un ertelenen kısmı açıkça ölçülüyor** (`hr.departments` FK'si
  ve tablosu = 0). Sıfırdan kurulum: **OK**, 273 passed, geçici DB temizlendi.
- **HTTP smoke (`scripts/smoke.sh`):** health/ready 200, `/admin/` 404, token 201 →
  logout 204 → 401, askıya alınmış kiracı 403; açık process/port bırakılmadı.
- **Eski garantiler (yeniden doğrulandı):** platform authorization testleri ✅, tenant
  RLS + FORCE ✅, `persopyro_app` NOBYPASSRLS ✅, `persopyro_platform` NOBYPASSRLS ✅,
  `auth_*`/`django_admin_log` yetki daraltmaları ✅, `api_tokens` M30 daraltması ✅.
- **Canlı DB guard kanıtı:** kapsamsız `dept_manager` ataması denemesi →
  `user_roles: UNIT kapsamli rol icin scope_org_unit_id ZORUNLUDUR (OD5 fail-closed)`
  (işlem geri alındı; canlı veri değişmedi).

### Kalan (bilinçli, kapsam dışı)

- **M9'un ertelenen parçası:** `hr.user_roles.scope_org_unit_id` → `hr.departments(id)`
  FK'si + "kapsam aynı kiracının **aktif** birimini göstermeli" guard'ı (M10 sonrası).
- **Organization scope resolver'ın alt ağaç hesabı** (recursive CTE) — M10'da
  `apps/rbac/services/scope.py` içine eklenecek; çağıran katman **değişmeyecek**.
- M10–M13 (departments/positions/employees), M17–M19 (platform → kiracı erişimi),
  izin kataloğu genişletmesi (matristeki `🔜` kodlar).

### Güvenlik

- **Yeni `SECURITY FINDING` yok.** Kapatılan risk: "birim yöneticisi izinlerinin
  tenant-wide görünmesi" (M7–M9 fail-closed köprüsü). Kalan risk: kapsam **önbellek
  bayatlığı** (M10 sonrası taşıma senaryosu) → karşı önlem OD5 §5.8'de tanımlı.
- **`OPEN DECISION` yok** (OD5 kapalı; bu turda yeni karar gerekmedi). Mevcut açık
  kararlar: OD6–OD18 (**13**), engelleyici yoktur.

## [0.20.0] — 2026-09-11 — OD5 kapandı: `dept_manager` organizasyon kapsamı kesinleşti (yalnızca doküman)

**Baseline (son commit `827fef2`):** çalışma ağacı temiz. Bu girişte **kod, migration
veya veritabanı değişikliği YAPILMADI** — yalnızca mimari karar (OD5) kesinleştirildi
ve ilgili dokümanlar güncellendi.

### Karar (özet)

- **OD5 — KAPALI ✅:** `dept_manager` kapsamı = **atandığı organizasyon birimi + tüm alt
  birimleri** (kapsam kodu **`UNIT`**, seçenek A). `UNIT_DIRECT` kodu korunur,
  `dept_manager` için kapalı davranış `UNIT`'tir; "birim bazında yapılandırma" ihtiyacı
  **yeni kod üretmeden** karşılandı: kapsam **tipi** rol düzeyinde (`roles.scope_type`),
  **birim** atama düzeyinde (`user_roles.scope_org_unit_id`).
- **İki eksen ayrı:** RBAC *"hangi işlem"*, kapsam *"hangi kayıt"* → karar
  **`izin ∧ kapsam`**; kapsam izin **üretmez**. Biri yoksa **reddet** (fail-closed).
- **NULL = fail-closed:** `UNIT`/`UNIT_DIRECT` tipli rolde `scope_org_unit_id IS NULL`
  ise atama **hiç kapsam üretmez**; NULL **asla** "tüm kiracı" değildir. Kiracı geneli
  yetki `TENANT` tipli rolle (mevcut kodlar: `tenant_admin`, `tenant_officer`).
- **Alt ağaç:** OD24 modeli — `departments.parent_id` **adjacency list** + `WITH
  RECURSIVE` CTE; **closure/materialized path/nested set yok**; derinlik **20**; döngü
  **DB tetikleyicisiyle** engellenir.
- **Çoklu atama:** aynı kiracı içinde birden fazla atama **birleşir (union)**; **kiracılar
  arası birleşim yok**; soft-delete edilmiş/süresi geçmiş atama ve pasif kullanıcı
  **kapsam üretmez**; kullanıcı kendi kapsamını **yükseltemez**.
- **`employee_manager` ≠ `dept_manager`:** biri çalışan bazlı raporlama/onay ilişkisi,
  diğeri RBAC rolünün birim kapsamı; **biri diğerini türetmez** (birincil yönetici olmak
  izin seti kazandırmaz, `dept_manager` olmak `employee_manager` satırı üretmez).
  `departments.manager_employee_id` de yetki üretmez.
- **Kişisel veri sınırı:** "çalışanı yönetebilir" ≠ "K2/K3'ü görebilir"; `employees` PII
  içermez, K2/K3 için ayrı izin + koşul (maskeleme, `MFA_OK`, `PURPOSE_GIVEN`,
  gerekirse `GRANT_VALID`) aynen geçerli.
- **Soft-delete/restore:** birim silinirse atama satırı kalır ama **kapsam üretmez**;
  birim restore edilse bile atama **sessizce** yeniden etkinleşmez (OD21) — açık
  `*.restore` + `role.manage` + denetim kaydı gerekir; "silinmiş birim + etkin atama"
  tutarlılık denetiminde raporlanır (otomatik onarım yok).
- **Taşıma (move):** kapsam **güncel** alt ağaçtır → taşıma kapsamı yeniden hesaplar
  (büyüyebilir/küçülebilir); **bilinçli** kabul edildi. Karşı önlemler: önbellek
  geçersiz kılma + taşımanın **yüksek etkili denetim olayı** olarak kaydı + döngü/derinlik
  ihlalinin DB'de reddi.

### Doğrulanan (ölçüm)

- **Kod tarafı çakışma yok:** `hr.roles` kolonları `id, created_at, updated_at,
  deleted_at, name, code, role_type, description, is_assignable, tenant_id` — **`scope_type`
  yok**; `hr.user_roles` — **`scope_org_unit_id` yok**; mevcut izin çözümleyicisi
  (`apps/rbac/services/permissions.py`) **yalnızca izin kodlarını** hesaplar, kapsam
  boyutunu **hiç** uygulamaz. Yani OD5 yeni bir katman tanımlar, mevcut davranışı
  bozmaz.
- **Kapsam kodları belgede mevcuttu** (`SYS`, `TENANT`, `UNIT`, `UNIT_DIRECT`,
  `ASSIGNED`, `SELF`) → **yeni kod/ isim uydurulmadı**.
- Kiracı rol kataloğunda `manager` şablonu (7 izin) ve `dept_manager` henüz **yok**;
  M7 göçü OD5 kapandığı için **uygulanabilir** durumda.

### Kalan / uygulanmadı (bilinçli)

`M7` (rol kodu göçü + kapsam tipi ataması), `M8` (`roles.scope_type`), `M9`
(`user_roles.scope_org_unit_id` + guard + `NULLS NOT DISTINCT` benzersizlik),
`M10–M13` (departments/positions/employees/employee_manager), **organization scope
resolver** ve testleri bu turda **uygulanmadı** — karar sonucunda bekleyen işler olarak
`docs/DATABASE.md` M7/M8/M9 satırlarına ve `docs/AUTHORIZATION.md` bölüm 13'e yazıldı.

### SECURITY FINDING

**Yeni bulgu yok.** Karar, iki bilinen riske karşı önlem içeriyor: (a) bayat kapsam
önbelleği (taşıma sonrası eski alt ağaç) → önbellek geçersiz kılma + test, (b) birim
taşıma ile **dolaylı** kapsam büyümesi → denetim kaydı + yalnızca açık yönetim işlemiyle
gerçekleşmesi.

### OPEN DECISION

**Yeni OD açılmadı.** OD5 kapandı; açık karar sayısı **14 → 13** (OD6–OD18). Paket
kapalı kararlarla uyumludur (OD5, OD21/K25 soft-delete, OD24 ağaç, OD27 tek tüzel kişilik,
AUTHORIZATION §4.3/§4.4/§6.2A).

## [0.19.0] — 2026-09-11 — Platform authorization paketi (M6 + M31–M33)

**Baseline (son commit `d8b3906`):** `bash scripts/run_tests.sh` → **207 passed**
(0 skip); çalışma ağacı temiz; **`persopyro_platform` rolü mevcut**
(`rollsuper=f, rolcreatedb=f, rolcreaterole=f, rolbypassrls=f, rolcanlogin=t`) →
M1–M5'teki platform yetki testleri artık **atlanmadan** koşuyor.

### Numaralandırma notu (ölçülmüş — sapma raporlanır)

İstenen "M6–M9 platform authorization paketi" ile `docs/DATABASE.md`'deki M6–M9
satırları **birebir örtüşmüyor**: belgedeki **M7** rol kodu göçü (`manager →
dept_manager`, `tenant_officer`), **M8** `hr.roles.scope_type`, **M9**
`hr.user_roles.scope_org_unit_id` — yani **kiracı RBAC işleri**; ayrıca **M9 `M10`
(departments/organizasyon) iş kalemine bağlıdır**. Bu turda organizasyon ve kiracı
org/employee tabloları **kapsam dışı** olduğu için **belgedeki M7–M9 uygulanmadı**.
Bunun yerine istenen **platform authorization** işleri, numara çakışması olmadan
belgeye **M31–M33** olarak eklendi (M6 zaten platform izin kataloğudur). Belgedeki
M7–M9 tanımları **değiştirilmedi**.

### M6 — Platform izin kataloğu ✅

- `apps/rbac/migrations/0004_od3_platform_permissions.py`: altı kod **ortak
  katalogda** (`hr.permissions`) — `platform.tenant.read`, `platform.tenant.manage`,
  `platform.settings.manage`, `platform.role.assign`, `platform.audit.read`,
  `platform.support.manage` (AUTHORIZATION.md §5.2/§6.2A; kodlar **uydurulmadı**).
  Katalog 29 → **35**; idempotent (`get_or_create`), kodlar sabit/benzersiz
  (`permissions_code_key`), jokersiz.
- **İkinci katalog açılmadı**: `apps/rbac/permission_catalog.py` içinde
  `PLATFORM_PERMISSIONS` ayrı liste; `ALL_PERMISSION_CODES` **değiştirilmedi** →
  `tenant_admin` gibi kiracı şablon rolleri platform izni **almaz** (test).
- **Yetki boşluğu kapatıldı (ölçülmüş):** app rolü `hr.permissions` üzerinde
  öncesi `SELECT/INSERT/UPDATE/DELETE = t/t/t/t`, sonrası **`t/f/f/f`** (+ TRUNCATE
  geri alındı). Aynı daraltma `scripts/02_grant_app_privileges.sql` içinde de var
  (betik yetkiyi geri açmıyor — regresyon testi).

### M31 — Platform default rolleri ve izin bağları ✅

- `platform/0004_od3_platform_soft_delete.py`: `platform.roles`,
  `platform.role_permissions`, `platform.user_roles` tablolarına **`deleted_at`**
  (K25 standardı: silme = `deleted_at`; silinen kayıt yetki vermez) + FK indeksleri.
- `platform/0005_od3_platform_rolleri_ve_izinleri.py`:
  - `superadmin` **6/6**, `platform_admin` **4/4** — izinler **açık bağlarla**;
    **izin devralma (inheritance) YOK** (hiyerarşi yalnız yönetim ilişkisi).
    `platform_admin`'de `settings.manage` ve `role.assign` **yok** → kendini
    superadmin yapamaz, platform ayarlarını değiştiremez.
  - Seed **idempotent** (var olan etkin bağ atlanır, çift kayıt yok); **soft-delete
    edilmiş rol/bağ bulunursa açık hata** (otomatik restore yok); **izin kodu
    katalogda yoksa sessiz geçilmez** (kiracı tarafındaki `MissingPermissionError`
    ile aynı ilke).
  - **Gerçek kullanıcı seed edilmez** (`platform.users` = 0; bootstrap komutu ayrı).
  - Platform rolüne `hr.permissions` üzerinde **SELECT** (paylaşılan katalog, salt
    okunur) — aynısını `scripts/03_platform_roles.sql` yapar; böylece test/sıfırdan
    kurulum ile üretim **aynı** sonucu üretir.
  - Ölçüm: `platform.role_permissions` = **10 satır** (6 + 4).

### M32 — Platform authorization servisi ✅ (`apps/platform/permissions.py`)

- **Tek merkezi çözümleyici:** etkin izin kümesi tek sorguyla (`ETKIN_IZIN_SQL`)
  hesaplanır — **yalnız aktif** kullanıcı (`status='active'`, `is_active`,
  `deleted_at IS NULL`), **yalnız aktif ve süresi geçmemiş** rol ataması, **yalnız
  aktif** rol ve rol-izin bağı. **Soft-delete edilen hiçbir kayıt yetki vermez.**
- **Django yetki sistemi kullanılmaz** (`auth_permission`/`auth_group`/`is_staff`
  yok — kaynak denetimi testi AST ile yorumları hariç tutarak yapar).
- **Tenant RBAC kod yoluna düşülmez:** `apps.rbac` import edilmez, kiracı tabloları
  sorgulanmaz; ortak katalogda **kiracı izni bağlansa bile** `platform.*` süzgeci
  nedeniyle platform yetkisi üretilmez (test).
- **Fail-closed:** karar verilemezse (pasif kullanıcı, süresi geçmiş atama, silinmiş
  rol/bağ, bilinmeyen kod) sonuç **izin yok**; `require_permission` hata verir.
- **Self-elevation koruması:** `assign_role`/`revoke_role` — kullanıcı **kendi**
  rolünü atayamaz/değiştiremez; atama için `platform.role.assign` **ve** atanacak
  rolün **tüm izinleri** gerekir (sahip olmadığı yetkiyi dağıtamaz) → `platform_admin`
  kendini/kimseyi superadmin yapamaz; yönetim hiyerarşisi (üst rol alt rolü atar)
  kontrol edilir; atama `granted_by_id` ile kayıtlanır, kaldırma **soft-delete**
  (denetim izi kalır); çift atama ve soft-delete edilmiş atamanın sessiz canlandırması
  reddedilir.
- **Kullanıcı durumu:** `platform_user_can_authenticate` + `authenticate_platform_token`
  → pasif/silinmiş platform kullanıcısı **yetki alamaz, token kullanamaz,
  kimlik doğrulayamaz** (login uç noktası bu pakette **yok**; kapı servis katmanında).

### M33 — İlk platform SUPERADMIN bootstrap ✅

`manage.py create_platform_superadmin --email ... --reason ... [--password-stdin]`

- Django Admin **kullanılmaz**; gerçek kullanıcı migration'a **seed edilmez**.
- **Varsayılan parola yok**; parola yalnızca `--password-stdin` veya etkileşimli
  `getpass` ile alınır — **`--password` seçeneği bilinçli olarak yok** (komut
  satırına/süreç listesine sızmaz); parola **hash'lenir** (Argon2id; Django
  `PASSWORD_HASHERS`), **ham parola loglanmaz/gösterilmez**; Django parola
  doğrulayıcıları (min 10 + benzerlik/yaygın/yalnız-sayı) dayatılır.
- **Sessiz yükseltme yok:** e-posta zaten varsa (aktif, pasif **veya soft-delete**)
  komut **açık hata** verir; mevcut kullanıcıya superadmin rolü eklenmez, parolası
  ezilmez. Atama `granted_by_id = NULL` + **zorunlu gerekçe (≥10 karakter)** ile
  kayıtlanır (bootstrap = açık yönetim operasyonu; aktör henüz yok).
- Platform şemasına erişemeyen rolle çalıştırılırsa **fail-closed** hata verir.

### Ölçümler (canlı DB)

- `hr.permissions`: 35 kod (6'sı `platform.*`); app rolü `t/f/f/f`.
- `platform.role_permissions`: superadmin 6, platform_admin 4, toplam **10**;
  `platform.users` **0**; `platform.roles` **2**.
- Platform rolü: `platform.*` altı tabloda `SELECT/INSERT/UPDATE/DELETE = true`
  (allowlist); `hr.permissions` SELECT `true`, INSERT `false`; `hr.tenants`
  SELECT/INSERT/UPDATE/DELETE **`false`**; `hr.users`/`hr.audit_logs`/`hr.api_tokens`
  ve `auth_*`/`django_admin_log` **`false`**; `platform` şema `CREATE` **`false`**;
  `rolsuper=f, rolbypassrls=f`.

### Doğrulama

- **Testler:** **244 passed, 0 skipped** (baseline 207 → +37 yeni test; iki yeni
  dosya: `tests/test_platform_authorization.py`, `tests/test_platform_bootstrap.py`).
  Self-elevation, soft-delete (rol/atama/bağ), pasif kullanıcı, inheritance yokluğu,
  Django-auth/tenant-RBAC ayrılığı, SUPERADMIN≠PII, allowlist, NOBYPASSRLS,
  hr.tenants yazma yasağı, seed idempotentliği ve bootstrap kuralları test edildi.
- **Sıfırdan kurulum:** `scripts/verify_fresh_install.sh` (yeni **4b/8** adımı:
  M6–M8 durumu) → tüm adımlar OK, **244 passed**, `SONUÇ: OK`, kalan geçici
  veritabanı **0**; `/admin/` 404, health/ready 200, token 201 → logout 204 → 401.
- **Mevcut DB upgrade:** `rbac/0004`, `platform/0004`, `platform/0005` uygulandı.
  NOT (şeffaflık): `platform/0005` uygulandıktan sonra dosyaya katalog yetkisi
  bloğu eklendiği için migration **geri alınıp yeniden uygulandı**
  (`migrate platform 0004` → `migrate.sh`); yani ters yön de gerçek veriyle denendi.
- **HTTP smoke (canlı DB):** health 200, ready 200, `/admin/` **404**, giriş 201,
  çıkış 204, çıkış sonrası aynı token 401, geçersiz token 401, askıdaki kiracı 403.
- **Kalite:** `ruff check .` → *All checks passed*; `bandit -q -r apps config` →
  **0 bulgu**; `manage.py check` temiz; `makemigrations --check` → *No changes
  detected*.

### Kalan / doğrulanmayan

- Platform **login/token uç noktaları yok** (bilinçli: bu paketin kapsamı dışı) →
  `authenticate_platform_token` HTTP seviyesinde test edilmedi, servis seviyesinde
  test edildi.
- Platform → kiracı verisi erişimi (talep/onay/süre/denetim) **M17–M19**'da; bu
  pakette platform rolüne kiracı tablolarında **yetki verilmedi**.
- Belgedeki **M7–M9 (kiracı RBAC + organizasyon bağımlılığı)** bu turda **uygulanmadı**
  (organizasyon modülü kapsam dışı).

### SECURITY FINDING

**Yok.** Aksine bir açık kapatıldı: kiracı runtime rolü (`persopyro_app`) global izin
kataloğunu (`hr.permissions`) **yazabiliyordu** (INSERT/UPDATE/DELETE açıktı,
`ALTER DEFAULT PRIVILEGES` kaynaklı) — M6 ile salt okunur hâle getirildi ve
regresyon testine bağlandı.

### OPEN DECISION

**Yok.** Paket kapalı kararlarla uygulandı (OD3/OD19/OD21/OD22 + AUTHORIZATION.md
§4.3/§5.2/§6.2A). Not: `platform.access_requests`/`access_grants` (OD1/OD4, M18–M19)
hâlâ ayrı iş kalemidir; bu turda açılmadı.

## [0.18.0] — 2026-09-11 — Platform temeli (M1–M5) + kalıcı sıfırdan kurulum doğrulaması

**Baseline (son commit `45db412`):** `bash scripts/run_tests.sh` → **171 passed**;
çalışma ağacı temiz; migration durumu tam uygulanmış (`accounts/0004`).

Bu girişte **iki kontrollü iş** yapıldı: (A) sıfırdan kurulum doğrulamasının kalıcı
betiği, (B) `docs/DATABASE.md` M1–M5 platform temel şeması. **Kapsam dışı ve
dokunulmadı:** organizasyon tabloları, employee/department/position, yeni tenant RBAC
tasarımı, açık OD5–OD18 kararları, mevcut tenant RLS politikaları. Hiçbir uygulanmış
migration değiştirilmedi; **hiçbir yere `BYPASSRLS` verilmedi**.

### A — `scripts/verify_fresh_install.sh` (yeni, kalıcı) ✅

Geçici `/tmp/fresh_verify.sh` mantığı repoya taşındı ve sertleştirildi:

- **Disposable DB:** `persopyro_verify_<UTC-zaman>_<pid>` (rastgele/benzersiz); hedef
  ad her kritik komutta **açıkça** verilir; `.env`'in `POSTGRES_DB`'sini ezmesi
  engellenir (`DJANGO_ENV_FILE=/dev/null` + açık `POSTGRES_*` env'i).
- **Güvenlik kalkanı:** `hedef_db_dogrula()` hedef adı `persopyro_verify...` desenine
  uymuyorsa **veya canlı DB'ye eşitse** betiği anında durdurur; canlı DB asla DROP/RESET
  edilmez.
- **Cleanup trap:** hata olsa da çalışır (EXIT trap) — runserver süreci + benzersiz
  port temizlenir, geçici DB (`WITH (FORCE)`) ve `test_<db>` silinir, yeniden denenir,
  kalan sayısı raporlanır, geçici `.env`/günlük silinir.
- **Ölçüm sağlamlığı:** yok olan tablo yüzünden patlamaz (`to_regclass`); süreç/port
  artığı kalmaz (benzersiz port + PID + `pkill` yedek kalkanı).
- **Kapsam:** migrate sıfırdan → `createcachetable` → `scripts/02_grant_app_privileges.sql`
  → (varsa süper kullanıcı ile `scripts/03_platform_roles.sql`) → yetki ölçümleri
  (app platform'da yetkisiz; OD19 dört teknik tablo; M30 `api_tokens`; RLS/FORCE ve
  politika ifadeleri) → `manage.py check` + `makemigrations --check` + denetim
  komutları → demo verisi + **gerçek HTTP** (health 200, ready 200, `/admin/` 404,
  token 201 / logout 204 / aynı token 401) → **tam test suite** → özet + temizlik.
- **CI uyumu:** mutlak geliştirici yolu yok (repo kökü betiğin konumundan, `PYTHON`
  otomatik: `.venv` varsa o, yoksa `python3`); `SUPER_USER_CMD`, `VERIFY_PORT`,
  `KEEP_DB`, `SKIP_TESTS` değişkenleriyle yapılandırılabilir.
- **Ölçüm (bu makinede, gerçek çalıştırma):** **205 passed, 2 skipped**; tüm adımlar
  `OK`; sonuç `SONUÇ: OK — sıfırdan kurulum doğrulandı (canlı veritabanı değiştirilmedi)`;
  **kalan geçici veritabanı = 0**; açık süreç/port yok.

### M1 — `platform` şeması + `persopyro_platform` rolü ve yetkileri ✅

- Şema **migration ile** oluşturulur (`CREATE SCHEMA IF NOT EXISTS platform
  AUTHORIZATION persopyro_owner`; `REVOKE ALL ... FROM PUBLIC`) → sıfırdan kurulumda
  tek başına çalışır.
- Rol ve yetki turu **`scripts/03_platform_roles.sql`** (yeni, **süper kullanıcı**
  gerektirir: `CREATE ROLE`; `persopyro_owner` `NOCREATEROLE`'dur). Betik saf SQL
  (psql'e özgü komut yok) ve **idempotent**; parola **atamaz** (sır repoya yazılmaz).
- Rol: `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`. `PUBLIC` şemada yetkisiz.
- Yetki turu **tablo bazlı allowlist** (`users, roles, role_permissions, user_roles,
  tokens, tenant_registry`); **`GRANT ALL ON ALL TABLES IN SCHEMA platform`
  kullanılmaz** ve platform için **`ALTER DEFAULT PRIVILEGES` tanımlanmaz**.
- `persopyro_app` (kiracı runtime yolu): platform şemasında **USAGE dâhil hiçbir
  yetki yok** — `scripts/02_grant_app_privileges.sql`'e açık **daraltma** bloğu
  eklendi (yeni platform bölümü).

### M2 — `platform.users` / `roles` / `role_permissions` / `user_roles` ✅

`apps/platform/migrations/0001_od3_platform_users_roles.py` (ham SQL).

- `platform.users`: **`tenant_id` kolonu yok**, **`hr.users` FK'si yok**,
  `is_staff`/`is_superuser`/`groups` **yok** (OD19/OD3: Django yetki alanları platform
  yetkisi değildir); `public_id` = `plt-<8×Crockford>` (rezerve `plt`, OD27/OD28);
  e-posta global UNIQUE + küçük harf `CHECK`; kilitlenme/MFA kolonları.
- `platform.roles`: kodlar **kapalı küme** (`CHECK code IN ('superadmin',
  'platform_admin')`) + UNIQUE; seed **idempotent** — **demo kullanıcıya bağlanmaz**.
- `platform.role_permissions`: FK → `platform.roles` + **`hr.permissions`** (paylaşılan
  katalog); **`hr.roles`'a bağlanmaz**.
- `platform.user_roles`: FK → `platform.users` + `platform.roles`; `expires_at`
  opsiyonel ama `> granted_at`.
- `apps/platform/` uygulaması: **ORM modeli yok** (platform nesneleri `platform`
  şemasında; Django'nun `db_table` şema hack'i yerine ham SQL + servis katmanı
  bilinçli seçildi), `constants.py` (kapalı kümeler) + `services.py` (OD22 token
  kuralları).

### M3 — `platform.tokens` (OD22 birebir) ✅

`0002_od22_platform_tokens.py` + `apps/platform/services.py`.

- Ham token **saklanmaz**: yalnızca SHA-256 özeti, `UNIQUE`; düz metin yalnızca
  üretim anında döner.
- `platform_user_id` (owner) ve `purpose` (≥10 karakter) zorunlu; `scope text[]`
  NOT NULL **ve boş olamaz** (`cardinality >= 1`), joker `*` yasak; `token_kind`
  kapalı küme.
- `expires_at` **NOT NULL** + **TTL tavanı DB `CHECK`'iyle**: `interactive` ≤ 24 saat,
  `service` ≤ 90 gün → **süresiz token imkânsız**; `expires_at > created_at`.
- İptal **tek yönlü** (`revoked_at`/`revoked_reason`, kapalı küme, ikisi birlikte
  NULL/NOT NULL); kayıt **silinmez**; kapsam/purpose/süre/tür/hash/owner **değişmez**
  (tetikleyici).
- **`service` token superadmin izin kümesiyle üretilemez** — `platform.role_permissions`
  + `hr.permissions` üzerinden kontrol eden `BEFORE INSERT` tetikleyicisi.
- Endpoint **eklenmedi** (bu paketin kapsamı değil); yalnızca şema/guard/servis/test.

### M4 — `platform.tenant_registry` + tek yönlü yansı (OD20) ✅

`0003_od20_tenant_registry.py`.

- Yalnızca **minimum** alanlar: `tenant_id, slug, domain, status, max_users,
  max_storage_mb, plan_code, billing_ref, internal_note, created_at, updated_at,
  deleted_at`. **PII kopyalanmaz** (`name/legal_name/tax_number/email/phone/address/
  country/city/settings` yok) — test kolon kümesini tam olarak sabitler.
- `hr.tenants` **canonical** kalır; yansı `platform.sync_tenant_registry()`
  `SECURITY DEFINER` tetikleyicisiyle **aynı işlem içinde** yazılır; fonksiyonun
  `search_path`'i **sabit** (`pg_catalog, pg_temp`) ve tüm nesneler tam nitelikli
  (search_path ele geçirme / enjeksiyon yüzeyi yok).
- **Fail-closed kanıtı:** yansı yazımı başarısız olursa (test: kasten çakıştırılmış
  registry `slug`'ı) kiracı INSERT'i **tümden geri alınır** — `hr.tenants` ve
  `tenant_directory`'de satır oluşmaz (regresyon testi).
- **Ters yön yok:** platform rolü `hr.tenants` üzerinde INSERT/UPDATE/DELETE
  yetkisine sahip değil (ölçüm + test); platform fonksiyonlarının gövdesinde
  `hr.tenants` yazımı yok.
- `plan_code`/`billing_ref`/`internal_note` **tek sahibi platformdur** — yansı bu
  alanları **ezmez** (test).
- Backfill: mevcut kiracılar (ölçüm: 2 kiracı — `demo` aktif, `askida` suspended)
  registry'ye taşındı; dizin ve registry satır sayıları eşit.

### M5 — `hr.tenants.public_id_prefix` + `hr.users.public_id` (OD25/OD28) ✅

`tenancy/0008_od25_public_id_helpers.py` (fonksiyonlar) →
`tenancy/0009_od25_public_id_columns.py` (prefix) → `accounts/0005_od25_user_public_id.py`
(public ID). M5 iki uygulamaya dağıldığı için üç migration'a bölündü; sıra bağımlılığı
ölçülerek belirlendi (aşağıdaki not).

- `hr.tenants.public_id_prefix`: nullable ekle → **mevcut kiracılar geri dolduruldu**
  → `NOT NULL` + **tam UNIQUE** (`tenants_public_id_prefix_key`) + format `CHECK`
  (`^[a-z0-9]{3,20}$`) + rezerve `CHECK` + **immutability tetikleyicisi**
  (`tenants_public_id_prefix_guard`); üretim tetikleyicisi
  (`tenants_public_id_prefix_assign`, slug'tan türetir).
- **Kalıcı rezerve:** UNIQUE **tam** (kısmi değil) → soft-delete edilen kiracının
  prefix'i yeniden tahsis edilmez (test).
- `hr.users.public_id`: `<prefix>-<8×Crockford>`; **global UNIQUE indeks**
  (`users_public_id_uniq_idx`), format `CHECK` (`^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$`),
  immutability tetikleyicisi; üretim `hr.generate_public_id(tenant_id)` +
  `hr.crockford_suffix(n)` (alfabe `I/L/O/U` **hariç**).
- **Fail-closed:** kiracı bağlamı yoksa `generate_public_id` `NULL` döner →
  `NOT NULL` ihlali; sessizce kapsamsız kimlik üretilmez (test).
- **Rezerve adlar** (`www, api, app, admin, portal, mail, support, hr, platform, plt,
  system`) hem slug hem prefix olarak DB `CHECK`'iyle reddedilir (`hr.is_reserved_name`).
- **Slug/rebrand değişimi kimliği kaydırmaz:** prefix ve mevcut kullanıcı
  `public_id`'leri sabit kalır; yeni kullanıcı **eski** prefix'le üretilir (test).
- Ölçüm (canlı DB, geri doldurma sonrası): prefix'ler `demo`, `askida`; kullanıcı
  kimlikleri `demo-3A8EAQ9B`, `askida-1CY29715`; `public_id` boş kullanıcı **0**.

### Yetki ölçümü (canlı DB) — platform ve korunan daraltmalar

- `persopyro_app` → `platform` şeması: USAGE **`false`**; altı platform tablosunda
  SELECT/INSERT/UPDATE/DELETE **hepsi `false`** (ölçüm + test).
- OD19 daraltmaları korundu: `auth_permission`, `auth_group`, `auth_group_permissions`,
  `django_admin_log` → app rolü tüm yetkilerde `false`.
- M30 daraltmaları korundu: `hr.api_tokens` tablo UPDATE/DELETE `false`, kolon
  UPDATE yalnızca `last_used_at`/`revoked_at`/`revoked_reason` `true`.
- Platform rolü (`persopyro_platform`): ayrı rol, `NOSUPERUSER`/`NOBYPASSRLS`;
  `hr` şemasında **yalnızca** `hr.permissions` SELECT (global katalog); kiracı
  tablolarına yetki **yok** — RLS'e tabi kiracı erişimi erişim-onay katmanıyla
  (M17–M19) açılır: **şema varlığı erişim yetkisi değildir**.

### RLS / BYPASSRLS doğrulaması ✅

- Kiracı RLS politikaları **değişmedi**: 7 tabloda `rls=true` **ve** `force_rls=true`,
  her birinde `tenant_isolation` politikası var ve ifadesi `current_tenant_id`
  içeriyor (test + fresh-install ölçümü).
- `persopyro_app` ve `persopyro_platform`: `rolsuper=false`, `rolbypassrls=false`.
- Platform tablolarına **tenant RLS kopyalanmadı** (platform şeması RLS'siz; kiracı
  verisi taşımaz) — kiracı bağlamı enjekte ederek platform verisine ulaşılamaz.

### Sıfırdan kurulum / mevcut DB yükseltme / HTTP smoke

- **Mevcut DB upgrade:** `bash scripts/migrate.sh` → `tenancy/0008`, `tenancy/0009`,
  `accounts/0005`, `platform/0001`, `platform/0002`, `platform/0003` uygulandı;
  ardından 02 yetki betiği tazelendi.
- **Sıfırdan kurulum:** `scripts/verify_fresh_install.sh` → **205 passed, 2 skipped**;
  `/admin/` 404, health/ready 200, token 201 → logout 204 → aynı token 401; geçici
  DB'ler silindi (kalan 0).
- **HTTP smoke (canlı DB, `scripts/smoke.sh`):** health 200, ready 200, `/admin/` 404,
  giriş 201, çıkış 204, çıkış sonrası token 401, geçersiz token 401, askıdaki kiracı
  403 — **bozulma yok**.

### Kalite

`bash scripts/run_tests.sh` → **205 passed, 2 skipped** (baseline 171 → +34 yeni test);
`ruff check .` → *All checks passed*; `bandit -q -r apps config` → **0 bulgu**;
`manage.py check` temiz; `makemigrations --check --dry-run` → *No changes detected*.

### Bilinçli sınırlar / doğrulanmayanlar (dürüstlük kaydı)

1. **`persopyro_platform` rolü süper kullanıcı adımına bağlıdır.** Bu ortamda sudo
   etkileşimli parola istediği için `scripts/03_platform_roles.sql` **çalıştırılmadı**;
   rol oluşturma ve platform rolü yetki ölçümü testleri bu yüzden **atlandı (2 skip)**.
   Betik ve testler hazırdır: `sudo -u postgres psql -d persopyro -f
   scripts/03_platform_roles.sql` çalıştırıldığında testler atlanmadan geçer.
   Şema/tablolar, `persopyro_app` daraltmaları, RLS ve registry davranışı **bu adımdan
   bağımsız olarak** doğrulandı.
   **Ek kanıt (rol dışı kısım gerçekten çalıştırıldı):** geçici DB'de (migrate
   uygulanmış) `psql -f scripts/03_platform_roles.sql` sahip rolüyle çalıştırıldı;
   **tek hata türü** `persopyro_platform` rolünün yokluğundan kaynaklanan
   `permission denied to create role` + `role "persopyro_platform" does not exist`.
   Başka hata/sözdizimi sorunu **yok**: şema/blok/allowlist döngüsü/fonksiyon turu ve
   son doğrulama bloğu koştu (idempotent), geçici DB silindi (kalan 0). Yani betik
   yalnızca süper kullanıcı adımına bağlıdır.
2. **Platform → kiracı erişimi bu pakette yoktur** (kasıtlı): platform rolüne kiracı
   tablolarında yetki verilmedi; erişim talebi/onay katmanı (M17–M19) ayrı iş kalemi.
3. **`hr.generate_public_id` çakışma denemesi RLS görünürlüğüyle sınırlıdır**
   (fonksiyon `SECURITY INVOKER`); global tekillik garantisi UNIQUE indekstir,
   çakışmada INSERT **fail-closed** hata verir (olasılık ~32⁻⁸).
4. **Sıra sapması (ölçülmüş, karar gereği değil teknik zorunluluk):** M2 migration'ı
   `hr.crockford_suffix` kullandığı için **M5'in yardımcı fonksiyonları M2'den önce**
   çalışır (`tenancy/0008` → `platform/0001`). M5'in kolon işleri farklı uygulamalara
   ait olduğu için iki migration'a bölündü.

### SECURITY FINDING

**Yok.** Bu turda beklenmeyen yeni yetki yolu, RLS gevşemesi veya yetki yükseltme
yüzeyi bulunmadı; platform sınırı **ölçüm + testle** sabitlendi (app için sıfır
platform yetkisi, `NOBYPASSRLS`, allowlist yetki turu, fail-closed registry yansısı).

### OPEN DECISION

**Yok.** M1–M5 dokümanlardaki kapalı kararlarla (OD3/OD20/OD22/OD25/OD28) uygulandı;
yeni karar gerekmedi. Not: M5'in `hr.permissions`'a `platform.*` kodları ekleyen
**M6** iş kalemi hâlâ açıktır — bu turda kapsam dışı bırakıldı.

## [0.17.0] — 2026-09-11 — OD19 uygulandı: Django Admin ve `auth_*` paralel yetki yolu kapatıldı (M22–M22d)

**Baseline (son commit `2e38952`):** `bash scripts/run_tests.sh` → **150 passed**
(beklenen değerle uyumlu). Bu girişte yalnızca M22–M22d kapsamı uygulandı; platform
şeması migrationlarına, organizasyon modülüne ve yeni RBAC tasarımına dokunulmadı.

### M22 — Django Admin kaldırıldı ✅

- `config/urls.py`: `from django.contrib import admin` ve `path("admin/", ...)`
  **kaldırıldı**; yalnızca API yolları kaldı.
- `config/settings.py`: `"django.contrib.admin"` **INSTALLED_APPS'ten çıkarıldı**
  (gerekçe koda yorum olarak yazıldı: admin'in yetki kapısı `is_active and is_staff`
  kendi RBAC'imizi atlar). `django.contrib.auth` **kaldı** — parola hash'leme, oturum
  kimlik doğrulaması ve `AbstractBaseUser` altyapısı için gereklidir.
- **Ölçüm:** `GET /admin/` ve `GET /admin/login/` → **404** (`resolve()` →
  `Resolver404`); uygulama kayıtlarında `django.contrib.admin` yok; kodda
  `admin.site.register` ya da `admin.py` **yok**; admin template/static bağımlılığı
  yok (`TEMPLATES` içinde admin bağlam işlemcisi hiç eklenmemişti).
- **Şema farkı (beklenen):** admin uygulaması çıktığı için **yeni kurulumda
  `django_admin_log` tablosu hiç oluşmaz**; mevcut veritabanında tablo (boş) kalır —
  silinmedi (geri dönüşü kolay, veri kaybı yok), yetkileri geri alındı.

### M22b — `auth_*` / `django_admin_log` yetkileri kapatıldı ✅

- **Önce ölçüm (körlemesine REVOKE yapılmadı):** `has_table_privilege` ile
  `persopyro_app`'in bu dört tabloda **SELECT/INSERT/UPDATE/DELETE** yetkisi olduğu
  görüldü (Django teknik tabloları `hr` şemasında; `public` şeması boştur).
- **Uygulama:** yeni migration
  `apps/accounts/migrations/0004_od19_is_staff_ve_django_auth_yetkileri.py` →
  `REVOKE ALL` (dört tablo, `to_regclass` korumalı: yeni kurulumda
  `django_admin_log` yoktur) + `scripts/02_grant_app_privileges.sql` içine **aynı**
  daraltma bloğu.
- **Neden iki yerde:** betik her migrate sonrası çalışır ve `ALTER DEFAULT
  PRIVILEGES` + `GRANT ... ON ALL TABLES` ile geniş DML verir; daraltma blokları o
  satırdan **sonra** gelmezse yetkiler sessizce geri açılırdı (bu turda ölçüldü ve
  regresyon testine bağlandı).
- **Yetki turu (owner/runtime ayrımı):** migration sahibi rol (`persopyro_owner`) ile
  çalışır, daraltma **runtime uygulama rolüne** (`persopyro_app`) uygulanır; roller
  birbirine karıştırılmaz (`SET ROLE` üyeliği yoktur — ölçüldü).
- **Ölçüm (canlı DB ve sıfırdan kurulum):** dört tabloda
  `SELECT/INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER` → **hepsi `false`**
  (`SELECT` dahil — uygulama bu tabloları çalışma anında okumuyor); tam test suite ve
  gerçek HTTP bunu doğruladı. `api_tokens` (M30) ve `tenant_directory` daraltmaları
  korunuyor; `django_session` DML'i **gerekli olduğu için** duruyor.

### M22c — `create_superuser` ve `is_staff` ✅

- **Veri ölçümü (önce):** `hr.tenant_directory` üzerinden tüm kiracılar dolaşılarak
  ölçüldü → 2 kiracı, 2 aktif kullanıcı, **`is_staff=True` kayıt: 0**. Kayıt
  olmadığı için bayrak **körlemesine değiştirilmedi**; migration yine de bir ön
  kontrol adımı içerir: kayıt bulursa migration **açık hata ile durur** ve
  hesapları listeler.
- **`create_superuser` artık kullanıcı üretmez:** `DjangoAdminRemovedError`
  yükseltir. Bu hata **iki atalıdır** (`NotImplementedError` + `ValidationError`) —
  ölçüm: bizim `createsuperuser` komut override'ı gölgede kalır (komut araması
  `INSTALLED_APPS` sırasına göre yapılır), Django'nun kendi komutu
  `ValidationError`'ı temiz `CommandError`'a çevirdiği için hata tipi bilinçli
  seçildi. Ölçüm: `manage.py createsuperuser --email ... --noinput` →
  `CommandError: create_superuser desteklenmiyor (OD19 — kapalı) ...` (çıkış kodu 1).
- **`is_staff` invariantı üç katmanlı:** (1) `User.save()` guard'ı — `is_staff=True`
  görürse açık hata; (2) veritabanı kısıtı `users_is_staff_disabled CHECK
  (NOT is_staff)` (ham SQL ile de açılamaz); (3) denetim komutu
  **`manage.py audit_is_staff`** — kiracıları dolaşıp ihlali ve kısıtın yokluğunu
  raporlar, ihlalde çıkış kodu 1 (cron için uygun). Ölçüm: canlı DB ve sıfırdan
  kurulumda `is_staff_true=0`, `DB kısıtı: var`.
- `is_superuser` alanı **eklenmedi**; kullanıcı modeli `PermissionsMixin`
  kullanmadığı için `groups` / `user_permissions` **yoktur** → Django model izinleri
  Persopyro izni üretemez (testle doğrulandı).

### M22d — Sıfırdan kurulum ve gerçek doğrulama ✅

- **A) Mevcut veritabanı (upgrade):** `bash scripts/migrate.sh` →
  `accounts.0004_od19_is_staff_ve_django_auth_yetkileri` uygulandı, yetkiler
  tazelendi; ölçüm ve testler yeşil.
- **B) Temiz/geçici veritabanı (fresh install, gerçek üretim DB'sine dokunulmadı):**
  geçici DB oluşturuldu → **migration'lar sıfırdan** uygulandı → `createcachetable` →
  yetki scripti → ölçüm → demo verisi → **gerçek HTTP** (uygulama rolüyle) → tam test
  suite → geçici DB'ler silindi (kalan: 0).
  - Sıfırdan kurulumda: `django_admin_log` **hiç oluşmadı** (admin migration kaydı
    **0**); `auth_permission`/`auth_group`/`auth_group_permissions` var ama app
    rolünde **hiç yetki yok**; `users_is_staff_disabled` kısıtı **var**;
    `api_tokens` SELECT/INSERT, `django_session` tam DML.
  - HTTP (app rolü): `/admin/` **404**, `/admin/login/` **404**, `/api/health`
    **200**, `/api/ready` **200**, `POST /auth/token` **201**, logout **204**,
    logout sonrası aynı token **401**.
  - Tam suite temiz DB üzerinden: **171 passed**.
  - **Ölçüm uyarısı (betik yazımı):** `has_table_privilege(rol, 'tablo', ...)` metin
    biçimi tablo yoksa **hata verir**, `false` dönmez → ölçüm `to_regclass` ile
    korunmalı. Ayrıca `. ./.env` `POSTGRES_DB`'yi ihraç ettiği için komutlara hedef
    veritabanı **açıkça** verilmelidir (aksi halde komut sessizce canlı DB'ye gider —
    bu turda fark edildi ve düzeltildi; canlı DB'de yalnızca no-op `migrate` oldu).

### Test

- Yeni **`tests/test_od19_django_admin_kapali.py`** (21 test): `/admin/` + alt
  yolları **404**/**Resolver404** · `django.contrib.admin` uygulama listesinde yok
  (auth kalıyor) · `is_staff=True` uygulama guard'ı, sonradan açma denemesi ve **ham
  SQL** (DB kısıtı) · kısıtın şemada varlığı (sıfırdan kurulumun sonucu) ·
  `audit_is_staff` çıktısı · `create_superuser` açık hata · hata tipinin CLI hata
  yoluna uygunluğu · `createsuperuser` komutu `CommandError` · tenant girişi + token
  akışı bozulmadı (200/201/200/200) · RBAC izin akışı bozulmadı (403 → 200) · Django
  yetki kavramları Persopyro izni üretmiyor · **dört teknik tabloda app rolü hiç
  yetkisiz** (parametrik, 7 yetki türü) · **grant scripti daraltmayı geri açmıyor**.
- **Güncellenen mevcut test:** `tests/test_smoke.py::test_admin_urlleri_cozulur` →
  `test_admin_yolu_yok` (karar tersine çevrildi: route **yok**, 404 beklenir).
- **Final:** `bash scripts/run_tests.sh` → **171 passed** (150 + 21).

### Ölçüm / doğrulama

- `manage.py check` temiz · `makemigrations --check` → "No changes detected" ·
  `ruff check` → **All checks passed** · `bandit` → **0 bulgu**.
- `bash scripts/migrate.sh` → `accounts.0004` uygulandı.
- **HTTP duman testi (`bash scripts/smoke.sh`, canlı DB, gerçek `runserver`):**
  `/api/health` 200 · `/api/ready` 200 · **`/admin/` 404 (beklenen — OD19)** ·
  token 201 · logout 204 · logout sonrası aynı token 401 · geçersiz token 401 ·
  askıdaki kiracı 403. Sunucu kapatıldı; açık process/port bırakılmadı.
- **Doküman tutarlılığı:** `docs/SECURITY.md` (T18 kapandı, §3 admin/`is_staff`
  maddeleri, §5 tek yetkilendirme yolu, G8), `docs/DATABASE.md` (A.1.2 tablosu, M22–
  M22d durumu, `users` kartı, migration listesi, H3), `docs/AUTHORIZATION.md` (OD19
  "TAMAMLANDI"), `docs/ARCHITECTURE.md` (K23).

### SECURITY FINDING

**Yeni bulgu yok.** Beklenmeyen yeni bir yetkilendirme yolu görülmedi: Django
`auth_*` tabloları artık uygulama rolü için **okunamaz ve yazılamaz**; `is_staff`,
Django grupları ve model izinleri Persopyro izni üretmiyor (testle doğrulandı).
Kapatma sırasında ölçülen iki nokta bulgu değil, kayıtlı davranıştır: (a) mevcut
veritabanında boş `django_admin_log` tablosu kalır (yetkisiz; yeni kurulumda hiç
oluşmaz); (b) `django.contenttypes`/`django_session` gibi diğer teknik tablolar bu
kararın kapsamı dışındadır (M22b dört tabloyu sayar).

### Değişmeyenler (bilinçli)

- Platform şeması migrationları (M1–M5/M17–M21), organizasyon modülü (OD5), yeni RBAC
  tasarımı, `permissions` yazma yetkisinin geri alınması (M6), `SoftDeleteManager`
  standardı (M25), F10 typosquatting (`docs/SECURITY.md`), Django Admin'i ileride
  açma koşulları (OD19'daki ön koşullar).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `config/urls.py` | `admin/` route'u ve admin import'u kaldırıldı |
| `config/settings.py` | `django.contrib.admin` uygulama listesinden çıkarıldı (gerekçe yorumu) |
| `apps/accounts/models.py` | `create_superuser` fail-closed (`DjangoAdminRemovedError`), `User.save` `is_staff` guard'ı, `users_is_staff_disabled` kısıtı, `is_staff` help_text |
| `apps/accounts/migrations/0004_od19_is_staff_ve_django_auth_yetkileri.py` | **Yeni migration**: ölçümlü ön kontrol + CHECK kısıtı + `REVOKE ALL` (M22b/M22c) |
| `apps/accounts/management/commands/audit_is_staff.py` | **Yeni**: `is_staff` denetim sorgusu (ihlalde çıkış kodu 1) |
| `apps/accounts/management/{__init__.py,commands/__init__.py}` | **Yeni**: komut paketi |
| `scripts/02_grant_app_privileges.sql` | Django teknik tablolarının yetkilerini geri alan blok (M22b) |
| `scripts/smoke.sh` | `/admin/` → 404 adımı |
| `tests/test_od19_django_admin_kapali.py` | **Yeni** (21 regresyon testi) |
| `tests/test_smoke.py` | `/admin/` testi tersine çevrildi (404/beklenen) |
| `docs/SECURITY.md`, `docs/DATABASE.md`, `docs/AUTHORIZATION.md`, `docs/ARCHITECTURE.md` | OD19/M22–M22d kapandı |
| `docs/CHANGELOG.md` | Bu kayıt |

---

## [0.16.0] — 2026-09-11 — F8 ve F11 kapatıldı (OD26 kodlandı: kapsamlı/iptal izli token + kiracı erişimi)

**Baseline (son commit `cdeeb0e`):** `bash scripts/run_tests.sh` → **110 passed**.
Bu girişte OD26'nın (kapalı karar) uygulaması ve **F11** kodlandı; organizasyon
modülüne, Django Admin M22 işlerine ve başka migration paketlerine dokunulmadı.

### F8 — Tenant API token'ı: kapsam + iptal izi (KAPATILDI ✅)

- **Şema (`M29`/`M30`/`F10` — `apps/accounts/migrations/0003_apitoken_od26.py`):**
  5 kolon (`token_kind`, `purpose`, `scopes`, `revoked_at`, `revoked_reason`),
  6 CHECK, **1 tetikleyici** (`api_tokens_guard_ins`: kapsam doğrulama + değişmezlik
  + tek yönlü iptal), **1 veri adımı** (F10) ve **yetki turu**. Uygulanmış
  migration'lar **değiştirilmedi**.
- **Doğrulama (canlı PostgreSQL ölçümü):** katalogda olmayan kapsam, `platform.*`,
  joker, boş kapsam (aktif satır) ve tekrarlı kapsam **reddediliyor**; `scopes`,
  `purpose`, `expires_at`, `token_kind`, `token_hash` UPDATE'i **reddediliyor**;
  `last_used_at` iptal alanları yazılabiliyor; iptal **geri alınamıyor**.
- **Yetki turu (M30):** uygulama rolü `api_tokens` üzerinde tablo düzeyinde UPDATE
  **edemiyor**, **silemiyor**; yalnızca `last_used_at`/`revoked_at`/`revoked_reason`
  kolonlarını yazabiliyor. Aynı daraltma `scripts/02_grant_app_privileges.sql`
  içine de eklendi — aksi halde migrate sonrası çalışan bu betik "ALL TABLES"
  yetkisiyle daraltmayı **sessizce geri alırdı** (ölçüldü).
- **Veri göçü (F10):** kapsamsız mevcut token'lar `revoked_reason='policy_migration'`
  + `scopes='{}'` + sentinel `purpose` ile **iptal** edilir (asla "tüm izinler"
  sayılmaz). Bugünkü ölçüm: tablo **0 satır** → 0 satır iptal edildi.
- **Kesişim kuralı (yetki):** tek merkez
  `apps/rbac/services/permissions.py::authorized_permission_codes(request)`:
  `etkin izinler = kullanıcının izinleri ∩ token kapsamı`. Token sahibinin sahip
  olmadığı yetkiyi **taşıyamaz**, sonradan verilen izni **kazanmaz**, izin geri
  alınırsa o izni **kullanamaz**. Oturum (session) ile gelen istekte davranış
  değişmez (kapsam yok → kullanıcının izinleri). DRF yetki sınıfları
  (`HasPermissionCodes`, `AnyPermissionCode`) bu tek noktayı kullanır.
- **Üretim servisi:** `apps/accounts/services.py` — `issue_api_token`,
  `revoke_token`, `revoke_tokens`, `revoke_tenant_tokens`,
  `revoke_tokens_for_inactive_user`. `purpose` ≥ 10 karakter, `scopes` boş olamaz,
  joker/`platform.*`/katalogda olmayan kod reddedilir, kapsam **sahibin izinlerinin
  alt kümesi** olmalıdır, TTL tavanı (interactive ≤ 720 sa, `service` ≤ 2160 sa)
  aşılırsa **açık hata** (`400`) döner — sessiz kırpma yok.
- **Uçlar:** `POST /api/v1/auth/token` → **201** (gövde artık `purpose` + `scopes`
  zorunlu; `kind?`, `ttl_hours?`, `label?`); `POST /api/v1/auth/token/logout` →
  **204** ve satır **silinmez** (`revoked_at` + `revoked_reason='logout'` + denetim
  kaydı); yeni `POST /api/v1/auth/token/revoke-all` → sahibinin tüm token'ları
  (`200` + iptal sayısı). İptal edilmiş token her istekte **401**.
- **Revoke nedenleri (kapalı küme):** `logout`, `owner_disabled`, `tenant_disabled`,
  `admin_revoke`, `security_incident`, `policy_migration` — doküman (OD26) esas
  alındı (istenen `manual`/`owner_inactive`/`tenant_inactive` adları kümeye karşılık
  gelir: `admin_revoke` / `owner_disabled` / `tenant_disabled`).

### F11 — Kiracı durumu erişimi kesiyor (KAPATILDI ✅)

- **Tek kaynak:** `apps/tenancy/access.py::tenant_access_allowed()` — izin verilen
  durumlar `active`/`trial`; `suspended`, `canceled` ve `deleted_at IS NOT NULL`
  **fail-closed** reddedilir (kiracı satırı okunamıyorsa erişim kapalıdır).
- **Uygulandığı dört yol:** (1) **giriş** — parola denenmeden `403 tenant_unavailable`
  (hem oturum hem token ucu; deneme denetim kaydına yazılır); (2) **istek hattı /
  oturum** — `403` + oturum anahtarı temizlenir; (3) **API token doğrulaması** —
  iptal edilmemiş token bile `401`; (4) **istek düzeyinde yetki kararı** — kiracı
  erişilemezse izin kümesi boş döner.
- **İstek hattı düzeltmesi (ölçümle zorunlu çıktı):** token ile gelen isteklerde
  kiracı bağlamı kurulmuyordu (yalnızca oturum ve alan adı çözümleniyordu). Bağlam
  olmadan RLS'li RBAC tabloları boş döndüğü için **kesişim kuralı işlevsiz** kalırdı.
  `TenantContextMiddleware` artık `Authorization: Token ...` başlığındaki token'ın
  kiracısını da çözer (öncelik: oturum → token → alan adı) ve kaynaklar farklı kiracı
  gösteriyorsa `400 tenant_mismatch` döner. Token kaydı `request._api_token`
  üzerinde paylaşılır (kimlik doğrulama ikinci sorgu yapmaz).
- **Toplu iptal (defense in depth):** `revoke_tenant_tokens(tenant_id, reason=
  'tenant_disabled')` — ancak güvenlik buna **bağlı değildir**: token iptal edilmemiş
  olsa bile kiracı durumu istek sırasında kontrol edilir.
- **Kullanıcı durumu:** token doğrulaması pasif (`inactive`/`archived`) ve
  soft-delete edilmiş kullanıcıyı reddeder; yeni `is_active` kolonu **eklenmedi**
  (mevcut türetilmiş property korunur).
- **RLS gevşetilmedi, `BYPASSRLS` kullanılmadı.**

### Test

- Yeni **`tests/test_token_scope_and_tenant.py`** (24 test): alt küme · sahip
  olunmayan/bilinmeyen/`platform.*`/joker/boş kapsam `400` · kısa `purpose` · TTL
  tavanı · `service` tavanı · kapsam dışı izin **403** · kapsam içi izin **200** ·
  izin geri alınınca token kaybeder · sonradan verilen izni eski token kazanmaz ·
  iptal/süre bitimi **401** · logout satırı silmez + `revoked_at`/`revoked_reason` ·
  logout diğer token'ları etkilemez · `revoke-all` · tek yönlü iptal · askıda/iptal/
  silinmiş kiracıda yeni giriş **403** ve mevcut token/oturum reddi · token doğrulama
  katmanı **401** · `active`/`trial` bozulmaz · pasif/silinmiş kullanıcı **401** ·
  `/api/health` + `/api/ready` **200** · token girişi **201**.
- Yeni **`tests/test_api_tokens_db_guards.py`** (5 test, **ham SQL**): tetikleyici
  kapsam kuralları · iptal edilmiş satırda boş kapsamın meşruluğu (sentinel) ·
  değişmezlik · iptalin geri alınamaması · uygulama rolü yetkilerinin daraltılmış
  olması (M30).
- **Güncellenen mevcut testler (`tests/test_api_auth.py`):** token sözleşmesi
  değiştiği için gövdeye `purpose`/`scopes` eklendi, kullanıcıya izin verildi;
  "logout kaydı siler" testi **"logout satırı korur ve iptal izi bırakır"** olarak
  düzeltildi; süresi geçmiş token testi artık `expires_at`'i UPDATE etmiyor
  (değişmezlik tetikleyicisi) — token doğrudan üretiliyor.
- **Final:** `bash scripts/run_tests.sh` → **150 passed** (110 + 40).

### Ölçüm / doğrulama

- **HTTP duman testi (`bash scripts/smoke.sh`, gerçek `runserver` + gerçek DB):**
  `/api/health` **200**, `/api/ready` **200**, token girişi (**purpose+scopes**) →
  token alındı (**201**), geçerli token ile çıkış **204**, **çıkış sonrası aynı token
  401**, geçersiz token **401**, **askıdaki kiracı** ile giriş **403**. Sunucu
  kapatıldı; açık process/port bırakılmadı.
- `manage.py check` temiz · `makemigrations --check` → "No changes detected" ·
  `ruff check` → **All checks passed** · `bandit` → **0 bulgu**.
- `bash scripts/migrate.sh` → `accounts.0003_apitoken_od26` **uygulandı**; uygulama
  rolü yetkileri tazelendi.
- **Doküman tutarlılığı:** `docs/SECURITY.md` F8/F11 **kapalı**, T21 kapandı, §3.5
  "uygulandı"; `docs/API.md` token/logout/revoke-all ve **201** sözleşmesi; `docs/
  DATABASE.md` `api_tokens` kartı + M29/M30/F10 durumu + yetki tabloları;
  `docs/AUTHORIZATION.md` OD26 "UYGULANDI"; `docs/ARCHITECTURE.md` K32.

### SECURITY FINDING

**Yeni bulgu yok.** Kapatma çalışması sırasında ölçülen iki nokta bulgu değil,
kayıtlı tasarım kararıdır: (a) token isteklerinde kiracı bağlamının istek hattında
kurulması (OD26 §3'ün gereği, yukarıda açıklandı); (b) `revoked_reason` için doküman
kümesinin esas alınması (isim farkları eşlendi).

### Değişmeyenler (bilinçli)

- Organizasyon modülü (OD5), Django Admin kaldırma (M22b–22d), `permissions` yazma
  yetkisinin geri alınması (M6), `SoftDeleteManager` standardı (M25),
  `*.restore` izni (M26'nın kalanı), F10 (typosquatting, `docs/SECURITY.md`).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `apps/accounts/migrations/0003_apitoken_od26.py` | **Yeni migration** (M29/M30/F10; ham SQL + tetikleyici) |
| `apps/accounts/services.py` | **Yeni**: token üretim/iptal servisi (tek yol) |
| `apps/tenancy/access.py` | **Yeni**: `tenant_access_allowed` (F11 tek kaynak) |
| `apps/accounts/models.py` | `ApiToken`: `token_kind`/`purpose`/`scopes`/`revoked_at`/`revoked_reason`, `revoke()`, `is_usable` |
| `apps/accounts/authentication.py` | İptal/süre/kiracı/kullanıcı reddi sırası |
| `apps/accounts/serializers.py` | `TokenIstekSerializer` (`purpose`, `scopes`, `kind`, `ttl_hours`) |
| `apps/accounts/views.py` | Giriş kiracı kontrolü, token üretimi (201), iptal (204), `revoke-all` |
| `apps/accounts/urls.py` | `token/revoke-all` rotası |
| `apps/rbac/services/permissions.py` | `authorized_permission_codes` (kesişim + fail-closed) |
| `apps/rbac/services/__init__.py` | Yeni servis dışa aktarımı |
| `apps/rbac/permissions.py` | DRF sınıfları tek merkezi kararı kullanır |
| `apps/tenancy/middleware.py` | Token'dan kiracı çözümü + `tenant_unavailable` reddi |
| `config/settings.py` | `django.contrib.postgres`, `API_TOKEN_TTL_MAX_HOURS` |
| `scripts/02_grant_app_privileges.sql` | `api_tokens` yetki daraltması (M30) |
| `scripts/_demo_data.py`, `scripts/smoke.sh` | Askıdaki demo kiracı + token/iptal/403 adımları |
| `tests/test_token_scope_and_tenant.py`, `tests/test_api_tokens_db_guards.py` | **Yeni testler** |
| `tests/test_api_auth.py`, `tests/urls.py`, `tests/views.py` | Sözleşme güncellemesi + sonda |
| `docs/SECURITY.md`, `docs/API.md`, `docs/DATABASE.md`, `docs/AUTHORIZATION.md`, `docs/ARCHITECTURE.md` | F8/F11 kapandı, OD26 uygulandı |

---

## [0.15.0] — 2026-09-11 — F7 ve F9 kapatıldı (kod + migration + test)

**Baseline (değişiklikten önce):** `bash scripts/run_tests.sh` → **97 passed**.
Bu girişte ilk kez **kod** değişti; diğer bulgulara (F8/F11/M22/organizasyon)
dokunulmadı.

### Eklenen / değişen

- **F7 — soft-delete yetki çürümesi (KAPATILDI).**
  `apps/rbac/services/permissions.py`: yetki çözümlemesindeki **her sorgu**
  `deleted_at IS NULL` koşulunu **açıkça** uygular (`UserRole` → atama; ardından
  `Role` → rol; `UserPermissionOverride` → istisna). Silinmiş rol, atama, `allow`
  istisnası yetki vermez; silinmiş `deny` istisnası **aktif karar sayılmaz**; etkin
  `deny` yine `allow`'u ezer. Pasif/silinmiş kullanıcı için izin kümesi **boş**
  döner (fail-closed). Gizli bir manager davranışına güvenilmedi — filtre sorguda
  görünür ve test edilebilir.
  `apps/rbac/services/roles.py`: `get_or_create` yerine açık sorgu + **ön kontrol**;
  silinmiş varsayılan rol bulunursa **otomatik restore edilmez, kopya üretilmez**,
  `SoftDeletedDefaultRoleError` yükseltilir (OD21: geri alma açık `*.restore` izni +
  denetim kaydı gerektirir).
- **F9 — denetim satırı bütünlüğü (KAPATILDI).**
  `apps/audit/models.py`: `AuditLog.user` → **`PROTECT`**. Yeni migration:
  `apps/audit/migrations/0002_alter_auditlog_user.py` (mevcut migration'lar
  değiştirilmedi).

### Ölçüm / doğrulama

- **Migration'ın gerçek PostgreSQL sonucu:** `sqlmigrate audit 0002` → **no-op**.
  Nedeni ölçüldü: Django FK DDL'inde `ON DELETE` **yazmaz**
  (`sql_create_fk` şablonu; `on_delete` tamamen ORM katmanında). Veritabanı kısıtı
  bu yüzden zaten `NO ACTION`'dı; migrate öncesi/sonrası değişmedi. Koruma artık
  ORM katmanında: kullanıcı silinmek istendiğinde **hiç SQL üretilmeden**
  `ProtectedError`. Ham SQL DELETE de `NO ACTION` nedeniyle engellenir, ancak FK'lar
  `DEFERRABLE INITIALLY DEFERRED` olduğu için denetim **commit anında** yapılır.
- `audit` migration'ları uygulandı: `[X] 0001_initial`, `[X] 0002_alter_auditlog_user`.
- `persopyro_app` yetkileri yeniden doğrulandı: `audit_logs` → UPDATE: **yok**,
  INSERT/SELECT: var.
- **Ölçüm düzeltmesi (doküman):** `hr.role_permissions` bir **bağ tablosudur** ve
  `deleted_at` kolonu **yoktur** (kolonlar: `id, created_at, updated_at,
  permission_id, role_id, tenant_id`). Önceki F7 metni bu tabloda bir `deleted_at`
  filtresinin eksik olduğunu varsayıyordu; **yanlıştı** — bağ kayıtları fiziksel
  silinir ve silinen kayıt yetki veremez (OD21 bağ tabloları için soft-delete
  öngörmez). Doküman düzeltildi.

### Test

- Yeni **`tests/test_rbac_soft_delete.py`** (8 test): silinmiş rol · silinmiş
  `UserRole` · kaldırılmış rol-izin bağı · silinmiş `ALLOW` · aktif `DENY` kazanır ·
  silinmiş `DENY` karar sayılmaz · silinmiş varsayılan rol otomatik restore
  edilmez · kaldırılmış bağ yeniden kurulabilir.
- Yeni **`tests/test_audit_integrity.py`** (5 test): ORM ile silme → `ProtectedError`
  · ham SQL DELETE → `IntegrityError` (`SET CONSTRAINTS ALL IMMEDIATE`) · denetim
  satırı değişmez (`user_id`, `updated_at`, `changes`) · `persopyro_app` yazma
  yetkisi yok · denetim kaydı `deleted_at` almaz.
- **RED→GREEN kanıtı:** düzeltme geçici olarak geri alındığında F7 testlerinden 4'ü
  kırmızı (`test_silinmis_rol_yetki_vermez`, `test_silinmis_user_role_yetki_vermez`,
  `test_silinmis_allow_override_izin_eklemez`,
  `test_silinmis_deny_aktif_karar_sayilmaz`); düzeltmeyle yeşil.
- **Final:** `bash scripts/run_tests.sh` → **110 passed** (97 + 13). `ruff check`
  temiz, `ruff format` uygulandı, `bandit` 0 bulgu, `manage.py check` temiz,
  `makemigrations --check` "No changes detected".

### Değişmeyenler (bilinçli)

- API token modeli ve F8/OD26 işleri (M29/M30), F11, Django Admin kaldırma (M22b–22d),
  organizasyon modülü, `SoftDeleteManager` standardı (M25: F7 için gerekli değil),
  `*.restore` izni (M26'nın kalan yarısı; silme özelliği yazıldığında).
- Küçük not (bulgu değil): `UserRole.granted_by` / `UserPermissionOverride.granted_by`
  hâlâ `SET_NULL` — fiziksel silmede "kim verdi" bilgisi kaybolur; yetki kararı
  vermediği için F9 kapsamına alınmadı.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `apps/rbac/services/permissions.py` | Açık `deleted_at IS NULL` filtreleri + pasif kullanıcı fail-closed |
| `apps/rbac/services/roles.py` | `get_or_create` kaldırıldı; `SoftDeletedDefaultRoleError` ön kontrolü |
| `apps/audit/models.py` | `AuditLog.user` → `PROTECT` |
| `apps/audit/migrations/0002_alter_auditlog_user.py` | **Yeni migration** (SQL no-op) |
| `tests/test_rbac_soft_delete.py` | Yeni (F7 regresyon) |
| `tests/test_audit_integrity.py` | Yeni (F9 regresyon) |
| `docs/SECURITY.md` | F7/F9 kapandı; T20 kapandı; ölçüm düzeltmesi |
| `docs/DATABASE.md` | `audit` 0001→0002; `role_permissions` soft-delete yok; M20/M25/M26/M28 durumu |
| `docs/AUTHORIZATION.md` | Açık bulgu listesi güncellendi |
| `docs/CHANGELOG.md` | Bu kayıt |

---

## [0.14.0] — 2026-09-11 — OD26 KAPATILDI: tenant API token modeli (F8 çözüm kararı; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Karar (OD26 — KAPALI)

- **Model:** `hr.api_tokens` genişletilir; **yeni/ikinci token sistemi kurulmaz**
  (OD22 ile aynı alan anlamları). Eklenenler: `token_kind` (`interactive`/`service`),
  `purpose` NOT NULL (≥ 10 karakter), `scopes text[]` NOT NULL (boş kapsam yalnızca
  iptal edilmiş satırda; **wildcard yasak**; değerler `hr.permissions.code`'dan
  doğrulanır; **`platform.*` yasak**), `revoked_at` + `revoked_reason` (kapalı küme).
- **Kesişim kuralı (fail-closed):**
  `effective = current_user_permissions ∩ token_scopes` — tek kilit nokta
  (`apps/rbac/permissions.py`). Token sahibinin sahip olmadığı yetkiyi taşıyamaz;
  sonradan verilen izni kazanmaz; izin geri alınırsa o izni kullanamaz → rol
  değişiminde ayrı token iptali gerekmez.
- **TTL:** `interactive` ≤ 30 gün (720 sa), `service` ≤ 90 gün (2160 sa), **süresiz
  yok**; tavan üstü istek açık hatayla reddedilir. `API_TOKEN_TTL_HOURS=720`
  varsayılan olarak korunur.
- **İptal:** `token/logout` artık **silmez, işaretler**; yeni `token/revoke-all`;
  süre bitimi, sahip pasif/silinmiş, **kiracı askıda/iptal/silinmiş**, güvenlik
  olayında toplu iptal. İptal **tek yönlü**, izli (`revoked_at`+`revoked_reason`+
  denetim).
- **Değişmezlik:** `scopes`/`purpose`/`expires_at`/`token_kind` üretimden sonra
  değişmez → değişiklik = iptal + yeniden üretim (sessiz yetki kayması olmaz).
- **Geriye dönük uyumluluk:** mevcut kapsamsız token'lar **"tüm izinler" sayılmaz**;
  migrasyonda **iptal edilir** (`revoked_reason='policy_migration'`, sentinel
  `scopes='{}'`) ve kullanıcı yeniden giriş yapıp kapsamlı token üretir. Ölçüm:
  bugün `hr.api_tokens` **0 satır**.

### Yeni iş kalemleri

- **M29** — `hr.api_tokens` kolonları + CHECK'ler + iki tetikleyici (kapsam
  doğrulama; değişmezlik + tek yönlü iptal).
- **M30** — yetki turu: UPDATE `last_used_at` (+ iptal akışı); `delete()` yolu
  kaldırılır.
- **F10 (veri göçü)** — mevcut token'ların iptali.

### SECURITY FINDING

- **F11 (Yüksek) — Kiracı askıya alınsa/iptal edilse bile erişim kesilmiyor.**
  Ölçüm: `TenantStatus` (`active`/`trial`/`suspended`/`canceled`) tanımlı ama
  **hiçbir kod bu değeri okumuyor**. Askıya alınmış/iptal edilmiş kiracının
  kullanıcıları **yeni giriş yapabiliyor** (dizin satırı yalnızca fiziksel silmede
  kalkar); soft-delete edilmiş kiracıda yeni giriş engelliyor ama **mevcut
  oturum/token çalışmaya devam ediyor** (RLS yalnızca `tenant_id` eşitliğine bakar,
  kiracı durumuna bakmaz). Etki: ticari kontrol (askıya alma/iptal) ve hesap
  kapatmanın güvenlik karşılığı yok. Kapatma yolu OD26 kapsamına eklendi
  (`status NOT IN ('active','trial')` veya `deleted_at IS NOT NULL` → reddet +
  askıya almada toplu token iptali). Kodlanmadı.
- **F8** — çözüm kararı verildi; kod bekliyor. **F9/F10** açık kalmaya devam ediyor.

### Ek düzeltme (doküman hatası, kod değil)

- `docs/API.md` §2 yanlış biçimde `Authorization: Bearer` ve `/api/v1/auth/refresh`
  yazıyordu; kod `Token` anahtar sözcüğünü kullanır ve **refresh ucu yoktur** →
  düzeltildi, token sözleşmesi OD26 kararıyla güncellendi.
- `docs/DATABASE.md`'ye **etiket çakışması** notu eklendi: `F#` bu belgede veri göçü,
  `SECURITY.md`'de güvenlik bulgusu anlamına gelir.

### Yeni OPEN DECISION

- **Yok.** Karar sayıları: kapalı **14** (OD1–OD4, OD19–OD28), açık **14**
  (OD5–OD18). Engelleyici açık karar yoktur; **OD5** organizasyon modülünden önce
  netleşmelidir.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD26 **KAPALI** (9 başlıklı kesin karar: model, kapsam kuralı, TTL/`token_kind`, iptal, migration, F8 iş listesi, çakışmalar, gerekçe/güvenlik etkisi); durum satırları ve sayılar |
| `docs/DATABASE.md` | `api_tokens` kartı hedef modelle güncellendi; B.3 satırı; **M29/M30**; veri göçü **F10**; bölüm I'de OD26 kapalı; etiket çakışması notu |
| `docs/SECURITY.md` | **§3.5 Kiracı API token'ları** (yeni); T21 karar verildi; F8 "çözüm kararı verildi"; **F11** bulgusu; §15'e G10 |
| `docs/ARCHITECTURE.md` | **K32** karar kaydı |
| `docs/API.md` | §2 kimlik doğrulama düzeltildi + token gövdesi/iptal sözleşmesi; §4.1 uç tablosu (`revoke-all`, iptal semantiği) |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma + ölçüm):** `ApiToken` alanları ve `issue()` imzası;
  `TokenLogoutView`'ın `delete()` çağrısı; `authenticate_credentials`'ta
  `revoked_at`/kiracı durumu kontrolünün **yokluğu**; `permission_catalog.py`'de
  **29 izin kodu** ve gerçek ad alanı; `TenantStatus`'un hiçbir kodda okunmadığı;
  `API_TOKEN_TTL_HOURS=720`; `tenant_directory` tetikleyicisinin yalnızca
  fiziksel silmede satır kaldırması.
- **Doğrulanmayan (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı;
  kesişim kuralı, tetikleyiciler ve uçlar **yazılmadı** (M29/M30/F10 iş kalemleri).

### Kalan

M2, M5, M10–M13, M25–M28, **M29/M30** ve F10; F7, F9, F11 açık. Açık kararlar:
OD5–OD18.

---

## [0.13.0] — 2026-09-11 — OD27 ve OD28 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD27 — Tenant içinde birden fazla tüzel şirket.** v1: **`1 tenant = 1 tüzel
  şirket`**; ayrı `companies`/`legal_entities` tablosu **eklenmez**; `departments`,
  `positions`, `employees` doğrudan `tenant_id`'ye bağlı kalır; **RBAC kapsamına
  şirket katmanı eklenmez** ve `company_id` kolonu **hiçbir tabloda oluşturulmaz**.
  Tüzel kişilik bilgisi zaten `hr.tenants` satırındadır (doğrulandı). Mimari
  gelecekte çok tüzel kişiliğe **kapalı değil**: ayrı katman + nullable→backfill→NOT
  NULL geçişi; ancak **tenant güvenlik sınırı olmaya devam eder**. Ölçülebilir
  tetikleyiciler yazıldı (≥ %10 veya ≥ 3 kiracı talebi; sözleşmesel bordro/izin
  ayrımı; ardışık ≥ 2 bloke anlaşma; ≥ 2 işveren sicili).
- **OD28 — `public_id_prefix` yeniden markalama istisnası.** v1'de prefix
  **immutable**; rebrand/slug değişimi mevcut prefix'i ve `public_id`'leri
  değiştirmez; **değiştirme uç noktası yoktur** ve **istisna mekanizması v1'de
  kodlanmaz**. İleride gerekirse yalnızca **platform operasyonu** olarak, denetimli,
  `public_id`'ler sabit tutularak ve eski prefix rezerve kalacak şekilde tasarlanır
  (koşullar yazıldı).

### Ek kontroller (istenen)

- **M10–M13 etkisi: DEĞİŞMEDİ.** Ölçüm: migration planında ve hedef şemada hiçbir
  yerde `company_id`/`legal_entity_id` yoktu; OD27(A) seçildiği için M10
  (`departments`+`positions`), M11 (ağaç garantileri), M12 (`employees`+
  `employee_personal_data`), M13 (`employee_manager`) **aynen kalır**. Tablolara
  "**OD27 kapalı: `company_id` EKLENMEZ**" notu eklendi (yanlışlıkla eklenmesin).
- **OD28 sonrası eksik görülen işler tamamlandı (kural olarak M2/M5'e eklendi):**
  (1) `platform.users.public_id` için **rezerve `plt` ön eki** kuralı (kiracı ad
  alanıyla çakışmaz) → **M2**; (2) üretim fonksiyonları
  (`hr.generate_public_id_prefix`, `hr.generate_public_id`, çakışmada yeniden
  deneme); (3) format `CHECK`
  (`^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$`); (4) **rezerve ad listesi**
  (`www, api, app, admin, portal, mail, support, hr, platform, plt, system`) → **M5**.

### Yeni SECURITY FINDING

- **F10 (Düşük) — Prefix/slug taklidi (typosquatting) engellenmiyor.** `slug`
  yalnızca tam eşleşme benzersizliği taşıyor; benzer görünen değerler serbest
  (`acme` / `acme1`). Risk bugün düşük (kiracı açılışı platform operasyonu), ancak
  `PROJECT.md`'de **self-servis kayıt** planlandığı için o yol açıldığında artar.
  Azaltım: rezerve ad listesi, prefix ≥ 3 karakter, açılışta manuel inceleme, çok
  yakın prefix tespitinde uyarı. Kod düzeltilmedi.

### Yeni OPEN DECISION

- **Yok.** Bu turda gerçekten zorunlu yeni bir belirsizlik çıkmadı; iki küçük boşluk
  (platform kullanıcısı prefix'i, üretim fonksiyonları/format/rezerve adlar)
  **kural** olarak kapatıldı.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD27 ve OD28 **KAPALI** (karar, gerekçe, şema/migration etkisi, güvenlik etkisi, çakışma, seçenekler + ölçülebilir tetikleyiciler); durum satırı ve özet sayıları (kapalı 13, açık 15) |
| `docs/DATABASE.md` | B.4'e OD27 sonucu notu; M10/M12'ye "`company_id` EKLENMEZ"; **M2**'ye `platform.users.public_id` (rezerve `plt` prefix), **M5**'e üretim fonksiyonları + format `CHECK` + rezerve ad listesi; `platform.users` kartına prefix kuralı; bölüm I'de OD27/OD28 kapalı |
| `docs/ORGANIZATION.md` | §9 **karar** olarak yeniden yazıldı (v1 tek tüzel kişilik, gelecek yolu, tetikleyiciler); referans farklar tablosu ve açık noktalar güncellendi |
| `docs/SECURITY.md` | §8'e prefix immutability + rezerve adlar + typosquatting notu; §5.1'e **F10** bulgusu |
| `docs/ARCHITECTURE.md` | **K30, K31** karar kayıtları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (ölçüm):** `docs/` içinde `companies`/`company_id`/`legal_entity`
  geçen **tüm** satırlar tarandı — migration planında ve hedef şemada `company_id`
  **hiç yok** (yalnızca karar maddeleri ve referans atıfları); `hr.tenants` tüzel
  kimlik alanlarını (`legal_name`, `tax_number`, adres/iletişim) zaten taşıyor;
  mevcut slug benzersizliği yalnızca tam eşleşme (`tenants_slug_..._uniq`).
- **Doğrulanmadı (kasıtlı):** kod/migration/DB değişikliği yapılmadı; prefix üretim
  fonksiyonları ve format kısıtı **yazılmadı** (M5 iş kalemi).

### Kalan

M2 (platform kullanıcı public_id), M5 (prefix + public_id + fonksiyonlar + `CHECK` +
rezerve adlar), M27 (kısmi indeks), M10–M13 (organizasyon/çalışan), M25–M28 (F7).
Açık kararlar: **OD5** (organizasyon modülünden önce netleşmeli), OD26 (tenant token).

---

## [0.12.0] — 2026-09-11 — OD23, OD24, OD25 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD23 — Denetim kaydında platform aktörü.** `actor_kind` **zorunlu** ve kapalı
  küme (`tenant_user`, `platform_user`, `service`, `system`); `user_id` yalnızca
  tenant kullanıcısı için; platform aktörü **FK'sız** `platform_actor_id` +
  `actor_label` (snapshot) ile temsil edilir; `purpose` ve `access_grant_ref`
  platform aktöründe **DB `CHECK` ile zorunlu** (gerekçesiz/onaysız platform erişimi
  kayıt dışı kalamaz). `source`/`changed_fields` **eklenmez** (sadeleştirme);
  `user` FK `SET_NULL` → **`PROTECT`** (append-only korunur). Kiracısız olaylar
  `platform.audit_logs`'a gider.
- **OD24 — Organizasyon ağacı DB modeli.** Adjacency list (`departments.parent_id`)
  + **özyinelemeli CTE**; materialized path/nested set/closure **v1'de yok**.
  Döngü ve derinlik (≤ 20) **veritabanı tetikleyicisiyle** engellenir; kiracı dışı
  ilişki mevcut guard ile yasak; bir çalışanın **tek birincil yöneticisi** olur
  (`employee_manager` + kısmi benzersizlik); **ağaç yetki mirası üretmez** (RBAC ayrı).
  Ölçek değerlendirmesi ve geri dönüş tetikleyicileri yazıldı.
- **OD25 — Tenant slug ve public ID prefix ilişkisi.** Yeni **immutable**
  `tenants.public_id_prefix` (tam UNIQUE, soft-deleted tenant'ta bile yeniden
  tahsis edilmez, DB tetikleyicisiyle değiştirilemez); `users.public_id` =
  `<prefix>-<8×Crockford>` ve **immutable**; public ID **yetki sınırı değildir**,
  kimlikle arama yapıldığında RLS aynen geçerlidir.

### Yeni SECURITY FINDING

- **F9 (Orta) — Denetim satırı `SET_NULL` ile güncellenebiliyor.** `AuditLog.user`
  ilişkisi `SET_NULL`; kullanıcı silinirse denetim satırına UPDATE yazılır ve bu,
  "denetim kaydı değiştirilemez" kuralıyla çelişir. Bugün fail-closed davranıyor
  (uygulama rolünün UPDATE yetkisi yok → silme hata verir), ama yetki gevşetilirse
  denetim geçmişi sessizce yeniden yazılır. Kapatma: `on_delete` → `PROTECT`
  (OD23 kararı).

### Yeni OPEN DECISION'lar

- **OD27 — Kiracı içinde birden fazla tüzel şirket (`companies`).** v1'de olacak mı?
  Öneri: v1 tek tüzel kişilik; `companies` ihtiyaç doğduğunda eklenir. **Kod
  yazımından önce netleştirilmeli** (kapsam çözümleyiciyi etkiler).
- **OD28 — Yeniden markalama sonrası `public_id_prefix` istisnası.** Öneri: v1'de
  istisna yok; sözleşmesel zorunlulukta denetimli "prefix göçü" (mevcut public ID'ler
  değişmez).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD23/OD24/OD25 **KAPALI** (karar, gerekçe, şema/migration etkisi, güvenlik etkisi, mevcut kodla çakışma, seçenekler); OD27 ve OD28 eklendi; durum ve özet sayıları (kapalı 11, açık 17) |
| `docs/DATABASE.md` | `audit_logs` hedef alanları + **F9 notu**; `tenants.public_id_prefix` (B.3, M5, M27, F1, E); `audit_logs` indeks planı; **M20** (audit genişletme + `CHECK` + `PROTECT`) ve **M11** (döngü/derinlik tetikleyicileri, path/closure **yok**) güncellendi; M9 bağımlılığı (M10) düzeltildi; bölüm I'de OD23/24/25 kapalı + OD27/OD28 |
| `docs/SECURITY.md` | §5.1'e **F9** bulgusu; §10 denetim bölümüne aktör modeli, platform aktörü kanıt zorunluluğu, `PROTECT` ve kiracısız olay kuralı |
| `docs/ORGANIZATION.md` | **Baştan yazıldı**: hedef şema (v1) + kişisel veri ayrımı, hiyerarşi modeli (OD24), döngü/tutarlılık garantileri tablosu, kapsam ve rol görünürlüğü (güncel rol kodları), birincil yönetici ve ek ilişkiler, yaşam döngüsü (OD21), referans tasarımdan bilinçli farklar, OD27 notu |
| `docs/ARCHITECTURE.md` | **K27, K28, K29** karar kayıtları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma + ölçüm):** `apps/audit/models.py` (alanlar,
  `SET_NULL`), `apps/audit/services.py` (`log_action` imzası, `tenant_id` zorunluluğu),
  `apps/tenancy/models.py` (`slug` SlugField unique, `public_id_prefix` yok),
  `hr.audit_logs` kısıtları (FK `NO ACTION`, uygulama rolünde yalnızca INSERT/SELECT),
  `hr.tenants` kolonları (`slug`/`domain`/`status`/`deleted_at`), mevcut tam benzersiz
  indeks listesi, `docs/ORGANIZATION.md` referans içeriği.
- **Doğrulanmadı (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı; ağaç
  performansı ve kapsam sorgu süreleri **ölçülmedi** (uygulama sonrası ölçülecek —
  geri dönüş tetikleyicileri OD24'te tanımlı).

### Kalan

`docs/DATABASE.md` M10/M11 (org ağacı + döngü garantileri), M20 (audit genişletme),
M5/M27 (prefix + public ID); M25–M28 (F7 kapatma). Açık kararlar: OD27 (v1 kapsamı
için kritik), OD28, OD26.

---

## [0.11.0] — 2026-09-11 — OD20, OD21, OD22 KAPATILDI (mimari karar; kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` gösterir).

### Kapatılan kararlar

- **OD20 — Kiracı üst verisinin sahibi.** `hr.tenants` **canonical** kalır;
  `platform.tenant_registry` **minimum referans indeksi**dir (ikinci tam kopya değil).
  Alan sahipliği tablosu yazıldı (kiracı alanları → `hr.tenants`; `plan_code`,
  `billing_ref`, `internal_note` → registry). Senkronizasyon **tek yönlü**
  (`hr.tenants` → registry) ve **fail-closed**: tetikleyici aynı işlemde çalışır,
  yansı yazılamazsa kaynak yazımı geri alınır → sapma oluşmaz. Ters yön yoktur.
- **OD21 — Soft delete ve benzersizlik politikası.** `deleted_at` = silme,
  `status` = yaşam döngüsü, **`is_active` sütunu yok** (yalnızca türeyen özellik).
  Fiziksel `DELETE` yalnızca CASCADE yansıları, KVKK saklama sonu ve geri alınmış
  migration için. **Sessiz yeniden etkinleştirme yasak** (`*.restore` izni + kayıt).
  Benzersizlik **kısmi indekslerle** (`WHERE deleted_at IS NULL`); **istisnalar:
  `public_id` ve kiracı `slug`ı kalıcı rezerve**. `audit_logs` `deleted_at` almaz.
  Tablo bazlı politika (users, employees, departments, positions, roles, documents,
  org ilişkileri) tek tek yazıldı.
- **OD22 — Platform API token'ları.** Yalnızca `platform.tokens`; ham token
  saklanmaz (SHA-256 özeti); zorunlu `scope` + `purpose`; `expires_at` NOT NULL,
  `revoked_at` ile iptal kaydı; `interactive` ≤ 24 saat, `service` ≤ 90 gün ve
  **superadmin kapsamı yasak**; boş kapsam reddedilir. Platform token'ı **tenant RLS
  sınırını aşamaz**; kiracı verisi yalnızca erişim onayı mekanizmasından geçer.

### Ortaya çıkan SECURITY FINDING'ler (kod düzeltilmedi, raporlandı)

- **F7 (Yüksek) — Soft-delete yetkiyi kaldırmıyor.** `SoftDeleteModel` var ama
  filtreleyen yönetici yok; `apps/rbac/services/permissions.py` içindeki
  `_active_role_ids`, `RolePermission`, `UserPermissionOverride` sorguları
  `deleted_at` filtresi kullanmıyor → silinmiş rol/atama/`allow` istisnası yetki
  vermeye devam eder. Bugün sömürü yolu yok (silme servisi yazılmadı) ama ilk
  "rolü sil" özelliğiyle gerçek açık hâline gelir. Ayrıca `create_default_roles`
  içindeki `get_or_create` silinmiş rolü "var" sayıp atlıyor. Kapatma: M25/M26/M28.
- **F8 (Orta) — Tenant token'ı kapsamsız ve iptal izi yok.** `hr.api_tokens`'ta
  `scope`/`revoked_at` yok, TTL sabit 30 gün → token sahibinin tüm yetkilerini
  taşır, iptal kanıtlanamaz. Platform tarafı OD22 ile kapatıldı; tenant tarafı
  **OD26** olarak açıldı (geriye dönük uyumlu ara adım: `revoked_at`).

### Yeni OPEN DECISION

- **OD26 — Tenant API token modelinin hizalanması** (kapsam + iptal izi). Öneri:
  `scope`, `purpose`, `revoked_at` ekle (OD22 ile simetri).

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD20/OD21/OD22 **KAPALI** (altı başlıklı yapı: karar, gerekçe, şema/migration etkisi, güvenlik etkisi, mevcut kodla çakışma, seçenekler); OD26 eklendi; durum satırı ve özet karar sayıları (kapalı 8, açık 18) |
| `docs/DATABASE.md` | `platform.tenant_registry` (authoritative alan tablosu + tek yönlü fail-closed senkron) ve `platform.tokens` (alanlar, TTL/kapsam kuralları, iptal) kartları güncellendi; indeks planına kısmi benzersizlikler + `tenants` istisnası eklendi; **M25–M28** eklendi (soft-delete yönetici standardı, yetki çözümlemesi `deleted_at`, kısmi e-posta indeksi, `get_or_create` davranışı); H2/H4 karara bağlandı; bölüm I'de OD20/21/22 kapalı + OD26 |
| `docs/SECURITY.md` | Tehdit modeline **T20** (yetki çürümesi) ve **T21** (kapsamsız token) eklendi; **§5.1 Açık bulgular (SECURITY FINDING)** bölümü açıldı (F7, F8); §6'ya platform token kuralları |
| `docs/ARCHITECTURE.md` | **K24, K25, K26** karar kayıtları eklendi |
| `docs/PROJECT.md` | PostgreSQL sürümü (18 / 18.6) ve şema listesi (`hr` + `platform`) düzeltildi; knox kalıntısı giderildi; `DATABASE.md` tanımı güncellendi |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma):** `hr.api_tokens` alanları ve `API_TOKEN_TTL_HOURS=720`
  (`config/settings.py`), `ApiToken` modeli (`apps/accounts/models.py`),
  `SoftDeleteModel` (`apps/common/models.py`), yetki çözümlemesi
  (`apps/rbac/services/permissions.py`), rol servisi (`apps/rbac/services/roles.py`),
  `Tenant`/`User` modelleri; mevcut tam benzersiz indeks listesi ve
  `tenant_directory` tetikleyici deseni (önceki turda ölçülmüştü).
- **Doğrulanmadı (kasıtlı):** hiçbir kod/migration/DB değişikliği yapılmadı;
  kararların uygulanması M-listesi iş kalemlerine bırakıldı.

### Kalan

`docs/DATABASE.md` M25–M28 (F7 kapatma) ve M3 (platform token tablosu);
OD26 kararı; mevcut engelleyici açık karar yok.

---

## [0.10.0] — 2026-09-11 — OD19 KAPATILDI: Django admin / `auth_*` paralel yetki yolu (kod yok)

Yalnızca doküman. **Kod, migration ve veritabanı değiştirilmedi** (doğrulandı:
`git status` yalnızca `.md` dosyaları gösterir).

### Kapatılan karar (OD19)

Üretimde **Django Admin kullanıcı yetkilendirmesi kullanılmaz**: `/admin/` yolu ve
`django.contrib.admin` kaldırılır; `auth_*` tabloları **gerçek RBAC kaynağı
değildir**; **`is_staff`/`is_superuser` tek başına hiçbir Persopyro yetkisi
vermez**; gerçek yetkilendirme yalnızca `docs/AUTHORIZATION.md` modeli üzerinden
yapılır. Django'nun teknik tabloları teknik amaçla kalabilir, izin kaynağı olamaz.
İleride admin kullanımı için ön koşullar yazılı olarak tanımlandı (ayrı alan adı/ağ
sınırı, `platform_users` ile kimlik doğrulama, MFA, `platform.*` izinleri, platform
denetim akışı).

**Seçim:** (A) kaldır → seçildi. (B) internal-only → yalnızca ön koşullarla
yeniden değerlendirilebilir. (C) olduğu gibi bırak → reddedildi.

### Kod incelemesi (gerçek dosyalardan)

| Nokta | Ölçüm |
|---|---|
| `/admin/` rotası | **var** — `config/urls.py:30` |
| `django.contrib.admin` | uygulama listesinde (`config/settings.py:78`) |
| `admin.py` / `admin.site.register` | **yok** (hiçbir model kayıtlı değil) |
| `is_staff` alanı | var (`apps/accounts/models.py:153`), varsayılan `False` |
| `create_superuser` | `is_staff=True` atıyor (`apps/accounts/models.py:135`) |
| `has_perm` | kendi RBAC'imize delege ediyor (`models.py:215-218`) |
| `has_module_perms` | `user_has_any_permission(self)` — "herhangi bir izin var mı" (`models.py:220-223`) |
| Django'nun admin kapısı | `AdminSite.has_permission` = `user.is_active and user.is_staff` (kurulu kaynak, `sites.py:207`) |
| `is_superuser` alanı | **yok** (`PermissionsMixin` kullanılmıyor) |
| DRF oturum kimlik doğrulaması | açık (`SessionAuthentication`) — izinler yine kendi sınıflarımızdan |
| `/admin/login/` formu | kiracı olmadan kimlik doğrulama **fail-closed** (`backends.py:59-62`) → form çalışmaz |

### Güvenlik bulguları (kod yazılmadı, yalnızca raporlandı)

- **F1 (Yüksek) — mevcut oturum + `is_staff` admin kapısını açıyor.** Kimlik
  doğrulama admin formuyla yapılamasa da, `is_staff=True` olan **aktif** bir
  kullanıcı, normal girişte aldığı oturumla `/admin/` dizinini açabilir. Bugünkü
  etki: boş admin dizini (0 kayıtlı model) + `django_admin_log` yazımı. İleride
  model kaydedilirse RBAC'i atlayan yol doğar. **Karar OD19 ile kapatıldı;
  uygulama M22–M22c bekliyor.**
- **F2 (Orta) — `has_module_perms` "herhangi bir izin" olarak yorumlanıyor.**
  Admin korunacak olsaydı, elinde tek bir izin bulunan bir kullanıcı modül
  kontrolünü geçerdi. Kaldırma kararı bu bulguyu da kapatıyor.
- **F3 (Orta) — `create_superuser` riski hedefliyor.** Akış hem kiracı bağını
  zorunlu kılıyor hem `is_staff=True` atıyor; yani "superuser" tam olarak riskli
  kombinasyonu (kiracı kullanıcısı + admin kapısı açık) üretiyor. Kodda bu akış
  hiçbir yerde **kullanılmıyor** (scripts/config/tests taraması: çağrı yok).
- **F4 (Düşük) — `is_superuser` alanı yok ama Django admin kodu/şablonları bu
  özniteliği okuyabilir.** Statik incelemede `django/contrib/admin/*.py` ve
  `options.py` içinde `is_superuser` okuması bulunamadı; şablonlarda
  (AttributeError'ı yutan) sessiz boş değer riski sürer. Kaldırma ile konu
  kapanıyor. **Bu madde çalıştırılarak doğrulanmadı** (aşağıya bakınız).
- **F5 (Bilgilendirme) — `SILENCED_SYSTEM_CHECKS = ["auth.E003","auth.W004"]`.**
  Bilinçli (e-posta kiracı başına tekil), gerekçesi `SECURITY.md` bölüm 3'te;
  kabul edilen risk olarak kayıtlı.
- **F6 (Bilgilendirme) — DRF'de `SessionAuthentication` açık.** Tasarım gereği;
  CSRF koruması ve her uçta izin sınıfı zorunluluğu (liste yoksa **reddet**)
  korunuyor.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD19 **KAPALI** olarak işaretlendi (yedi maddelik karar + ölçüm + implementasyon kalemleri + ileride admin kullanımı ön koşulları); durum satırı ve özet karar sayıları güncellendi (kapalı 5, açık 20) |
| `docs/SECURITY.md` | Bölüm 3'e **admin kapısı kapalı** + **`is_staff`/`is_superuser` kuralı** + "admin giriş formu kiracısız çalışmaz" maddeleri; bölüm 5 "tek yetkilendirme yolu" **karar verildi** olarak güncellendi; **T18** kontrolü karara bağlandı; **G8 kapatıldı**, G1/G8 kapalı kararlar başlığı altında toplandı |
| `docs/ARCHITECTURE.md` | Django Admin'i gerekçe gösteren 4 yer düzeltildi (teknoloji tablosu, django-q2 gerekçesi, FastAPI reddi, gelişmiş gerekçe bölümü); **K23 eklendi** |
| `docs/DATABASE.md` | A.1.2 tablo satırları + karar notu; **M22 → M22/M22b/M22c/M22d** olarak detaylandırıldı (route/app kaldırma, `REVOKE ALL`, `create_superuser` düzeltmesi + `is_staff` denetimi, sıfırdan kurulum testi); D.6 yetki tablosuna kullanılan/kullanılmayan Django tabloları ayrımı; **H3** riski "uygulama kalemi"ne döndü; bölüm I'de OD19 kapalı |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı (kaynak okuma):** Yukarıdaki "kod incelemesi" tablosunun tamamı
  gerçek dosyalardan ve kurulu Django kaynağından okunarak çıkarıldı
  (`config/urls.py`, `config/settings.py`, `apps/accounts/models.py`,
  `apps/accounts/backends.py`, `apps/rbac/permissions.py`,
  `.venv/.../django/contrib/admin/sites.py`). `scripts/`, `config/`, `tests/`
  içinde `create_superuser`/`is_staff` **kullanımı yok** (tarama).
- **Doğrulanmadı:** `/admin/` erişimini uçtan uca kanıtlayacak çalıştırma
  denemesi **yapılmadı** (komut onay almadı ve hiç çalışmadı). Bu nedenle F1/F4
  "kaynak okumaya dayalı" bulgulardır; kanıt, Django'nun `AdminSite.has_permission`
  gövdesi (`is_active and is_staff`) ve `is_staff` alanının varlığıdır — bu ikisi
  kesin. "Oturumla admin dizininin açıldığı" sonucu bu ikisinden **türetilmiştir**,
  çalıştırılarak gösterilmemiştir.

### Kalan

`docs/DATABASE.md` **M22, M22b, M22c, M22d** (uygulama kalemleri) ve OD19'da
listelenen kod düzeltmeleri (`/admin/` route, uygulama listesi, `create_superuser`,
yetki geri alma, `is_staff` denetim sorgusu/testi). Karar durumu: **kapalı 5**
(OD1–OD4, OD19), **açık 20** (OD5–OD18, OD20–OD25), engelleyici yok.

---

## [0.9.0] — 2026-09-11 — Veritabanı envanteri, hedef şema ve migration planı (kod yok)

Yalnızca doküman. **Uygulama kodu, migration dosyaları ve veritabanı
değiştirilmedi** (doğrulandı: `git status` yalnızca `.md` gösterir). Bu turun
amacı, kesinleşen yetkilendirme/security tasarımını **mevcut veritabanıyla
karşılaştırmak** ve güvenli migration planını yazmaktı.

### Ölçümle çıkarılan mevcut durum

- `hr` şemasında **21 tablo**: 10 uygulama + 11 Django/teknik tablo.
- **7** tablo `RLS + FORCE` (hepsinde politika `tenant_id = hr.current_tenant_id()`;
  `tenants` için `id = ...`). Politikalarda platform istisnası **yok**.
- **9** kiracılar arası referans guard tetikleyicisi (6 tablo), **3** fonksiyon.
- Roller: `persopyro_app` ve `persopyro_owner` (ikisi de `NOSUPERUSER`,
  `NOBYPASSRLS`). `python` rolü yok; `postgres` uygulamada kullanılmıyor.
- Uygulanmış migration: `tenancy` 0001–0007, `accounts` 0001–0002,
  `rbac` 0001–0003, `audit` 0001.
- Gerçek veri (kiracı bağlamı kurularak ölçüldü): 1 kiracı, 1 kullanıcı, 4 rol
  (`tenant_admin` 29, `hr_manager` 22, `manager` 7, `employee` 6 izin),
  64 rol-izin bağı, 29 izin kodu, 2 denetim kaydı.
- `public_id` (genel kimlik) **şemada ve kodda yok** → hedef şemada eklenecek.

### Bulgular (ölçümle bulunan gerçek durumlar)

1. **Kiracı bağlamı olmadan kiracı tabloları 0 satır döndürüyor.** İlk ölçümde
   `tenants/users/roles` "0" göründü; bağlam kurulunca 1/1/4 çıktı. RLS'in
   fail-closed çalıştığının canlı kanıtı — ve her ölçümün bağlamla yapılması
   gerektiğinin.
2. **Django'nun kendi yetki tabloları `hr` şemasında** (`auth_permission`,
   `auth_group`, `auth_group_permissions`, `django_admin_log`) ve `/admin/` yolu
   **rotalı** (`config/urls.py`); `create_superuser` `is_staff=True` atıyor. Bu,
   uygulamanın RBAC'inden **bağımsız ikinci bir yetkilendirme yolu** → **OD19**.
3. **Fazla yetki:** uygulama rolü `hr.permissions` üzerinde INSERT/UPDATE/DELETE
   yetkisine sahip (ölçüldü). Global katalog salt okunur olmalı → T19, G9.
4. **Rol kodu farkı:** mevcut şablonda `manager` var; tasarım `dept_manager` ve
   `tenant_officer` kullanıyor → veri migration'ı gerekiyor (M7/F2/F3).
5. **Soft-delete ile benzersizlik:** `(tenant_id, email)`, `(tenant_id, code)`,
   `(user_id, role_id)` benzersiz indeksleri `deleted_at`'i dikkate almıyor →
   **OD21**.
6. **Ölü kod temizliği doğrulandı:** `tenancy/0007`, `0006`'da oluşturulan
   `hr.tenant_id_by_slug/domain` fonksiyonlarını düşürmüş; veritabanında yalnızca
   3 fonksiyon var (doküman bunu artık doğru gösteriyor).
7. **Doküman düzeltmesi:** `DATABASE.md` eski 3.3 listesindeki `roles.created_by_id`
   guard'ı gerçekte yok (kolon da yok) — düzeltildi.

### Karar

- **OD2 KAPATILDI** (kullanıcı kararı): genel kimlik biçimi
  `<kiracı-slug>-<8×Crockford Base32>`, rastgele, benzersiz, tahmin edilemez.
- Bu turda ortaya çıkan **7 yeni açık karar**: OD19 (Django admin/paralel yetki
  yolu — mevcut risk), OD20 (kiracı üst verisinin sahibi), OD21 (soft-delete +
  benzersizlik), OD22 (platform token'larının yeri), OD23 (denetimde platform
  aktörü), OD24 (organizasyon ağacı mekanizması), OD25 (slug değişince ön ek).
- Karar durumu: **kapalı 4** (OD1–OD4), **açık 21** (OD5–OD25), **engelleyici yok**.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/DATABASE.md` | **Baştan yazıldı**: bölüm A (mevcut şema, tablo kartları, RLS/politikalar, guard'lar, fonksiyonlar, roller/yetkiler, indeksler, migration geçmişi, ölçülen veri), B (hedef şema: `platform` şeması + `persopyro_platform` rolü, platform tabloları, kiracı iş tabloları, ilişki haritası), C (M1–M24 migration planı, 3 parti), D (RLS planı + yetki hijyeni), E (indeks planı), F (veri göçü/backfill F1–F9), G (rollback), H (12 risk), I (veritabanı OPEN DECISION listesi) |
| `docs/AUTHORIZATION.md` | OD2 **KAPALI** olarak işaretlendi (biçim kararı); OD19–OD25 eklendi; 8.2 güncellendi; özet karar durumu düzeltildi |
| `docs/SECURITY.md` | Tehdit modeline **T18** (paralel yetki yolu) ve **T19** (fazla yetki) eklendi; bölüm 5'e "tek yetkilendirme yolu" ve "tanım tabloları salt okunur" ilkeleri; G8/G9 açık kararları |
| `docs/CHANGELOG.md` | Bu kayıt |

### Doğrulanan / doğrulanmayan

- **Doğrulandı:** Tüm mevcut-durum iddiaları canlı veritabanından ve dosyalardan
  ölçüldü (tablo/kolon/kısıt/indeks/politika/tetikleyici/fonksiyon/yetki/migration
  listesi/veri sayıları). Hedef şema, kapatılan kararlarla (OD1/OD3/OD4) çelişmiyor:
  RLS politikası gevşetilmiyor, `BYPASSRLS` yok, platform kullanıcıları kiracıya bağlı
  değil.
- **Doğrulanmadı (kasıtlı):** Hedef şemanın SQL'i **yazılmadı**; hiçbir migration
  çalıştırılmadı. Bu turun çıktısı plan, uygulama değil.

### Kalan

`docs/DATABASE.md` bölüm C'deki **M1–M24** iş kalemleri (Parti 1: M1–M5, M22 →
Parti 2: M6–M9, M17, M20 → Parti 3: M10–M16, M18–M19, M21, M23–M24). Parti 2
öncesi şifreli yedek + geri yükleme testi zorunlu; her partiden önce boş
veritabanında sıfırdan kurulum doğrulaması yapılır.

---

## [0.8.0] — 2026-09-11 — Yetkilendirme: OD1, OD3, OD4 KAPATILDI (kod yok)

Yalnızca doküman güncellemesi. Uygulama kodu, migration ve veritabanı
değiştirilmedi (doğrulandı: `git status` yalnızca `.md` dosyaları gösterir).

### Kapatılan kararlar

- **OD1 — SUPERADMIN kişisel veri erişimi: KAPALI.** Varsayılan erişim **yok**.
  Erişim yalnızca gerekçeli talep + onay + **süreli** erişim kaydı ile açılır;
  süre dolduğunda **otomatik kapanır**; erişim ve nedeni denetim kaydına yazılır.
- **OD3 — Platform kullanıcıları: KAPALI.** SUPERADMIN/ADMIN için **ayrı
  `platform_users` modeli**; kiracıya bağlı değil, kiracı tablosuyla
  birleştirilmez. **Kiracı RLS kuralları gevşetilmez**, **`BYPASSRLS`
  kullanılmaz**; platform erişimi **ayrı ve kontrollü servis/authorization
  katmanı** üzerinden tasarlanır.
- **OD4 — Kişisel veri erişim onayı: KAPALI.** Kiracı içinde **şirket yöneticisi /
  onay yetkilisi** temel onay merciidir; platform tarafında **çift onay (ikinci
  kontrol)**. **Boş/anlamsız gerekçeyle talep oluşturulamaz.** Talep, onay, red,
  erişim başlangıcı ve erişim bitişi denetim kaydına yazılır.

### Kararın mimari sonucu (kritik)

RLS politikasında platform istisnası **tasarımı geri çekildi** (önceki taslakta
koşullu bayrak vardı). Yeni tasarım: kiracı politikaları **hiç değişmez**; platform
erişimi ayrı şema + ayrı veritabanı rolü (`persopyro_platform`) ve ayrı servis
katmanıyla yürür; kiracı bağlamını yalnızca platform katmanı, geçerli ve süreli bir
erişim onayıyla **tek işlem** için kurar. Böylece "izolasyonu gevşetmeden platform
erişimi" mümkün olur. Kalan artık risk (bağlam kurulduktan sonra RLS'in platformu
kiracıdan ayırt edememesi) açıkça yazıldı ve uygulama katmanı kontrolleriyle
(ayrı rol, tek işlem, her kullanımda kayıt, kiracıya görünürlük) yönetiliyor.

### Değişen dosyalar

| Dosya | Değişiklik |
|---|---|
| `docs/AUTHORIZATION.md` | OD1/OD3/OD4 KAPALI olarak işaretlendi; bölüm **3.4** yeniden yazıldı (RLS gevşetilmez, ayrı katman/şema/rol); **5.4**'e `PURPOSE_QUALITY` ve `DUAL_APPROVAL` koşulları ve `GRANT_VALID` netleştirmesi; **6.2/A**, **6.2/I**, **6.3**, **7.3**, **10.1** kararlara göre güncellendi; **12**'ye T15–T18 negatif testleri; **13** geçiş planı 12 → 14 iş kalemine çıkarıldı; **14**'e OD17 ve OD18 eklendi; **15** özet karar durumu |
| `docs/SECURITY.md` | **4.2** platform erişimi yeniden yazıldı (politika değişmez, `BYPASSRLS` yasak, ayrı katman/rol, çift onay, gerekçe niteliği, otomatik kapanma); **5** ve **6**'ya karar maddeleri; tehdit **T12** güncellendi; **15**'te **G1 kapalı** olarak kayda geçti, **G7** eklendi |
| `docs/ARCHITECTURE.md` | **K19 kabul edildi** (OD1/OD3/OD4 kapalı); **K21** (ayrı şema + ayrı DB rolü) ve **K22** (ayrı platform denetim akışı) eklendi; `django-rest-knox` kalıntıları düzeltildi (K4 ve teknoloji tablosu — K15 ile tutarlı) |
| `docs/CHANGELOG.md` | Bu kayıt |

### Yeni açık kararlar

- **OD17 — Platform çift onayının uygulanabilirliği:** tek platform yöneticisi
  varsa veya acil durumda ikinci onay alınamıyorsa ne olacak? Öneri: fail-closed
  varsayılan + sınırlı, kayıtlı ve sonradan incelenen break-glass.
- **OD18 — Kiracının platform erişimindeki rolü:** bilgilendirme mi, onay mı?
  Öneri: yalnızca K3 (özel nitelikli) veri ve toplu erişimde kiracı onayı.
- `docs/SECURITY.md` **G7** bu iki kararla ilişkilidir; **G6** (break-glass
  sınırları) OD17'ye bağlandı.

### Doğrulanan

- OD numaralandırma ve atıf bütünlüğü kontrol edildi (tanımsız OD atfı yok;
  OD1, OD3, OD4 "KAPALI" olarak işaretli ve kararları metne işlenmiş).
- Karar durumu: kapalı 3, açık 15 (OD2, OD5–OD18). **Engelleyici açık karar yok.**

### Kalan

Karar bekleyenler: OD2, OD5–OD18 (hiçbiri engelleyici değil). OD17 uygulama
sırasında netleştirilmelidir. Kod aşaması: `docs/AUTHORIZATION.md` bölüm 13'teki
14 iş kalemi.

---

## [0.7.0] — 2026-09-11 — Yetkilendirme ve erişim kontrolü TASARIMI (kod yok)

Yalnızca tasarım: bu sürümde uygulama kodu yazılmadı, veritabanı/API/frontend
değişmedi. Amaç, kodlamadan önce yetki modelini ve sınırlarını netleştirmekti.

### Değişen (doküman)

- `docs/AUTHORIZATION.md` baştan yazıldı: 15 bölüm, 16 "OPEN DECISION".
- `docs/SECURITY.md` yeniden yazıldı: kişisel veri katmanları, ayrıcalıklı erişim
  (platform/destek), genel kimlik güvenliği, evrak sınıflandırması, denetim
  bütünlüğü, olay müdahalesi ve 7 yeni tehdit satırı (T11–T17).

### Tasarım kararları

- **Model:** RBAC + kapsam (scope) + kapalı koşul kümesi (hibrit). Tam ABAC
  (OPA/Cedar benzeri politika motoru) reddedildi; geri gelme tetikleyicileri
  ölçülebilir şekilde yazıldı (koşul türü > 15, kiracı başına > 40 özel rol,
  sözleşmesel politika dosyası talebi).
- **Roller:** 6 rol — 2 platform (`superadmin`, `platform_admin`), 4 kiracı
  (`tenant_admin`, `tenant_officer`, `dept_manager`, `employee`). Hiyerarşi
  izin devralma **yaratmaz**; yalnızca yönetim ilişkisini belirler.
- **Kapsam kodları:** `SYS`, `TENANT`, `UNIT`, `UNIT_DIRECT`, `ASSIGNED`, `SELF`;
  etkin kapsam = en dar olan. Rol kapsamı kişi bazında daraltılabilir,
  genişletilemez.
- **Permission matrisi:** rol özeti (gör/oluştur/değiştir/sil/onayla/sınır) +
  9 modül için ayrıntılı matris; SUPERADMIN ve ADMIN kişisel veri sütunlarında
  `GRANT_VALID`/`—` ile sınırlandı.
- **Kişisel veri:** K0–K4 sınıflandırması; varsayılan maskeleme (sunucu tarafı);
  talep → onay → süreli kullanım → kayıt akışı; boş gerekçeyle erişim yok.
- **Genel kimlik:** `kiracı-XXXXXXXX` (Crockford Base32, 8 rastgele karakter).
  Sıralı `00XXXX` biçimi **reddedildi**: numaralandırmaya açık.
- **Evrak:** yüklemede "kişisel veri içeriyor mu?" + gizlilik düzeyi + saklama;
  "emin değilim" = en kısıtlayıcı politika. "Varlığı bilmek" ≠ "içeriği görmek".
- **Denetim kaydı:** append-only (mevcut ✅), kişisel verinin değerini taşımaz;
  `purpose`, `grant_id`, `changed_fields` eklenecek; zincirleme önerildi.
- **Platform erişimi:** kiracı izolasyonunun tek istisnası; süreli (≤60 dk),
  gerekçeli, çift kontrol, kiracıya görünür ve kayıtlı.

### Mimari açıdan kritik tespit

Mevcut kod ile yeni tasarım arasında **üç çelişki** var; hiçbiri kod yazılmadan
çözülmeli:

1. **Platform kullanıcıları barındırılamıyor** (OD3): `users.tenant_id` zorunlu
   ve RLS korumalı; SUPERADMIN/ADMIN hiçbir kiracıya ait olmamalı. Öneri: ayrı
   `platform_users` tablosu + ayrı giriş yolu (kiracı politikaları hiç değişmez).
2. **Kiracılar arası hiçbir okuma yolu yok**: RLS + FORCE tüm tablolarda etkin;
   SUPERADMIN için koşullu, onay kaydına bağlı politika bayrağı tasarlandı (OD1).
3. **Rol kodları değişiyor**: `manager` → `dept_manager`, `tenant_officer`
   ekleniyor; veri migration'ı gerekiyor (uygulanmış migration değiştirilmez).

### Doğrulanan

- Dokümanların mevcut koda uygunluğu ölçümle kontrol edildi: RLS uygulanan
  tablolar (`tenants`, `users`, `roles`, `role_permissions`, `user_roles`,
  `user_permission_overrides`, `audit_logs`), mevcut 29 izin kodunun tam listesi
  ve mevcut rol şablonları (`tenant_admin`, `hr_manager`, `manager`, `employee`)
  ile karşılaştırıldı; tasarım belgesinde her madde ✅/🔜/⚠️/❓ olarak işaretlendi.
- Kod değişmediği için test koşusu gerekmedi (önceki durum: 97 test yeşil).

### Kalan (kod aşaması)

`docs/AUTHORIZATION.md` bölüm 13'teki 12 iş kalemi ve bölüm 14'teki kararlar.
Engelleyici kararlar: **OD3** (platform kullanıcı barındırma), **OD1**
(SUPERADMIN kişisel veri erişimi), **OD4** (kişisel veri erişimini kim onaylar).

## [0.6.0] — 2026-09-11 — Faz 2 (2. dilim): istek hattı, kimlik doğrulama uçları, MFA ve token modeli

Bu dilimde uygulama ilk kez **çalışan bir giriş akışına** kavuştu: istek hattı
kiracı bağlamını kuruyor, oturum ve token uçları parola + MFA doğruluyor, izinler
kataloğu ve varsayılan roller veritabanında. **Yedi gerçek hata** ölçümle
bulundu ve düzeltildi; biri (2 numaralı bulgu) üretimdeki TÜM girişleri
kırıyordu.

### Eklenen — istek hattı

- `apps/tenancy/middleware.py`:
  - `RequestIdMiddleware` — her isteğe kimlik verir, `X-Request-ID` döner.
  - `TenantContextMiddleware` — kiracıyı OTURUMDAN, yoksa ALAN ADINDAN çözer ve
    istek boyunca bağlamı kurar. Oturum ile alan adı uyuşmazsa istek reddedilir
    (oturum taşıma saldırısına karşı).
- `apps/accounts/middleware.py`: `ApiTokenTenantMiddleware` — token ile gelen
  isteklerde kiracıyı token'dan çözer.
- SIRA: üçü de `AuthenticationMiddleware`'ten ÖNCE çalışır (aksi halde oturumdan
  kullanıcı yüklenemez; RLS satırı gizler).

### Eklenen — kiracı çözümleme

- `Tenant.slug` (tekil) ve `Tenant.domain` (tekil, opsiyonel).
- `hr.tenant_directory`: slug/alan adı → kiracı kimliği eşlemesi. **RLS dışıdır**
  (gerekçe aşağıda), uygulama rolü yalnızca OKUR; dizini tetikleyici
  (`hr.sync_tenant_directory`, SECURITY DEFINER) günceller. Böylece bir kiracı
  başkasının alan adı eşlemesini ele geçiremez.
- `apps/tenancy/context.py`: `tenant_id_by_slug`, `tenant_id_by_domain`.

### Eklenen — kimlik doğrulama uçları (`/api/v1/auth/`)

| Uç nokta | Yöntem | Notlar |
|---|---|---|
| `login` | POST | Oturum çerezi (HttpOnly) + **CSRF korumalı** |
| `logout` | POST | Oturumu kapatır |
| `token` | POST | API token üretir (çerez yok, CSRF gerekmez) |
| `token/logout` | POST | Yalnızca o cihazın token'ını iptal eder |

- Hata mesajları GENEL tutulur (kullanıcı/kiracı numaralandırmasına karşı);
  "MFA gerekli" bilgisi yalnızca parola doğruysa döner.
- Kimlik doğrulama uçları en katı hız sınırı sınıfındadır (`throttle_scope="auth"`).
- Ayrıca `/api/health` (canlılık) ve `/api/ready` (veritabanı hazır olma) uçları.

### Eklenen — MFA (TOTP)

- `apps/accounts/mfa.py`: kod doğrulama (`valid_window=1`), kayıt için anahtar
  üretimi. TOTP sırrı veritabanında **şifreli** saklanır
  (`FIELD_ENCRYPTION_KEY`, Fernet; `apps/common/crypto.py`).
- MFA, OTURUM ve TOKEN yollarının İKİSİNDE de uygulanır (ortak yardımcı).
- Anahtar yoksa/çözülemezse giriş reddedilir (fail-closed).

### Eklenen — API token modeli (knox kaldırıldı)

- `apps/accounts/models.py`: `ApiToken` — SHA-256 özeti, zorunlu süre, etiket,
  son kullanım, cihaz bazlı iptal. Düz metin token **saklanmaz**.
- `apps/accounts/authentication.py`: `ApiTokenAuthentication` (401 dönebilmesi
  için `authenticate_header` tanımlı).
- `django-rest-knox` bağımlılıktan **çıkarıldı** ve knox tablosu migration ile
  düşürüldü.

### Eklenen — RBAC kataloğu ve varsayılan roller

- `apps/rbac/permission_catalog.py` + `rbac.0003_seed_permissions` (29 izin kodu).
- `create_default_roles(tenant)`: `tenant_admin`, `hr_manager`, `manager`,
  `employee`. Kataloğda olmayan izin sessizce atlanmaz, hata verir.
- `apps/rbac/permissions.py`: `HasPermissionCodes` (**varsayılan reddet**; izin
  listesi tanımsızsa erişim verilmez) ve `AnyPermissionCode`.

### Eklenen — altyapı

- `CACHES`: veritabanı cache'i (paylaşımlı; hız sınırlama sayaçları için gerekli).
  Tablo `createcachetable` ile oluşur; `scripts/migrate.sh` bunu çalıştırır.
- `SESSION_ENGINE`: veritabanı oturumları.

### Verilen kararlar

- **K15** — API token'ları için üçüncü parti paket kullanılmaz; kendi `ApiToken`
  modelimiz kullanılır (gerekçe: bulgu 3).
- **K16** — Giriş ÖNCESİ okunması gereken eşleme tabloları RLS dışıdır, ancak
  sıkı kurallarla: `tenant_directory` yalnızca okunur (yazma tetikleyicide);
  `api_tokens` yalnızca özet + kiracı kimliği tutar, düz metin token saklamaz.
- **K17** — Cache veritabanı üzerinden (süreç-yerel bellek yerine), Redis yok.
- Kiracı çözümleme sırası: oturum → alan adı → token. Çakışma (oturum ≠ alan adı)
  reddedilir.

### Doğrulanan (gerçek çalıştırma)

- **pytest: 97 test geçti** (önceki 57 → +40). Yeni testler: kiracı bağlamı ve
  alan adı çözümleme, oturum/token girişi, CSRF, kilitlenme, MFA (kodsuz/yanlış/
  doğru kod, token yolunda da MFA), token hash/süre/iptal, izin sınıfı (izin yok →
  403; izin tanımsız uç → 403), varsayılan roller, hız sınırı, kiracı dizini
  güvenliği.
- **Sıfırdan kurulum doğrulandı**: boş bir veritabanında `migrate` çalıştırıldı;
  7 kiracıya bağlı tabloda `rls=true force=true`, `api_tokens` ve
  `tenant_directory` bilinçli olarak RLS dışı, uygulama rolü `audit_logs` UPDATE
  ve `tenant_directory` INSERT yapamıyor.
- **Uçtan uca duman testi (`scripts/smoke.sh`)**: gerçek HTTP + gerçek veritabanı
  üzerinden demo kiracı/kullanıcı oluşturuldu (4 rol, 29 izin), `/api/health` ve
  `/api/ready` yanıt verdi, `POST /api/v1/auth/token` ile gerçek giriş yapıldı ve
  token alındı, geçerli token ile çıkış `204`, geçersiz token `401` döndü.
- `ruff`, `bandit` (0 bulgu), `manage.py check`,
  `makemigrations --check` ("No changes detected") temiz.

### Bulgular (ölçümle bulunan ve düzeltilen gerçek hatalar)

0. **`tenant_context` gerçek ortamda bağlamı KAYBEDİYORDU (en kritik bulgu).**
   Ayarı kuran işlem, dış bir işlem yoksa hemen commit oluyor ve `SET LOCAL`
   ayarı düşüyordu; yani her sorgu bağlamsız kalıp fail-closed (0 satır)
   dönüyordu. Testler pytest'in dış işlemi sayesinde bu hatayı MASKELİYORDU —
   yalnızca gerçek HTTP + gerçek veritabanı ile yapılan duman testi yakaladı.
   `tenant_context` artık işlemi `yield` boyunca açık tutuyor. (Bu, gerçek
   isteklerde ürünün tamamen çalışmaz olması demekti.)
1. **FORCE RLS, SECURITY DEFINER fonksiyonu da bağlıyor.** Kiracıyı slug ile
   çözen fonksiyon, tablo sahibi olarak çalışsa bile satırı göremedi (FORCE tablo
   sahibini de kapsar). Çözüm: RLS dışı, salt-okunur **dizin tablosu** (K16).
2. **`login()` sinyali kiracı bağlamı dışında çalışıyordu** → kullanıcı satırını
   güncelleyen sinyal 0 satır etkiliyor ve "Save with update_fields did not affect
   any rows" hatası veriyordu. Yani üretimde **hiçbir oturum girişi çalışmazdı.**
   Oturum kurulumu `tenant_context` içine alındı.
3. **knox, RLS ile çalışmıyor**: token doğrularken kullanıcıyı `select_related`
   ile çekiyor, o anda kiracı bağlamı olmadığı için RLS satırı gizliyor ve token
   asla doğrulanamıyordu (401). Kendi token modelimize geçildi (K15).
4. **Aynı tuzak kendi modelimizde tekrarlandı**: `select_related("user")`
   kullanınca token yine doğrulanamadı; ilişki yalnızca bağlam kurulduktan sonra
   yükleniyor.
5. **`authenticate_header` eksikti** → DRF kimlik doğrulama hatalarında 401 yerine
   403 dönüyordu.
6. **Uygulanmış migration'a sonradan sütun eklenmişti** (`tenancy.0002` içindeki
   `role_permissions`), bu yüzden **sıfırdan kurulum bozuluyordu**: temiz bir
   veritabanında RLS, `role_permissions.tenant_id` oluşmadan çalışıyordu. Dosya
   ilk hâline döndürüldü; RLS ayrı migration'da (`0004`). Sıfırdan kurulum artık
   ayrıca test ediliyor.
7. **SlugField'ın `db_index` davranışı**: iki adımlı alan eklemede PostgreSQL'in
   `_like` indeksi iki kez yaratılmaya çalışıldı ("relation already exists").
   İlk adım `CharField`, son adım `SlugField` olacak şekilde düzeltildi.
8. **`fetchone()` boş sonuçta TypeError** → eşlemesi olmayan HER istekte 500
   (ör. localhost). Boş sonuç artık normal kabul ediliyor.
9. **`CACHES` tanımsızdı** → hız sınırlama sayaçları süreç-yerel bellekte
   tutuluyordu; çok işçili kurulumda tutarsız olurdu. Veritabanı cache'ine
   geçildi (K17).
10. **DRF hız oranları sınıf özniteliğine bağlanır**; ayar değiştirmek yetmiyor.
    Testler bu yüzden yanlış kurulmuştu; test kurulumu düzeltildi.
11. **`check --deploy`**: üretilen secret 43 karakterdi (50'den kısa). Üretim artık
    64 karakterlik secret üretir.

### Kalan (Faz 2'nin 3. dilimi)

- Parola sıfırlama akışı ve e-posta gönderimi (kuyruk üzerinden).
- MFA **kayıt** ucu (QR/anahtar gösterimi) ve kapatma akışı.
- Süresi geçmiş oturum/token temizliği (periyodik görev), kullanıcı CRUD uçları.
- Nesne seviyesi yetki kapsamı (kendi kaydı / kendi ekibi) — organizasyon
  modülüyle birlikte (faz 3).

---

## [0.5.0] — 2026-09-11 — Faz 2 (1. dilim): veri modeli, ilk migration, RLS ve kimlik doğrulama çekirdeği

Bu sürümde ilk kez uygulama modelleri ve migration'lar yazıldı; veritabanı
şemasının sahibi artık Django migration'larıdır (`docs/ARCHITECTURE.md` bölüm 5.2).
Ayrıca kimlik doğrulama ve RBAC çekirdeği kuruldu. **Dört gerçek hata testler
tarafından yakalandı** (aşağıda "Bulgular").

### Eklenen — uygulamalar

- `apps/common/models.py` — soyut temeller: `UUIDModel`, `TimeStampedModel`,
  `SoftDeleteModel`, `TenantScopedModel`.
- `apps/tenancy/` — `Tenant` modeli (+`TenantStatus`), kiracı bağlamı yönetimi
  (`apps/tenancy/context.py`: `tenant_context`, `require_tenant_id`,
  `TenantContextMissingError`).
- `apps/accounts/` — özel `User` modeli (e-posta ile giriş, zorunlu `tenant`,
  kilit/MFA/token alanları, `UserManager`) ve `TenantScopedModelBackend`.
- `apps/rbac/` — `Role`, `Permission`, `RolePermission`, `UserRole`,
  `UserPermissionOverride` + `services.py` (etkin izin çözümlemesi).
- `apps/audit/` — `AuditLog` (+`AuditAction`) ve `services.py` (`log_action`,
  `masked_changes`).

### Eklenen — migration ve veritabanı güvenliği

- `tenancy.0001_initial` — `hr` şemasını ve `hr.current_tenant_id()` fonksiyonunu
  oluşturur (tablolar oluşmadan önce).
- `tenancy.0002_enable_rls` — kiracıya bağlı 7 tabloda **RLS + FORCE** ve
  `tenant_isolation` politikası.
- `tenancy.0003_same_tenant_guard` — **kiracılar arası referans guard'ı**:
  `hr.ensure_same_tenant_ref()` fonksiyonu ve 8 tetikleyici. RLS yalnızca satırın
  kendi `tenant_id`'sini denetlediği için, başka kiracının nesnesine bağlanan
  satırlar bu tetikleyicilerle engellenir.
- `rbac.0002_rolepermission_tenant` — `role_permissions` tablosuna zorunlu
  `tenant_id` eklendi (bkz. Bulgular #2).
- `tenancy.0004_rls_role_permissions` — yukarıdaki tablonun RLS'i (bkz. Bulgular #4).
- `scripts/migrate.sh` — migration'ları **sahip rolle** çalıştırır ve ardından
  uygulama rolü yetkilerini tazeler (uygulama rolünün `hr` şemasında DDL yetkisi
  yoktur; bu bilinçlidir).

### Verilen kararlar

- **E-posta kiracı başına tekildir**, global tekil değil. Bu nedenle Django'nun
  `ModelBackend`'i kullanılamaz; kendi backend'i yazıldı ve `auth.E003`/`auth.W004`
  denetimleri bilinçli olarak susturuldu.
- **Kimlik doğrulama kiracı bağlamı içinde çalışır** (RLS nedeniyle zorunludur).
- **Kiracı, isteğin sorumluluğundadır**: istemciden gelen gövde/parametre ile
  belirlenmez; oturum/alan adı üzerinden çözümlenir ve `authenticate()`'e açıkça
  verilir. Kiracı çözümlenemezse giriş yapılmaz (fail-closed).
- **`permissions` tablosu globaldir** (kiracı verisi içermez, RLS dışıdır);
  diğer tüm kiracıya bağlı tablolar `tenant_id` taşır ve RLS'lidir.

### Doğrulanan (gerçek çalıştırma, PostgreSQL 18.6)

- `manage.py migrate`: 4 uygulamanın migration'ları gerçek kümede uygulandı;
  `makemigrations --check`: **"No changes detected"** (model ile migration uyumlu).
- Gerçek kümede sorgulanarak doğrulandı: `tenants`, `users`, `roles`,
  `role_permissions`, `user_roles`, `user_permission_overrides`, `audit_logs`
  tablolarında `rls=true force=true`; `permissions` bilinçli olarak RLS dışı;
  8 guard tetikleyicisi kurulu.
- `audit_logs` yetkileri: uygulama rolü `SELECT=true INSERT=true UPDATE=false
  DELETE=false` → denetim kaydı **değiştirilemez**.
- **pytest: 57 test geçti** (önceki 19 → +38). Yeni testler:
  `tests/test_tenant_isolation.py` (yapısal RLS denetimi, fail-closed davranış,
  çapraz kiracı okuma/yazma/güncelleme/silme reddi, iç içe bağlam, guard'lar,
  denetim kaydı yetkileri) ve `tests/test_auth_rbac.py` (izin çözümlemesi,
  allow/deny istisnaları, süresi geçmiş rol, giriş/kilitlenme/pasif kullanıcı,
  denetim kayıtları).
- `ruff`, `bandit`, `manage.py check`: temiz.

### Bulgular (testlerin yakaladığı gerçek hatalar)

1. **Kimlik doğrulama kiracı bağlamı olmadan çalışıyordu** → RLS kullanıcıyı
   göstermediği için hiç kimse giriş yapamazdı. Backend, tüm akışı
   `tenant_context` içine alacak şekilde düzeltildi.
2. **`role_permissions` tablosu çapraz kiracı açığıydı.** İlk tasarımda "tanım
   tablosu" sayılıp RLS dışı bırakılmıştı; A kiracısı bağlamında B kiracısının
   rolüne izin kaydı eklenebiliyordu. Tabloya zorunlu `tenant_id` eklendi, RLS'e
   alındı. Kural sadeleşti: kiracıya bağlı her tablo `tenant_id` taşır.
3. **RLS tek başına çapraz kiracı referansı engellemiyor.** Yabancı anahtar
   kontrolü RLS'ten etkilenmediği için, `tenant_id` doğru olsa bile satır başka
   kiracının nesnesine bağlanabiliyordu. Guard tetikleyicileri eklendi (#2 ve #3
   aynı kök nedeni paylaşır: "tanım tablosu" varsayımı).
4. **Uygulanmış bir migration dosyası sonradan düzenlenmişti.** `role_permissions`
   RLS'i ilk migration'ın listesine eklenmiş, ancak o migration zaten
   uygulandığı için veritabanına yansımamıştı. Düzeltme ayrı bir migration ile
   yapıldı (`tenancy.0004_rls_role_permissions`). Kural: uygulanmış migration
   dosyası değiştirilmez; düzeltme yeni migration'la gelir.
5. Test kaynaklı yanlış varsayımlar da düzeltildi: beklenen veritabanı hataları
   savepoint içinde üretilmeli (aksi halde işlem kilitleniyor) ve `refresh_from_db`
   kiracı bağlamı gerektirir.

### Kalan (Faz 2'nin 2. dilimi)

- **İstek hattı (middleware)**: kiracı bağlamını istek başına kurmak —
  `AuthenticationMiddleware`'ten ÖNCE çalışmalıdır, aksi halde oturumdan kullanıcı
  yüklenemez (RLS). Ayrıca `request_id` üretimi.
- **Görünümler ve API**: giriş/çıkış uçları, DRF yetki sınıfı (`required_permissions`),
  knox token uçları, OpenAPI şeması.
- **MFA (TOTP)**: alan modelde hazır; kayıt/doğrulama akışı ve testleri yazılacak.
- **Parola sıfırlama** uçları (`password_reset_tokens`), e-posta gönderimi.
- Örnek izin kodları ve varsayılan rollerin veri migration'ı ile eklenmesi.

---

## [0.4.0] — 2026-09-11 — Faz 1: veritabanı, rol ayrımı ve kiracı izolasyonu (RLS)

PostgreSQL 18.6 kullanıcı tarafından kuruldu (sudo gerektirdiği için ajan
kuramadı). Bu sürümde veri katmanının güvenlik temeli kuruldu ve
**çalıştırılarak kanıtlandı**.

### Eklenen

- `scripts/01_bootstrap.sql` — roller (`persopyro_owner`, `persopyro_app`),
  veritabanı, `pgcrypto`. Süper kullanıcı çalıştırır, idempotent.
- `scripts/02_grant_app_privileges.sql` — `hr` şeması, şema yetkileri, varsayılan
  yetkiler (tablo/sequence/fonksiyon/**tip**), mevcut nesnelerin yetkileri,
  denetim tablosu koruması. **Saf SQL** (psql'e özgü komut yok), bu yüzden test
  veritabanında da (conftest) çalıştırılabilir.
- `scripts/03_verify_isolation.sql` — kiracı izolasyonu kanıtı: A1–A9 iddiaları,
  başarısızlıkta `RAISE EXCEPTION`. Geçici `spike` şeması kurar ve siler; `hr`
  şemasına dokunmaz.
- `scripts/bootstrap.sh` — tek komutla kurulum: roller/DB/şema/yetkiler →
  rastgele parola üretimi → `.env` yazımı → izolasyon doğrulaması → uygulama
  rolüyle TCP bağlantı sınaması (parola + `search_path`).
- `scripts/run_tests.sh` — testleri sahip rolüyle çalıştırır (test veritabanı
  oluşturma yetkisi uygulama rolünde bilinçli olarak yoktur).
- `tests/conftest.py` — test veritabanında `hr` şemasını ve uygulama rolü
  yetkilerini uygular (geçiş dönemi çözümü; faz 2'de ilk migration devralacak).
- `tests/test_database_roles.py` — 8 test: uygulama rolü superuser değil, RLS'i
  atlayamaz, veritabanı/rol oluşturamaz, `hr`/`public` şemasında DDL yapamaz,
  `hr` şemasını kullanabilir; tenant bağlamı fail-closed; `search_path` doğru;
  roller ayrı.
- `config/settings.py`: `.env` yükleyici (ek bağımlılık yok, gerçek ortam
  değişkenleri kazanır), `DJANGO_ENV_FILE` ile dosya yolu geçersiz kılınabilir;
  `APP_DB_ROLE` / `OWNER_DB_ROLE` ayarları.

### Verilen kararlar

- **Kimlik doğrulama:** kimlik doğrulama uygulama katmanında kalır;
  veritabanındaki `crypt()` tabanlı fonksiyon üretimde kullanılmaz (önceki sürüm
  kararı doğrulandı).
- **`uuid-ossp` eklentisi gerekmez:** `gen_random_uuid()` PostgreSQL 13+ ile
  çekirdekte. Mevcut tasarımın `uuid-ossp` kullanımı sadeleştirildi.
- **Tenant bağlamı işlem başına** (`set_config(..., is_local=true)`) kurulur;
  oturum boyunca kalan (`false`) kullanım yasaktır.
- **Testler sahip rolüyle çalışır**, uygulama rolüne `CREATEDB` verilmez;
  izolasyon `FORCE RLS` sayesinde yine bağlayıcıdır.

### Doğrulanan (gerçek çalıştırma, PostgreSQL 18.6)

Geçici, ajan tarafından yönetilen bir küme (port 5433, `initdb` ile kuruldu ve
iş bitince silindi) üzerinde:

- `scripts/bootstrap.sh` uçtan uca çalıştı; `SONUC: KIRACI IZOLASYONU
  DOGRULAMASI BASARILI (A1-A9)`.
- **A1** uygulama rolü superuser değil, `rolbypassrls=false`, `CREATEDB`/
  `CREATEROLE` yok, `hr` şemasında `CREATE` yok.
- **A2** uygulama rolü tablo sahibi değil.
- **A3** RLS etkin **ve FORCE etkin**.
- **A4** kiracı bağlamı yokken 0 satır (fail-closed).
- **A5** A kiracısı yalnızca kendi 1 kaydını görür.
- **A6** A kiracısı B kiracısı adına yazamaz (`WITH CHECK` ihlali engellendi).
- **A7** B kiracısının satırı güncellenemedi/silinemedi (0 satır etkilendi).
- **A8** işlem sonunda bağlam sıfırlandı.
- **A9** `FORCE RLS` sayesinde **tablo sahibi rol bile** kiracı sınırını aşamadı.
- Uygulama rolü TCP + parola (scram-sha-256) ile bağlandı; `search_path=hr,public`.
- `pytest`: **19 test geçti** (8'i veritabanı rol/izolasyon testi), gerçek
  PostgreSQL üzerinde; test veritabanı otomatik oluşturuldu ve silindi.
- `manage.py check`, `ruff`, `bandit -c pyproject.toml -r .`, `pip-audit`: temiz.

### Bulgular (düzeltilen gerçek hatalar)

- **PostgreSQL `SET LOCAL` sonrası ayar `NULL` değil boş string'e döner.**
  Bu yüzden tasarımdaki `NULLIF(current_setting('app.tenant_id', TRUE), '')`
  kullanımı zorunludur — süs değil. Kanıt A8'dedir.
- `GRANT ... ON ALL TYPES IN SCHEMA` **PostgreSQL'de yok**; tip yetkileri döngüyle
  verilir (`02_grant_app_privileges.sql`).
- `bootstrap.sh` içinde `:-` varsayılanı, açıkça boş verilen `SUPER_USER_CMD`
  değerini geçersiz kılıyordu; `-` kullanımına çevrildi.
- Dev `.env` dosyası varken "üretim çerezi güvenli mi" testi yanlış sonuç
  veriyordu; test artık ayarları temiz ortamda (`.env` devre dışı) yükler.
- Test veritabanında `hr` şeması ve yetkiler yoktu; conftest ile eklendi.

### Yapılmayan

- `manage.py migrate` çalıştırılmadı: özel kullanıcı modeli (`AUTH_USER_MODEL`)
  ilk migration'dan önce tanımlanmalıdır. Faz 2'nin ilk işidir.
- Uygulama modülleri (kiracı, kullanıcı, organizasyon) kodlanmadı.
- `~/hrsaas_db/` altındaki dosyalara dokunulmadı.

### Sıradaki adım

Kullanıcı sistem kümesinde `bash scripts/bootstrap.sh` çalıştırır; sonra Faz 2:
`tenancy` ve `accounts` uygulamaları, özel kullanıcı modeli, `hr` şemasını
oluşturan ilk migration, kimlik doğrulama + RBAC çekirdeği.

---

## [0.3.0] — 2026-09-11 — Faz 0: ortam kurulumu, depo ve test iskeleti

Karar: Python **3.13** ile devam (bkz. `ARCHITECTURE.md` karar kaydı K11).
Gerekçe: Django 5.2.8+ Python 3.14'ü desteklese de üçüncü parti paketlerin
uyumluluğu 3.13'te daha oturmuş durumda.

### Eklenen

- Python **3.13.15** sanal ortamı (uv; 3.13 zaten uv ile kuruluydu, indirme
  gerekmedi).
- `pyproject.toml` — bağımlılıklar kilitli: Django `~=5.2.0` (LTS), DRF,
  psycopg 3, django-rest-knox, django-q2, boto3, argon2-cffi, pyotp, gunicorn,
  drf-spectacular, sentry-sdk; geliştirme grubunda pytest + pytest-django +
  factory-boy + coverage + ruff + bandit + pip-audit.
- `uv.lock` — tekrarlanabilir kurulum için kilit dosyası.
- Django iskeleti: `manage.py`, `config/` (settings, urls, wsgi, asgi),
  `apps/` (uygulama modülleri için paket), `tests/`.
- `config/settings.py` — mimari kararlara göre yazıldı:
  - DEBUG varsayılan **kapalı** (üretim davranışı). `DJANGO_SECRET_KEY` her
    ortamda (geliştirme dahil) zorunludur; koda gömülü bir yedek değer **yoktur**,
    bu yüzden yanlışlıkla üretime taşınabilecek güvensiz bir varsayılan oluşamaz.
  - Veritabanı PostgreSQL'e sabit; bağlantı seçeneği olarak
    `search_path=hr,public` verildi (şema kararı uygulandı).
  - Uygulama rolü `persopyro_app` (tablo sahibi değil — RLS için kritik).
  - `PASSWORD_HASHERS`: Argon2id birincil, bcrypt uyumluluk amaçlı.
  - DRF: varsayılan izin `IsAuthenticated` (reddet); kimlik doğrulama için knox
    (opak token) + oturum; hız sınırları tanımlı.
  - django-q2: broker `orm` (Redis yok), geliştirmede senkron.
  - E-posta konsol backend'i (geliştirme), nesne depolama ve Sentry anahtarları
    ortamdan; Sentry DSN yoksa tamamen devre dışı.
  - DEBUG kapalıyken güvenlik başlıkları/çerez politikaları (HSTS, Secure,
    HttpOnly, SameSite, nosniff, DENY) otomatik uygulanır.
  - JSON formatlı loglama; kişisel veri loglanmaz.
- `tests/test_smoke.py` — veritabanına bağlanmayan 10 iskelet testi (güvenli
  varsayılanlar, PostgreSQL + hr şeması, Argon2id, reddet politikası, ORM
  broker, oturum çerezi güvenliği, admin URL çözümü, secret yoksa başlamama).
- `.env.example` — yalnızca anahtar isimleri (gerçek değer yok).
- `.gitignore` — `.env` ve türevleri, anahtarlar, sanal ortam, önbellekler hariç.

### Doğrulanan (gerçek çalıştırma)

- `manage.py check`: sorun yok (0 hata).
- `pytest`: **10 test geçti** (0,30 sn).
- `ruff check .`: temiz.
- `bandit -r config apps`: temiz (`# nosec` gerekmedi; güvensiz yedek secret
  tamamen kaldırıldığı için bulgu üretilmiyor).
- `pip-audit`: bilinen güvenlik açığı yok.
- Django sürümü: 5.2.17 (LTS), Python: 3.13.15.

### Yapılmayan / engellenen

- **PostgreSQL kurulmadı.** Kurulum `sudo` şifresi gerektirdiği için bu ortamda
  yapılamadı; komut `docs/ARCHITECTURE.md` bölüm 18'de kullanıcı için yazıldı.
  Bu nedenle `migrate` çalıştırılmadı, veritabanı oluşturulmadı.
- Uygulama modülü (kiracı, kullanıcı, organizasyon vb.) kodlanmadı; faz 1 ve
  sonrasına ait.
- Özel kullanıcı modeli (`AUTH_USER_MODEL`) henüz tanımlanmadı; ilk migration'dan
  önce ayarlanması gerektiği `config/settings.py` içinde not edildi.

### Sıradaki adım

Faz 1 (veritabanı spike'ı): PostgreSQL 17 kurulumu → `hr` şeması ve ayrı
roller (`persopyro_owner`, `persopyro_app`) → RLS + tenant bağlamı
(`SET LOCAL app.tenant_id`) → kiracılar arası erişimin reddedildiğini gösteren
ilk güvenlik testi.

---

## [0.2.0] — 2026-09-11 — Teknik mimari ve teknoloji seçimleri

### Eklenen

- `docs/ARCHITECTURE.md` tamamen yenilendi: teknoloji seçimleri, gerekçeler,
  reddedilen alternatifler, bağımlılık envanteri, karar kaydı, riskler ve
  uygulama fazları (bkz. içindekiler: bölüm 1 karar özeti, 3–16 teknoloji
  bölümleri, 19 bağımlılıklar, 20 karar kaydı, 21 riskler, 22 fazlar).

### Seçilen teknolojiler (özet)

- Backend: Python 3.13 + Django 5.2 LTS + Django REST Framework
- Frontend: Django şablonları + HTMX + Alpine.js + Tailwind CSS
- Veritabanı: PostgreSQL 17 (minimum 15), `hr` şeması, RLS (FORCE) + rol ayrımı
- Kimlik doğrulama: özel User modeli, Argon2id, HttpOnly oturum çerezi (web),
  opak ve hash'li token `django-rest-knox` (API); JWT kullanılmaz
- Yetkilendirme: mevcut RBAC tabloları; HTTP → servis → RLS üç katman
- Dosya saklama: S3 uyumlu özel nesne depolama + boto3 + ön imzalı URL
- Cache: Django DB cache; **Redis ilk sürümde yok**
- Arka plan görevleri: `django-q2` (Django ORM broker) + systemd worker
- E-posta: Django SMTP + sağlayıcı bağımsız relay, kuyruk üzerinden
- Denetim kaydı: `audit_logs` + tetikleyiciler, uygulama rolüne yalnızca INSERT/SELECT
- API: REST/JSON `/api/v1`, DRF + OpenAPI; idempotency anahtarı (kritik yazmalar)
- Test: pytest + pytest-django + factory_boy, gerçek PostgreSQL üzerinde
- Docker: üretimde kullanılmaz (systemd + venv); yerelde de zorunlu değil
- Deployment: tek Ubuntu LTS VPS, nginx + gunicorn + systemd + certbot + yedekleme

### Verilen kararlar (kod yazılmadan)

- Şemanın sahibi **Django migration'ları** olacak; mevcut `hrsaas_db` SQL
  dosyaları salt-okunur referans olarak korunacak, değiştirilmeyecek.
- Kimlik doğrulama **uygulama katmanında** yapılacak; veritabanı seviyesindeki
  `hr.authenticate_user` üretimde kullanılmayacak (parola veritabanına
  gönderilmeyecek).
- Uygulama ve migration için **ayrı veritabanı rolleri**; uygulama rolü tablo
  sahibi olmayacak (aksi halde RLS atlanır).
- Redis, Celery, Docker orkestrasyonu, Kubernetes, SPA sunucusu ve AI bağımlılığı
  bilinçli olarak **dışarıda**; her biri için yeniden değerlendirme tetikleyicisi
  tanımlandı (`ARCHITECTURE.md` bölüm 6.2 ve 20).
- Doğrulanması gereken teknik noktalar kayda geçti: RLS + bağlantı havuzu,
  PostgreSQL `ENUM` ile Django `TextChoices` farkı, `hr` şeması/`search_path`
  yaklaşımı (`ARCHITECTURE.md` bölüm 5.4).

### Değişen

- `docs/PROJECT.md`: durum "mimari planlandı"ya güncellendi; seçilen teknoloji
  yığını özeti, kapsam dışı maddeleri (Redis/Celery/Docker/K8s/AI/SSO) ve başarı
  ölçütleri (düşük maliyet, AI bağımsızlığı) eklendi.
- `docs/SECURITY.md`: parola/parola hash yaklaşımı Argon2id + uygulama katmanı
  kimlik doğrulamasına göre güncellendi; veritabanı rol ayrımı ve MFA zorunluluğu
  eklendi.
- `docs/DATABASE.md`: şema yönetimi kararı (Django migration'ları) ve kimlik
  doğrulama/parola hash kararı yansıtıldı; açık noktalar listesi güncellendi.
- `README.md`: durum satırı ve doküman açıklaması güncellendi.

### Doğrulanan (kaynak taraması)

- Django 5.2 **LTS**; en az 3 yıl güvenlik güncellemesi; Python 3.10–3.14
  destekliyor (5.2.8 itibarıyla).
- PostgreSQL güncel sürüm hattı: 18 (destekli 14–18); proje hedefi 17.
- `django-q2` aktif geliştiriliyor (v1.10.0, Mayıs 2026) ve Django ORM broker
  desteği ile Django 5.2 / Python 3.13 uyumlu.
- `django-rest-knox` (Jazzband, 5.1.0) token'ları hash'li saklıyor, süre
  sınırı ve cihaz bazlı iptal sunuyor.

### Doğrulanmayan / henüz test edilmedi

- Yığının kurulumu ve çalışması test edilmedi (paket kurulmadı, veritabanı
  oluşturulmadı, kod yazılmadı).
- RLS + tenant bağlamı davranışı gerçek PostgreSQL üzerinde denenmedi
  (ortamda `psql` kurulu değil).
- Maliyet aralığı sağlayıcıya göre doğrulanmalı.

### Yapılmayan (kapsam dışı bırakılan)

- Uygulama kodu, API, arayüz yazılmadı; paket kurulmadı; veritabanı
  oluşturulmadı. `~/hrsaas_db/` altındaki hiçbir dosya değiştirilmedi.

---

## [0.1.0] — 2026-09-11 — Proje başlangıcı ve dokümantasyon altyapısı

### Eklenen

- Persopyro projesi için çalışma alanı oluşturuldu: `/home/hakan/persopyro/`
- Proje dokümantasyon yapısı `docs/` altında kuruldu:
  - `PROJECT.md` — amaç, kapsam, durum, doküman dizini
  - `ARCHITECTURE.md` — hedef sistem mimarisi, katmanlar, tenant bağlamı
  - `SECURITY.md` — güvenlik ilkeleri, tehdit modeli, kontroller
  - `AUTHORIZATION.md` — kimlik doğrulama, RBAC, izin kodlama biçimi
  - `DATABASE.md` — mevcut `hrsaas_db` şemasının envanteri ve veri kuralları
  - `API.md` — API sözleşme kuralları ve kaynak grupları
  - `ORGANIZATION.md` — şirket/departman/pozisyon/çalışan yapısı
  - `FEATURES.md` — özellik listesi, durumları ve öncelik sırası
  - `DEVELOPMENT_RULES.md` — zorunlu geliştirme kuralları
  - `CHANGELOG.md` — bu dosya
- Kök dizine `README.md` eklendi (proje tanıtımı ve doküman bağlantıları).

### Kararlar (dokümana yazılan, kod henüz yok)

- Çok kiracılılık modeli: paylaşımlı veritabanı, `hr` şeması, satır bazlı
  izolasyon (`tenant_id`) + PostgreSQL RLS.
- İzolasyon üç katmanda uygulanır: API, servis, veri katmanı (RLS son savunma).
- Kiracı bağlamı istemciden gelen veriye göre belirlenmez.
- Yetkilendirme: RBAC + kullanıcı bazlı istisna; varsayılan davranış reddetmek.
- Kişisel veriler varsayılan görünümde maskelenir.
- Uygulama dili/çatısı henüz seçilmedi — karar bekliyor.

### Doğrulanan

- Çalışma ortamı: Ubuntu 26.04.1 LTS, git 2.53.0, Node v26.8.1, npm 11.19.0,
  Python 3.14.4, uv 0.12.10.
- Bulunmayan araçlar: `psql` (PostgreSQL istemcisi), docker, cargo, go.
- Mevcut varlık: `~/hrsaas_db/` içinde 12 SQL dosyası ve
  `README_DATABASE.md` (yaklaşık 90 tablo, 16 enum, RLS ve audit fonksiyonları).
- `~/ckay/` dizini bu projeyle ilgisizdir (ayrı bir Python sanal ortamı).

### Değiştirilmeyen

- `~/hrsaas_db/` altındaki tüm dosyalar olduğu gibi bırakıldı.
- Hiçbir dosya silinmedi veya taşınmadı.

### Yapılmayan (kapsam dışı bırakılan)

- Uygulama kodu, veritabanı oluşturma, API ve arayüz kodlaması yapılmadı.

### Sıradaki adımlar

1. Uygulama dili ve çatısının seçilmesi (`ARCHITECTURE.md` tablosunun
   güncellenmesi).
2. `hrsaas_db` şemasının gerçek bir PostgreSQL örneğinde doğrulanması
   (bu ortamda istemci kurulu değil).
3. Uygulama veritabanı rolü ve `GRANT` planının yazılması; RLS
   etkinleştirilecek tablo listesinin netleştirilmesi.
4. Sürüm kontrolü kurulumu (git deposu başlatılması) — kullanıcı onayı ile.
