"""
Persopyro — Django ayarları.

Tasarım kararları: docs/ARCHITECTURE.md
Tüm gizli değerler ortam değişkenlerinden okunur (kural: secret koda yazılmaz).

Tek ayar dosyası kullanılır; ortam farkı (geliştirme/üretim) yalnızca ortam
değişkenleriyle yönetilir. Amaç: gereksiz dosya ve karmaşıklık eklememek.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """
    Basit .env yükleyici (ek bağımlılık yok).

    Geliştirme kolaylığı içindir: değerler zaten tanımlıysa üzerine yazılmaz, yani
    üretimde systemd/secret store tarafından verilen ortam değişkenleri kazanır.
    Gerçek .env dosyası git'e girmez (.gitignore).
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# Farklı bir ortam dosyası göstermek için DJANGO_ENV_FILE kullanılabilir.
_load_env_file(Path(os.environ.get("DJANGO_ENV_FILE", BASE_DIR / ".env")))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Temel güvenlik ayarları -------------------------------------------------

# Varsayılan olarak üretim davranışı (DEBUG kapalı). Geliştirmede DJANGO_DEBUG=1.
DEBUG = env_bool("DJANGO_DEBUG", False)

# Secret koda yazılmaz ve hiçbir ortam için koda gömülü yedek değer bulunmaz.
# Her ortamda (geliştirme dahil) ortam değişkeninden okunur; tanımsızsa uygulama
# bilinçli olarak başlamaz. Geliştirmede: export DJANGO_SECRET_KEY=...
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY ortam değişkeni tanımlı değil. "
        "Secret'lar kaynak koda yazılmaz (bkz. docs/DEVELOPMENT_RULES.md). "
        "Geliştirme için: export DJANGO_SECRET_KEY=$(python3 -c "
        "'import secrets;print(secrets.token_urlsafe(50))')"
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# --- Uygulamalar -------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Üçüncü parti
    "rest_framework",
    "knox",
    "django_q",
    # Yerel uygulamalar (faz 2'den itibaren apps/<modül> biçiminde eklenir)
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # NOT: Kiracı bağlamı (tenant_id) ara yazılımı faz 1/2'de buraya eklenir.
    # Eklendiğinde "tenant bağlamı yoksa sorgu çalışmasın" kuralı zorunlu olacaktır
    # (docs/ARCHITECTURE.md bölüm 2.3).
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Veritabanı --------------------------------------------------------------
# PostgreSQL zorunludur (RLS, JSONB, kısıtlar). SQLite kullanılmaz; çünkü
# çok kiracılı izolasyon testleri SQLite üzerinde yanlış güven verir.

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "persopyro"),
        # Uygulama rolü tablo sahibi DEĞİLDİR: sahibi olursa RLS atlanır.
        "USER": os.environ.get("POSTGRES_USER", "persopyro_app"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Şema kararı: tüm iş tabloları hr şemasında yaşar.
        "OPTIONS": {"options": "-c search_path=hr,public"},
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
    }
}

# --- Veritabanı rolleri -----------------------------------------------------
# Rol adları burada sabit isim olarak tutulur: testler bu adların YETKİLERİNİ
# sınar, oysa testlerin hangi rolle bağlandığı değişebilir (bkz. scripts/run_tests.sh).
# Bağlanan rol ile yetkisi sınanan rolü karıştırmamak izolasyon denetiminin temelidir.
APP_DB_ROLE = os.environ.get("POSTGRES_APP_ROLE", "persopyro_app")
OWNER_DB_ROLE = os.environ.get("POSTGRES_OWNER_USER", "persopyro_owner")

# --- Kimlik doğrulama --------------------------------------------------------
# NOT: Özel kullanıcı modeli (AUTH_USER_MODEL) faz 2'de tanımlanacak ve
# İLK MIGRATION'DAN ÖNCE ayarlanmalıdır; sonradan değiştirmek yüksek maliyetlidir
# (docs/ARCHITECTURE.md bölüm 5.2).

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Parola hash'leme: Argon2id birincil; bcrypt yalnızca mevcut hash'ler için uyumluluk.
# Argon2id birincil olduğu için listenin başında olmalıdır.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# --- API (DRF) ---------------------------------------------------------------
# Varsayılan davranış: reddet. Açık izin yoksa erişim yoktur.

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "knox.auth.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "10/min",  # kimlik doğrulama uçları en katı sınıfta
        "user": "1000/hour",
        "anon": "60/min",
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "rest_framework.views.exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Persopyro API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- Arka plan görevleri (django-q2) ----------------------------------------
# Redis yok: broker olarak Django ORM kullanılır (docs/ARCHITECTURE.md bölüm 6.1).

Q_CLUSTER = {
    "name": "persopyro",
    "workers": int(os.environ.get("Q_WORKERS", "2")),
    "timeout": 300,
    "retry": 600,
    "max_attempts": 3,
    "catch_up": False,
    "save_limit": 500,
    "orm": "default",
    "sync": DEBUG,  # geliştirmede senkron çalışır, worker gerekmez
    "poll": 2,
}

# --- E-posta ----------------------------------------------------------------
# Geliştirmede konsola yazılır; üretimde sağlayıcı bağımsız SMTP relay kullanılır.

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Persopyro <no-reply@localhost>")

# --- Dosya saklama ----------------------------------------------------------
# Belge içerikleri nesne deposunda (S3 uyumlu) tutulur; veritabanı yalnızca üst
# veri saklar. Anahtar tanımlı değilse belge yükleme çalışmaz (kasıtlı).

STORAGE_S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")
STORAGE_S3_REGION = os.environ.get("S3_REGION", "")
STORAGE_S3_BUCKET = os.environ.get("S3_BUCKET", "")
STORAGE_S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
STORAGE_S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
STORAGE_SIGNED_URL_TTL_SECONDS = int(os.environ.get("S3_SIGNED_URL_TTL_SECONDS", "300"))

# --- Uluslararasılaştırma / zaman -------------------------------------------

LANGUAGE_CODE = "tr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Statik dosyalar --------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Üretim güvenlik ayarları ----------------------------------------------
# Yalnızca DEBUG kapalıyken uygulanır; üretimde güvensiz varsayılan bırakılmaz.

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
    CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# --- Loglama ----------------------------------------------------------------
# Kişisel veri loglanmaz. Denetim kaydı ayrıdır (audit_logs); bu ayar teknik log içindir.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "format": (
                '{"time":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# --- Hata izleme (opsiyonel) ------------------------------------------------
# DSN tanımlı değilse Sentry tamamen devre dışıdır; sistem onsuz tam çalışır.

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=False,  # kişisel veri gönderilmez
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
    )
