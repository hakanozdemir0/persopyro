"""
OD19 (kapalı) regresyon testleri — Django Admin / `auth_*` paralel yetki yolu.

Kapsam (M22–M22d):

  * `/admin/` route'u YOK (404) ve `django.contrib.admin` uygulama listesinde yok
  * `is_staff` invariantı: uygulama koruyucusu + veritabanı kısıtı (ham SQL dahil)
  * `create_superuser` / `createsuperuser` fail-closed (Django Admin erişimi üretmez)
  * Django teknik tablolarında (`auth_*`, `django_admin_log`) uygulama rolünün
    **hiç** yetkisi yok — SELECT dahil; grant scripti bu daraltmayı geri açmıyor
  * normal tenant girişi ve Persopyro RBAC akışı bozulmuyor; Django `auth_*`
    tabloları hiçbir Persopyro yetkisi üretmiyor

Yetki ölçümü, bu projede yerleşik yöntemle yapılır (bkz. `tests/test_database_roles.py`):
bağlantı rolü sahip (owner) olduğu için davranış denemesi yerine
`has_table_privilege(APP_DB_ROLE, ...)` ile **hedef rol** sınanır.
"""

from io import StringIO
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import Resolver404, resolve

from apps.accounts.models import DjangoAdminRemovedError, User
from apps.rbac.services import user_has_permission
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

ADMIN_YOLLARI = ("/admin/", "/admin/login/", "/admin/auth/user/")
TEKNIK_TABLOLAR = (
    "auth_permission",
    "auth_group",
    "auth_group_permissions",
    "django_admin_log",
)
YETKI_TURLERI = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _tablo_var(tablo: str) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT to_regclass('hr.' || %s) IS NOT NULL", [tablo])
        return cur.fetchone()[0]


def _yetki(tablo: str, yetki: str) -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, 'hr.' || %s, %s)",
            [settings.APP_DB_ROLE, tablo, yetki],
        )
        return cur.fetchone()[0]


# --- 1) /admin/ yok ---------------------------------------------------------


def test_admin_yolu_404_doner() -> None:
    """GET /admin/ → 404 (route artık yok)."""
    assert Client().get("/admin/").status_code == 404


@pytest.mark.parametrize("yol", ADMIN_YOLLARI)
def test_admin_yollari_cozulemez(yol) -> None:  # noqa: ANN001
    """Admin'in alt yolları da (login, model listesi) çözümlenemez."""
    with pytest.raises(Resolver404):
        resolve(yol)


# --- 2) uygulama listesi ----------------------------------------------------


def test_django_admin_uygulama_listesinde_yok() -> None:
    assert "django.contrib.admin" not in settings.INSTALLED_APPS
    assert "django.contrib.admin" not in {a.name for a in apps.get_app_configs()}
    # `django.contrib.auth` KALIR: parola hash'leme/oturum altyapısı; yetki kaynağı değil.
    assert "django.contrib.auth" in settings.INSTALLED_APPS
    assert settings.AUTH_USER_MODEL == "accounts.User"


# --- 3) is_staff invariantı -------------------------------------------------


def test_is_staff_true_uygulama_koruyucusu(tenant_a) -> None:  # noqa: ANN001
    """Kiracı kullanıcısı `is_staff=True` oluşturulamaz (fail-closed)."""
    with tenant_context(tenant_a.id), pytest.raises(ValueError, match="is_staff"):
        User.objects.create_user(
            email="staff@ornek.test",
            password="Guclu-Parola-123",
            tenant=tenant_a,
            is_staff=True,
        )


def test_is_staff_kolonu_sonradan_acilamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """Var olan kullanıcıda bayrak sonradan açılamaz (uygulama katmanı)."""
    with tenant_context(tenant_a.id), pytest.raises(ValueError, match="is_staff"):
        user_a.is_staff = True
        user_a.save(update_fields=["is_staff"])


def test_is_staff_db_kisiti_ham_sqlde_de_engeller(tenant_a, user_a) -> None:  # noqa: ANN001
    """ORM atlatılsa bile veritabanı kısıtı bayrağı açtırmaz."""
    with tenant_context(tenant_a.id):
        with pytest.raises(IntegrityError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE hr.users SET is_staff = true WHERE id = %s", [str(user_a.pk)]
                )


def test_is_staff_kisiti_semada_var() -> None:
    """12) Migration'lardan türeyen şemada kısıt yerinde (sıfırdan kurulumun sonucu)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'hr.users'::regclass"
            " AND conname = 'users_is_staff_disabled'"
        )
        assert cur.fetchone()[0] == 1


def test_audit_komutu_invarianti_dogrular(tenant_a, user_a) -> None:  # noqa: ANN001
    """Denetim sorgusu (M22c): ihlal yok + DB kısıtı var."""
    cikti = StringIO()
    call_command("audit_is_staff", stdout=cikti)
    metin = cikti.getvalue()
    assert "is_staff_true=0" in metin
    assert "users_is_staff_disabled: var" in metin


# --- 4) create_superuser / createsuperuser ---------------------------------


def test_create_superuser_desteklenmiyor() -> None:
    """Django Admin erişimi üretecek akış fail-closed: açık hata, kayıt yok."""
    with pytest.raises(NotImplementedError, match="desteklenmiyor"):
        User.objects.create_superuser(email="root@ornek.test", password="Guclu-Parola-123")


def test_django_admin_kaldirildi_hatasi_cli_icin_uygun() -> None:
    """
    Hata tipi, Django'nun `createsuperuser` komutunun hata yoluna uyar.

    Ölçüm: `django.contrib.auth`'ın kendi komutu etkindir (komut aramasında daha
    önce geldiği için bir override gölgede kalır) ve `ValidationError`'ı temiz bir
    `CommandError` mesajına çevirir; bu yüzden hata iki ataya sahiptir.
    """
    hata = DjangoAdminRemovedError("deneme")
    assert isinstance(hata, NotImplementedError)
    assert isinstance(hata, ValidationError)
    assert hata.messages == ["deneme"]


def test_createsuperuser_komutu_acik_hata_verir() -> None:
    with pytest.raises(CommandError, match="desteklenmiyor"):
        call_command("createsuperuser", email="root@ornek.test", interactive=False)


# --- 5-6) mevcut akışlar bozulmuyor ----------------------------------------


def test_tenant_girisi_ve_token_akisi_bozulmadi(  # noqa: ANN001
    api_client, tenant_a, user_a, izinli_kullanici, settings
) -> None:
    settings.ROOT_URLCONF = "tests.urls"
    izinli_kullanici(user_a, tenant_a, "employee.read")

    giris = api_client.post(
        "/api/v1/auth/login",
        {"tenant": tenant_a.slug, "email": user_a.email, "password": "Guclu-Parola-123"},
        format="json",
    )
    assert giris.status_code == 200

    token = api_client.post(
        "/api/v1/auth/token",
        {
            "tenant": tenant_a.slug,
            "email": user_a.email,
            "password": "Guclu-Parola-123",
            "purpose": "od19 regresyon testi icin token",
            "scopes": ["employee.read"],
        },
        format="json",
    )
    assert token.status_code == 201

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token.json()['data']['token']}")
    assert api_client.get("/_probe/korumali").status_code == 200
    assert api_client.get("/_probe/izinli").status_code == 200


def test_rbac_izin_akisi_bozulmadi(  # noqa: ANN001
    api_client, tenant_a, user_a, izinli_kullanici, settings
) -> None:
    settings.ROOT_URLCONF = "tests.urls"
    api_client.post(
        "/api/v1/auth/login",
        {"tenant": tenant_a.slug, "email": user_a.email, "password": "Guclu-Parola-123"},
        format="json",
    )
    assert api_client.get("/_probe/izinli").status_code == 403  # izin yok

    izinli_kullanici(user_a, tenant_a, "employee.read")
    assert api_client.get("/_probe/izinli").status_code == 200  # izin var


def test_django_auth_tablolari_persopyro_yetkisi_uretmez(tenant_a, user_a) -> None:  # noqa: ANN001
    """
    Django'nun yetki kavramları Persopyro izni ÜRETMEZ.

    Kullanıcı izni yalnızca Persopyro RBAC tablolarından çözülür; `PermissionsMixin`
    kullanılmadığı için `groups`/`user_permissions` ve `is_superuser` alanları
    **yoktur**, `is_staff` ise hiçbir izin vermez.
    """
    assert user_has_permission(user_a, "employee.read") is False
    assert not hasattr(user_a, "groups")
    assert not hasattr(user_a, "user_permissions")
    assert not hasattr(user_a, "is_superuser")
    assert user_a.is_staff is False
    assert user_a.has_perm("employee.read") is False  # Django arayüzü de aynı sonucu verir


# --- 7-12) DB yetkileri -----------------------------------------------------


@pytest.mark.parametrize("tablo", TEKNIK_TABLOLAR)
def test_django_teknik_tablolarinda_uygulama_rolu_yetkisiz(tablo) -> None:  # noqa: ANN001
    """
    7-10) `persopyro_app` bu tablolarda HİÇBİR yetkiye sahip değildir (SELECT dahil).

    `django_admin_log` YENİ kurulumda hiç oluşmaz (admin uygulaması yok); o durumda
    "yazma yolu yok" koşulu tablonun yokluğuyla sağlanır.
    """
    if not _tablo_var(tablo):
        assert tablo == "django_admin_log", f"{tablo} beklenmedik şekilde yok"
        return

    verilen = [y for y in YETKI_TURLERI if _yetki(tablo, y)]
    assert verilen == [], f"{tablo}: uygulama rolüne yetki verilmiş → {verilen}"


def test_grant_scripti_daraltmayi_geri_acmaz() -> None:
    """
    11) `scripts/02_grant_app_privileges.sql` tekrar çalıştırılsa bile yetkiler geri gelmez.

    Betik `ALTER DEFAULT PRIVILEGES` + "ALL TABLES" ile geniş DML verir; daraltma
    blokları bu satırdan SONRA geldiği için sonuç değişmez. (KRİTİK: bu testin
    kırmızı olması, üretimde migrate sonrası yetkilerin sessizce geri açıldığını
    gösterir.)
    """
    sql = (Path(settings.BASE_DIR) / "scripts" / "02_grant_app_privileges.sql").read_text(
        encoding="utf-8"
    )
    with connection.cursor() as cur:
        cur.execute(sql)

    for tablo in TEKNIK_TABLOLAR:
        if not _tablo_var(tablo):
            continue
        verilen = [y for y in YETKI_TURLERI if _yetki(tablo, y)]
        assert verilen == [], f"betik tekrar çalıştırıldıktan sonra {tablo} → {verilen}"

    # Diğer daraltmalar da korunmalı (regresyon): api_tokens UPDATE/DELETE kapalı.
    assert _yetki("api_tokens", "DELETE") is False
    assert _yetki("api_tokens", "UPDATE") is False
