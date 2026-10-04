"""
Token kapsamı (OD26 / F8) ve kiracı durumu (F11) regresyon testleri.

Kapsam:
  * Üretim doğrulaması: `purpose`/`scopes` zorunlu; alt küme, katalog, joker,
    `platform.*`, TTL tavanı kuralları (`400`).
  * Kesişim kuralı: `etkin izinler = kullanıcı izinleri ∩ token kapsamı`
    (izin sonradan verilirse eski token kazanmaz; geri alınırsa kaybeder).
  * İptal: `logout` satır silmez, `revoked_at`/`revoked_reason` yazar; iptal ve
    süre bitimi `401`; `revoke-all` tüm token'ları iptal eder.
  * Kiracı durumu: `suspended`/`canceled`/soft-delete kiracıda yeni giriş ve
    mevcut oturum/token reddedilir; `active`/`trial` bozulmaz.

DİKKAT (ölçülmüş): `expires_at` değişmezdir; veritabanı tetikleyicisi UPDATE ile
değiştirilmesini engeller. Süresi geçmiş token bu yüzden UPDATE ile değil,
doğrudan INSERT ile üretilir (`ApiToken.objects.create`) — gerçek hayatta da bu
alan üretimden sonra değişmez.
"""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import ApiToken, TokenRevokeReason
from apps.rbac.models import RolePermission
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TOKEN_URL = "/api/v1/auth/token"
TOKEN_LOGOUT_URL = "/api/v1/auth/token/logout"
TOKEN_REVOKE_ALL_URL = "/api/v1/auth/token/revoke-all"
LOGIN_URL = "/api/v1/auth/login"
HEALTH = "/api/health"
READY = "/api/ready"
BAGLAM = "/_probe/baglam"
KORUMALI = "/_probe/korumali"
IZINLI = "/_probe/izinli"
IZINLI_LEAVE = "/_probe/izinli-leave"

SIFRE = "Guclu-Parola-123"
AMAC = "regresyon testi icin token amaci"


@pytest.fixture(autouse=True)
def _url_konfigurasyonu(settings) -> None:  # noqa: ANN001
    settings.ROOT_URLCONF = "tests.urls"


# --- yardımcılar ------------------------------------------------------------


def token_iste(api_client, tenant, user, *, scopes=("employee.read",), beklenen=201, **ek):
    """`POST /auth/token` çağrısı (varsayılan kapsam: `employee.read`)."""
    govde = {
        "tenant": tenant.slug,
        "email": user.email,
        "password": SIFRE,
        "purpose": AMAC,
        "scopes": list(scopes),
    }
    govde.update(ek)
    yanit = api_client.post(TOKEN_URL, govde, format="json")
    assert yanit.status_code == beklenen, yanit.content[:300]
    return yanit


def token_uret(api_client, tenant, user, **kw):  # noqa: ANN001, ANN202
    """Başarılı token üretir ve düz metni döner."""
    return token_iste(api_client, tenant, user, **kw).json()["data"]["token"]


def kiracı_guncelle(tenant, **alanlar) -> None:  # noqa: ANN001
    """Kiracı durumunu değiştirir (RLS nedeniyle bağlam içinde)."""
    with tenant_context(tenant.id):
        Tenant.objects.filter(pk=tenant.id).update(**alanlar)


def suresi_gecmis_token(user, tenant) -> str:  # noqa: ANN001
    """Süresi geçmiş token'ı doğrudan üretir (UPDATE tetikleyiciyle engellenir)."""
    duz = "suresi-gecmis-token-degeri"
    with tenant_context(tenant.id):
        ApiToken.objects.create(
            tenant_id=tenant.id,
            user=user,
            token_hash=ApiToken.hash_token(duz),
            label="suresi gecmis",
            token_kind="interactive",
            purpose=AMAC,
            scopes=["employee.read"],
            expires_at=timezone.now() - timedelta(minutes=1),
        )
    return duz


# --- 1-6: üretim doğrulaması ------------------------------------------------


def test_token_alt_kume_ile_uretilir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """1) Token yalnızca sahibinin izin alt kümesiyle üretilebilir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    veri = token_iste(api_client, tenant_a, user_a).json()["data"]
    assert veri["scopes"] == ["employee.read"]
    assert veri["token_kind"] == "interactive"
    assert veri["expires_at"]


def test_sahibinde_olmayan_kapsam_400(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """2) Sahibinde olmayan kapsam reddedilir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    yanit = token_iste(
        api_client, tenant_a, user_a, scopes=["leave.request.approve"], beklenen=400
    )
    assert "scopes" in yanit.json()


def test_bilinmeyen_kapsam_400(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """3) Katalogda olmayan kod reddedilir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, scopes=["yok.boyle.bir.izin"], beklenen=400)


def test_platform_kapsami_400(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """4) `platform.*` kiracı token'ında yasaktır."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, scopes=["platform.tenants.read"], beklenen=400)


def test_joker_kapsam_400(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """5) Joker (`*`) yasaktır."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, scopes=["*"], beklenen=400)


def test_bos_kapsam_400(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """6) Boş kapsamla token üretilemez."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, scopes=[], beklenen=400)


def test_kisa_amac_400(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """`purpose` trim sonrası en az 10 karakter olmalıdır."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, purpose="kisa", beklenen=400)


def test_ttl_tavani_asilirsa_400(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """Tavan üstü TTL sessizce kırpılmaz; açık hata döner."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_iste(api_client, tenant_a, user_a, ttl_hours=721, beklenen=400)


def test_service_token_uzun_ttl_ile_uretilir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """`service` türü 90 güne kadar izinlidir (tavan ayrı)."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    veri = token_iste(
        api_client, tenant_a, user_a, kind="service", ttl_hours=2160
    ).json()["data"]
    assert veri["token_kind"] == "service"


# --- 7-10: kesişim kuralı ---------------------------------------------------


def test_kapsam_disi_izin_kullanilamaz(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """7) Kullanıcının izni var ama token kapsamı dışında → 403."""
    izinli_kullanici(user_a, tenant_a, "employee.read", "leave.request.approve")
    token = token_uret(api_client, tenant_a, user_a, scopes=["employee.read"])

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(IZINLI_LEAVE).status_code == 403


def test_kapsam_icindeki_izin_kullanilir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """8) Kapsam içindeki izin kullanılır (kesişim boş değil)."""
    izinli_kullanici(user_a, tenant_a, "employee.read", "leave.request.approve")
    token = token_uret(api_client, tenant_a, user_a, scopes=["employee.read"])

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(IZINLI).status_code == 200


def test_izin_geri_alinirsa_token_da_kaybeder(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """9) İzin geri alınırsa token o izni kullanamaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(IZINLI).status_code == 200

    # Ölçüm: token'a dokunulmaz; yalnızca rol-izin bağı kaldırılır.
    with tenant_context(tenant_a.id):
        RolePermission.objects.filter(permission__code="employee.read").delete()
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(IZINLI).status_code == 403


def test_sonradan_verilen_izni_eski_token_kazanmaz(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """10) Token sonradan verilen bir izni kendiliğinden kazanmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a, scopes=["employee.read"])

    izinli_kullanici(user_a, tenant_a, "leave.request.approve")  # sonradan verilir

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(IZINLI_LEAVE).status_code == 403
    assert api_client.get(IZINLI).status_code == 200


# --- 11-15: iptal ve süre ---------------------------------------------------


def test_iptal_edilmis_token_401(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """11) İptal edilmiş token reddedilir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    with tenant_context(tenant_a.id):
        kayit = ApiToken.objects.get(user=user_a)
        assert kayit.revoke(TokenRevokeReason.ADMIN_REVOKE) is True

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 401


def test_suresi_gecmis_token_401(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """12) Süresi geçmiş token reddedilir."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {suresi_gecmis_token(user_a, tenant_a)}")
    assert api_client.get(KORUMALI).status_code == 401


def test_logout_fiziksel_silme_yapmaz(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """13) Logout satırı silmez."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.post(TOKEN_LOGOUT_URL, format="json").status_code == 204

    with tenant_context(tenant_a.id):
        assert ApiToken.objects.filter(user=user_a).count() == 1


def test_logout_iptal_izi_birakir(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """14) Logout `revoked_at` + `revoked_reason` yazar."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.post(TOKEN_LOGOUT_URL, format="json").status_code == 204

    with tenant_context(tenant_a.id):
        kayit = ApiToken.objects.get(user=user_a)
    assert kayit.revoked_at is not None
    assert kayit.revoked_reason == TokenRevokeReason.LOGOUT
    assert kayit.is_usable is False

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 401  # 15) logout sonrası 401


def test_logout_yalnizca_o_tokeni_iptal_eder(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """Logout diğer cihazların token'ını etkilemez."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    ilk = token_uret(api_client, tenant_a, user_a)
    ikinci = token_uret(api_client, tenant_a, user_a)

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {ilk}")
    assert api_client.post(TOKEN_LOGOUT_URL, format="json").status_code == 204

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {ikinci}")
    assert api_client.get(KORUMALI).status_code == 200


def test_revoke_all_tum_tokenlari_iptal_eder(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """`token/revoke-all` sahibinin tüm token'larını iptal eder."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    ilk = token_uret(api_client, tenant_a, user_a)
    ikinci = token_uret(api_client, tenant_a, user_a)

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {ilk}")
    yanit = api_client.post(TOKEN_REVOKE_ALL_URL, format="json")
    assert yanit.status_code == 200
    assert yanit.json()["data"]["revoked"] == 2

    for token in (ilk, ikinci):
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        assert api_client.get(KORUMALI).status_code == 401


def test_iptal_tek_yonludur(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """İptal edilen token yeniden etkinleştirilemez."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token_uret(api_client, tenant_a, user_a)
    with tenant_context(tenant_a.id):
        kayit = ApiToken.objects.get(user=user_a)
        assert kayit.revoke(TokenRevokeReason.LOGOUT) is True
        assert kayit.revoke(TokenRevokeReason.ADMIN_REVOKE) is False
        assert kayit.revoked_reason == TokenRevokeReason.LOGOUT


# --- 16-22: kiracı ve kullanıcı durumu (F11) --------------------------------


def test_askida_kiracida_yeni_oturum_girisi_reddedilir(api_client, tenant_a, user_a) -> None:
    """16) `suspended` kiracıda yeni giriş yapılamaz."""
    kiracı_guncelle(tenant_a, status="suspended")
    yanit = api_client.post(
        LOGIN_URL,
        {"tenant": tenant_a.slug, "email": user_a.email, "password": SIFRE},
        format="json",
    )
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"


def test_suresi_dolan_kiraciya_erisim_reddedilir(tenant_a) -> None:
    """Sona ermiş şirket kullanım süresi tenant erişimini kapatır."""
    from apps.tenancy.access import tenant_access_allowed

    kiracı_guncelle(tenant_a, valid_until=date.today())
    assert tenant_access_allowed(tenant_a.id) is False


def test_kullanim_baslangici_gelmemis_kiraciya_erisim_reddedilir(tenant_a) -> None:
    from apps.tenancy.access import tenant_access_allowed

    with tenant_context(tenant_a.id):
        Tenant.objects.filter(pk=tenant_a.pk).update(valid_from=date.today() + timedelta(days=1))
    assert tenant_access_allowed(tenant_a.id) is False


def test_iptal_edilmis_kiracida_yeni_token_girisi_reddedilir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """17) `canceled` kiracıda yeni giriş yapılamaz (token yolu)."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    kiracı_guncelle(tenant_a, status="canceled")
    yanit = token_iste(api_client, tenant_a, user_a, beklenen=403)
    assert yanit.json()["error"]["code"] == "tenant_unavailable"


def test_askida_kiracinin_mevcut_tokeni_reddedilir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """18) Askıya alınan kiracının mevcut token'ı çalışmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 200

    kiracı_guncelle(tenant_a, status="suspended")
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    yanit = api_client.get(KORUMALI)
    # İstek hattı, kiracı erişime kapalıyken bağlamı HİÇ KURMAZ (403).
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"


def test_iptal_edilmis_kiracinin_mevcut_tokeni_reddedilir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """19) İptal edilen kiracının mevcut token'ı çalışmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    kiracı_guncelle(tenant_a, status="canceled")

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    yanit = api_client.get(KORUMALI)
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"


def test_silinmis_kiracinin_tokeni_ve_oturumu_reddedilir(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """20) Soft-delete edilmiş kiracının token'ı VE oturumu çalışmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)

    # Önce geçerli bir oturum kurulur (kiracı hâlâ aktifken).
    assert (
        api_client.post(
            LOGIN_URL,
            {"tenant": tenant_a.slug, "email": user_a.email, "password": SIFRE},
            format="json",
        ).status_code
        == 200
    )

    kiracı_guncelle(tenant_a, deleted_at=timezone.now())

    # Mevcut oturum: istek hattı reddeder.
    yanit = api_client.get(BAGLAM)
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"

    # Mevcut token: istek hattı reddeder (bağlam kurulmaz).
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    yanit = api_client.get(KORUMALI)
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"


def test_kimlik_dogrulama_katmani_da_kiracıyı_reddeder(tenant_a, user_a, izinli_kullanici) -> None:
    """
    Savunma katmanı (7. madde): kiracı durumu yalnızca toplu iptale bağlı DEĞİL.

    İptal edilmemiş bir token bile kiracı askıya alındığında **token doğrulama
    katmanında** reddedilir; yani istek hattı bir gün atlanırsa erişim yine
    kesilir (`401`).
    """
    from rest_framework.exceptions import AuthenticationFailed

    from apps.accounts.authentication import ApiTokenAuthentication

    izinli_kullanici(user_a, tenant_a, "employee.read")
    duz_metin = "katman-testi-token-degeri"
    with tenant_context(tenant_a.id):
        ApiToken.objects.create(
            tenant_id=tenant_a.id,
            user=user_a,
            token_hash=ApiToken.hash_token(duz_metin),
            purpose=AMAC,
            scopes=["employee.read"],
            expires_at=timezone.now() + timedelta(days=1),
        )

    kiracı_guncelle(tenant_a, status="suspended")
    with pytest.raises(AuthenticationFailed):
        ApiTokenAuthentication().authenticate_credentials(duz_metin)


def test_aktif_ve_trial_kiracida_davranis_bozulmaz(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """21) `active` ve `trial` kiracılar çalışmaya devam eder."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 200

    kiracı_guncelle(tenant_a, status="trial")
    assert (
        api_client.post(
            LOGIN_URL,
            {"tenant": tenant_a.slug, "email": user_a.email, "password": SIFRE},
            format="json",
        ).status_code
        == 200
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 200


def test_pasif_kullanici_token_kullanamaz(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """22) Pasif (inactive) kullanıcı token'ı çalışmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)

    with tenant_context(tenant_a.id):
        user_a.status = "inactive"
        user_a.save(update_fields=["status", "updated_at"])

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 401


def test_silinmis_kullanici_token_kullanamaz(
    api_client, tenant_a, user_a, izinli_kullanici
) -> None:
    """22b) Soft-delete edilmiş kullanıcı token'ı çalışmaz."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    token = token_uret(api_client, tenant_a, user_a)

    with tenant_context(tenant_a.id):
        user_a.deleted_at = timezone.now()
        user_a.save(update_fields=["deleted_at", "updated_at"])

    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_client.get(KORUMALI).status_code == 401


# --- HTTP sözleşmesi --------------------------------------------------------


def test_saglik_uclari_200(api_client) -> None:  # noqa: ANN001, ARG001
    assert api_client.get(HEALTH).status_code == 200
    assert api_client.get(READY).status_code == 200


def test_token_girisi_201_doner(api_client, tenant_a, user_a, izinli_kullanici) -> None:
    """Belgelenen sözleşme: başarılı token üretimi `201 Created`."""
    izinli_kullanici(user_a, tenant_a, "employee.read")
    assert token_iste(api_client, tenant_a, user_a).status_code == 201
