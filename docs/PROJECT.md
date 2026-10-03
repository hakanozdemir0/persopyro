# Persopyro

Persopyro, şirketlerin çalışanlarını, organizasyon yapılarını, izinlerini, vardiyalarını, evraklarını ve İK süreçlerini yönetebildiği multi-tenant bir SaaS platformudur.

## Durum (bu dokümanın güncellendiği an: 2026-09-11)

- Aşama: **Mimari planlama tamamlandı, kod yazılmadı.** Uygulama kodu, API,
  arayüz, veritabanı kurulumu ve paket kurulumu yapılmadı.
- Teknoloji seçimleri `ARCHITECTURE.md` içinde gerekçeleriyle tanımlanmıştır.
- Var olan varlık: `~/hrsaas_db/` altında daha önce hazırlanmış PostgreSQL veritabanı
  tasarımı (12 SQL dosyası, `hr` şeması, çok kiracılı yapı). Bu tasarım Persopyro'nun
  veri katmanı **referansı** olarak kabul edilmiştir; bu aşamada **değiştirilmemiştir**.
  Şemanın sahibi bundan sonra Django migration'ları olacak, mevcut dosyalar
  salt-okunur referans olarak korunacaktır.
- Kod yazımına geçmeden önce `ARCHITECTURE.md` bölüm 5.4'teki doğrulanacak
  teknik noktaların (RLS + rol ayrımı, enum yaklaşımı, tenant bağlamı)
  çözülmesi gerekir.

## Seçilen teknoloji yığını (özet)

Ayrıntılı gerekçeler: `ARCHITECTURE.md`.

| Katman | Seçim |
|---|---|
| Backend | Python 3.13 + Django 5.2 LTS + Django REST Framework |
| Frontend | Django şablonları + HTMX + Alpine.js + Tailwind CSS |
| Veritabanı | PostgreSQL 18 (kurulu 18.6; minimum 15), `hr` şeması (kiracı) + `platform` şeması (platform) ve RLS |
| Kimlik doğrulama | Django auth + özel User modeli, Argon2id; web'de HttpOnly çerez, API'de opak ve yalnızca özeti saklanan süreli token (K15) |
| Yetkilendirme | Mevcut RBAC tabloları; HTTP → servis → RLS olmak üzere üç katman |
| Dosya saklama | S3 uyumlu özel nesne depolama (boto3), ön imzalı URL |
| Cache / Redis | Django DB cache; **Redis ilk sürümde yok** |
| Background jobs | django-q2 (Django ORM broker) + systemd worker |
| E-posta | Django SMTP + sağlayıcı bağımsız SMTP relay, kuyruk üzerinden |
| Denetim kaydı | PostgreSQL `audit_logs` + tetikleyiciler, uygulamaya yalnızca INSERT/SELECT |
| API | REST/JSON, `/api/v1`, DRF + OpenAPI şeması |
| Test | pytest + pytest-django + factory_boy, gerçek PostgreSQL üzerinde |
| Docker | Üretimde kullanılmaz (systemd + venv); yerelde de zorunlu değil |
| Deployment | Tek Ubuntu LTS VPS: nginx + gunicorn + systemd + yerel PostgreSQL |

Yığının toplam bakım yükü kasten düşüktür: Redis, Celery, Docker orkestrasyonu,
Kubernetes, SPA sunucusu ve yapay zekâ servisi **bilinçli olarak dışarıda
bırakılmıştır**. Her biri, gerçekleşmesi ölçülebilir bir ihtiyaç doğduğunda
(bkz. `ARCHITECTURE.md` bölüm 6.2 ve 20) eklenir; ihtiyaç doğmadan eklenmez.

## Ürünün amacı

KOBİ'lerden büyük ölçekli şirketlere kadar geniş bir müşteri kitlesinin:

- çalışan kayıtlarını ve organizasyon yapısını,
- izin (yıllık izin, mazeret vb.) süreçlerini,
- vardiya ve çalışma düzenini,
- evrak ve belge saklama/erişim süreçlerini,
- İK iş akışlarını (onay, bildirim, takip)

tek bir bulut platformundan, kendi verileri diğer şirketlerden tamamen izole
biçimde yönetebilmesini sağlamak.

## Platform şirket profili — plan, kararlar açık

Platform panelinde şirket profili oluşturma ve düzenleme akışı vardır. SUPERADMIN
şirket bilgilerini ikinci bir platform onayı olmadan doğrudan düzenleyebilir;
değişen alanlar platform işlem kaydında görünür. Kaydın sonunda isterse şirket
e-posta adresine genel bir bilgilendirme e-postası gönderebilir. E-postada eski/yeni
kişisel veri değerleri bulunmaz. Şirket başına hangi ek profil alanlarının
kullanılacağı oluşturma ve düzenleme ekranında elle seçilir.

Şirket profili planındaki ek gereksinimler:

- Şirketin hizmet başlangıç/bitiş tarihleri ve planı yönetilir. Başlangıç tarihi
  gelmemiş veya bitiş tarihi geçmiş şirket oturum açamaz.
- Kullanıcı hesabı, depolama, aktif çalışan ve toplam çalışan kaydı limitleri
  birbirinden ayrıdır; `0` sınırsız anlamına gelir. Şirket ayrıntılarında mevcut
  çalışan sayıları ve ilk 100 çalışan kaydı görünür.
- Vergi/iletişim gibi şirket profil alanları yalnız SUPERADMIN şirket ayrıntısında
  görünür ve doğrudan düzenlenir; değişiklik alanları işlem kaydına yazılır.
- Parola sıfırlama bağlantısı gönderme ve parola değiştirme kesin gereksinimdir.
  Çalışan profilindeki parola değiştirme ekranı mevcut parolayı, yeni parolayı
  ve yeni parolanın tekrarını ister. Parola sıfırlama bağlantısı da desteklenir.
- Şirket çalışanlarının kendilerine ait profil ekranı olacaktır. Çalışan bu
  ekrandan kişisel bilgilerini görebilmeli ve düzenleyebilmelidir. Alanın
  politikasına göre değişiklik doğrudan kaydedilir veya onaya gönderilir;
  bu davranış alan bazında tanımlanır. Onay gereken değişiklikler mevcut değeri
  değiştirmeden talep olarak saklanır ve onay/red sonucu çalışana bildirilir.
- Windows hesabıyla giriş entegrasyonu yalnızca olası gelecek seçeneğidir;
  gereksinim ve kimlik sağlayıcı kararı verilmemiştir.

Çalışanların kişisel profil değişikliği için doğrudan kayıt/onay politikası ve
çalışan bildirimleri bu şirket yönetimi akışından ayrıdır.

## Kapsam

Bu bölüm, projenin ne olduğunu ve ne olmadığını ayırır. Yeni özellik eklenmeden önce
bu kapsam ve `ARCHITECTURE.md` kontrol edilmelidir.

Kapsam içi (planlanan modüller):

- Kiracı (tenant) yönetimi ve self-servis kayıt
- Kullanıcı, rol ve yetki yönetimi (RBAC)
- Organizasyon yapısı: şirket, departman, pozisyon, çalışan
- İzin yönetimi ve onay akışları
- Vardiya / çalışma düzeni ve devam takibi
- Evrak ve belge yönetimi
- İK süreç takibi ve denetim kayıtları (audit log)
- Abonelik ve faturalama (SaaS)

Kapsam dışı (şimdilik, bilinçli karar):

- Bordro hesaplama motorunun resmi mevzuat hesapları (bu, ilgili mevzuat ve
  muhasebe entegrasyonu gerektirir; ayrı bir kararla ele alınacaktır)
- Muhasebe/ERP sistemleriyle canlı entegrasyon
- Mobil uygulama (önce web)
- Mikroservis mimarisi, Kubernetes, çok sunuculu kurulum (tek VPS'den başlanır)
- Redis, Celery, Docker (ihtiyaç doğduğunda; tetikleyiciler `ARCHITECTURE.md`
  bölüm 6.2 ve 20'de tanımlı)
- Yapay zekâ / vektör veritabanı bağımlılığı — sistem AI olmadan tam çalışır
- SSO (SAML/OIDC) — kurumsal müşteri talebi geldiğinde

## Başarı ölçütleri

1. Bir şirketin verisi, başka bir şirketin kullanıcısı tarafından hiçbir sorgu,
   rapor veya dosya yoluyla okunamaz/değiştirilemez.
2. Kişisel veriler varsayılan olarak en az görünürlükle sunulur.
3. Her kritik işlem kim, ne zaman, hangi kayıt üzerinde izlenebilir.
4. Yeni bir İK modülü, mevcut mimariyi bozmadan eklenebilir.
5. Üretim, tek bir küçük VPS üzerinde çalışır ve aylık altyapı maliyeti düşük
   kalır (veri hacmi büyüdüğünde nesne depolamaya, kullanıcı sayısı büyüdüğünde
   dikey/yatay ölçeklemeye geçilebilir).
6. Sistem, hiçbir dış yapay zekâ servisi olmadan tam işlevseldir.

## Doküman dizini

| Doküman | İçerik |
|---|---|
| `PROJECT.md` | Bu dosya: amaç, kapsam, durum |
| `ARCHITECTURE.md` | Sistem mimarisi ve teknoloji seçimleri (gerekçeli) |
| `SECURITY.md` | Güvenlik ilkeleri ve tehdit modeli |
| `AUTHORIZATION.md` | Kimlik doğrulama, RBAC, yetki modeli |
| `DATABASE.md` | Veri modeli, mevcut şema envanteri (ölçülmüş), hedef şema ve migration planı, RLS |
| `API.md` | API sözleşmesi kuralları |
| `ORGANIZATION.md` | Şirket/departman/pozisyon/çalışan yapısı |
| `FEATURES.md` | Özellik listesi ve durumları |
| `DEVELOPMENT_RULES.md` | Uyulması zorunlu geliştirme kuralları |
| `HR_ROADMAP.md` | **Uçtan uca HR ürün mimarisi, modül tasarımları, fazlandırma (P0–P9) ve önerilen açık kararlar (OD32–OD57)** — plan; uygulanan şey burada "yapıldı" diye yazılmaz |
| `CHANGELOG.md` | Sürüm ve değişiklik kaydı |

## Dil ve terminoloji

- Dokümanlar Türkçe yazılır.
- Kod, tablo/kolon adları, API alanları ve commit mesajları İngilizce yazılır.
- Para birimi ve tarih alanlarında açık birim belirtilir (ör. `amount_try`,
  `created_at TIMESTAMPTZ`).
