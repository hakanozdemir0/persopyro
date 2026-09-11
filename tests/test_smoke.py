"""
Persopyro — iskelet kontrol testleri.

Bu testler veritabanına bağlanmaz; yalnızca kurulumun ve güvenli
varsayılanların yerinde olduğunu doğrular. Kiracı izolasyonu, yetki ve
denetim kaydı testleri ilgili fazlarda eklenir (docs/ARCHITECTURE.md bölüm 14).
"""

import os
import subprocess
import sys

from django.conf import settings
from django.urls import resolve


def test_debug_varsayilan_kapali() -> None:
    """Üretim davranışı varsayılan olmalı; DEBUG açıkça istenmeli."""
    assert settings.DEBUG is False


def test_veritabani_postgresql_ve_hr_semasi() -> None:
    db = settings.DATABASES["default"]
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert "search_path=hr,public" in db["OPTIONS"]["options"]


def test_uygulama_rolu_tablo_sahibi_degil() -> None:
    """Uygulama rolü sahip olursa RLS atlanır; rol adı bunu yansıtmalı."""
    assert settings.DATABASES["default"]["USER"] == "persopyro_app"


def test_utfki_saat_dilimi() -> None:
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True


def test_api_varsayilan_reddet() -> None:
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]


def test_parola_hash_argon2_birincil() -> None:
    assert settings.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"


def test_kuyruk_brokeri_orm_redis_yok() -> None:
    assert settings.Q_CLUSTER["orm"] == "default"
    assert "redis" not in settings.Q_CLUSTER


def test_uretim_cerezleri_guvenli() -> None:
    assert settings.SESSION_COOKIE_SECURE is True
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.CSRF_COOKIE_SECURE is True


def test_admin_urlleri_cozulur() -> None:
    assert resolve("/admin/login/").func is not None


def test_secret_key_yoksa_uygulama_baslamaz() -> None:
    """Secret koda yazılmaz; tanımsızsa uygulama açıkça hata vermelidir."""
    env = {k: v for k, v in os.environ.items() if k not in {"DJANGO_SECRET_KEY", "DJANGO_DEBUG"}}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=str(settings.BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr
