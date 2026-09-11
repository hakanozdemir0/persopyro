# Persopyro

KOBİ'ler ve daha büyük şirketler için çalışan yönetimi, insan kaynakları, izin,
vardiya, organizasyon, evrak ve süreç takibi sunan çok kiracılı (multi-tenant)
HR SaaS platformu.

Persopyro, şirketlerin çalışanlarını, organizasyon yapılarını, izinlerini,
vardiyalarını, evraklarını ve İK süreçlerini yönetebildiği multi-tenant bir SaaS
platformudur.

## Durum

Faz 0 (ortam, depo, test iskeleti) ve Faz 1 (veritabanı: roller, `hr` şeması,
RLS ile kiracı izolasyonu) tamamlandı. Uygulama özellikleri henüz kodlanmadı.

- Kurulu: Python 3.13.15 (uv), Django 5.2.17 LTS, bağımlılıklar (`uv.lock`),
  PostgreSQL 18.6 (localhost), roller + `hr` şeması + RLS + kiracı dizini.
- Çalışan: giriş/çıkış (oturum **ve** API token), MFA (TOTP), rol/izin yönetimi
  altyapısı, kiracı bağlamı istek hattı.
- Doğrulanmış: kiracı izolasyonu iddiaları A1–A9; **97 test yeşil**; sıfırdan
  kurulum (boş veritabanı) ayrıca doğrulandı; şema Django migration'larının sahibi.
- Kalan (faz 2, 3. dilim): parola sıfırlama + e-posta, MFA kayıt ucu, süresi
  geçmiş oturum/token temizliği, kullanıcı CRUD uçları
  (`docs/CHANGELOG.md` 0.6.0).

## Geliştirme ortamı

```bash
# 1) Bağımlılıkları kur (uv gerekir)
uv sync

# 2) Veritabanını kur: roller, veritabanı, hr şeması, yetkiler, .env ve
#    kiracı izolasyonu (RLS) doğrulaması — sudo şifresi istenir
bash scripts/bootstrap.sh

# 3) Kontroller
.venv/bin/python manage.py check
bash scripts/migrate.sh            # şema (sahip rolle) + yetki tazeleme
bash scripts/run_tests.sh          # testler (gerçek PostgreSQL üzerinde)
.venv/bin/ruff check .
.venv/bin/bandit -c pyproject.toml -r .
.venv/bin/pip-audit
```

`scripts/` içeriği:

| Dosya | Görev | Çalıştıran rol |
|---|---|---|
| `01_bootstrap.sql` | Roller, veritabanı, eklentiler | süper kullanıcı |
| `02_grant_app_privileges.sql` | `hr` şeması ve uygulama rolü yetkileri (saf SQL, her veritabanında çalışır) | sahip |
| `03_verify_isolation.sql` | Kiracı izolasyonu kanıtı (A1–A9 iddiaları) | süper kullanıcı |
| `bootstrap.sh` | Yukarıdakileri sırayla çalıştırır, parola üretir, `.env` yazar | kullanıcı |
| `migrate.sh` | Migration'ları sahip rolle çalıştırır, cache tablosunu hazırlar, yetkileri tazeler | kullanıcı |
| `run_tests.sh` | Testleri doğru rolle çalıştırır | kullanıcı |
| `smoke.sh` | Uçtan uca duman testi: demo veri + gerçek HTTP giriş akışı | kullanıcı |

Örnek ortam değişkenleri: `.env.example` (gerçek değerler `.env` dosyasında
tutulur ve depoya girmez; `bootstrap.sh` üretir).

Not: Testler `scripts/run_tests.sh` ile çalıştırılır; çünkü testler kendi test
veritabanını oluşturmak zorundadır ve uygulama rolüne bilinçli olarak
veritabanı oluşturma yetkisi verilmemiştir.

## Dokümantasyon

Tüm tasarım ve kurallar `docs/` altındadır. Başlangıç noktası: `docs/PROJECT.md`.

- `docs/PROJECT.md` — amaç, kapsam, durum
- `docs/ARCHITECTURE.md` — sistem mimarisi
- `docs/SECURITY.md` — güvenlik ilkeleri ve tehdit modeli
- `docs/AUTHORIZATION.md` — kimlik doğrulama ve yetki modeli
- `docs/DATABASE.md` — veri modeli ve mevcut veritabanı tasarımı
- `docs/API.md` — API sözleşme kuralları
- `docs/ORGANIZATION.md` — organizasyon ve çalışan yapısı
- `docs/FEATURES.md` — özellikler ve durumları
- `docs/DEVELOPMENT_RULES.md` — uyulması zorunlu geliştirme kuralları
- `docs/CHANGELOG.md` — değişiklik kaydı

## Kod yazmadan önce

`docs/DEVELOPMENT_RULES.md` okunur. Güvenlik ve çok kiracılı veri izolasyonu
pazarlık konusu değildir.

## Mevcut veri katmanı tasarımı

PostgreSQL şema tasarımı `~/hrsaas_db/` altında (12 SQL dosyası) duruyor ve
Persopyro'nun veri katmanı referansı olarak kabul edilmiştir. Ayrıntı:
`docs/DATABASE.md`.
