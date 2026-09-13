# Mimari — Persopyro

Bu doküman Persopyro'nun teknik mimarisini ve teknoloji seçimlerini tanımlar.
**Bu aşamada hiçbir kod yazılmamıştır**; buradaki kararlar bağlayıcı tasarım
kararlarıdır ve `DEVELOPMENT_RULES.md` gereği gerekçesiz değiştirilmez.

Sürüm: 0.2.0 — 2026-09-11 (teknoloji seçimleri)
Önceki durum (0.1.0): yalnızca katmanlar ve izolasyon ilkeleri tanımlıydı;
teknoloji seçimleri bu sürümde yapılmıştır.

## 0. Öncelik sırası (kararların dayanağı)

1. Güvenlik
2. Multi-tenant izolasyon
3. Düşük sunucu maliyeti
4. Kolay bakım
5. KOBİ'den başlayıp büyüyebilme
6. VPS üzerinde kolay deployment
7. İleride AI olmadan bağımsız çalışabilme
8. Gereksiz teknoloji/dependency kullanmama

Her seçim bu sıraya göre değerlendirilmiştir. Örneğin "daha az dependency"
(8) ile "daha iyi bakım" (4) çatıştığında, bakımı uzun vadede kolaylaştıran tek
bir olgun kütüphane, elle yazılan onlarca satır koda tercih edilmiştir.

## 1. Karar özeti

| Katman | Seçim | Ana gerekçe |
|---|---|---|
| Backend | Python 3.13 + Django 5.2 LTS + Django REST Framework | Yetki, oturum, parola hash'leme, CSRF/XSS/SQLi korumaları ve migration çekirdekte; az sayıda bağımlılık; LTS = az bakım. **Django Admin kullanılmaz** (K23) — iç panel ihtiyacı kendi RBAC'imizle karşılanır |
| Frontend | Django şablonları + HTMX + Alpine.js + Tailwind CSS | Tek kod tabanı, sunucu taraflı render (tarayıcıda token yok), SPA altyapı maliyeti yok |
| Veritabanı | PostgreSQL 17 (minimum 15) | Mevcut tasarım zaten PostgreSQL; RLS, JSONB, kısıtlar ve güçlü tip sistemi izolasyon için gerekli |
| Kimlik doğrulama | Django auth + özel User modeli + Argon2id; web'de HttpOnly çerez, API'de opak ve DB'de yalnızca **özeti** saklanan süreli token (kendi modelimiz — K15) | JWT'nin iptal edilememe sorunu yok; token anında geçersiz kılınabilir |
| Yetkilendirme | Mevcut RBAC tabloları (`roles`, `permissions`, `user_roles`, `role_permissions`, `user_permission_overrides`) + özel Django auth backend | Şema zaten var; kiracı bazlı rol ve istisna desteği hazır; varsayılan reddet |
| Dosya saklama | S3 uyumlu özel nesne depolama (boto3), ön imzalı URL | VPS diskini büyütmek zorunda kalmadan ölçeklenir; yedekleme kolay; public bucket yok |
| Cache | Django'nun yerleşik veritabanı cache'i (+ oturumlar DB'de) | Ek servis yok, ek RAM yok; Redis gerektirmez |
| Redis | **İlk sürümde yok** | Bir ihtiyaç değil, bir maliyet (RAM + operasyon). Tetikleyiciler bölüm 6.2'de |
| Background jobs | django-q2 (Django ORM broker) + systemd worker | Ayrı broker servisi yok; zamanlanmış + asenkron görev; ek servis yok |
| E-posta | Django SMTP + sağlayıcı bağımsız SMTP relay (SES/Postmark/Mailgun vb.) | Sağlayıcıya kilitlenme yok; kuyruk + yeniden deneme uygulama tarafında |
| Audit log | PostgreSQL `audit_logs` + tetikleyiciler; uygulama rolüne yalnızca INSERT/SELECT | Uygulama kaydı silemez/değiştiremez; ayrıcalıklı kullanıcı bile kaydı bozamaz |
| Teknik log | Yapılandırılmış JSON → stdout → journald (+ isteğe bağlı Sentry) | Kişisel veri içermez; merkezî log altyapısı gerekmez |
| API | REST/JSON, `/api/v1`, DRF + OpenAPI şeması | Sözleşme net; mobil/entegrasyon için hazır; `API.md` ile uyumlu |
| Test | pytest + pytest-django + factory_boy (+ gerçek PostgreSQL) | SQLite RLS/JSONB/enum desteklemez; güvenlik testleri zorunlu |
| Docker | **Üretimde kullanılmaz** (systemd + venv); yerelde de zorunlu değil | Küçük VPS'de RAM ve operasyon tasarrufu; tek komutla dağıtım ihtiyacı yok |
| Deployment | Ubuntu LTS VPS + nginx + gunicorn + systemd + certbot | En düşük maliyetli, en kolay bakımlı üretim kurulumu |

## 2. Sistem mimarisi

### 2.1 Katmanlar

```
Tarayıcı
  │ HTTPS (TLS, HSTS)
  ▼
nginx ── statik dosyalar, istek hızı sınırı, TLS sonlandırma
  │
  ▼
gunicorn + Django
  ├─ HTTP katmanı: oturum/token, izin kontrolü, girdi doğrulama
  ├─ Servis katmanı: iş kuralları, onay akışları, nesne seviyesi yetki
  ├─ Veri erişim katmanı: tenant bağlamı zorunlu repository'ler
  └─ Şablon (HTML/HTMX) + DRF (JSON) — aynı servis fonksiyonlarını çağırır
  │
  ├──────────────► PostgreSQL (hr şeması, RLS)
  ├──────────────► django-q2 kuyruğu (aynı PostgreSQL, ORM broker)
  └──────────────► S3 uyumlu nesne depolama (ön imzalı URL ile)

systemd: persopyro-web, persopyro-worker, postgresql, nginx
```

### 2.2 Değişmez mimari kurallar

1. Veritabanı erişimi tenant bağlamı olmadan yapılmaz.
2. RLS politikaları devre dışı bırakılarak pratik çözüm üretilmez.
3. Kişisel veri; liste, rapor ve dışa aktarma çıktılarında varsayılan olarak
   maskelenir.
4. HTTP katmanından doğrudan veritabanına gidilmez; iş kuralı servis
   katmanında yazılır.
5. HTML arayüzü ile JSON API aynı servis fonksiyonlarını çağırır; iş kuralı iki
   kez yazılmaz.
6. Üretimde uygulama, veritabanı tablosunun sahibi olan rolle çalışmaz
   (bkz. bölüm 5.3 — RLS'in atlanmaması için zorunlu).
7. Karar gerektiren her yeni teknoloji, bu doküman güncellenmeden eklenmez.

### 2.3 İstek yaşam döngüsü (hedef davranış)

1. İstek nginx'ten gunicorn'a gelir (TLS, hız sınırı uygulanmış).
2. Django oturumu/token'ı doğrular; kullanıcı ve kiracı belirlenir.
3. Kiracı bağlamı veritabanı işlemi içinde ayarlanır:
   `SET LOCAL app.tenant_id = '<uuid>'` (işlem sonunda otomatik sıfırlanır).
4. İzin kontrolü yapılır (uç noktanın gerektirdiği izin + nesne kapsamı yoksa
   istek reddedilir).
5. Servis katmanı çalışır; tüm sorgular tenant filtresiyle yürür.
6. RLS, son savunma hattı olarak tenant dışı satırları gizler.
7. Önemli işlem `audit_logs`'a yazılır (tercihen tetikleyici ile).
8. Yanıt HTML (HTMX parçası dahil) veya JSON olarak döner.

## 3. Backend

**Seçim: Python 3.13 + Django 5.2 LTS + Django REST Framework.**

Gerekçeler:

- Django 5.2, Nisan 2025'te yayımlanmış **LTS** sürümdür ve en az üç yıl
  güvenlik güncellemesi alır; Python 3.10–3.14 desteklenir. Uzun destek süresi
  "kolay bakım" önceliğine doğrudan hizmet eder.
- Yetki, oturum, parola hash'leme, CSRF/XSS/SQL injection korumaları ve
  migration sistemi çekirdekte gelir. Bu, FastAPI gibi bir çatıda elle
  birleştirilecek 8-10 kütüphanenin yerine tek ve güvenlik açısından olgun bir
  temel sağlar (öncelik 8).
- **Django Admin kullanılmaz** (karar K23, 2026-09-11): admin'in yetki modeli
  (`is_staff` + `has_module_perms`) uygulamanın RBAC'inden bağımsız **ikinci bir
  yetkilendirme yolu** yaratır. Ölçüm: Django'nun `AdminSite.has_permission`
  kapısı `is_active and is_staff`'tır; yani `is_staff=True` olan aktif bir kiracı
  kullanıcısı, mevcut oturumuyla admin dizinini açabilir. Bu yüzden `/admin/`
  rotası ve `django.contrib.admin` kaldırılır; iç panel ihtiyacı kendi
  RBAC'imizle (`platform.*` izinleri) karşılanır.
- Python, yerel ortamda zaten var (`python3 3.14.4`, `uv 0.12.10`); raporlama,
  veri işleme ve ileride analitik için aynı dil kullanılır.
- DRF, `API.md` ile tanımlanan sürümlü REST sözleşmesini ve izin sınıflarını
  doğal biçimde karşılar.

Reddedilen alternatifler:

| Alternatif | Neden reddedildi |
|---|---|
| FastAPI | Yetki, oturum ve migration elle birleştirilir; daha çok bağımlılık ve daha çok güvenlik sorumluluğu |
| NestJS (Node) | Aynı CRUD/ağırlıklı İK işi için daha fazla glue kod; hazır iç panel yok; ikinci dil |
| Laravel (PHP) | Eşdeğer ölçüde uygun bir aday; ancak ortamda Python hazır, veri/rapor tarafı için ikinci dil gerekir |
| Go | Üretim maliyeti düşük ama geliştirme hızı yavaş; bu ürün CRUD ve iş kuralı ağırlıklı |
| Ham SQL + mikro framework | Yetki/oturum/migration elle yazılır → güvenlik ve bakım riski |

Bağımlılık disiplini: çalışma zamanı bağımlılıkları bölüm 11'de listelenmiştir;
liste kasten kısadır ve her yeni bağımlılık gerekçesiyle eklenir.

## 4. Frontend

**Seçim: Django şablonları + HTMX + Alpine.js + Tailwind CSS.**

Gerekçeler:

- **Sunucu taraflı render**, tarayıcıda erişim token'ı saklamayı gerektirmez;
  oturum HttpOnly çerezde kalır. Bu, XSS etkisini doğrudan azaltır (öncelik 1).
- Tek kod tabanı ve tek dil: ayrı bir SPA projesi, ayrı build hattı, ayrı
  dağıtım ve ayrı sürüm yönetimi gerekmez (öncelik 3, 4, 6).
- HTMX sunucudan HTML parçası döndürmeye dayanır; İK ekranlarının çoğu (form,
  liste, onay, filtre) bu modelle hızlı ve az kodla yazılır.
- Alpine.js (~15 KB) yalnızca açılır menü, sekme, modal gibi yerel durumlar için;
  build gerektirmez.
- Tailwind CSS **yalnızca geliştirme sırasında** derlenir; üretimde çıktı küçük
  bir CSS dosyasıdır, çalışma zamanı bağımlılığı yoktur.
- Büyüme yolu açık: DRF API zaten birinci sınıf sözleşme olduğu için, ileride
  mobil uygulama veya React/Vite tabanlı SPA **backend değişmeden** eklenebilir.

Reddedilen alternatifler:

| Alternatif | Neden şimdi reddedildi |
|---|---|
| React/Vite SPA | Ayrı build/dağıtım, durum yönetimi, token saklama sorunu; v1 için fazladan karmaşıklık |
| Next.js | Üretimde Node süreci çalıştırır (RAM maliyeti) ve SSR ihtiyacı yok |
| Vue SPA | Aynı SPA maliyetleri; HTMX ile karşılanan ekranlar için gereksiz |
| Ağır bileşen kütüphanesi + hazır tema | Görsel kontrol ve bağımlılık yükü; güvenlik yüzeyi büyür |

Geçiş tetikleyicisi: vardiya planlama ızgarası veya gerçek zamanlı bildirim
ekranları HTMX ile bakımı zor hale gelirse, ilgili ekran için Vite + React
parçası eklenir; tüm uygulama SPA'ya çevrilmez.

## 5. Veritabanı

### 5.1 Seçim

**PostgreSQL 18** (minimum 15; projede kurulu sürüm 18.6). Gerekçeler:

- Mevcut veri tasarımı (`~/hrsaas_db/`) PostgreSQL'e göre yazılmıştır
  (`pgcrypto`, `uuid-ossp`, JSONB, kısıtlar, RLS).
- **RLS (Row Level Security)** çok kiracılı izolasyonun son savunma hattıdır ve
  yalnızca PostgreSQL bu olgunlukta sunar (öncelik 2).
- Kiracı izolasyonu, kısıtlar (`CHECK`, `UNIQUE`, yabancı anahtar) ve JSONB
  aynı motorda; ek veri deposu gerekmez (öncelik 8).
- 18, mevcut kararlı sürümdür ve topluluk desteği 2030'a kadar sürer; PostgreSQL
  sürümleri 5 yıl boyunca desteklenir. Projede kurulu sürüm: **18.6**. En az
  gereken sürüm 15'tir.
- Alternatif olarak MySQL/MariaDB reddedildi: RLS yok, izolasyon tamamen
  uygulamaya kalırdı.

### 5.2 Veri erişimi ve şema yönetimi

- **Şemanın tek sahibi Django migration'larıdır (source of truth).** Mevcut
  `hrsaas_db` SQL dosyaları **tasarım referansı** olarak olduğu gibi korunur;
  hiçbiri değiştirilmez veya silinmez.
- Şema `hr` adında kalır; veritabanı rolünde
  `search_path = hr, public` ayarlanır. Böylece Django tabloları `hr` şemasında
  toplanır ve mevcut tasarımla uyumlu kalır.
- Tetikleyici, fonksiyon ve RLS politikaları Django modelinde ifade
  edilemediğinden migration içinde ham SQL (`RunSQL`) ile yazılır.
- Port yöntemi: modül modül ilerlenir (kiracı → kullanıcı/RBAC → **organizasyon/çalışan
  (M10–M13 ✅ uygulandı)** → **izin (M14 ✅ uygulandı)** →
  izin → vardiya/devam → evrak → diğer). Her modül için ilk migration, mevcut
  DDL ile karşılaştırılarak doğrulanır.
- **Kritik önkoşul:** özel kullanıcı modeli (`AUTH_USER_MODEL`) ve şema /
  `search_path` yaklaşımı, ilk migration yazılmadan önce kesinleşir. Sonradan
  değiştirmek yüksek maliyetlidir.

### 5.3 Üretim veritabanı rolleri (RLS'in atlanmaması için zorunlu)

| Rol | Yetki | Kullanım |
|---|---|---|
| `persopyro_owner` | Şema sahibi, DDL | Yalnızca migration çalıştırır |
| `persopyro_app` | Tablo sahibi değil, superuser değil; DML + gerekli `GRANT` | Uygulama (web + worker) |
| `persopyro_readonly` | Yalnızca `SELECT` | Raporlama/geliştirme (opsiyonel) |

Gerekçe: PostgreSQL'de tablo sahibi ve superuser RLS'i atlar. Mevcut tasarımda
`FORCE ROW LEVEL SECURITY` öngörülmüş olması doğru yöndedir; buna ek olarak
uygulama rolü sahip olmamalıdır. `audit_logs` tablosunda `persopyro_app` rolüne
yalnızca `INSERT, SELECT` verilir; `UPDATE/DELETE` verilmez.

### 5.4 Bilinçli olarak doğrulanacak teknik noktalar (kod yazmadan çözülecek)

Bunlar risk olarak kaydedilmiştir; ilk "spike" görevinin konusudur:

1. **RLS + bağlantı yönetimi**: tenant bağlamı işlem başına
   `SET LOCAL app.tenant_id` ile ayarlanır. İleride PgBouncer benzeri bir
   bağlantı havuzu kullanılırsa transaction pooling modunda uyumluluk
   doğrulanmalıdır.
2. **PG `ENUM` tipleri**: mevcut tasarım 16 PostgreSQL enum tipi kullanır;
   Django'nun enum desteği sınırlıdır. Karar: yeni alanlarda `TextChoices` +
   `CHECK` kısıtı tercih edilir, mevcut enum tipleri korunur ve gerekirse ham
   SQL ile yönetilir. Fark, port sırasında test edilir.
3. **Veritabanı seviyesinde kimlik doğrulama kullanılmaz**: mevcut tasarımdaki
   `hr.authenticate_user` fonksiyonu parolayı veritabanına gönderir. Karar:
   kimlik doğrulama **uygulama katmanında** yapılır (parola uygulamadan çıkmaz,
   politika ve kilit mantığı tek yerde). Bu fonksiyon üretimde kullanılmaz;
   mevcut parola hash'leri için Django'nun bcrypt hasher'ı uyumluluk amaçlı açık
   tutulur, yeni parolalar Argon2id ile saklanır.
4. **UUID üretimi**: DB varsayılanı `gen_random_uuid()` korunur; Django
   modelinde de alan varsayılanı tanımlanır.

### 5.5 Veri kuralları

Tüm iş tablolarında `tenant_id NOT NULL`; kiracılar arası yabancı anahtar yasak;
`created_at`/`updated_at` (`TIMESTAMPTZ`); soft delete (`deleted_at`); kişisel
veri alanları maskelenebilir olmalı. Ayrıntı: `DATABASE.md`.

## 6. Cache ve Redis

### 6.1 Karar

**İlk sürümde Redis kullanılmaz.** Yerine:

| İhtiyaç | Çözüm | Gerekçe |
|---|---|---|
| Oturum saklama | Django DB oturumları | Ek servis yok; kiracı/kullanıcı iptali kolay |
| Genel cache | Django DB cache tablosu | Ayrı süreç ve RAM gerekmez |
| Hız sınırlama (throttling) | DRF throttling + DB cache | Küçük ölçekte yeterli |
| Görev kuyruğu | django-q2 + ORM broker | Broker servisi gerekmez |

Redis maliyeti: ayrı bir süreç, ~50-100 MB RAM, ayrı yedekleme/izleme ve
sürüm yönetimi. Bu, tek VPS'de çalışan bir SaaS için gereksizdir (öncelik 3, 4, 8).

### 6.2 Redis'in ekleneceği tetikleyiciler

Aşağıdakilerden **biri** gerçekleşirse Redis eklenir ve bu doküman güncellenir:

1. Web işçi sayısı 2'nin üzerine çıktığında ve süreçler arası tutarlı cache
   gerektiğinde.
2. Hız sınırlama sayacının veritabanını yormaya başladığı ölçek (kaba eşik:
   saniyede ~100+ yazma).
3. Gerçek zamanlı bildirim (websocket/SSE) ihtiyacı doğduğunda.
4. Görev kuyruğu gecikmesi kabul edilebilir sınırı aştığında (kaba eşik: kuyruk
   gecikmesi sürekli > 30 sn).
5. Cache nedeniyle veritabanı okuma yükü baskın hale geldiğinde.

## 7. Kimlik doğrulama (authentication)

**Seçim: Django auth + özel kullanıcı modeli; web'de HttpOnly oturum çerezi,
API'de opak (DB'de hash'li saklanan) token.**

| Konu | Karar | Gerekçe |
|---|---|---|
| Kullanıcı modeli | Özel User: e-posta ile giriş, `tenant_id` alanı | E-posta girişi İK kullanımına uygun; tenant alanı izolasyonun çekirdeği; ilk migration'dan önce kararlaştırılmalı |
| Parola hash | Argon2id (mevcut bcrypt hash'ler için uyumluluk hasher'ı açık) | Bellek sertliği yüksek; Argon2 Django'da birinci sınıf destekli |
| Web oturumu | HttpOnly + Secure + SameSite çerez, CSRF koruması | Token tarayıcı JS'inden erişilemez |
| API kimlik doğrulama | Kendi `ApiToken` modelimiz: her cihaz için ayrı, DB'de **SHA-256 özeti**, süreli, cihaz bazlı iptal | JWT'nin iptal edilememe sorunu yok; düz metin token saklanmaz. Üçüncü parti paket (knox) kullanılmaz çünkü doğrulama sırasında RLS ile çakışır (K15) |
| JWT | Kullanılmaz | İptal/geri alma zor; kısa ömür + refresh akışı ek karmaşıklık; token içeriği sızdırılabilir |
| Başarısız giriş | Sayaç + hesap kilitleme | Mevcut şemada alanlar hazır (`failed_login_count`, `lockout_until`) |
| Çok faktörlü doğrulama | TOTP; `tenant_admin` ve `super_admin` için zorunlu | Yönetici hesapları en kritik risk; ek servis maliyeti yok |
| Parola sıfırlama | Tek kullanımlık, süreli, hash'li token | Mevcut tasarımda `password_reset_tokens` var |
| SSO (SAML/OIDC) | v1 dışı; kurumsal müşteri talebi gelirse eklenir | Şimdi eklemek gereksiz karmaşıklık (öncelik 8) |
| Giriş kaydı | `login_audit` + `audit_logs` | Kural 6 |

## 8. Yetkilendirme (authorization / RBAC)

**Seçim: mevcut RBAC tabloları kaynak kabul edilir; kontrol üç katmanda uygulanır.**

- Mevcut tablolar zaten kiracı bazlı rol, sistem/özel rol ayrımı ve kullanıcı
  bazlı istisna destekliyor; yeniden yazılmaz (kural: mevcut mimariye bak).
- Django tarafında **özel auth backend** bu tabloları okur; `is_superuser` gibi
  hazır bayraklara güvenilmez. Etkin izin kümesi:
  `rollerin izinleri + kullanıcı eklemeleri − kullanıcı çıkarmaları`.
- Kontrol katmanları:

| Katman | Ne yapar |
|---|---|
| HTTP (DRF izin sınıfı) | Uç noktanın gerektirdiği izin var mı |
| Servis | Nesne seviyesi kapsam (kendi ekibi mi, kendi kaydı mı) |
| Veritabanı (RLS) | Tenant dışı satır görünmez (son savunma) |

- İlke: **varsayılan reddet**. Açık izin yoksa işlem yapılmaz.
- İzin kodları `<kaynak>.<işlem>` biçiminde, veritabanında tutulur.
- Özel kurallar: kullanıcı kendi rolünü yükseltemez; kiracı başına son
  `tenant_admin` kaldırılamaz; `super_admin` erişimi bile kayıtlanır.

Ayrıntı: `AUTHORIZATION.md` (izin listesi ve nesne kapsamı kuralları).

## 9. Dosya saklama (file storage)

**Seçim: S3 uyumlu özel nesne depolama + boto3 (django-storages kullanılmaz).**

| Konu | Karar | Gerekçe |
|---|---|---|
| Sağlayıcı | S3 uyumlu (Hetzner Object Storage, Cloudflare R2, Backblaze B2, AWS S3) | Egress/alan maliyeti düşük; VPS diskini büyütmek gerekmez; yedekleme bağımsız |
| Erişim | Yalnızca özel (public bucket yok), kısa ömürlü ön imzalı URL | Yetkisiz erişim ve link paylaşımı riski azalır |
| Kod | İnce bir depolama soyutlaması + `boto3` | Sağlayıcı değiştirilebilir; fazladan kütüphane yok. Alternatif: `django-storages` (elendi: az kod için fazladan bağımlılık) |
| Yol düzeni | `tenant/<tenant_id>/<kategori>/<uuid>` | Kiracı bazlı önek; yanlışlıkla karışan dosya tespiti kolay |
| Bütünlük | `checksum_sha256` (mevcut tasarımda var) | Bozulma/oynama tespiti |
| Gizli belge | Sunucu tarafı şifreleme + `is_confidential` için uygulama seviyesi ek şifreleme | Evrak modülünün en hassas verisi |
| Erişim kaydı | Her indirme `document_access` + audit log | Kural 6 |
| Yükleme doğrulama | Boyut, MIME türü, uzantı; virüs taraması isteğe bağlı (ileride) | Zararlı dosya riski |
| Geliştirme | Yerel dosya sistemi | Maliyet yok |
| Yedekleme | Nesne deposu sürümleme + yaşam döngüsü kuralı | Silinen belgenin kurtarılabilmesi |

## 10. Background jobs

**Seçim: django-q2, Django ORM broker ile.**

Gerekçe: ayrı broker süreci (Redis/RabbitMQ) gerektirmez; zamanlanmış (cron
benzeri) ve asenkron görevleri yeniden denemeyle birlikte sağlar; Python 3.13 /
Django 5.2 desteği günceldir (aktif geliştiriliyor).

Planlanan görevler (kiracı bazlı çalışır, tenant bağlamı zorunludur):

- E-posta kuyruğu (izin onayı, vardiya değişikliği, davet, parola sıfırlama)
- İzin bakiyesi tahakkuku (dönemsel)
- Vardiya/izin çakışma uyarıları
- Devam kayıtlarından gecikme/fazla mesai hesaplama
- Zorunlu evrak eksikliği hatırlatmaları
- Rapor/dışa aktarma üretimi
- Süresi geçen token/oturum temizliği
- Veri saklama (retention) ve arşivleme işleri
- Yedek doğrulama ve sağlık kontrolü

Kurallar:

- Görevler kullanıcı girdisiyle değil, kayıtlı veriyle çalışır; her görev tek
  kiracı kapsamında yürür.
- Uzun süren görevler (rapor üretimi) ayrı kuyrukta çalıştırılır ve sonucu
  kullanıcıya bildirilir.
- Yeniden deneme ve hata kaydı zorunludur; sessiz başarısızlık yasak.

Yükseltme tetikleyicisi: dakikada yüzlerce görev veya çok sunuculu kurulum
gerekirse Celery + Redis değerlendirilir.

## 11. E-posta

**Seçim: Django'nun SMTP backend'i + sağlayıcı bağımsız SMTP relay.**

- Sağlayıcılar (SES, Postmark, Mailgun, Yandex vb.) yalnızca ayar farkıdır; kod
  sağlayıcıya bağlanmaz. Kilitlenme riski yok (öncelik 5).
- Geliştirmede konsol backend'i (e-posta gönderilmez, ekrana yazılır).
- Gönderim **kuyruk üzerinden** yapılır (django-q2): yavaş SMTP isteği yanıt
  süresini etkilemez, yeniden deneme mümkündür.
- Kimlik doğrulama: SPF, DKIM, DMARC kuralları zorunlu; aksi halde teslim
  edilebilirlik düşer ve itibar zarar görür.
- Şablonlar kodda; çok dilli içerik desteği (TR/EN) hedeflenir.
- İçerik kuralları: e-postaya kişisel veri yazılmaz; bağlantıya tıklayarak
  görüntüleme tercih edilir.
- Uyum notu: ticari/pazarlama içeren iletiler için İYS yükümlülükleri
  değerlendirilmelidir; işlemsel bildirimler (izin onayı vb.) ayrı ele alınır.

## 12. Audit log (denetim kaydı)

**Teknik log ile denetim kaydı ayrı şeylerdir ve ayrı yerde tutulur.**

| | Denetim kaydı | Teknik log |
|---|---|---|
| Amaç | "Kim, ne zaman, neyi değiştirdi" | Hata ayıklama, performans |
| Yer | PostgreSQL `audit_logs` / `login_audit` | stdout → journald (+ Sentry) |
| Saklama | Uzun süreli, politika ile | Kısa süreli (logrotate) |
| Kişisel veri | Yalnızca tanımlayıcı, değer değil | Asla |
| Yazım | Tetikleyici ile otomatik (`hr.audit_trigger_fn`) | Uygulama |

Kurallar:

- Denetim tablolarında uygulama rolüne yalnızca `INSERT` ve `SELECT` verilir;
  `UPDATE/DELETE` verilmez (mevcut tasarım `is_confidential`/doküman yaklaşımıyla
  uyumlu).
- Yazılacak işlemler: kimlik doğrulama, rol/izin değişikliği, çalışan
  oluşturma/güncelleme/silme, izin onay/red, vardiya değişikliği, evrak
  yükleme/indirme/silme, abonelik ve faturalama.
- Kayıt içeriği: `tenant_id`, `user_id`, `action`, `table_name`, `record_id`,
  `changes`, zaman damgası, istek kimliği (`request_id`) ve istemci IP'si
  (kişisel veri minimizasyonu gözetilerek).
- Denetim kaydını görüntülemek `audit.read` izni gerektirir ve görüntüleme
  işlemi de kayıtlanır.
- Raporlama için ayrı, salt-okunur görünüm/tablo kullanılır; canlı tablo
  üzerinde ağır sorgu çalıştırılmaz.

## 13. API yapısı

**Seçim: REST/JSON, `/api/v1`, DRF; OpenAPI şeması üretilir.**

- Sözleşme ve biçim kuralları `API.md` içindedir; bu doküman onu değiştirmez.
- HTML arayüzü ile API **aynı servis fonksiyonlarını** çağırır; iş kuralı
  kopyalanmaz.
- Kimlik doğrulama: API çağrılarında opak token (bölüm 7); tarayıcı içi
  çağrılarda oturum çerezi + CSRF.
- `tenant_id` istek gövdesinde kabul edilmez; oturumdan çözümlenir.
- Sürümleme yol üzerinden; kırıcı değişiklik yeni sürüm gerektirir.
- Sık kullanılan liste uçlarında `limit/offset`, büyük tablolarda imleç
  (cursor) tabanlı sayfalama.
- Kritik yazma işlemlerinde (izin onayı, ödeme, evrak işleme) **idempotency
  anahtarı** desteği hedeflenir; ağ tekrarı veriyi ikizlemez.
- Hata gövdesi iç durumu sızdırmaz; yetkisiz kayıt için 403 değil 404 tercih
  edilir (varlık sızmasın).
- Hız sınırlama: nginx seviyesinde kaba sınır + DRF throttling; kimlik
  doğrulama uçları en katı sınıfta.
- OpenAPI şeması tek bir ek bağımlılıkla (`drf-spectacular`) üretilir; mobil
  istemci üretimi ve sözleşme testi kolaylaşır.

## 14. Testing

**Seçim: pytest + pytest-django + factory_boy + coverage; testler gerçek
PostgreSQL üzerinde çalışır.**

- SQLite kullanılmaz: RLS, JSONB, `CHECK`/enum davranışı ve kısıtlar farklıdır;
  yanlış güven verir (öncelik 1, 2).
- Zorunlu test sınıfları (güvenlik odaklı):

| Test | Neyi garanti eder |
|---|---|
| Kiracı izolasyonu | A kiracısının kullanıcısı B kiracısının kaydını okuyamaz/yazamaz (API + ORM + dosya yolu) |
| RLS doğrulaması | Tenant bağlamı olmadan sorgu 0 satır döner; `FORCE RLS` etkin |
| Yetki matrisi | Her rol × her uç nokta beklenen sonucu verir (varsayılan reddet) |
| Nesne seviyesi kapsam | Manager yalnızca kendi ekibini onaylar |
| Denetim kaydı | Kritik işlemler kayıt üretir; kayıt sonradan değiştirilemez |
| Kişisel veri maskeleme | Liste/rapor/dışa aktarma maskesiz veri döndürmez |
| Ön imzalı URL | Süresi geçen/başka kiracıya ait bağlantı reddedilir |
| Kimlik doğrulama | Kilitlenme, token iptali, MFA zorunluluğu, parola sıfırlama tek kullanım |

- Test komutu tek olmalı (`pytest`); CI'da sırayla: lint (`ruff`), statik güvenlik
  (`bandit`), bağımlılık taraması (`pip-audit`), migration tutarlılık kontrolü,
  testler.
- Her hata düzeltmesi için önce test yazılır (regresyon önleme).
- Kapsam hedefi: servis ve yetki katmanlarında yüksek kapsam; şablonlarda zorunlu
  değil.

## 15. Docker kullanımı

**Karar: üretimde Docker kullanılmaz; yerelde de zorunlu değildir.**

| Ortam | Karar | Gerekçe |
|---|---|---|
| Üretim (VPS) | systemd + venv + gunicorn + nginx | Daha az katman, daha az RAM, daha kolay hata ayıklama; tek sunuculu kurulumda konteyner orkestrasyonu kazanç sağlamaz (öncelik 3, 4, 6) |
| Yerel geliştirme | Yerel PostgreSQL (apt) + uv ile sanal ortam | Bu ortamda Docker kurulu değil; kurmak ek yük. Konteyner zorunluluğu geliştirici makinesini ağırlaştırır |
| Gelecek | Çok sunuculu/çok servisli kurulum veya tümüyle tekrarlanabilir dağıtım gerekirse | O noktada değerlendirilir |

Not: Ortamda Docker kurulu olmadığı doğrulanmıştır; bu karar mevcut durumla
uyumludur ve ek kurulum gerektirmez. Docker yasağı değil, **şimdilik gereksiz**
kabulüdür.

## 16. Deployment (VPS)

**Seçim: tek Ubuntu LTS VPS; nginx + gunicorn + systemd + yerel PostgreSQL.**

Bileşenler:

| Bileşen | Rol | Not |
|---|---|---|
| nginx | TLS, statik dosyalar, ters vekil, hız sınırı | Ücretsiz, olgun |
| gunicorn | WSGI sunucusu (sync, 2–4 worker, `--max-requests`) | RAM'e göre ayarlanır |
| systemd | `persopyro-web`, `persopyro-worker` birimleri | Otomatik yeniden başlatma, log yönetimi |
| PostgreSQL | Yerel, yalnızca localhost'a açık | Uzak erişim kapalı |
| certbot | Let's Encrypt TLS, otomatik yenileme | HSTS açık |
| ufw + fail2ban | Güvenlik duvarı, kaba kuvvet engelleme | Yönetim portları kısıtlı |
| unattended-upgrades | Güvenlik yamaları otomatik | |
| pgBackRest veya `pg_dump` | Yedekleme → nesne depolama, şifreli | Geri yükleme denenmeden yedek "yok" sayılır |
| journald + logrotate | Log saklama | Kişisel veri yok |
| Sentry (opsiyonel) | Hata izleme | Ücretsiz katman yeterli olabilir |

Dağıtım akışı (hedef, kod yazıldığında betikleştirilir):

```
git pull → uv ile bağımlılık kurulumu → migrate → collectstatic
→ systemd servislerini yeniden başlat → sağlık kontrolü
```

- Sırlar: `EnvironmentFile` (izin `0600`) veya systemd kimlik bilgileri; asla
  depoda değil.
- Geri alma: sürüm etiketi + geri alınabilir migration; migration öncesi yedek.
- Staging: ödeme yapan müşteri sayısı artana kadar aynı VPS'de ayrı veritabanı +
  ayrı servis kabul edilebilir; **ancak üretim verisi staging'e kopyalanmaz**
  (kişisel veri).
- Bakım pencereleri: migration'lar geriye uyumlu yazılır; kesinti gerektiren
  değişiklik ayrı planlanır.

Ölçekleme yolu (kozmetik değil, sıralı):

1. Tek VPS'de dikey büyüme (RAM/CPU).
2. Uygulama ve veritabanını ayrı sunuculara ayırma.
3. Yönetimli PostgreSQL + nesne depolama + CDN.
4. Birden çok uygulama sunucusu + yük dengeleyici → bu aşamada Redis gerekir
   (bölüm 6.2).

Yaklaşık maliyet (sağlayıcıya göre doğrulanmalı): küçük/orta tek VPS, nesne
depolama, e-posta gönderim, alan adı ve ücretsiz izleme katmanlarıyla başlangıç
için aylık tek haneli–çift haneli Euro aralığı. Redis, Docker orkestrasyonu,
Kubernetes ve yönetimli veritabanı başlangıçta maliyete dahil edilmez.

## 17. AI bağımsızlığı (öncelik 7)

- Mimarinin **hiçbir bileşeni** bir yapay zekâ servisine, modeline veya vektör
  veritabanına bağlı değildir. Sistem, AI olmadan tam işlevseldir.
- Sistemin çalışması için hiçbir dış API anahtarı zorunlu değildir.
- İleride AI destekli bir özellik (ör. İK politikası asistanı, özgeçmiş özeti)
  eklenirse kurallar: (a) varsayılan olarak kapalı, kiracı bazında açılır,
  (b) kişisel veri yalnızca açık rıza ve sözleşme ile gönderilir, (c) sağlayıcı
  değiştirilebilir olacak şekilde soyutlanır, (d) devre dışı bırakıldığında
  ürünün hiçbir temel akışı bozulmaz.
- Bu nedenle bugün hiçbir AI/vektör bağımlılığı eklenmez (öncelik 8).

## 18. Ortam uyumu ve eksikler

Doğrulanan ortam (Ubuntu 26.04.1 LTS): git 2.53.0, Node v26.8.1, npm 11.19.0,
Python 3.14.4, uv 0.12.10. Disk 25 GB (12 GB boş), RAM 3,3 GB.

| Konu | Durum | Not |
|---|---|---|
| Python | **Kuruldu: 3.13.15** (uv) | Karar K11. Proje `.venv` içinde; `requires-python = ">=3.13,<3.14"` |
| Node/npm | Var | Yalnızca Tailwind derlemesi için gerekli (geliştirme zamanı) |
| PostgreSQL | **Kuruldu ve çalışıyor: 18.6** (127.0.0.1:5432) | Kurulum `sudo` gerektirdiği için kullanıcı tarafından yapıldı. Rol/şema/yetki kurulumu: `bash scripts/bootstrap.sh` |
| Docker | **Kurulu değil** | Gerekli de değil (bölüm 15) |
| RAM 3,3 GB | Sınırlı | Yerelde PostgreSQL + 2-4 gunicorn worker + worker süreci sığar; ağır IDE/derleme eşzamanlı çalıştırılmamalı |
| Disk 12 GB boş | Yeterli | Nesne depolama bu nedenle tercih edildi |
| Django | **Kuruldu: 5.2.17 LTS** | `pyproject.toml` ile `~=5.2.0` aralığına kilitli |

Veritabanı sunucusu kuruludur (PostgreSQL 18.6, yalnızca localhost). Sıradaki adım
tek komuttur: `bash scripts/bootstrap.sh` (roller, veritabanı, `hr` şeması,
yetkiler, parolalar ve `.env` dosyası; ardından izolasyon doğrulaması çalışır).

## 19. Bağımlılık envanteri (kasten kısa)

Çalışma zamanı:

| Paket | Neden |
|---|---|
| Django (5.2 LTS) | Çekirdek çatı |
| djangorestframework | API katmanı |
| psycopg (3.x) | PostgreSQL sürücüsü |
| django-q2 | Arka plan görevleri (ORM broker) |
| cryptography | Alan şifreleme (TOTP sırrı gibi değerler için; Fernet) |
| boto3 | Nesne depolama erişimi |
| argon2-cffi | Argon2id parola hash'leme |
| pyotp (veya django-otp) | TOTP ile çok faktörlü doğrulama |
| gunicorn | WSGI sunucusu |
| drf-spectacular | OpenAPI şeması (tek bağımlılık, sözleşme disiplini) |
| sentry-sdk | Hata izleme (opsiyonel, kapatılabilir) |

Geliştirme/test: pytest, pytest-django, factory-boy, coverage, ruff, bandit,
pip-audit.

Frontend (yalnızca geliştirme zamanı): htmx, Alpine.js, Tailwind CSS.
Tarayıcıya gönderilen JS/CSS statik dosyadır; çalışma zamanı bağımlılığı yoktur.

Bilinçli olarak **eklenmeyenler**: Celery, Redis, RabbitMQ, docker-compose
zorunluluğu, django-storages, SPA çatısı, Kubernetes, vektör veritabanı, ağır
bileşen kütüphanesi. Her biri bölüm 6.2 / 19'daki tetikleyiciler gerçekleşirse
tekrar değerlendirilir.

## 20. Karar kaydı ve yeniden değerlendirme tetikleyicileri

| # | Karar | Durum | Yeniden değerlendirme tetikleyicisi |
|---|---|---|---|
| K1 | Django + DRF | Kabul | API ağırlığı async I/O'ya kayarsa (yüksek eşzamanlı akış) |
| K2 | Sunucu taraflı HTML + HTMX | Kabul | Vardiya/gerçek zamanlı ekranlar HTMX ile bakımı zorlaşırsa |
| K3 | PostgreSQL 18 (kurulu: 18.6) | Kabul ve uygulandı | Yönetimli hizmet sürümü zorunlu kılarsa |
| K4 | Opak token (kendi modelimiz, DB'de özet), JWT yok | Kabul ve uygulandı (K15) | Üçüncü parti entegrasyon/token paylaşımı gerekirse (OAuth2) |
| K5 | Redis yok | Kabul | Bölüm 6.2 tetikleyicileri |
| K6 | django-q2 | Kabul | Görev hacmi veya çok sunucululuk Celery gerektirirse |
| K7 | S3 uyumlu nesne depolama | Kabul | Sağlayıcı maliyeti/uyumu değişirse (API aynı kalır) |
| K8 | Docker yok (üretim) | Kabul | Çok sunuculu kurulum veya tekrarlanabilir dağıtım ihtiyacı |
| K9 | Django migration'ları şema sahibi | Kabul | Mevcut SQL ile uyumsuzluk çıkarsa (spike sonucu) |
| K10 | Uygulama katmanında kimlik doğrulama | Kabul | Halihazırda çalışan bir veri göçü gerektirirse (uyumluluk hasher'ı) |
| K11 | Python **3.13** (3.14 değil) | Kabul ve uygulandı | Bağımlılık sürümleri 3.13'ü desteklemezse veya 3.14 ekosistemi olgunlaşırsa |
| K12 | E-posta **kiracı başına tekil** (global değil) + özel auth backend | Kabul ve uygulandı | Global tekil e-posta zorunlu olursa (o zaman kullanıcı adı politikası değişir) |
| K13 | Kiracılar arası referans **guard tetikleyicisiyle** engellenir | Kabul ve uygulandı | PostgreSQL'de bileşik yabancı anahtar desteği Django'ya gelirse tetikleyici yerine kısıt kullanılabilir |
| K14 | Bağlam yokken **fail-closed**: DB 0 satır döndürür, uygulama açık hata verir | Kabul ve uygulandı | — (güvenlik davranışı; değiştirilmez) |
| K15 | API token'ları için **kendi model** (knox kaldırıldı) | Kabul ve uygulandı | Token gereksinimleri (ör. OAuth2) değişirse yeniden değerlendirilir |
| K16 | Giriş öncesi okunan eşleme tabloları **RLS dışı** ama sıkı yetkili (`tenant_directory` salt-okunur, `api_tokens` yalnızca özet) | Kabul ve uygulandı | Bu tablolara kiracı verisi eklenirse tasarım yeniden ele alınmalı |
| K17 | Cache **veritabanı tablosu** üzerinden (Redis yok) | Kabul ve uygulandı | Bölüm 6.2 tetikleyicileri |
| K18 | Yetkilendirme modeli: RBAC + kapsam + kapalı koşul kümesi (tam ABAC değil) | Tasarım (2026-09-11) — **kapsam katmanı OD5 ✅ kapalı, M7–M13 ✅ UYGULANDI:** karar `izin ∧ kapsam`; `dept_manager` = `UNIT` (atandığı birim + **alt ağaç**, OD24 recursive CTE ≤ 20); rol kapsamı `hr.roles.scope_type` (kapalı küme `CHECK`), atama kapsamı `user_roles.scope_org_unit_id` (**gerçek FK** + guard tetikleyicisi), **NULL fail-closed**; çoklu atama birleşimi tek kiracı içinde; çözülemeyen/silinmiş kapsam izin üretmez; nesne kararı `object_in_org_scope()` / `can_access_employee()` | Koşul türü > 15, kiracı başına > 40 özel rol, sözleşmesel politika talebi |
| K19 | Platform (kiracılar arası) erişim: **ayrı servis/authorization katmanı**; varsayılan erişim **yok**, gerekçeli + onaylı + süreli (60 dk, otomatik kapanır) + kayıtlı; kiracı RLS politikaları **değişmez**, `BYPASSRLS` yasak | **Kabul edildi (2026-09-11) — OD1/OD3/OD4 kapalı**, uygulanacak | Uygulama sonrası gözden geçirme; break-glass sınırları OD17'ye bağlı |
| K20 | Genel kullanıcı kimliği: `kiracı-XXXXXXXX` (rastgele, değişmez, türetilmemiş) | Tasarım (OD2) | Biçim değişirse tüm arayüz/entegrasyon etkilenir |
| K21 | Platform verisi **ayrı şema**da; kiracı yolu (`persopyro_app`) ile platform yolu (`persopyro_platform`) **ayrı veritabanı rolleri**; platform kullanıcıları `platform_users` (kiracı tablosuyla birleştirilmez) | **Kabul edildi (2026-09-11) — OD3 kapalı; şema/rol tarafı UYGULANDI ✅ (M1–M8)**: `platform` şeması + `persopyro_platform` rolü (NOSUPERUSER/NOBYPASSRLS), tablo bazlı yetki allowlist'i, `persopyro_app` için sıfır platform yetkisi; **M6/M31**: platform izin kataloğu (6 kod, ortak `hr.permissions`, salt okunur) ve roller (superadmin 6/6, platform_admin 4/4, devralma yok); **M32**: tek merkezi authorization çözümleyici + self-elevation koruması; kiracı verisine erişim M17–M19 | Kiracı rolüne platform şeması yetkisi verilirse tasarım bozulur |
| K22 | Platform denetim akışı **ayrı** tutulur (kiracı denetim tablosuna tenant'sız satır eklenmez); platform erişimleri ayrıca kiracının denetim akışına da yazılır | **Kabul edildi (2026-09-11)**, uygulanacak | Denetim tek akışta birleştirilmek istenirse kiracı RLS'i gevşetmek gerekir → yeniden değerlendirilir |
| K23 | **Django Admin ve `auth_*` paralel yetki yolu kapatılır**: `/admin/` kaldırılır, `django.contrib.admin` uygulama listesinden çıkarılır, `auth_*` ve `django_admin_log` tablolarının uygulama rolü yetkileri geri alınır, `is_staff`/`is_superuser` tek başına yetki vermez | **Kabul edildi (2026-09-11) — OD19 kapalı; uygulandı ✅** (M22–M22d: `GET /admin/` → 404, `auth_*` yetkileri yok, `is_staff` guard + DB kısıtı) | Django Admin ileride kullanılacaksa OD19'daki ön koşullar yazılı tanımlanmalı |
| K24 | **Kiracı üst verisinin sahibi `hr.tenants`**: `platform.tenant_registry` minimum referans indeksidir; senkronizasyon **tek yönlü** (`hr.tenants` → registry) ve **fail-closed** (yansı yazılamazsa kaynak yazımı geri alınır) | **Kabul edildi (2026-09-11) — OD20 kapalı; UYGULANDI ✅ (M4)**: `platform.tenant_registry` + `SECURITY DEFINER` yansı tetikleyicisi (sabit `search_path`, aynı işlem, fail-closed rollback testi); `plan_code`/`billing_ref`/`internal_note` yalnızca platformun | Registry'ye yeni alan eklenirse authoritative tablo yeniden değerlendirilir |
| K25 | **Soft delete politikası**: `deleted_at` = silme, `status` = yaşam döngüsü, `is_active` sütunu **yok**; benzersizlik kısmi indekslerle (`WHERE deleted_at IS NULL`); `public_id` ve kiracı `slug`ı **kalıcı rezerve**; denetim kaydı soft-delete **edilmez**; sessiz yeniden etkinleştirme **yasak** | **Kabul edildi (2026-09-11) — OD21 kapalı**, uygulanacak | F7 bulgusu kapanana kadar yetki çözümlemesi tek başına yeterli sayılmaz |
| K26 | **Platform token'ları yalnızca `platform.tokens`**: zorunlu `scope`+`purpose`, `expires_at` NOT NULL, `revoked_at`; `interactive` ≤ 24 saat, `service` ≤ 90 gün ve superadmin kapsamı **yasak**; tenant RLS sınırını **aşamaz** | **Kabul edildi (2026-09-11) — OD22 kapalı; UYGULANDI ✅ (M3)**: `platform.tokens` — ham token yok, `scope`/`purpose` zorunlu, boş kapsam + joker yasak, TTL tavanı DB `CHECK`, iptal tek yönlü, service+superadmin kombinasyonu tetikleyiciyle reddedilir | Servis entegrasyonu ihtiyaçları değişirse TTL/kapsam sınırları gözden geçirilir |
| K27 | **Denetim kaydında aktör modeli**: `actor_kind` (4 değerli kapalı küme) + platform aktörü için **FK'sız** referans (`platform_actor_id`) + `actor_label` snapshot + `purpose`/`access_grant_ref` (platform aktöründe DB `CHECK` ile zorunlu); `user` FK `PROTECT` (append-only) | **Kabul edildi (2026-09-11) — OD23 kapalı**, uygulanacak | Yeni aktör türü gerekirse kapalı küme kararla genişletilir |
| K28 | **Organizasyon ağacı: adjacency list + özyinelemeli CTE** (path/nested set/closure yok); döngü ve derinlik **DB tetikleyicisiyle** yasak; tek birincil yönetici; ağaç **yetki mirası üretmez** | **Kabul edildi (2026-09-11) — OD24 kapalı; UYGULANDI ✅ (M10–M13)**: `hr.departments` (`parent_id` adjacency list, `(tenant_id, parent_id)` indeksi), `hr.departments_tree_guard` (döngü + derinlik ≤ 20 + silinmiş ata yasağı), `hr.employee_manager` (tek aktif birincil yönetici kısmi benzersizliği, silinmiş çalışanla yeni aktif ilişki yasağı), `hr.employees.department_id` **NOT NULL**; kapsam çözümlemesi tek noktada (`apps/rbac/services/scope.py`); **`path`/closure kolonu eklenmedi** | Derinlik > 20, birim > 5.000 veya kapsam sorgusu p95 > 50 ms → closure tablosu değerlendirilir |
| K29 | **Public ID prefix'i tenant slug'ından bağımsız ve immutable**: `tenants.public_id_prefix` (tam UNIQUE, yeniden tahsis yok, DB tetikleyicisiyle değişmez); `users.public_id` immutable; public ID **yetki sınırı değil** | **Kabul edildi (2026-09-11) — OD25 kapalı; UYGULANDI ✅ (M5)**: `hr.tenants.public_id_prefix` (tam UNIQUE, kalıcı rezerve, DB tetikleyicisiyle immutable) + `hr.users.public_id` (global UNIQUE, immutable, Crockford 8); slug/rebrand değişimi kimliği kaydırmaz | Yeniden markalama istisnası gerekirse OD28 kararı beklenir |
| K30 | **v1'de tek tüzel kişilik**: `1 tenant = 1 tüzel şirket`; `companies`/`legal_entities` **yok**, hiçbir tabloda `company_id` yok; RBAC kapsamı **tek boyutlu** (tenant) kalır; gelecekte ayrı katman eklenebilir ama **tenant izolasyon sınırı korunur** | **Kabul edildi (2026-09-11) — OD27 kapalı; UYGULANDI ✅ (M10–M13):** `hr.departments`/`positions`/`employees`/`employee_personal_data`/`employee_manager` doğrudan `tenant_id`'ye bağlı; `company_id`/`legal_entity_id` **hiçbir tabloda yok** (fresh-install adım 6d bunu ölçer) | ≥ %10 veya ≥ 3 kiracı talebi; sözleşmesel bordro/izin ayrımı; ardışık ≥ 2 bloke anlaşma; ≥ 2 işveren sicili |
| K31 | **`public_id_prefix` v1'de immutable**: rebrand/slug değişimi prefix'i ve mevcut `public_id`'leri değiştirmez; **değiştirme uç noktası yok**; istisna mekanizması **kodlanmaz** (ileride yalnızca platform operasyonu + denetim + `public_id` sabit + eski prefix rezerve) | **Kabul edildi (2026-09-11) — OD28 kapalı**, uygulanacak | Yasal birleşme zorunluluğu; ≥ 3 tekrarlı değişim talebi; ≥ 5 destek karışıklığı |
| K32 | **Tenant API token'ı kapsamlı ve iptal izli**: `hr.api_tokens` genişletilir (ikinci token sistemi yok) — zorunlu `purpose` + `scopes` (katalog doğrulamalı, `platform.*` ve wildcard yasak), `token_kind` (interactive ≤ 30 gün / service ≤ 90 gün, süresiz yasak), `revoked_at`/`revoked_reason` (tek yönlü iptal); **yetki = `current_user_permissions ∩ token_scopes`** (fail-closed, tek kilit nokta) | **Kabul edildi (2026-09-11) — OD26 kapalı; uygulandı ✅** (`accounts/0003_apitoken_od26` + servis/uç/test; F8 kapandı) | Daha uzun `service` TTL'i gerekirse yeni karar; kiracı durumu kuralı F11 ile birlikte uygulandı |
| K33 | **İzin bakiyesi: güncel durum + append-only olay defteri** (event sourcing'in tamamı değil): `leave_balances` güncel durumdur (türetilen `available_days` **DB'de üretilir**), her değişiklik `leave_balance_entries`'e aynı işlemde yazılır; hakediş **idempotenttir** (dönem başına tek `accrual` kaydı); onay adımları **genel** tablodur (`subject_kind`/`subject_id`, ileriki modüllerle paylaşılır) | **Kabul edildi (2026-09-11) — M14 ✅ UYGULANDI:** çift tüketim `(leave_request_id, entry_kind)` kısıtıyla, negatif bakiye politika guard'ıyla, sıra atlama DB tetikleyicisiyle engellenir; manuel düzeltme gerekçeli + denetimli | Ledger sorguları yavaşlarsa veya uzlaştırma (reconciliation) ihtiyacı doğarsa özet/denetim sorguları eklenir |

K11 gerekçesi: Django 5.2.8+ Python 3.14'ü de destekliyor, ancak üçüncü parti
paketlerin (psycopg, django-q2, argon2-cffi vb.) 3.13 üzerindeki uyumluluğu daha
oturmuştur. Proje Python 3.13.15 üzerine kurulmuştur; `pyproject.toml` içinde
`requires-python = ">=3.13,<3.14"` ile sabitlenmiştir.

## 21. Riskler ve önlemler

| Risk | Etki | Önlem |
|---|---|---|
| RLS'in yanlış yapılandırılması (uygulama rolü tablo sahibi olursa izolasyon sessizce kaybolur) | Felaket | Rol ayrımı (5.3), `FORCE RLS`, cross-tenant testlerinin CI'da zorunlu olması |
| Tenant bağlamının bir istekte unutulması | Yüksek | İki katman: (a) veritabanı **fail-closed** — bağlam yoksa RLS 0 satır döndürür, sızma olmaz; (b) uygulama katmanı `require_tenant_id()` ile açık hata verir. Bağlam, istek hattında zorunlu kılınır (faz 2, 2. dilim) |
| Django şeması ile mevcut SQL tasarımının ayrışması | Yüksek | Modül modül port + DDL karşılaştırması; mevcut dosyalar salt-okunur referans |
| Kişisel verinin loga/rapora sızması | Yüksek | Log filtresi, maskeleme, test |
| Tek VPS = tek hata noktası | Orta | Otomatik yedek + denenmiş geri yükleme; ölçekleme yolu hazır |
| Paralı olmayan e-posta ile teslim edilebilirlik sorunu | Orta | SPF/DKIM/DMARC + itibar izleme |
| Kapsam büyümesi (10 modül aynı anda) | Orta | `FEATURES.md` öncelik sırası; modül bitmeden sonrakine geçilmez |
| Disk/RAM sınırı (12 GB boş, 3,3 GB RAM) | Orta | Nesne depolama, log rotasyonu, dikey büyüme |

## 22. Uygulama fazları (kod yazmadan plan)

| Faz | İçerik | Çıkış ölçütü |
|---|---|---|
| 0 | Ortam kurulumu, depo başlatma, test iskeleti — **TAMAMLANDI (2026-09-11)** | `pytest` 10 test geçti, `ruff`/`bandit`/`pip-audit` temiz, `manage.py check` sorunsuz |
| 1 | Veritabanı spike'ı: RLS + rol ayrımı + tenant bağlamı + enum/uuid kararları — **TAMAMLANDI (2026-09-11)** | `scripts/03_verify_isolation.sql` A1–A9 iddialarının tamamı geçti; roller ve yetkiler kurulu; 19 test yeşil |
| 2 | Kiracı + kullanıcı + kimlik doğrulama + RBAC çekirdeği — **1. ve 2. dilim TAMAMLANDI (2026-09-11)**: modeller, migration'lar, RLS + guard tetikleyicileri, istek hattı (middleware), oturum ve token uçları, MFA (TOTP), izin kataloğu + varsayılan roller, 97 test | Kalan dilim: parola sıfırlama + e-posta, MFA kayıt ucu, süresi geçmiş oturum/token temizliği, kullanıcı CRUD uçları |
| 3 | Organizasyon + çalışan | Yetki matrisi testleri geçer |
| 4 | İzin yönetimi | Bakiye ve onay testleri geçer |
| 5 | Vardiya + devam | Çakışma/hesaplama testleri geçer |
| 6 | Evrak | Ön imzalı URL ve erişim kaydı testleri geçer |
| 7 | Denetim/raporlama | Denetim kaydı değiştirilemezlik testi geçer |
| 8 | İşe alım, performans, abonelik/faturalama | Modül testleri geçer |
| 9 | Üretim dağıtımı | Yedekten geri yükleme denenmiş, TLS/HSTS ayarlı |

Her fazın sonunda: `CHANGELOG.md` güncellenir, `DEVELOPMENT_RULES.md` kontrol
listesi uygulanır.

> **Ürün kapsamı ve faz planı (2026-09-12):** yukarıdaki tablo altyapı/teslim fazlarını
> gösterir. **Modül bazlı ürün fazlandırması (P0–P9)**, modül sınırları (modular monolith:
> yeni modüller `apps/<modül>` olarak eklenir, ayrı servis açılmaz), hangi tabloların hangi
> fazda geleceği ve kabul kriterleri **`docs/HR_ROADMAP.md` §21–§22** içindedir. Bu dokümandaki
> §9 (dosya saklama), §10 (background jobs → django-q2) ve §11 (e-posta) kararları roadmap'te
> **uygulama işi** olarak planlanır; yeniden karar verilmez.
>
> **P1 modül sınırı kararı (2026-09-13):** P1 tabloları (`hr.workplaces`,
> `hr.employee_employment_terms`, `hr.employee_assignments`, `hr.form_field_definitions`)
> **mevcut `apps/organization` uygulamasında** yaşar; ayrı bir `apps/people` açılmaz (aynı bounded
> context: kiracı → organizasyon → çalışan). Belge (P3) ve bildirim (P2) kendi uygulamalarını alır.
> **P1-A ✅ uygulandı (2026-09-13):** dört tablo `apps/organization` içinde oluşturuldu
> (`organization/0014–0018`, `accounts/0006`, `rbac/0012`); RLS+FORCE ve guard'lar mevcut
> standartla uygulandı. Karar kaydı: `docs/AUTHORIZATION.md` §16, tasarım: `docs/HR_ROADMAP.md` §28.

## 23. Özet: neden bu yığın

- **Az sayıda, olgun bileşen**: Django + PostgreSQL + django-q2 + S3 uyumlu
  depolama. Yeni bir servis (Redis, Docker, SPA sunucusu) eklenmeden ürün
  çalışır ve tek VPS'de barınır.
- **Güvenlik varsayılan**: çerçevenin hazır korumaları, Argon2id, iptal
  edilebilir opak token, üç katmanlı yetki (HTTP → servis → RLS), değiştirilemez
  denetim kaydı.
- **İzolasyon iki katmanda zorlanır**: uygulama filtresi + veritabanı RLS
  (uygulama hatası izolasyonu bozmaz).
- **Büyüme yolu açık**: aynı veri modeli ve aynı API ile mobil uygulama, SPA,
  yönetimli veritabanı ve çok sunuculu kurulum sonradan eklenebilir; bugün için
  ödenmeyen bir bedel yoktur.
