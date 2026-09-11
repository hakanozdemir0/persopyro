# Değişiklik Kaydı — Persopyro

Kayıt biçimi: en yeni sürüm en üstte. Her giriş tarih, sürüm, eklenen /
değişen / doğrulanan / kalan başlıklarını içerir.

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
