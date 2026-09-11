# Değişiklik Kaydı — Persopyro

Kayıt biçimi: en yeni sürüm en üstte. Her giriş tarih, sürüm, eklenen /
değişen / doğrulanan / kalan başlıklarını içerir.

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
  - DEBUG varsayılan **kapalı** (üretim davranışı), `DJANGO_SECRET_KEY`
    tanımsızsa üretimde uygulama bilinçli olarak başlamaz.
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
- `bandit -r config apps`: temiz (geliştirme amaçlı sabit anahtar satırı
  gerekçesiyle `# nosec B105` ile işaretlendi).
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
