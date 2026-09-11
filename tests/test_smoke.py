"""
Persopyro — iskelet kontrol testleri.

Bu testler veritabanına bağlanmaz; yalnızca kurulumun ve güvenli
varsayılanların yerinde olduğunu doğrular. Kiracı izolasyonu, yetki ve
denetim kaydı testleri ilgili fazlarda eklenir (docs/ARCHITECTURE.md bölüm 14).
"""

import os
import subprocess
import sys

import pytest
from django.conf import settings
from django.urls import Resolver404, resolve

BASE_DIR = str(settings.BASE_DIR)


def _probe(code: str, *, env_file: str = "/dev/null", remove: tuple[str, ...] = (), **extra: str):
    """
    Ayarları TEMİZ bir ortamda (projedeki .env devre dışı) yükleyip bir ifadeyi yazdırır.

    Gereklidir: geliştirme .env dosyası DJANGO_DEBUG=1 içerdiğinde "varsayılan
    güvenli mi?" sorusu ancak dosya devre dışıyken yanıtlanabilir.
    """
    env = {k: v for k, v in os.environ.items() if k not in set(remove)}
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["DJANGO_ENV_FILE"] = env_file
    env.update(extra)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_debug_varsayilan_kapali() -> None:
    """Üretim davranışı varsayılan olmalı; DEBUG açıkça istenmeli."""
    result = _probe(
        "import config.settings as s; print(s.DEBUG)",
        remove=("DJANGO_DEBUG",),
        DJANGO_SECRET_KEY="yalnizca-test-icin",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_veritabani_postgresql_ve_hr_semasi() -> None:
    db = settings.DATABASES["default"]
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert "search_path=hr,public" in db["OPTIONS"]["options"]


def test_uygulama_rolu_tablo_sahibi_degil() -> None:
    """Uygulama rolü sahip olursa RLS atlanır; rol adı bunu yansıtmalı."""
    assert settings.APP_DB_ROLE == "persopyro_app"
    assert settings.APP_DB_ROLE != settings.OWNER_DB_ROLE


def test_utc_saat_dilimi() -> None:
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
    """
    DEBUG kapalıyken (varsayılan) üretim çerez/başlık politikaları devreye girer.

    .env dosyası devre dışı bırakılarak sınanır; geliştirme .env'i DJANGO_DEBUG=1
    içerdiği için üretim davranışı ancak böyle doğrulanabilir.
    """
    result = _probe(
        "import config.settings as s; "
        "print(s.SESSION_COOKIE_SECURE, s.SESSION_COOKIE_HTTPONLY, s.CSRF_COOKIE_SECURE, "
        "s.SECURE_CONTENT_TYPE_NOSNIFF, s.X_FRAME_OPTIONS, s.SECURE_SSL_REDIRECT)",
        remove=("DJANGO_DEBUG",),
        DJANGO_SECRET_KEY="yalnizca-test-icin",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True", "True", "True", "DENY", "True"]


def test_admin_yolu_yok() -> None:
    """
    OD19 (kapalı): Django Admin kaldırılmıştır; `/admin/` bir route DEĞİLDİR.

    Beklenen davranış 404'tür. Bu test bilinçli olarak VERİTABANI KULLANMAZ
    (`test_smoke.py` kuralı); gerçek HTTP 404 davranışı
    `tests/test_od19_django_admin_kapali.py` içinde sınanır.
    """
    from django.urls import get_resolver

    with pytest.raises(Resolver404):
        resolve("/admin/")
    assert not any("admin" in str(p.pattern) for p in get_resolver().url_patterns)
    assert "django.contrib.admin" not in settings.INSTALLED_APPS


def test_secret_key_yoksa_uygulama_baslamaz() -> None:
    """Secret koda yazılmaz; tanımsızsa uygulama açıkça hata vermelidir."""
    result = _probe(
        "import config.settings",
        remove=("DJANGO_SECRET_KEY", "DJANGO_DEBUG"),
    )
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr


def test_env_dosyasi_ortam_degiskenini_ezmez() -> None:
    """
    Gerçek ortam değişkeni .env'den öncelikli olmalı; aksi halde üretimde
    systemd/secret store değerleri sessizce gölgelenirdi.
    """
    result = _probe(
        "import config.settings as s; print(s.ALLOWED_HOSTS)",
        DJANGO_SECRET_KEY="yalnizca-test-icin",
        DJANGO_ALLOWED_HOSTS="ornek.test",
    )
    assert result.returncode == 0, result.stderr
    assert "ornek.test" in result.stdout
