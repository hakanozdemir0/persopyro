"""
İstek hattı, kimlik doğrulama uç noktaları ve yetki sınıfı testleri (Faz 2, 2. dilim).

Kapsam:
  * Kiracı bağlamı: istek başına kurulur, oturumdan ve alan adından çözümlenir,
    uyuşmazlık reddedilir.
  * Oturum girişi: başarılı, hatalı parola, kilitli/pasif kullanıcı, CSRF.
  * Token girişi: token üretimi, iptal (yalnızca o cihaz), korumalı uç nokta.
  * MFA: açıkken kodsuz giriş reddedilir, doğru kodla giriş yapılır.
  * Yetki sınıfı: izin varsa geçer, yoksa 403; izin belirtilmemişse reddedilir.
  * Kullanıcı numaralandırmasına karşı genel hata mesajı.
"""

import pyotp
import pytest

from apps.common.crypto import encrypt_str
from apps.rbac.models import Permission, Role, RolePermission
from apps.rbac.services import create_default_roles
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sonda_urlleri(settings) -> None:  # noqa: ANN001
    """Testlerde sonda görünümlerini içeren URL yapılandırması kullanılır."""
    settings.ROOT_URLCONF = "tests.urls"

LOGIN_URL = "/api/v1/auth/login"
TOKEN_URL = "/api/v1/auth/token"
TOKEN_LOGOUT_URL = "/api/v1/auth/token/logout"
LOGOUT_URL = "/api/v1/auth/logout"
BAGLAM = "/_probe/baglam"
KORUMALI = "/_probe/korumali"
IZINLI = "/_probe/izinli"
IZIN_TANIMSIZ = "/_probe/izin-tanimsiz"


def giris_yap(api_client, tenant, user, sifre="Guclu-Parola-123"):  # noqa: ANN001
    return api_client.post(
        LOGIN_URL,
        {"tenant": tenant.slug, "email": user.email, "password": sifre},
        format="json",
    )


def token_govdesi(  # noqa: ANN001
    tenant,
    user,
    *,
    scopes=("employee.read",),
    sifre="Guclu-Parola-123",
    **ek,
) -> dict:
    """
    OD26 sonrası token gövdesi.

    `purpose` ve `scopes` ZORUNLUDUR (kapsamsız token üretilemez); kapsam
    sahibinin izinlerinin alt kümesi olmalıdır → testler kullanıcıya
    `izinli_kullanici` ile izin verir.
    """
    govde = {
        "tenant": tenant.slug,
        "email": user.email,
        "password": sifre,
        "purpose": "test icin token amaci",
        "scopes": list(scopes),
    }
    govde.update(ek)
    return govde


# --- Kiracı bağlamı ve istek hattı -----------------------------------------


def test_request_id_yanit_basliginda_doner(api_client, tenant_a) -> None:  # noqa: ANN001
    yanit = api_client.get(BAGLAM, HTTP_HOST="localhost")
    assert yanit.headers.get("X-Request-ID")


def test_oturum_kurulunca_baglam_istek_boyunca_aktif(api_client, tenant_a, user_a) -> None:
    assert giris_yap(api_client, tenant_a, user_a).status_code == 200
    veri = api_client.get(BAGLAM).json()
    assert veri["tenant"] == str(tenant_a.id)
    assert veri["user"] == "a@ornek.test"


def test_oturum_yoksa_baglam_kurulmaz(api_client, tenant_a) -> None:  # noqa: ANN001, ARG001
    veri = api_client.get(BAGLAM, HTTP_HOST="localhost").json()
    assert veri["tenant"] is None
    assert veri["user"] is None


def test_alan_adi_ile_kiracı_cozulur(api_client, tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        tenant_a.domain = "ik.kiraci-a.test"
        tenant_a.save(update_fields=["domain", "updated_at"])

    veri = api_client.get(BAGLAM, HTTP_HOST="ik.kiraci-a.test").json()
    assert veri["tenant"] == str(tenant_a.id)


def test_oturum_ve_alan_adi_uyusmazsa_reddedilir(api_client, tenant_a, tenant_b, user_a) -> None:
    """Bir kiracının oturumuyla başka kiracının alan adına istek yapılamaz."""
    with tenant_context(tenant_b.id):
        tenant_b.domain = "ik.kiraci-b.test"
        tenant_b.save(update_fields=["domain", "updated_at"])

    assert giris_yap(api_client, tenant_a, user_a).status_code == 200
    yanit = api_client.get(BAGLAM, HTTP_HOST="ik.kiraci-b.test")
    assert yanit.status_code == 400
    assert yanit.json()["error"]["code"] == "tenant_mismatch"


# --- Oturum girişi ----------------------------------------------------------


def test_basarili_oturum_girisi(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    yanit = giris_yap(api_client, tenant_a, user_a)
    assert yanit.status_code == 200
    assert yanit.json()["data"]["email"] == "a@ornek.test"
    assert api_client.session["tenant_id"] == str(tenant_a.id)


def test_hatali_parola_401_ve_genel_mesaj(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    yanit = giris_yap(api_client, tenant_a, user_a, sifre="yanlis-parola")
    assert yanit.status_code == 401
    assert yanit.json()["error"]["code"] == "invalid_credentials"


def test_olmayan_kullanici_ile_hatali_parola_ayni_yaniti_verir(  # noqa: ANN001
    api_client, tenant_a, user_a
) -> None:
    """Kullanıcı numaralandırmasını engellemek için yanıtlar aynı olmalı."""
    yok = api_client.post(
        LOGIN_URL,
        {"tenant": tenant_a.slug, "email": "yok@ornek.test", "password": "herhangi"},
        format="json",
    )
    yanlis = giris_yap(api_client, tenant_a, user_a, sifre="yanlis-parola")
    assert yok.status_code == yanlis.status_code == 401
    assert yok.json() == yanlis.json()


def test_bilinmeyen_kiracı_401(api_client, user_a) -> None:  # noqa: ANN001, ARG001
    yanit = api_client.post(
        LOGIN_URL,
        {"tenant": "olmayan-kiraci", "email": "a@ornek.test", "password": "Guclu-Parola-123"},
        format="json",
    )
    assert yanit.status_code == 401
    assert yanit.json()["error"]["code"] == "invalid_credentials"


def test_baska_kiracinin_alan_adinda_giris_yapilamaz(  # noqa: ANN001
    api_client, tenant_a, tenant_b, user_a
) -> None:
    """A kullanıcısı, B kiracısının slug'ı ile giriş yapamaz."""
    assert giris_yap(api_client, tenant_b, user_a).status_code == 401


def test_pasif_kullanici_giris_yapamaz(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        user_a.status = "inactive"
        user_a.save(update_fields=["status", "updated_at"])
    assert giris_yap(api_client, tenant_a, user_a).status_code == 401


def test_kilitli_kullanici_dogru_parolayla_da_giremez(api_client, tenant_a, user_a) -> None:
    for _ in range(5):
        giris_yap(api_client, tenant_a, user_a, sifre="yanlis-parola")
    assert giris_yap(api_client, tenant_a, user_a).status_code == 401


def test_oturum_ucu_csrf_korumali(tenant_a, user_a) -> None:  # noqa: ANN001
    """Tarayıcı girişi CSRF korumalıdır."""
    from rest_framework.test import APIClient

    istemci = APIClient(enforce_csrf_checks=True)
    yanit = istemci.post(
        LOGIN_URL,
        {"tenant": tenant_a.slug, "email": user_a.email, "password": "Guclu-Parola-123"},
        format="json",
    )
    assert yanit.status_code == 403


def test_cikis_oturumu_kapatir(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    giris_yap(api_client, tenant_a, user_a)
    assert api_client.post(LOGOUT_URL, format="json").status_code == 204
    assert api_client.get(BAGLAM).json()["tenant"] is None


def test_cikis_kimlik_dogrulama_gerektirir(api_client, tenant_a) -> None:  # noqa: ANN001, ARG001
    """Kimliksiz çıkış isteği reddedilir (401/403); oturum kapanmaz."""
    yanit = api_client.post(LOGOUT_URL, format="json")
    assert yanit.status_code in (401, 403)


# --- Token girişi -----------------------------------------------------------


def test_token_girisi_token_uretir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    izinli_kullanici(user_a, tenant_a, "employee.read")
    yanit = api_client.post(TOKEN_URL, token_govdesi(tenant_a, user_a), format="json")
    assert yanit.status_code == 201
    assert yanit.json()["data"]["token"]


def test_token_korumali_uc_noktaya_erisir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = api_client.post(
        TOKEN_URL, token_govdesi(tenant_a, user_a), format="json"
    ).json()["data"]["token"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    yanit = api_client.get(KORUMALI)
    assert yanit.status_code == 200
    # Token ile gelen istekte de kiracı bağlamı kurulur.
    assert yanit.json()["tenant"] == str(tenant_a.id)


def test_token_cikisi_yalnizca_o_tokeni_iptal_eder(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    izinli_kullanici(user_a, tenant_a, "employee.read")
    ilk = api_client.post(
        TOKEN_URL, token_govdesi(tenant_a, user_a), format="json"
    ).json()["data"]["token"]
    ikinci = api_client.post(
        TOKEN_URL, token_govdesi(tenant_a, user_a), format="json"
    ).json()["data"]["token"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {ilk}")
    assert api_client.post(TOKEN_LOGOUT_URL, format="json").status_code == 204
    assert api_client.get(KORUMALI).status_code == 401

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {ikinci}")
    assert api_client.get(KORUMALI).status_code == 200


def test_tokensiz_korumali_uc_nokta_401(api_client, tenant_a) -> None:  # noqa: ANN001, ARG001
    assert api_client.get(KORUMALI).status_code == 401


def test_token_duz_metin_saklanmaz(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """Veritabanında düz metin token bulunmamalı; yalnızca özeti saklanır."""
    from apps.accounts.models import ApiToken

    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = api_client.post(
        TOKEN_URL, token_govdesi(tenant_a, user_a), format="json"
    ).json()["data"]["token"]

    with tenant_context(tenant_a.id):
        kayit = ApiToken.objects.get(user=user_a)
    assert kayit.token_hash != token
    assert kayit.token_hash == ApiToken.hash_token(token)
    assert len(kayit.token_hash) == 64  # SHA-256
    # OD26: zorunlu alanlar kaydedilir; sır (düz metin) hiçbir kolonda yoktur.
    assert kayit.purpose == "test icin token amaci"
    assert list(kayit.scopes) == ["employee.read"]
    assert kayit.revoked_at is None


def test_suresi_gecmis_token_reddedilir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """
    Süresi geçmiş token reddedilir.

    DİKKAT: `expires_at` değişmezdir (veritabanı tetikleyicisi UPDATE'i engeller),
    bu yüzden süresi geçmiş token doğrudan üretilir — UPDATE ile geriye alınmaz.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.accounts.models import ApiToken

    izinli_kullanici(user_a, tenant_a, "employee.read")
    duz_metin = "suresi-gecmis-token-degeri"
    with tenant_context(tenant_a.id):
        ApiToken.objects.create(
            tenant_id=tenant_a.id,
            user=user_a,
            token_hash=ApiToken.hash_token(duz_metin),
            purpose="test icin token amaci",
            scopes=["employee.read"],
            expires_at=timezone.now() - timedelta(minutes=1),
        )

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {duz_metin}")
    assert api_client.get(KORUMALI).status_code == 401


def test_gecersiz_token_reddedilir(api_client, tenant_a) -> None:  # noqa: ANN001, ARG001
    api_client.credentials(HTTP_AUTHORIZATION="Token uydurma-bir-token")
    assert api_client.get(KORUMALI).status_code == 401


def test_token_cikisi_kaydi_korur_ve_iptal_izi_birakir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """Logout satırı SİLMEZ (OD26): iptal işaretlenir, denetim izi kalır."""
    from apps.accounts.models import ApiToken

    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = api_client.post(
        TOKEN_URL, token_govdesi(tenant_a, user_a), format="json"
    ).json()["data"]["token"]
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.post(TOKEN_LOGOUT_URL, format="json").status_code == 204

    with tenant_context(tenant_a.id):
        kayit = ApiToken.objects.filter(user=user_a).first()
    assert kayit is not None, "logout satırı silmemelidir (iptal izi kaybolur)"
    assert kayit.revoked_at is not None
    assert kayit.revoked_reason == "logout"


# --- MFA --------------------------------------------------------------------


def _mfa_ac(tenant, user) -> str:  # noqa: ANN001
    """Kullanıcıda MFA'yı açar; kullanılacak TOTP sırrını döndürür."""
    sir = pyotp.random_base32()
    with tenant_context(tenant.id):
        user.multi_factor_enabled = True
        user.totp_secret = encrypt_str(sir)
        user.save(update_fields=["multi_factor_enabled", "totp_secret", "updated_at"])
    return sir


def test_mfa_acikken_kodsuz_giris_reddedilir(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    _mfa_ac(tenant_a, user_a)
    yanit = giris_yap(api_client, tenant_a, user_a)
    assert yanit.status_code == 401
    assert yanit.json()["error"]["code"] == "mfa_required"


def test_mfa_acikken_yanlis_kod_reddedilir(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    _mfa_ac(tenant_a, user_a)
    yanit = api_client.post(
        LOGIN_URL,
        {"tenant": tenant_a.slug, "email": user_a.email, "password": "Guclu-Parola-123",
         "totp_code": "000000"},
        format="json",
    )
    assert yanit.status_code == 401
    assert yanit.json()["error"]["code"] == "mfa_invalid"


def test_mfa_acikken_dogru_kodla_giris_yapilir(api_client, tenant_a, user_a) -> None:
    sir = _mfa_ac(tenant_a, user_a)
    yanit = api_client.post(
        LOGIN_URL,
        {"tenant": tenant_a.slug, "email": user_a.email, "password": "Guclu-Parola-123",
         "totp_code": pyotp.TOTP(sir).now()},
        format="json",
    )
    assert yanit.status_code == 200


def test_mfa_acikken_token_girisi_de_kod_ister(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """MFA, token yolunda da uygulanır: hiçbir giriş yolu atlamaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    _mfa_ac(tenant_a, user_a)
    yanit = api_client.post(TOKEN_URL, token_govdesi(tenant_a, user_a), format="json")
    assert yanit.status_code == 401
    assert yanit.json()["error"]["code"] == "mfa_required"


def test_mfa_kapaliyken_kod_istenmez(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    assert giris_yap(api_client, tenant_a, user_a).status_code == 200


# --- Yetki sınıfı -----------------------------------------------------------


def test_izni_olan_kullanici_gecer(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    izinli_kullanici(user_a, tenant_a, "employee.read")
    giris_yap(api_client, tenant_a, user_a)
    assert api_client.get(IZINLI).status_code == 200


def test_izni_olmayan_kullanici_403_alir(api_client, tenant_a, user_a) -> None:  # noqa: ANN001
    giris_yap(api_client, tenant_a, user_a)
    assert api_client.get(IZINLI).status_code == 403


def test_izin_belirtilmemis_uc_nokta_reddedilir(  # noqa: ANN001, ARG001
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """Varsayılan reddet: izni belirtilmemiş uç nokta hiç kimseye açık değildir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    giris_yap(api_client, tenant_a, user_a)
    assert api_client.get(IZIN_TANIMSIZ).status_code == 403


def test_baska_kiracinin_rolu_izin_vermez(  # noqa: ANN001, ARG001
    api_client, tenant_a, tenant_b, user_a, izinli_kullanici
) -> None:
    """
    B kiracısında tanımlı bir rol, A kullanıcısına izin kazandırmamalı.

    Bu atama zaten veritabanı guard'ı tarafından engellenir (kiracılar arası
    referans); testin amacı sonuç durumunu da doğrulamaktır: A'da izin yok.
    """
    from django.db import DatabaseError, transaction

    with pytest.raises(DatabaseError), transaction.atomic():
        izinli_kullanici(user_a, tenant_b, "employee.read")

    giris_yap(api_client, tenant_a, user_a)
    assert api_client.get(IZINLI).status_code == 403


# --- Varsayılan roller ------------------------------------------------------


def test_varsayilan_roller_uretiliyor(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        kodlar = set(roller)
        assert kodlar == {
            "tenant_admin",
            "tenant_officer",  # M7: §4.2 hedef kataloğu (şirket yetkilisi)
            "hr_manager",  # şablon rol olarak korunur (§4.2)
            "dept_manager",  # M7: eski `manager` rolünün yerini alır (OD5)
            "employee",
        }
        # tenant_admin tüm izinlere sahip olmalı.
        admin_izinleri = set(
            RolePermission.objects.filter(role=roller["tenant_admin"]).values_list(
                "permission__code", flat=True
            )
        )
        assert {"employee.read", "role.manage", "audit.read", "billing.manage"} <= admin_izinleri


def test_varsayilan_roller_idempotent(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        create_default_roles(tenant_a)
        ilk = Role.objects.count()
        create_default_roles(tenant_a)
        assert Role.objects.count() == ilk


def test_varsayilan_rol_uretiminde_eksik_izin_sessizce_gecilmez(tenant_a) -> None:  # noqa: ANN001
    """İzin kataloğu eksikse hata verilir; 'izin verildi sanılan' durum olmaz."""
    from apps.rbac.services import MissingPermissionError

    with tenant_context(tenant_a.id):
        Permission.objects.filter(code="employee.read").delete()
        with pytest.raises(MissingPermissionError):
            create_default_roles(tenant_a)


# --- Hız sınırlama ----------------------------------------------------------


def test_giris_ucu_hiz_sinirlidir(api_client, monkeypatch) -> None:  # noqa: ANN001
    """
    Kimlik doğrulama uçları en katı sınıftadır (docs/API.md bölüm 9).

    Oran, DRF'nin sınıf özniteliği üzerinden bu test için sıkılaştırılır
    (testlerde varsayılan olarak kapalıdır; bkz. conftest).
    """
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setattr(
        SimpleRateThrottle,
        "THROTTLE_RATES",
        {"auth": "2/min", "user": None, "anon": None},
    )
    gövde = {"tenant": "olmayan", "email": "x@ornek.test", "password": "gecersiz-parola"}
    kodlar = [api_client.post(LOGIN_URL, gövde, format="json").status_code for _ in range(3)]
    assert kodlar[0] == 401
    assert kodlar[1] == 401
    assert kodlar[2] == 429
