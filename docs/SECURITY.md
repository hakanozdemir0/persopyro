# Güvenlik — Persopyro

Güvenlik bu projede birinci önceliktir (`docs/DEVELOPMENT_RULES.md`). Bu doküman
güvenlik ilkelerini, tehdit modelini, uygulanmış kontrolleri ve uygulanacak
kontrolleri tanımlar. Yetkilendirme modelinin ayrıntısı
`docs/AUTHORIZATION.md` içindedir.

**İşaretler:** ✅ uygulandı ve test edildi · 🔜 tasarlandı, kodlanmadı ·
❓ karar bekliyor (`docs/AUTHORIZATION.md` bölüm 14).

---

## 1. Değişmez ilkeler

1. Güvenlik, teslim hızından önce gelir.
2. Kiracı izolasyonu hem uygulama hem veritabanı katmanında uygulanır;
   veritabanı katmanı **fail-closed**'dur.
3. Kişisel veriler varsayılan olarak korunur ve minimum düzeyde gösterilir.
4. Her önemli işlem denetim kaydına yazılır; yazılamazsa işlem tamamlanmaz 🔜.
5. Secret ve API anahtarları kaynak kodda yer almaz.
6. Ayrıcalıklı erişim (platform, destek) **süreli, gerekçeli, onaylı ve kayıtlı**
   olmak zorundadır.
7. Güvenlik kararı, "şimdilik hızlı çözelim" gerekçesiyle gevşetilmez.

---

## 2. Tehdit modeli

| # | Tehdit | Etki | Temel kontrol |
|---|---|---|---|
| T1 | Bir kiracının başka bir kiracının verisine erişmesi (tenant escape) | Felaket | Zorunlu kiracı bağlamı + RLS + FORCE + kiracılar arası FK yasağı ✅ |
| T2 | Yetkisiz kullanıcının yetkisi dışındaki işlemi yapması | Yüksek | RBAC + kapsam + koşul; servis katmanında kontrol ✅/🔜 |
| T3 | Kimlik bilgisi sızması (parola, token) | Yüksek | Argon2id, hash'li süreli token, MFA (TOTP) ✅ |
| T4 | Evrak dosyalarına yetkisiz erişim | Yüksek | Kısa ömürlü imzalı bağlantı, erişim kaydı, sınıflandırma ✅/🔜 |
| T5 | Denetim kaydının manipüle edilmesi/eksik kalması | Orta-Yüksek | Append-only tablo, uygulama rolüne UPDATE/DELETE yasağı ✅; zincirleme 🔜 |
| T6 | Dosya yükleme üzerinden zararlı içerik | Orta | MIME/boyut/checksum doğrulaması ✅; içerik taraması ❓ |
| T7 | Yatay/dikey yetki yükseltme (IDOR) | Yüksek | Her kayıt erişiminde kiracı + kapsam kontrolü ✅/🔜 |
| T8 | Kişisel verinin gereğinden fazla gösterilmesi/sızması | Orta-Yüksek | Varsayılan maskeleme, en az veri, amaca bağlama 🔜 |
| T9 | Secret'ın depoya veya loglara düşmesi | Yüksek | Ortam değişkeni, log temizleme, tarama ✅ |
| T10 | Brute force / parola deneme saldırısı | Orta | Deneme sınırı, hesap kilitleme, hız sınırlama ✅ |
| **T11** | **İçeriden kötüye kullanım (insider): yetkili bir kişinin gereksiz kişisel veri bakması** | **Yüksek** | **Maskeleme + amaç zorunluluğu + erişim kaydı + kiracı görünürlüğü** 🔜 |
| **T12** | **Platform/destek yetkisinin kötüye kullanımı veya kalıcı arka kapı haline gelmesi** | **Felaket** | **Süreli erişim izni, çift onay, otomatik kapanma, kiracıya görünürlük, ayrı katman + ayrı DB rolü (OD1/OD3/OD4 — karar verildi)** 🔜 |
| **T13** | **Genel kimlik üzerinden kişisel veri numaralandırması (enumeration)** | **Orta** | **Rastgele kimlik, hız sınırı, kayıtsız çözümleme yasağı, ayırt edilemez yanıt** 🔜 |
| **T14** | **Evrakın yanlış sınıflandırılması (kişisel veri "yok" denmesi)** | **Yüksek** | **"Emin değilim" = en kısıtlayıcı politika, sınıflandırma onayı, rastgele denetim** 🔜 |
| **T15** | **Süresi geçmiş/iptal edilmiş onayla erişim (yetki kalıntısı)** | **Yüksek** | **Koşul kontrolü her istekte (GRANT_VALID), veritabanı politika kontrolü** 🔜 |
| **T16** | **Kendi kendini yetkilendirme (self-elevation)** | **Yüksek** | **Ayrılık ilkesi: talep eden onaylayamaz, kendi kapsamını genişletemez** 🔜 |
| **T17** | **Yedek/export üzerinden toplu veri sızması** | **Yüksek** | **Maskeli varsayılan export, kayıtlı/kısıtlı toplu indirme, şifreli yedek** 🔜 |
| **T18** | **Paralel yetkilendirme yolu: `/admin/` + `is_staff` + `auth_permission` uygulamanın RBAC'ini atlar** | **Yüksek** | **Karar verildi (OD19 — kapalı): `/admin/` kaldırılır, `auth_*` tablolarının yetkileri geri alınır, `is_staff`/`is_superuser` tek başına yetki vermez.** Uygulama bekliyor 🔜 |
| **T19** | **Fazla yetki: uygulama rolünün global tanım tablolarını (`permissions`) ve yansıları yazabilmesi** | **Orta** | **`permissions` yazma yetkisi geri alınır; yeni tablolarda yetki turu (`docs/DATABASE.md` D.6)** 🔜 |
| **T20** | **Yetki çürümesi: soft-delete edilmiş rol, atama veya izin istisnası yetki vermeye devam ediyor** | **Yüksek** | **OD21 kararı: yetki çözümlemesi `deleted_at IS NULL` uygular; `objects` silinmişi gizler; geri alma açık izin + kayıt gerektirir (F7)** ⚠️ uygulama bekliyor |
| **T21** | **Kapsamsız/uzun ömürlü API token'ı: token kullanıcının tüm yetkilerini taşır, iptalin denetim izi kalmaz** | **Orta** | **Platform token'ları OD22 ile sınırlandı (zorunlu kapsam, `revoked_at`, kısa TTL); tenant tarafı OD26 kararı bekliyor (F8)** ⚠️ |

---

## 3. Kimlik ve parola güvenliği

- **Kimlik doğrulama uygulama katmanında yapılır** ✅. Parola veritabanı
  fonksiyonuna gönderilmez; politika, kilitleme ve MFA mantığı tek yerdedir.
- Parolalar asla düz metin saklanmaz: **Argon2id** ✅ (mevcut bcrypt hash'leri
  için uyumluluk hasher'ı vardır; yeni parolalar Argon2id ile yazılır).
- Kullanıcı adı (e-posta) **kiracı başına** benzersizdir ✅; giriş kiracı
  belirtilmeden yapılmaz. Aynı e-posta farklı kiracılarda farklı kişilerdir.
- Kullanıcı numaralandırmasına karşı: var olmayan kullanıcı, yanlış parola ve
  pasif hesap **aynı** yanıtı verir ✅.
- Başarısız girişler sayılır ve hesap kilitlenir ✅
  (`LOGIN_MAX_FAILED_ATTEMPTS=5`, `LOGIN_LOCKOUT_MINUTES=15`); kilit süresince
  doğru parola da kabul edilmez.
- **MFA (TOTP)** ✅ uygulandı: hem oturum hem API token girişinde zorunludur
  (hesapta açıksa). TOTP sırrı veritabanında **şifreli** saklanır (Fernet ✅);
  anahtar okunamazsa giriş reddedilir (fail-closed).
- Giriş uçları en katı hız sınırı sınıfındadır ✅ (`auth`, önbellek destekli);
  sınır aşımında 429.
- **Oturum** ✅: veritabanında tutulur, ömrü 12 saat, çıkışta sunucu tarafında
  geçersiz kılınır. Üretimde `Secure`/`HttpOnly`/`SameSite` çerez ayarları
  zorunludur (`DEBUG=0` ile etkin).
- **API token** ✅: opak, yalnızca **SHA-256 özeti** saklanır, varsayılan ömrü
  30 gündür, cihaz bazlı iptal edilir; geçersiz/süresi geçmiş token 401 döner.
  JWT kullanılmaz — iptal edilebilirlik güvenlik gereğidir.
- Parola sıfırlama 🔜: tek kullanımlık, süreli, hash'li token; kullanıcı
  numaralandırmasına karşı her durumda aynı yanıt.
- MFA zorunluluğu rollerle ilişkilendirilir ❓OD10.
- **Django Admin kapısı kapalı (OD19 — kapalı):** üretimde `/admin/` yolu
  yayınlanmaz ve `django.contrib.admin` uygulama listesinde tutulmaz. Belirleyici
  ölçüm: Django'nun `AdminSite.has_permission` kapısı `is_active and is_staff`
  (kurulu kaynak, `django/contrib/admin/sites.py:207`) — yani `is_staff=True` olan
  **aktif** bir kullanıcı, ayrı bir admin girişi yapmadan mevcut oturumuyla admin
  dizinini açabilir. Bugün etki boş dizinle sınırlıdır (hiçbir model kayıtlı
  değil), ama kapı kapatılır.
- **`is_staff` / `is_superuser` kuralı (OD19 — kapalı):** `is_superuser` alanı
  **hiç eklenmez** (`PermissionsMixin` kullanılmaz); `is_staff` **tek başına hiçbir
  Persopyro yetkisi vermez**, varsayılanı `False`'dur ve üretimde **hiçbir kiracı
  kullanıcısında `True` olmamalıdır**; `create_superuser` akışı artık
  `is_staff=True` atamaz (uygulama kalemi). Üretimde `is_staff = true` olan aktif
  kullanıcı bulunması **denetim sorgusu** ile izlenir ve testle zorlanır.
- **Admin giriş formu kiracısız çalışmaz (mevcut koruma):** kimlik doğrulama
  backend'imiz `tenant` olmadan `None` döner (fail-closed), dolayısıyla
  `/admin/login/` formu bir kiracı kullanıcısıyla giriş yapamaz. Riski yaratan şey
  bu form değil, **mevcut oturum + `is_staff`** birleşimidir.
- Kurtarma akışı (MFA cihaz kaybı) kimlik doğrulamalı ve kayıtlı olmalıdır 🔜;
  kurtarma, güvenlik zincirinin en zayıf halkasıdır.

---

## 4. Çok kiracılı izolasyon (kritik kontrol)

### 4.1 Uygulanmış ✅

- Kiracıya bağlı **her** tabloda `tenant_id` vardır; kiracılar arası yabancı
  anahtar **veritabanı tetikleyicisiyle** engellenir.
- Tüm iş tablolarında **RLS + FORCE** etkindir; politika
  `tenant_id = hr.current_tenant_id()` üzerinedir.
- Uygulama rolü `persopyro_app`: süper kullanıcı değildir, tablo sahibi
  değildir, RLS'i atlayamaz, şema/rol oluşturamaz. Migration ayrı rolle
  (`persopyro_owner`) çalışır.
- Kiracı bağlamı, kimlik doğrulamadan **önce** kurulur
  (`RequestIdMiddleware` → `TenantContextMiddleware` → `ApiTokenTenantMiddleware`
  → `AuthenticationMiddleware`).
- Oturumdaki kiracı ile alan adının kiracısı uyuşmazsa istek **reddedilir**.
- Bağlam yokken sorgu **0 satır** döner (hata değil, sessiz reddetme) — sızma
  yerine erişimsizlik.
- Kiracı kimliği istemciden gelen veriye dayanarak **belirlenmez**; oturum,
  alan adı veya token'dan çözülür.
- Giriş öncesi okunması gereken eşleme verisi (`tenant_directory`) RLS dışıdır
  ama uygulama rolüne **yalnızca okuma** yetkisi verilmiştir (yazma
  tetikleyiciyle yapılır); gerekçesi `docs/DATABASE.md` bölüm 3.4'tedir.

### 4.2 Platform erişimi: ayrı ve kontrollü katman 🔜 KARAR (OD1/OD3 — kapalı)

Platform erişimi kiracı izolasyonunu **gevşetmez**. Karar (2026-09-11):

- **RLS politikaları değişmez.** Kiracı tablolarındaki politikalara platform
  istisnası (koşullu bayrak vb.) **eklenmez**; `tenant_id = hr.current_tenant_id()`
  ifadesi olduğu gibi kalır.
- **`BYPASSRLS` yoktur** (kesin yasak); süper kullanıcı hesabı kullanılmaz.
- **Ayrı katman, ayrı rol:** platform erişimi kiracı istek hattından ayrı bir
  servis/authorization katmanından ve **ayrı bir veritabanı rolünden**
  (`persopyro_platform`) yürür. Kiracı rolünün (`persopyro_app`) platform
  şemasında hiçbir yetkisi yoktur; platform katmanı kiracı istek hattından
  **çağrılamaz**.
- **Kiracı bağlamı yalnızca onayla kurulur:** bağlamı kurabilen tek yer platform
  katmanıdır ve bunu yalnızca geçerli, süreli bir `AccessGrant` doğrulandıktan
  sonra, **tek bir işlem** için yapar.
- **Kiracı barındırma yasağı:** platform kullanıcıları kiracı kullanıcı
  tablosunda barındırılmaz, birleştirilmez.

Aşağıdaki koşulların **tümü** sağlanmadan erişim yoktur:

| Koşul | Değer |
|---|---|
| Varsayılan | **Erişim yok** (fail-closed) |
| Onay kaydı | Geçerli `AccessGrant`; kiracı içinde tek onay, platformda **çift onay** (talep sahibi onaylayamaz) |
| Gerekçe | Zorunlu ve anlamlı (boş, tek kelime veya şablon metin reddedilir) |
| Süre | Varsayılan 60 dakika; dolduğunda **otomatik kapanır**; uzatma yeni onay gerektirir |
| Kapsam | Tek kiracı, talep edilen kaynak türleri ve alanlar |
| Kişisel veri | Varsayılan maskeli; maskesiz erişim ayrı onay (K2/K3) |
| Kayıt | Talep, onay, red, erişim başlangıcı, erişim bitişi → denetim kaydı |
| Görünürlük | Kiracı, kendi verisine yapılan erişimi görür (kapsam: **OD15** açık) |

Yasaklar: kalıcı platform erişimi, kişisel veriyi toplu çekme, onay kaydı olmadan
kiracı bağlamı değiştirme, `BYPASSRLS`, platform kullanıcısını kiracı tablosunda
barındırma, kiracı RLS politikasını platform için gevşetme.

---

## 5. Yetkilendirme

Ayrıntı: `docs/AUTHORIZATION.md`. Güvenlik açısından özet:

- Model **RBAC + kapsam + kapalı koşul kümesi**; varsayılan **reddetme** ✅.
- Kod `<kaynak>.<işlem>` biçimindedir ✅; kapsam ve koşul koda gömülmez.
- Çakışmada **reddet kazanır** ✅ (kişiye özel yasak, rol iznini geçersiz kılar).
- Yetki kontrolü dört katmanda: uç nokta → servis → nesne → veritabanı (RLS) ✅.
- Kapsam dışı kayıt için "yetkin yok" değil **"bulunamadı"** döner (varlık
  sızdırmama) 🔜.
- Kendi kendini yükseltme yasağı 🔜: kullanıcı kendi rolüne izin ekleyemez,
  kapsamını genişletemez, kendi talebini onaylayamaz (ayrılık ilkesi).
- Son kiracı yöneticisi kaldırılamaz/pasifleştirilemez 🔜 (kilitlenme koruması).
- **Tek yetkilendirme yolu — KARAR VERİLDİ (OD19 kapalı):** Yetki kararı
  **yalnızca** bu modelde verilir. Django'nun kendi yetki sistemi
  (`auth_permission`, `auth_group`, `/admin/`, `is_staff`, `is_superuser`) üretimde
  **hiçbir yetki vermez**; `/admin/` kaldırılır; `auth_*` ve `django_admin_log`
  tablolarında uygulama rolünün **tüm yetkileri geri alınır**. Bu tablolar teknik
  olarak kalabilir (`django.contrib.auth`, `AbstractBaseUser`, parola hash'leme ve
  oturum kimlik doğrulaması için gereklidir) ama **izin kaynağı değildir**.
  İleride admin kullanımının ön koşulları OD19'da yazılıdır.
- **Tanım tabloları salt okunur:** `hr.permissions` (global katalog) ve yansı/dizin
  tabloları uygulama rolünde yalnızca **SELECT** olmalıdır; `ALTER DEFAULT
  PRIVILEGES` ile gelen geniş DML her yeni tabloda daraltılır 🔜
  (`docs/DATABASE.md` D.6).

- **Platform rolleri ayrı kimlik alanında yaşar** 🔜 KARAR (OD3 — kapalı): platform
  kullanıcıları kiracı kullanıcı tablosunda barındırılmaz; kiracı RLS politikaları
  onlar için gevşetilmez.
- **Gerekçe niteliği ve çift onay denetlenir** 🔜 KARAR (OD4 — kapalı): boş veya
  anlamsız gerekçeyle erişim talebi açılamaz; platform tarafında iki farklı
  onaylayıcı aranır.

### 5.1 Açık bulgular (SECURITY FINDING)

**F7 — Soft-delete yetkiyi kaldırmıyor (Yüksek, açık).** Ölçüm:
`apps/common/models.py` içindeki `SoftDeleteModel` alanı sağlıyor ama **filtreleyen
yönetici tanımlı değil**; `apps/rbac/services/permissions.py` içindeki
`_active_role_ids`, `RolePermission` ve `UserPermissionOverride` sorguları
**`deleted_at` filtresi kullanmıyor**. Sonuç: soft-delete edilmiş bir rol, rol
ataması veya `allow` istisnası **yetki vermeye devam eder** (yetki çürümesi).
Bugün **aktif bir sömürü yolu yoktur** (silme/geri alma servisi henüz yazılmadı),
ancak politika ve temel model hazır olduğu için ilk "rolü sil" özelliğiyle birlikte
gerçek bir açık hâline gelir. Ayrıca `create_default_roles` içindeki `get_or_create`
silinmiş rolü "var" sayıp atlar (işlevsel hata). **Kapatma yolu (OD21 kararı):**
`objects` yöneticisi silinmişi gizler, yetki çözümlemesi `deleted_at IS NULL`
uygular, geri alma açık `*.restore` izni + denetim kaydı gerektirir (T20).

**F8 — Tenant API token'ı kapsamsız ve iptal izi yok (Orta, açık).** Ölçüm:
`hr.api_tokens` alanları `tenant_id, user_id, token_hash, label, expires_at,
last_used_at, created_at`tır; **`scope` ve `revoked_at` yoktur**, TTL sabittir
(`API_TOKEN_TTL_HOURS=720` = 30 gün). Sonuç: bir token sahibinin **tüm** yetkilerini
taşır (en az yetki kimlik bilgisi seviyesinde uygulanmaz) ve iptal satır silme
olduğu için **iptal edildiği kanıtlanamaz**. Platform token'ları OD22 ile
sınırlandırıldı; tenant tarafı **OD26** kararına bağlandı. Geriye dönük uyumlu ara
adım: `revoked_at` eklemek (kapsam kararı ayrı) (T21).

---

## 6. Ayrıcalıklı erişim güvenliği (platform ve destek)

Bu bölüm, sistemin en yüksek riskli kod yolunu tanımlar: kiracı sınırını aşan
erişim.

- **Süreli ve gerekçeli:** her ayrıcalıklı erişim bir talep kaydına bağlıdır;
  gerekçe zorunludur, anlamlı olmalıdır ve denetim kaydına geçer 🔜.
- **Varsayılan erişim yok:** onay yoksa, süre dolmuşsa veya kapsam dışıysa kiracı
  verisi görünmez; bu kural **SUPERADMIN için de geçerlidir** 🔜 (OD1 — kapalı).
- **Ayrı katman ve ayrı veritabanı rolü:** platform erişimi kiracı istek
  hattından bağımsızdır; kiracı RLS politikaları gevşetilmez ve `BYPASSRLS`
  kullanılmaz 🔜 (OD3 — kapalı, bölüm 4.2).
- **Çift onay:** platform tarafındaki kişisel veri erişimini **iki farklı**
  onaylayıcı onaylar; talebi açan onaylayamaz 🔜 (OD4 — kapalı).
- **Ayrılık ilkesi:** bir kişi hem platform hem kiracı yöneticisi rolü taşımaz
  ❓OD11.
- **Kapsam en azdır:** talep edilen kaynakla sınırlıdır; "tüm kiracı verisi"
  şeklinde toplu erişim verilmez.
- **Otomatik kapanma:** süre dolduğunda erişim kendiliğinden biter; uzatma yeni
  bir onay gerektirir 🔜.
- **İzlenebilirlik:** ayrıcalıklı oturumlar normal oturumlardan ayırt edilebilir
  şekilde kayıtlıdır; şüpheli desen (gece saatleri, olağandışı hacim, sürekli
  erişim) alarm üretir 🔜.
- **Kiracıya şeffaflık:** kiracı yöneticisi kendi verisine yapılan platform
  erişimlerini görür ❓OD15.
- **Platform token'ları (OD22 — kapalı):** yalnızca `platform.tokens`'ta tutulur;
  ham değer saklanmaz (yalnızca SHA-256 özeti); **kapsam (`scope`) ve amaç
  (`purpose`) zorunludur**; `expires_at` zorunlu, `revoked_at` ile iptal kayıtlıdır;
  `interactive` TTL ≤ 24 saat, `service` TTL ≤ 90 gün ve **superadmin izin kümesiyle
  üretilemez**; boş kapsamla üretim reddedilir. **Platform token'ı tenant RLS sınırını
  hiçbir koşulda aşamaz** (platform rolü `NOBYPASSRLS`); kiracı verisi gerektiğinde
  erişim onayı mekanizmasından geçer. Uzun ömürlü sınırsız SUPERADMIN token **yasaktır**. 🔜
- **Acil durum (break-glass):** önceden onay alınamayan gerçek acil durumlarda
  erişim açılabilir; ancak **sonradan gözden geçirme** zorunludur ve kayıt
  silinemez. Sınırları (kim kullanabilir, nasıl incelenir) **OD17** kararına
  bağlıdır — bu, OD1/OD4'ün kapatılmasıyla ortaya çıkan yeni açık karardır.

---

## 7. Kişisel veri güvenliği (KVKK/GDPR yaklaşımı)

Ayrıntı: `docs/AUTHORIZATION.md` bölüm 7.

- **Sınıflandırma 🔜:** K0 (kişisel değil) · K1 (kurumsal kimlik) · K2 (kişisel
  veri) · K3 (özel nitelikli: sağlık, biyometri, din, sendika, ceza kaydı) ·
  K4 (evrak içeriği).
- **Varsayılan maskeleme 🔜:** ad, özel e-posta, telefon, adres, doğum tarihi,
  TCKN ve tüm K3 alanları varsayılan olarak maskelidir; maskeleme **sunucu
  tarafında** yapılır, istemciye maskesiz veri gönderilmez.
- **Amaç bağlama 🔜:** maskesiz görüntüleme, gerekçe girilmeden mümkün değildir;
  gerekçe kayda geçer.
- **Süreli izin 🔜:** K3 ve toplu erişim, onaylı ve süreli izin gerektirir
  (koşul: `GRANT_VALID`).
- **Özel nitelikli veriler 🔜:** K3 alanları veritabanında şifreli saklanır
  (mevcut Fernet altyapısı ✅ bu deseni TOTP sırrı için zaten kullanıyor).
- **En az ifşa:** yalnızca iş için gereken alanlar döner; liste uçları tam
  kişisel veri döndürmez 🔜.
- **Log ve hata mesajlarına kişisel veri yazılmaz** ✅ (denetim kaydı da verinin
  değerini değil, alan adını taşır 🔜).
- **Veri sahibi hakları 🔜:** erişim, düzeltme, silme, itiraz talepleri
  (`dsr.*`) kayıtlı bir akışla işlenir; talep ve yanıt süreleri takip edilir.
- **Saklama ve anonimleştirme 🔜:** saklama süresi tanımlıdır; süre dolunca
  kayıt anonimleştirilir veya silinir; hangi alanların anonimleştirileceği
  `docs/DATABASE.md` içinde kayıtlıdır.
- **Yedekler 🔜:** şifrelidir ve erişim kaydı tutulur; yedek, canlı sistemin
  tüm veri koruma kurallarına tabidir (yedek erişimi bir ayrıcalıklı erişimdir).

---

## 8. Genel kullanıcı kimliği güvenliği

Ayrıntı: `docs/AUTHORIZATION.md` bölüm 8.

- Kimlik **rastgele** üretilir (Crockford Base32, 8 karakter, `secrets`),
  isim/e-posta/ID'den **türetilmez**, kullanıcı tarafından değiştirilemez 🔜.
- Sıralı/sayılabilir kimlik ❌ reddedilir: numaralandırma saldırısına açıktır.
- Ters çözümleme (`public_id` → gerçek kişi) izin + gerekçe + kayıt gerektirir;
  **toplu çözümleme API'de desteklenmez** 🔜.
- Çözümleme denemeleri hız sınırlıdır; başarısız denemeler de sayılır 🔜.
- Uç nokta, "kayıt yok" ile "yetkin yok" arasında **ayırt edilemez** yanıt
  verir (varlık sızdırmama) 🔜.
- Genel kimlik tek başına kişisel veri **değildir**; ancak eşleme tablosuna
  erişim ayrıcalıklı işlem sayılır ve kayıtlanır.

---

## 9. Evrak ve dosya güvenliği

- Dosyalar nesne depolamada tutulur; veritabanı yalnızca üst veri saklar ✅.
- `checksum_sha256` ile bütünlük doğrulanır ✅.
- Doğrudan genel URL verilmez; **kısa ömürlü imzalı bağlantı** üretilir ✅.
- Yüklemede boyut, MIME türü ve uzantı doğrulanır ✅; içerik taraması
  (zararlı yazılım) ❓ karar bekliyor.
- **Yükleme anında sınıflandırma 🔜:** "Bu evrak kişisel veri içeriyor mu?"
  işareti + gizlilik düzeyi + saklama süresi zorunludur. "Emin değilim"
  seçilirse evrak **en kısıtlayıcı** politikayla başlar ve sınıflandırma onayı
  bekler ❓OD9 (işaretin kim tarafından doğrulanacağı).
- **Erişim politikası 🔜:** kişisel veri içeren evraklar yalnızca yetki +
  gerekçe (+ gerekiyorsa süreli izin) ile açılır; K3 evrakı için varsayılan
  erişim **reddedilir**.
- **Varlık ≠ içerik 🔜:** "evrak var" bilgisi ile "içeriğini gör/indir" farklı
  izinlerdir.
- Görüntüleme/indirme/paylaşım/silme işlemleri denetim kaydına yazılır 🔜.
- Paylaşım bağlantıları süreli, tek amaçlı ve iptal edilebilirdir 🔜.
- Silme, mantıksal silme + saklama süresi sonunda gerçek silme şeklindedir;
  hukuki saklama gerektiren evraklar kullanıcı tarafından silinemez ❓OD6.

---

## 10. Denetim kaydı ve bütünlük

- `hr.audit_logs` ✅: kim (iç kimlik + **genel kimlik**), kiracı, işlem kodu,
  hedef türü/kimliği, sonuç, istek kimliği, IP, kullanıcı aracısı, zaman (UTC).
- Uygulama rolünün tablo üzerinde **INSERT ve SELECT** yetkisi vardır;
  `UPDATE`/`DELETE` yetkisi **yoktur** ✅ — kayıt değiştirilemez/silinemez.
- Yazım `log_action` servisi üzerinden yapılır ve kiracı bağlamı içinde
  çalışır ✅.
- Kaydedilmesi zorunlu olaylar listesi: `docs/AUTHORIZATION.md` bölüm 10.1
  (kimlik doğrulama, yetki değişikliği, kullanıcı yaşam döngüsü, veri
  görüntüleme, veri değiştirme, onay/red, evrak işlemleri, platform erişimi,
  yapılandırma).
- Eklenecek alanlar 🔜: `purpose` (gerekçe), `grant_id` (onay kaydı),
  `changed_fields` (değer değil, alan adı), `access_scope`, `source`.
- **Bütünlük 🔜:** kayıtlar zincirlenir (önceki kaydın özeti); zincir kırılırsa
  tespit edilir ❓OD13.
- **Fail-closed 🔜:** güvenlik açısından kritik işlemlerde denetim kaydı
  yazılamazsa işlem geri alınır ❓OD13.
- **Kiracı görünürlüğü:** kiracı, yalnızca kendi denetim kayıtlarını görür
  (RLS) ✅.
- Saklama süresi ve arşivleme ❓OD13; teknik loglar denetim kaydından ayrıdır ve
  daha kısa süre saklanır.
- Teknik loglar kişisel veri içermez ✅; log dosyalarına erişim kısıtlıdır ve
  üretimde merkezî toplama hedeflenir 🔜.

---

## 11. Secret yönetimi

- Secret'lar ortam değişkeninden okunur; koda, örnek dosyaya veya dokümana
  gerçek değer yazılmaz ✅. Depoda yalnızca `.env.example` bulunur ✅.
- `.env` dosyası depo dışındadır ve izinleri `0600`'dür ✅ (bootstrap betiği
  üretir).
- `DJANGO_SECRET_KEY` her ortamda zorunludur; gömülü varsayılan **yoktur** ✅
  (üretimde en az 50 karakter; `check --deploy` bunu denetler ✅).
- Parola/anahtar üretimi `secrets` modülüyle yapılır ✅; üretilen değerler
  ekrana yazılırken log'a düşmez.
- Alan şifreleme anahtarı (`FIELD_ENCRYPTION_KEY`) koddan ayrıdır ✅; anahtar
  değişimi (rotasyon) planı 🔜 gereklidir — mevcut şifreli alanların yeniden
  şifrelenmesi gerekir.
- Bir secret sızdığından şüphelenilirse önce döndürülür (rotate), sonra olay
  kaydı açılır.

---

## 12. Altyapı ve üretim güvenliği

- Tüm trafik TLS üzerinden taşınır; HSTS üretimde etkindir.
- Uygulama veritabanına sınırlı yetkili `persopyro_app` rolüyle bağlanır ✅;
  migration ayrı sahip rolle çalışır ✅.
- Girdi doğrulama her uç noktada zorunludur; sorgular parametreli çalıştırılır ✅.
- Bağımlılıklar denetlenir (`pip-audit`, `bandit`, `ruff`) ✅; bulgu işlenmeden
  teslim edilmez.
- Hata mesajları iç durum, sorgu veya yığın izi sızdırmaz ✅; giriş/hata
  yanıtları ayırt edilemez ✅.
- Üretim ayarları `manage.py check --deploy` ile denetlenir ✅.
- Yedekleme 🔜: şifreli, otomatik ve **geri yükleme testi** yapılmış olmalıdır;
  yedek dosyaları canlı sistemle aynı erişim kurallarına tabidir.
- Sistem güncellemeleri (işletim sistemi, PostgreSQL, Python bağımlılıkları)
  planlı pencerede uygulanır; güvenlik yaması ertelenmez.

---

## 13. Olay müdahalesi (incident response)

1. **Tespit:** şüpheli erişim desenleri, hız sınırı alarmları, denetim
   incelemesi veya bildirim.
2. **Sınırlama:** etkilenen oturum/token'ları iptal et, hesabı kilitle, gerekirse
   ayrıcalıklı erişimi kapat.
3. **Kanıt:** denetim kayıtları **değiştirilmeden** saklanır (append-only ✅).
4. **Değerlendirme:** hangi kiracılar, hangi veriler, kaç kişi etkilendi.
5. **Bildirim:** kişisel veri ihlali ise KVKK gereği **en kısa sürede** (72 saat
   kuralı) ilgili makama ve etkilenen kişilere bildirim değerlendirilir.
6. **Düzeltme:** kök neden giderilir, test eklenir, kontrol listesi güncellenir.
7. **Kapanış:** olay kaydı ve alınan dersler yazıya geçirilir; secret'lar
   döndürülür.

---

## 14. Güvenlik kontrol listesi (her değişiklikte)

- [ ] Bu değişiklik kiracı izolasyonunu etkiliyor mu?
- [ ] Yeni uç nokta/tablo eklendiyse kiracı filtresi ve RLS var mı?
- [ ] Yetki kontrolü tanımlandı mı; varsayılan reddetme korunuyor mu?
- [ ] Kapsam (scope) ve koşul (condition) kontrolü yapıldı mı; kayıt düzeyinde
      sahiplik doğrulandı mı?
- [ ] Kişisel veri dönüşü minimum ve maskeli mi; gerekçe alınıyor mu?
- [ ] Genel kimlik dışında kişisel veri gereksiz yere gösteriliyor mu?
- [ ] Ayrıcalıklı (platform/destek) bir yol açıldıysa süreli, onaylı ve kayıtlı mı?
- [ ] Kendi kendini yetkilendirme veya talep-onay birleşmesi mümkün mü?
- [ ] Kritik işlem denetim kaydına yazılıyor mu; kayıt başarısızsa işlem duruyor mu?
- [ ] Secret veya gerçek anahtar koda/log'a sızdı mı?
- [ ] Evrak eklendiyse sınıflandırma ve erişim politikası tanımlı mı?
- [ ] Test edildi mi; negatif test (yetkisiz/başka kiracı) eklendi mi?

---

## 15. Açık güvenlik kararları

Yetkilendirme tarafındaki kararlar `docs/AUTHORIZATION.md` bölüm 14'te
listelenmiştir (OD1–OD25).

**Kapatılan güvenlik kararları (2026-09-11):**

- **G1** — platform erişiminin teknik mekanizması (OD1/OD3 kapalı): **ayrı
  servis/authorization katmanı + ayrı veritabanı rolü, kiracı RLS politikaları
  değişmez, `BYPASSRLS` yasak** (bölüm 4.2).
- **G8** — Django'nun paralel yetki yolu (OD19 kapalı): **`/admin/` kaldırılır,
  `auth_*` tablolarının yetkileri geri alınır, `is_staff`/`is_superuser` tek
  başına yetki vermez** (bölüm 3 ve 5).

Açık olanlar:

| # | Karar | Öneri |
|---|---|---|
| G2 | Yüklenen dosyaların zararlı içerik taraması | VPS kaynaklarına uygun hafif bir tarama; boyut/MIME doğrulaması tek başına yeterli değildir |
| G3 | Denetim kaydı bütünlüğü (zincirleme) ve saklama süresi | Zincirleme + 10 yıl (OD13) |
| G4 | Yedek şifreleme ve geri yükleme testi sıklığı | Şifreli, haftalık geri yükleme testi |
| G5 | `FIELD_ENCRYPTION_KEY` rotasyon planı | Anahtar sürümleme + yeniden şifreleme betiği |
| G6 | Acil durum (break-glass) erişiminin sınırları ve sonradan gözden geçirme | Sonradan zorunlu inceleme; kayıt silinemez; **OD17** ile birlikte kararlaştırılır |
| G7 | Platform çift onayının tek yönetici / acil durum durumunda uygulanması | Fail-closed varsayılan + sınırlı break-glass (**OD17**) |
| G9 | Fazla yetki turu: global tanım tabloları ve yansılarda uygulama rolü yazma yetkisi | `permissions` yazma yetkisi geri alınır; yeni tablolarda yetki turu (`docs/DATABASE.md` D.6) |
