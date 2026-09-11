# Persopyro

KOBİ'ler ve daha büyük şirketler için çalışan yönetimi, insan kaynakları, izin,
vardiya, organizasyon, evrak ve süreç takibi sunan çok kiracılı (multi-tenant)
HR SaaS platformu.

Persopyro, şirketlerin çalışanlarını, organizasyon yapılarını, izinlerini,
vardiyalarını, evraklarını ve İK süreçlerini yönetebildiği multi-tenant bir SaaS
platformudur.

## Durum

Faz 0 (ortam kurulumu, depo, test iskeleti) tamamlandı. Uygulama özellikleri
henüz kodlanmadı. Seçilen teknoloji yığını ve gerekçeleri
`docs/ARCHITECTURE.md` içindedir.

Kurulu olan: Python 3.13.15 sanal ortamı (uv), Django 5.2.17 LTS ve
`docs/ARCHITECTURE.md` bölüm 19'daki bağımlılıklar, iskelet ayarlar, test
çalıştırıcı. Kurulu olmayan: PostgreSQL (kurulum `sudo` gerektirir, faz 1'de
yapılacak).

## Geliştirme ortamı

```bash
# Bağımlılıkları kur (uv gerekir)
uv sync

# Sırları ortamdan ver (koda yazılmaz; hiçbir ortamda koda gömülü yedek yoktur,
# tanımsızsa uygulama bilinçli olarak başlamaz)
export DJANGO_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"
export DJANGO_DEBUG=1

# Kontroller
.venv/bin/python manage.py check
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/bandit -r config apps -q
.venv/bin/pip-audit
```

Örnek ortam değişkenleri: `.env.example` (gerçek değerler `.env` dosyasında
tutulur ve depoya girmez).

Not: Veritabanı gerektiren komutlar (`migrate`, `runserver` ile veri erişimi)
PostgreSQL kurulana kadar çalışmaz; ayrıntı `docs/CHANGELOG.md` faz notları.

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
