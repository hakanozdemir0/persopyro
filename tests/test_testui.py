"""
Manuel test arayüzü (test UI) testleri — `/test-ui/`.

Kapsam: ilk tur senaryoları (giriş/çıkış, kendi talepleri, alt ağaç görünürlüğü,
sibling DENY, onay/red ve bakiye, askıdaki kiracı, cross-tenant, arayüz kapalıyken
404) **+** kullanıcı bildirimindeki 4 kritik eksik: (1) çıkışın oturumu gerçekten
kapatması, (2) yıllık izin bakiyesi görünürlüğü (çalışan/müdür/sibling), (3) unvan +
rol + yetki ekranı ve self-elevation yasakları, (4) birim taşıma seçenekleri ve
backend döngü guard'ı.

Önemli: testler arayüzü **gerçek HTTP yoluyla** dener (Django test istemcisi +
CSRF çerezi/başlığı), servisleri doğrudan çağırmaz. Böylece "arayüz güvenliği
atlamıyor" iddiası ölçülmüş olur.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.leave.models import LeaveBalance, LeavePolicy
from apps.leave.services import create_leave_request, decide_approval_step
from apps.organization.services import create_position
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.rbac.services import (
    RoleAssignmentError,
    assign_role_to_user,
    create_default_roles,
    update_role_permissions,
    user_has_permission,
)
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro, rol_ver

#: `tests/conftest.py` içindeki `user_a` parolası; `leave_helpers` kullanıcıları
#: ayrı bir parola kullanır (aşağıda `PAROLA_LEAVE`).
PAROLA_A = "Guclu-Parola-123"
PAROLA_B = "Guclu-Parola-456"
PAROLA_LEAVE = "Gizli-Parola-123"


@pytest.fixture
def client():  # noqa: ANN201
    """
    CSRF doğrulaması **açık** test istemcisi.

    Varsayılan istemci CSRF'i atlar; gerçek tarayıcı atlamaz. Bu yüzden bu modülde
    her istek gerçek CSRF sözleşmesinden geçer (bildirilen "çıkış çalışmıyor"
    hatasının kökü tam olarak buydu).
    """
    from django.test import Client  # noqa: PLC0415

    return Client(enforce_csrf_checks=True)


@pytest.fixture(autouse=True)
def _test_ui_ayarlari(settings) -> None:  # noqa: ANN001
    """Arayüz açık; CSRF çerezi test istemcisinde http üzerinden taşınabilsin."""
    settings.TEST_UI_ENABLED = True
    settings.CSRF_COOKIE_SECURE = False


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN001, ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiracı = request.getfixturevalue("tenant_a")
    with tenant_context(kiracı.id):
        yield


def _csrf(client) -> str:  # noqa: ANN001
    """Giriş sayfası CSRF çerezini üretir (tarayıcıdaki akışın aynısı)."""
    client.get("/test-ui/login/")
    return client.cookies["csrftoken"].value


def _giris(client, tenant, email: str, parola: str):  # noqa: ANN001, ANN202
    return client.post(
        "/api/v1/auth/login",
        data={"tenant": tenant.slug, "email": email, "password": parola},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )


def _post(client, yol: str, govde: dict | None = None):  # noqa: ANN001, ANN202
    return client.post(
        yol,
        data=govde or {},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )


def _talep_olustur(client, *, tur_id: str, employee_id: str, gun: int = 3, gerekce: str = "test"):
    baslangic = timezone.localdate() + timedelta(days=14)
    return _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": employee_id,
            "leave_type_id": tur_id,
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=gun - 1)).isoformat(),
            "reason": gerekce,
            "submit": True,
        },
    )


# --- 1–3) giriş ---------------------------------------------------------------


def test_ui_login_sayfasi_acilir(client, db) -> None:  # noqa: ANN001, ARG001
    """(1) Giriş sayfası açılır ve test arayüzü olduğu açıkça yazar."""
    yanit = client.get("/test-ui/login/")
    assert yanit.status_code == 200
    govde = yanit.content.decode()
    assert "Manuel Test Arayüzü" in govde
    assert "yalnızca test" in govde


def test_ui_gecerli_kullanici_giris_yapar(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(2) Geçerli kullanıcı mevcut oturum ucuyla giriş yapar; panel verisi gelir."""
    yanit = _giris(client, tenant_a, user_a.email, PAROLA_A)
    assert yanit.status_code == 200

    me = client.get("/test-ui/api/me")
    assert me.status_code == 200
    veri = me.json()["data"]
    assert veri["user"]["email"] == user_a.email
    assert veri["tenant"]["name"] == tenant_a.name
    assert "permissions" in veri and isinstance(veri["permissions"], list)
    assert "scope" in veri

    panel = client.get("/test-ui/api/dashboard")
    assert panel.status_code == 200


def test_ui_hatali_giris_reddedilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(3) Hatalı parola/kiracı kombinasyonu giriş yaptırmaz."""
    yanit = _giris(client, tenant_a, user_a.email, "yanlis-parola")
    assert yanit.status_code in (400, 401, 403)
    assert client.get("/test-ui/api/me").status_code in (401, 403)


def test_ui_oturum_yoksa_panel_login_e_yonlendirir(client, db) -> None:  # noqa: ANN001, ARG001
    """Oturum yokken panel 302 ile girişe gider; API 401/403 döner."""
    yanit = client.get("/test-ui/")
    assert yanit.status_code == 302
    assert "/test-ui/login/" in yanit["Location"]
    assert client.get("/test-ui/api/me").status_code in (401, 403)


# --- 4–6) görünürlük ve kapsam ------------------------------------------------


def test_ui_calisan_yalniz_kendi_taleplerini_gorur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(4) Çalışan (SELF) yalnızca kendi izin taleplerini görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    kendi = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gerekce="kendi talebi"
    )
    assert kendi.status_code == 201
    baska = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["kardes"].pk), gerekce="kardes talebi"
    )
    assert baska.status_code == 201
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    yanit = client.get("/test-ui/api/leave-requests?scope=mine")
    assert yanit.status_code == 200
    kayitlar = yanit.json()["data"]
    assert [t["employee_no"] for t in kayitlar] == [lot["personel"].employee_no]
    # Bekleyen havuzunda da yalnızca kendi (onay yetkisi yok) görünür.
    bekleyen = client.get("/test-ui/api/leave-requests?scope=pending")
    assert bekleyen.status_code == 200
    assert all(t["employee_no"] == lot["personel"].employee_no for t in bekleyen.json()["data"])


def test_ui_dept_manager_alt_agac_taleplerini_gorur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(5) Finans müdürü (UNIT kapsamı) Muhasebe çalışanının talebini görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    assert (
        _talep_olustur(
            client, tur_id=yillik, employee_id=str(lot["personel"].pk), gerekce="subtree"
        ).status_code
        == 201
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    yanit = client.get("/test-ui/api/leave-requests?scope=pending")
    assert yanit.status_code == 200
    no_lar = [t["employee_no"] for t in yanit.json()["data"]]
    assert lot["personel"].employee_no in no_lar
    assert lot["kardes"].employee_no not in no_lar


def test_ui_sibling_departman_talebi_gorunmez_ve_onaylanamaz(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(6) Kapsam dışı (İK) çalışanın talebi müdüre görünmez; onay denemesi 403."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    ik_talebi = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["kardes"].pk), gerekce="ik talebi"
    )
    assert ik_talebi.status_code == 201
    ik_id = ik_talebi.json()["data"]["id"]
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    havuz = client.get("/test-ui/api/leave-requests?scope=pending").json()["data"]
    gorunen = [t["id"] for t in havuz]
    assert ik_id not in gorunen

    karar = _post(
        client,
        f"/test-ui/api/leave-requests/{ik_id}/decision",
        {"decision": "approved", "reason": ""},
    )
    assert karar.status_code == 403
    assert "yetkiniz veya organizasyon kapsamınız yok" in karar.json()["error"]["message"]


# --- 7–10) onay/red ve bakiye -------------------------------------------------


def test_ui_manager_onay_verir_bakiye_guncellenir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(7/9) Müdür adımı onaylar; HR/tenant yetkilisi nihai onayı verir → bakiye düşer."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    talep = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gun=3, gerekce="onay akışı"
    )
    assert talep.status_code == 201
    talep_id = talep.json()["data"]["id"]
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # Personel bakiyesi (öncesi): reserve edilmiş, kullanım yok.
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    panel = client.get("/test-ui/api/dashboard").json()["data"]
    bakiye = [b for b in panel["balances"] if b["leave_type_code"] == "annual_leave"][0]
    assert bakiye["reserved_days"] == "3.0"
    assert bakiye["used_days"] == "0.0"
    assert bakiye["available_days"] == "11.0"
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # 1. adım: birincil yönetici.
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    adim1 = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert adim1.status_code == 200
    assert adim1.json()["data"]["status"] == "pending"
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # 2. adım (nihai): kiracı geneli yetkili rol.
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    son = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert son.status_code == 200
    assert son.json()["data"]["status"] == "approved"

    # Bakiye: rezervasyon çözüldü, kullanım yazıldı (tek kez).
    me = client.get("/test-ui/api/me").json()["data"]
    assert me["user"]["email"] == lot["admin"].email
    with tenant_context(tenant_a.id):
        kayit = LeaveBalance.objects.get(
            employee=lot["personel"], period_year=timezone.localdate().year
        )
        assert kayit.reserved_days == Decimal("0.0")
        assert kayit.used_days == Decimal("3.0")
        assert kayit.available_days == Decimal("11.0")

    # İkinci kez onaylanamaz (409: durum çakışması).
    tekrar = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert tekrar.status_code == 409


def test_ui_yetkisiz_calisan_onay_veremez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(8) Onay izni olmayan çalışan onay denediğinde 403 alır (backend kararı)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    talep_id = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gerekce="yetki testi"
    ).json()["data"]["id"]
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["kardes_user"].email, PAROLA_LEAVE)
    yanit = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert yanit.status_code == 403


def test_ui_red_bakiyeyi_tuketmez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(10) Red: rezervasyon çözülür, kullanım yazılmaz."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    talep_id = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gun=2, gerekce="red testi"
    ).json()["data"]["id"]
    yanit = _post(
        client,
        f"/test-ui/api/leave-requests/{talep_id}/decision",
        {"decision": "rejected", "reason": "Ekip kapasitesi yetersiz"},
    )
    assert yanit.status_code == 200
    assert yanit.json()["data"]["status"] == "rejected"

    with tenant_context(tenant_a.id):
        kayit = LeaveBalance.objects.get(
            employee=lot["personel"], period_year=timezone.localdate().year
        )
        assert kayit.used_days == Decimal("0.0")
        assert kayit.reserved_days == Decimal("0.0")
        assert kayit.available_days == Decimal("14.0")


def test_ui_calisan_kendi_talebini_iptal_eder(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan kendi talebini iptal edebilir; iptal gerekçesi zorunludur."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    talep_id = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gerekce="iptal testi"
    ).json()["data"]["id"]

    # Gerekçesiz iptal reddedilir.
    assert _post(client, f"/test-ui/api/leave-requests/{talep_id}/cancel", {}).status_code == 400
    iptal = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/cancel", {"reason": "Planım değişti"}
    )
    assert iptal.status_code == 200
    assert iptal.json()["data"]["status"] == "canceled"


# --- 11–14) oturum, admin yolu, askıdaki kiracı, cross-tenant -----------------


def test_ui_logout_calisir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(11) Çıkış oturumu kapatır; panel erişimi kesilir."""
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    assert client.get("/test-ui/api/me").status_code == 200
    cikis = client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    assert cikis.status_code == 204
    assert client.get("/test-ui/api/me").status_code in (401, 403)


def test_ui_admin_yolu_hala_404(client, db) -> None:  # noqa: ANN001, ARG001
    """(12) Django Admin yolu kapalı kalır (OD19) ve test arayüzü bunu değiştirmez."""
    assert client.get("/admin/").status_code == 404
    assert client.get("/admin/login/").status_code == 404


def test_ui_askida_kiracida_giris_yok(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(13) Askıya alınmış kiracıda giriş 403 döner; panel verisi açılmaz."""
    with tenant_context(tenant_a.id):
        tenant_a.status = "suspended"
        tenant_a.save(update_fields=["status", "updated_at"])
    yanit = _giris(client, tenant_a, user_a.email, PAROLA_A)
    assert yanit.status_code == 403
    assert yanit.json()["error"]["code"] == "tenant_unavailable"
    assert client.get("/test-ui/api/me").status_code in (401, 403)


def test_ui_cross_tenant_veri_gorunmez(client, tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """(14) Başka kiracıya geçen oturum, ilk kiracının verisini göremez."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yillik = str(lot["turler"]["annual_leave"].pk)
    talep_id = _talep_olustur(
        client, tur_id=yillik, employee_id=str(lot["personel"].pk), gerekce="cross tenant"
    ).json()["data"]["id"]
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # B kiracısına giriş: A'nın çalışanları/talepleri görünmez.
    with tenant_context(tenant_b.id):
        rol_ver(tenant_b, user_b, "tenant_admin")
    assert _giris(client, tenant_b, user_b.email, PAROLA_B).status_code == 200
    assert client.get("/test-ui/api/employees").json()["data"] == []
    assert client.get("/test-ui/api/leave-requests?scope=pending").json()["data"] == []
    assert client.get("/test-ui/api/departments").json()["data"] == []
    # A'nın talebine karar denemesi: RLS nesneyi göstermez → 404.
    assert (
        _post(
            client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
        ).status_code
        == 404
    )


# --- ek güvenlik/davranış testleri -------------------------------------------


def test_ui_kapaliyken_404_doner(client, settings, tenant_a, user_a) -> None:  # noqa: ANN001
    """Arayüz kapalıyken (üretim varsayılanı) hiçbir `/test-ui/*` yolu çalışmaz."""
    settings.TEST_UI_ENABLED = False
    assert client.get("/test-ui/login/").status_code == 404
    assert client.get("/test-ui/").status_code == 404
    assert client.get("/test-ui/api/me").status_code == 404
    assert client.get("/test-ui/api/departments").status_code == 404


def test_ui_departman_olusturma_yetki_ile(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Departman oluşturma: yetkili kullanıcı 201, yetkisiz 403 (servis kararı)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yeni = _post(
        client, "/test-ui/api/departments", {"code": "UI-1", "name": "Arayüz Birimi"}
    )
    assert yeni.status_code == 201
    kodlar = [d["code"] for d in client.get("/test-ui/api/departments").json()["data"]]
    assert "UI-1" in kodlar
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert (
        _post(client, "/test-ui/api/departments", {"code": "UI-2", "name": "Olmamalı"}).status_code
        == 403
    )


def test_ui_calisan_listesi_kapsamla_sinirli(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan listesi kapsamla sınırlıdır: müdür İK çalışanını görmez, kendini görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    no_lar = {c["employee_no"] for c in client.get("/test-ui/api/employees").json()["data"]}
    assert lot["yonetici"].employee_no in no_lar  # kendi kaydı (Finans)
    assert lot["personel"].employee_no in no_lar  # alt ağaç (Muhasebe)
    assert lot["kardes"].employee_no not in no_lar  # kapsam dışı (İK)


def test_ui_kisisel_veri_uclardan_donmez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Arayüz uçları K2/K3 kişisel veri alanlarını **hiç** döndürmez."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    govde = client.get("/test-ui/api/employees").content.decode()
    for alan in (
        "first_name",
        "last_name",
        "national_id",
        "iban",
        "personal_email",
        "personal_phone",
        "health_notes",
        "disability_status",
        "biometric_ref",
    ):
        assert alan not in govde

# =============================================================================
# 4 kritik eksik düzeltmesi — regresyon testleri (madde 1–4)
# =============================================================================


# --- madde 1: çıkış gerçekten oturumu kapatır --------------------------------


def test_ui_logout_oturumu_sunucuda_kapatir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(1/2/3) Çıkış sonrası: oturum kapalı, korunan yol girişe yönlenir, yenileme açmaz."""
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    token = _csrf(client)
    assert client.get("/test-ui/api/me").status_code == 200

    cikis = client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=token)
    assert cikis.status_code == 204

    # Sunucu tarafı: oturum gerçekten kapatıldı (yalnızca yerel durum temizlenmedi).
    me = client.get("/test-ui/api/me")
    assert me.status_code == 403
    assert me.json()["error"]["code"] == "not_authenticated"
    # Korunan sayfa artık girişe yönlendirir → geri tuşu/yenileme panel açmaz.
    sayfa = client.get("/test-ui/")
    assert sayfa.status_code == 302
    assert "/test-ui/login/" in sayfa["Location"]
    assert client.get("/test-ui/login/").status_code == 200
    # Oturum çerezi sunucu tarafından boşaltılır (tarayıcı siler: Max-Age=0).
    cerez = client.cookies.get("sessionid")
    assert cerez is None or cerez.value == ""


def test_ui_logout_csrf_basligi_olmadan_403_verir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(regresyon kökü) Başlıksız çıkış isteği 403 alır ve oturum **kapanmaz**."""
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    yanit = client.post("/api/v1/auth/logout")  # CSRF başlığı YOK
    assert yanit.status_code == 403
    assert client.get("/test-ui/api/me").status_code == 200  # oturum hâlâ açık


def test_ui_frontend_csrfyi_yazma_isteklerinde_gonderir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(regresyon) Arayüz CSRF başlığını gövdesiz yazma isteklerinde de gönderir."""
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    govde = client.get("/test-ui/").content.decode()
    assert 'const guvenli = method === "GET" || method === "HEAD";' in govde
    assert 'if (!guvenli) basliklar["X-CSRFToken"] = this.csrf();' in govde
    # Çıkış akışı: gerçek yanıt beklenir, başarısızlıkta kullanıcı bilgilendirilir.
    assert 'PERSOPYRO_UI.cagir("/api/v1/auth/logout", { method: "POST" })' in govde
    assert 'window.location.replace("/test-ui/login/")' in govde


def test_ui_kapali_pencereler_gorunmez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """
    (regresyon) Kapatılmış pencere/gizli alanlar CSS nedeniyle görünür kalmamalı.

    Bildirilen hata: `.modal-backdrop { display: flex }` kuralı `hidden` özniteliğini
    ezdiği için giriş sonrası panelde "Onayla / Vazgeç" penceresi boşta duruyordu.
    """
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    govde = client.get("/test-ui/").content.decode()
    assert "[hidden] { display: none !important; }" in govde
    # Pencere düğmeleri başlangıçta işlevsiz görünmemeli: başlık boş gelir.
    assert '<h2 id="modal-title"></h2>' in govde
    # Escape ile kapatma bağlı.
    assert 'olay.key !== "Escape"' in govde


# --- madde 2: yıllık izin bakiyesi görünürlüğü -------------------------------


def test_ui_calisan_kendi_yillik_bakiyesini_gorur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(4) Çalışan kendi panelinde hak/kullanılan/bekleyen/kalan değerlerini görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)

    panel = client.get("/test-ui/api/dashboard").json()["data"]
    yillik = panel["annual"]
    assert yillik["hakedis_var"] is True
    assert yillik["leave_type_code"] == "annual_leave"
    assert yillik["entitled_days"] == "14.0"
    assert yillik["used_days"] == "0.0"
    assert yillik["reserved_days"] == "0.0"
    assert yillik["available_days"] == "14.0"
    # Çalışan özetinde de aynı değerler (tek kaynak: backend).
    assert panel["employee"]["annual"]["available_days"] == "14.0"


def test_ui_manager_kapsam_icindeki_calisanin_bakiyesini_gorur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(5) Müdür, alt ağacındaki çalışanın yıllık izin özetini listede görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)

    veri = {c["employee_no"]: c for c in client.get("/test-ui/api/employees").json()["data"]}
    assert set(veri) == {"FIN-M1", "MUH-1"}
    muhasebe = veri["MUH-1"]
    assert muhasebe["annual"]["hakedis_var"] is True
    assert muhasebe["annual"]["entitled_days"] == "14.0"
    assert muhasebe["annual"]["available_days"] == "14.0"
    assert muhasebe["department"] == "Muhasebe"
    # Yetki bölümü kapalı olsa bile yanıt şekli kararlıdır.
    detay = client.get(f"/test-ui/api/employees/{muhasebe['id']}").json()["data"]
    assert detay["is_self"] is False and detay["yetki_gorunur"] is False


def test_ui_sibling_calisan_bakiyesi_gorunmez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(6) Kapsam dışı (sibling) çalışanın bakiyesi ve detayı görünmez (404)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    kardes_id = str(lot["kardes"].pk)
    assert client.get(f"/test-ui/api/employees/{kardes_id}/balances").status_code == 404
    assert client.get(f"/test-ui/api/employees/{kardes_id}").status_code == 404


def test_ui_hakedis_yoksa_anlasilir_durum_doner(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Bakiyesi olmayan çalışan için hata değil, açık durum mesajı döner."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yeni = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "YENI-1",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
        },
    )
    assert yeni.status_code == 201
    detay = client.get(f"/test-ui/api/employees/{yeni.json()['data']['id']}").json()["data"]
    assert detay["annual"]["hakedis_var"] is False
    assert detay["annual"]["mesaj"] == "Henüz yıllık izin hakedişi oluşturulmamış."
    assert detay["annual"]["available_days"] is None


def test_ui_onay_sonrasi_bakiye_ui_degeri_guncellenir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(7) Onay sonrası panel/liste değerleri backend'den güncel gelir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    baslangic = timezone.localdate() + timedelta(days=14)
    talep = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(lot["turler"]["annual_leave"].pk),
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=1)).isoformat(),
            "reason": "UI bakiye güncelleme",
            "submit": True,
        },
    )
    assert talep.status_code == 201
    talep_id = talep.json()["data"]["id"]

    once = client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]["annual"]
    assert (once["reserved_days"], once["used_days"], once["available_days"]) == (
        "2.0",
        "0.0",
        "12.0",
    )

    # 1. adım birincil yöneticide, nihai adım kiracı geneli yetkilide (M14 onay sırası).
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    assert (
        _post(client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"})
        .status_code
        == 200
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    nihai = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert nihai.status_code == 200
    assert nihai.json()["data"]["status"] == "approved"

    sonra = client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]["annual"]
    assert (sonra["reserved_days"], sonra["used_days"], sonra["available_days"]) == (
        "0.0",
        "2.0",
        "12.0",
    )


# --- madde 3: unvan / rol / yetki görünürlüğü ve yönetimi -------------------


def test_ui_calisan_unvani_ve_rolu_gorunur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(8/9) Liste ve detayda unvan (position), birim ve Türkçe rol adı görünür."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        pozisyon = create_position(
            actor=lot["admin"], tenant=tenant_a, code="MUH-UZ", name="Muhasebe Uzmanı"
        )
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yeni = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "MUH-2",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "position_id": str(pozisyon.pk),
            "hire_date": timezone.localdate().isoformat(),
        },
    )
    assert yeni.status_code == 201
    assert yeni.json()["data"]["position"] == "Muhasebe Uzmanı"

    liste = {c["employee_no"]: c for c in client.get("/test-ui/api/employees").json()["data"]}
    assert liste["MUH-2"]["position"] == "Muhasebe Uzmanı"
    # Kullanıcısı olmayan çalışanda rol listesi boş; yöneticide Türkçe rol adı var.
    assert liste["MUH-2"]["roles"] == []
    assert [r["name"] for r in liste["FIN-M1"]["roles"]] == ["Departman Yöneticisi"]
    assert [r["code"] for r in liste["FIN-M1"]["roles"]] == ["dept_manager"]
    assert liste["FIN-M1"]["roles"][0]["scope_text"] == "Finans + alt birimleri"


def test_ui_yetkili_kullanici_rol_atayabilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(10) Şirket yöneticisi rol atar; atama listede ve etkin izinlerde görünür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)

    roller = client.get("/test-ui/api/roles").json()
    assert roller["meta"]["can_manage"] is True
    ik_rolu = next(r for r in roller["data"] if r["code"] == "hr_manager")
    assert ik_rolu["atanabilir"] is True

    yanit = _post(
        client,
        f"/test-ui/api/employees/{lot['personel'].pk}/roles",
        {"role_id": ik_rolu["id"], "reason": "İK sorumluluğu"},
    )
    assert yanit.status_code == 201
    assert yanit.json()["data"]["name"] == "İK Yöneticisi"

    detay = client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]
    assert "hr_manager" in [r["code"] for r in detay["roles"]]
    # `document.confidential.read` yalnız İK/şirket yöneticisindedir (ayırt edici kod).
    assert "document.confidential.read" in detay["effective_permissions"]
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(
            action="role.assign", record_id=yanit.json()["data"]["id"]
        ).exists()


def test_ui_unit_rolunde_birim_kapsami_zorunlu(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(11) UNIT rolünde kapsam zorunludur; seçilen birim kapsam metnine yansır."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    tum_roller = client.get("/test-ui/api/roles").json()["data"]
    dm = next(r for r in tum_roller if r["code"] == "dept_manager")
    assert dm["kapsam_gerekli"] is True

    eksik = _post(
        client, f"/test-ui/api/employees/{lot['kardes'].pk}/roles", {"role_id": dm["id"]}
    )
    assert eksik.status_code == 400
    assert "kapsam" in eksik.json()["error"]["message"].lower()

    tam = _post(
        client,
        f"/test-ui/api/employees/{lot['kardes'].pk}/roles",
        {"role_id": dm["id"], "scope_org_unit_id": str(lot["birimler"]["ik"].pk)},
    )
    assert tam.status_code == 201
    assert tam.json()["data"]["scope_text"] == "İnsan Kaynakları + alt birimleri"

    # Kapsamsız (TENANT) role birim kapsamı verilemez → 400.
    admin_rolu = next(r for r in tum_roller if r["code"] == "tenant_officer")
    fazla = _post(
        client,
        f"/test-ui/api/employees/{lot['kardes'].pk}/roles",
        {"role_id": admin_rolu["id"], "scope_org_unit_id": str(lot["birimler"]["ik"].pk)},
    )
    assert fazla.status_code == 400


def test_ui_self_elevation_reddedilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(12) Kullanıcı kendi rolünü yükseltemez (hem servis hem HTTP katmanı)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    kendi = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "ADM-1",
            "department_id": str(lot["birimler"]["gm"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "user_email": lot["admin"].email,
        },
    )
    assert kendi.status_code == 201
    calisan_id = kendi.json()["data"]["id"]
    detay = client.get(f"/test-ui/api/employees/{calisan_id}").json()["data"]
    assert detay["is_self"] is True

    rol = next(r for r in detay["assignable_roles"] if r["code"] == "hr_manager")
    yanit = _post(client, f"/test-ui/api/employees/{calisan_id}/roles", {"role_id": rol["id"]})
    assert yanit.status_code == 403
    assert "self-elevation" in yanit.json()["error"]["detail"]

    # Servis katmanı (arayüzden bağımsız): aynı kullanıcı hedef olamaz.
    with tenant_context(tenant_a.id), pytest.raises(RoleAssignmentError):
        rol_kaydi = Role.objects.get(tenant=tenant_a, code="hr_manager")
        assign_role_to_user(actor=lot["admin"], target_user=lot["admin"], role=rol_kaydi)


def test_ui_cross_tenant_rol_atamasi_reddedilir(client, tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """(13) Cross-tenant rol ataması reddedilir; yabancı kiracı verisi görünmez."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_b.id):
        roller_b = create_default_roles(tenant_b)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert client.get(f"/test-ui/api/employees/{uuid4()}/balances").status_code == 404
    with tenant_context(tenant_a.id), pytest.raises(RoleAssignmentError):
        assign_role_to_user(
            actor=lot["admin"], target_user=user_b, role=roller_b["hr_manager"]
        )


def test_ui_rol_kaldirma_ve_geri_alma_calisir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(14) Rol kaldırma (OD21 soft delete) ve açık geri alma çalışır; denetlenir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    roller = client.get("/test-ui/api/roles").json()["data"]
    ik_rolu = next(r for r in roller if r["code"] == "hr_manager")
    atama = _post(
        client,
        f"/test-ui/api/employees/{lot['personel'].pk}/roles",
        {"role_id": ik_rolu["id"], "reason": "geçici görev"},
    )
    assert atama.status_code == 201
    atama_id = atama.json()["data"]["id"]

    kaldir = _post(
        client, f"/test-ui/api/role-assignments/{atama_id}/revoke", {"reason": "görev bitti"}
    )
    assert kaldir.status_code == 200
    detay = client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]
    assert "hr_manager" not in [r["code"] for r in detay["roles"]]
    assert [r["code"] for r in detay["revoked_assignments"]] == ["hr_manager"]
    assert "document.confidential.read" not in detay["effective_permissions"]

    # OD21: kaldırılmış atama sessizce canlandırılmaz.
    tekrar = _post(
        client, f"/test-ui/api/employees/{lot['personel'].pk}/roles", {"role_id": ik_rolu["id"]}
    )
    assert tekrar.status_code == 400
    assert "geri alma" in tekrar.json()["error"]["message"]

    geri = _post(client, f"/test-ui/api/role-assignments/{atama_id}/restore", {"reason": "iptal"})
    assert geri.status_code == 200
    assert "hr_manager" in [
        r["code"]
        for r in client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]["roles"]
    ]
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="role.revoke", record_id=atama_id).exists()
        assert AuditLog.objects.filter(action="role.restore", record_id=atama_id).exists()


def test_ui_yetkisiz_roller_rol_yonetemez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetkisiz roller (dept_manager, tenant_officer) rol dağıtamaz; detay yetki bölümü kapalı."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        rol_ver(tenant_a, lot["kardes_user"], "tenant_officer")

    # dept_manager: `role.read` yok → rol listesi dahi kapalı.
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/roles").status_code == 403
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['personel'].pk}/roles",
            {"role_id": str(Role.objects.get(tenant=tenant_a, code="employee").pk)},
        ).status_code
        == 403
    )
    assert (
        client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]["yetki_gorunur"]
        is False
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # tenant_officer: `role.read` var → listeyi görür ama **düzenleyemez**.
    _giris(client, tenant_a, lot["kardes_user"].email, PAROLA_LEAVE)
    roller = client.get("/test-ui/api/roles").json()
    assert roller["meta"]["can_manage"] is False
    assert all(r["atanabilir"] is False for r in roller["data"])
    birinci = roller["data"][0]["id"]
    detay = client.get(f"/test-ui/api/roles/{birinci}/permissions").json()["data"]
    assert detay["can_edit"] is False
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{birinci}/permissions",
            {"permission_codes": ["employee.read"]},
        ).status_code
        == 403
    )
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['personel'].pk}/roles",
            {"role_id": birinci},
        ).status_code
        == 403
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # employee (yalnız `employee` rolü): ne rol listesi ne yetki ekranı.
    with tenant_context(tenant_a.id):
        UserRole.objects.filter(user=lot["kardes_user"], role__code="tenant_officer").update(
            deleted_at=timezone.now()
        )
    _giris(client, tenant_a, lot["kardes_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/roles").status_code == 403
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{birinci}/permissions",
            {"permission_codes": ["employee.read"]},
        ).status_code
        == 403
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))


# --- madde 4: birim taşıma UX -------------------------------------------------


def test_ui_tasima_hedefleri_kendini_ve_alt_agaci_haric_tutar(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(15/16/17) Taşıma seçenekleri kendini ve alt ağacı içermez (sunucu üretir)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)

    finans = str(lot["birimler"]["finans"].pk)
    veri = client.get(f"/test-ui/api/departments?moves_for={finans}").json()
    assert veri["meta"]["moves_for"] == finans
    adlar = {h["name"] for h in veri["meta"]["move_options"]}
    assert "Finans" not in adlar  # kendisi
    assert "Muhasebe" not in adlar  # alt birimi
    assert {"Genel Müdürlük", "İnsan Kaynakları"} <= adlar

    muhasebe = str(lot["birimler"]["muhasebe"].pk)
    adlar2 = {
        h["name"]
        for h in client.get(f"/test-ui/api/departments?moves_for={muhasebe}").json()["meta"][
            "move_options"
        ]
    }
    assert "Muhasebe" not in adlar2
    assert {"Genel Müdürlük", "Finans", "İnsan Kaynakları"} <= adlar2


def test_ui_tasima_dongu_ve_self_parent_backend_guardlari(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(18) UI engellese bile döngü/kendi-üstü denemesi backend'de reddedilir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    finans = str(lot["birimler"]["finans"].pk)

    dongu = _post(
        client,
        f"/test-ui/api/departments/{finans}/move",
        {"parent_id": str(lot["birimler"]["muhasebe"].pk), "reason": "döngü denemesi"},
    )
    assert dongu.status_code in (400, 409)

    kendi = _post(
        client, f"/test-ui/api/departments/{finans}/move", {"parent_id": finans}
    )
    assert kendi.status_code == 400
    assert "kendi üst birimi" in kendi.json()["error"]["message"]

    # Ağaç bozulmadı.
    agac = {
        d["name"]: d["parent_id"]
        for d in client.get("/test-ui/api/departments").json()["data"]
    }
    assert agac["Muhasebe"] == finans


def test_ui_tasima_sonrasi_agac_ve_kapsam_guncellenir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(19) Taşıma sonrası ağaç ve kapsam yeniden hesaplanır (müdür erişimini kaybeder)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yanit = _post(
        client,
        f"/test-ui/api/departments/{lot['birimler']['muhasebe'].pk}/move",
        {"parent_id": str(lot["birimler"]["ik"].pk), "reason": "yeniden yapılanma"},
    )
    assert yanit.status_code == 200

    agac = {
        d["name"]: d["parent_id"]
        for d in client.get("/test-ui/api/departments").json()["data"]
    }
    assert agac["Muhasebe"] == str(lot["birimler"]["ik"].pk)
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    no_lar = {c["employee_no"] for c in client.get("/test-ui/api/employees").json()["data"]}
    assert no_lar == {"FIN-M1"}  # MUH-1 artık İK alt ağacında, kapsam dışı


def test_ui_testleri_admin_yolu_ve_izolasyonu_bozmaz(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(20/21) `/admin/` kapalı kalır; yeni uçlar kiracı izolasyonunu gevşetmez."""
    lot = kadro(tenant_a, user_a)
    assert client.get("/admin/").status_code == 404
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/roles").status_code == 200
    assert client.get(f"/test-ui/api/employees/{uuid4()}").status_code == 404
    assert client.get("/api/v1/auth/token/revoke-all").status_code in (401, 403, 405)

# =============================================================================
# Kullanıcı bildirimi (3 eksik): kullanıcı seçimi/unvan, izin türü yönetimi,
# panelde karara bağlanan izinler
# =============================================================================


def test_ui_calisan_kullanicisiz_ve_kullanicili_olusturulur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan kaydı kullanıcı seçmeden de açılır; seçilirse hesap bağlanır."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    musait = client.get("/test-ui/api/lookups").json()["data"]["users"]
    kullanici = next(u for u in musait if u["email"] == lot["kardes_user"].email)
    assert kullanici["bagli_employee_no"] == "IK-1"  # bilgi amaçlı görünür

    hesapsiz = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "YENI-9",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
        },
    )
    assert hesapsiz.status_code == 201
    assert hesapsiz.json()["data"]["user_email"] is None

    bagli = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "YENI-10",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "user_id": kullanici["id"],
        },
    )
    assert bagli.status_code == 201
    assert bagli.json()["data"]["user_email"] == lot["kardes_user"].email
    assert [r["code"] for r in bagli.json()["data"]["roles"]] == ["employee"]


def test_ui_calisana_yabanci_veya_gecersiz_kullanici_reddedilir(client, tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001, E501
    """Bildirilen hata: e-posta serbest metindi. Artık kullanıcı listesi + anlaşılır 400."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    govde = {
        "employee_no": "YENI-11",
        "department_id": str(lot["birimler"]["muhasebe"].pk),
        "hire_date": timezone.localdate().isoformat(),
    }
    yabanci = _post(client, "/test-ui/api/employees", {**govde, "user_id": str(user_b.pk)})
    assert yabanci.status_code == 400
    assert "kullanıcı" in yabanci.json()["error"]["message"].lower()

    bilinmeyen = _post(
        client, "/test-ui/api/employees", {**govde, "user_email": "olmayan@ornek.test"}
    )
    assert bilinmeyen.status_code == 400
    assert "listeden seçin" in bilinmeyen.json()["error"]["message"].lower()


def test_ui_unvan_ekleme_ve_calisanda_gorunmesi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetkili kullanıcı yeni unvan tanımlar; form seçeneklerine ve kayda yansır."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/lookups").json()["meta"]["can_manage_positions"] is True

    yeni = _post(
        client, "/test-ui/api/positions", {"code": "MUH-UZ", "name": "Muhasebe Uzmanı"}
    )
    assert yeni.status_code == 201
    unvan_id = yeni.json()["data"]["id"]

    bakiyeler = client.get("/test-ui/api/lookups").json()["data"]["positions"]
    assert "Muhasebe Uzmanı" in [p["name"] for p in bakiyeler]

    calisan = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "MUH-9",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "position_id": unvan_id,
            "hire_date": timezone.localdate().isoformat(),
        },
    )
    assert calisan.status_code == 201
    assert calisan.json()["data"]["position"] == "Muhasebe Uzmanı"


def test_ui_unvan_ekleme_yetkisiz_reddedilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """`org.position.manage` olmayan kullanıcı unvan tanımlayamaz."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    yanit = _post(client, "/test-ui/api/positions", {"code": "X", "name": "Olmamalı"})
    assert yanit.status_code == 403


def test_ui_izin_turu_eklenir_ve_talep_formunda_gorunur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Şirket yöneticisi kiracıya özel izin türü ekler; talep açılabilir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)

    yeni = _post(
        client,
        "/test-ui/api/leave-types",
        {
            "code": "dogum_izni",
            "name": "Doğum İzni",
            "category": "maternity",
            "is_paid": True,
            "uses_balance": False,
            "requires_approval": True,
            "requires_document": False,
        },
    )
    assert yeni.status_code == 201
    assert yeni.json()["data"]["aktif"] is True
    assert yeni.json()["data"]["category_label"] == "Doğum"

    liste = client.get("/test-ui/api/leave-types").json()
    kodlar = [t["code"] for t in liste["data"]]
    assert "dogum_izni" in kodlar and "annual_leave" in kodlar
    assert liste["meta"]["can_manage"] is True

    aktif_turler = client.get("/test-ui/api/lookups").json()["data"]["leave_types"]
    assert "dogum_izni" in [t["code"] for t in aktif_turler]

    baslangic = timezone.localdate() + timedelta(days=20)
    talep = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": yeni.json()["data"]["id"],
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=4)).isoformat(),
            "reason": "Yeni türle talep",
            "submit": True,
        },
    )
    assert talep.status_code == 201
    assert Decimal(talep.json()["data"]["days_requested"]) == Decimal("5.0")
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="leave.type.create").exists()


def test_ui_izin_turu_sistem_kodu_ve_yetki_reddi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Sistem kodları rezervedir; yetkisiz kullanıcı tür ekleyemez."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    sistem = _post(
        client,
        "/test-ui/api/leave-types",
        {"code": "annual_leave", "name": "Kopya", "category": "annual"},
    )
    assert sistem.status_code == 400
    assert "rezerve" in sistem.json()["error"]["message"]

    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/leave-types").json()["meta"]["can_manage"] is False
    assert (
        _post(
            client, "/test-ui/api/leave-types", {"code": "kopya", "name": "Olmamalı"}
        ).status_code
        == 403
    )


def test_ui_izin_turu_devre_disi_birakilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Devre dışı bırakılan tür yeni taleplerde görünmez; kayıt silinmez."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    tur_id = next(
        t["id"]
        for t in client.get("/test-ui/api/leave-types").json()["data"]
        if t["code"] == "sick_leave"
    )
    kapat = _post(
        client, f"/test-ui/api/leave-types/{tur_id}/deactivate", {"reason": "deneme"}
    )
    assert kapat.status_code == 200
    assert kapat.json()["data"]["aktif"] is False

    liste = client.get("/test-ui/api/leave-types").json()["data"]
    assert next(t for t in liste if t["code"] == "sick_leave")["aktif"] is False
    aktif = [t["code"] for t in client.get("/test-ui/api/lookups").json()["data"]["leave_types"]]
    assert "sick_leave" not in aktif
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="leave.type.deactivate").exists()


def test_ui_panel_karara_baglanan_izinleri_gosterir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Panelde onaylanan/reddedilen talepler ve durum sayıları görünür (kapsam süzgeçli)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    baslangic = timezone.localdate() + timedelta(days=30)
    talep_id = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(lot["turler"]["annual_leave"].pk),
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=2)).isoformat(),
            "reason": "Panel karar testi",
            "submit": True,
        },
    ).json()["data"]["id"]

    panel_once = client.get("/test-ui/api/dashboard").json()["data"]
    assert panel_once["status_counts"]["pending"] == 1
    assert panel_once["status_counts"]["approved"] == 0
    assert panel_once["decided_requests"] == []

    # İki adımlı onay: birincil yönetici + nihai (kiracı geneli).
    assert (
        _post(client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"})
        .status_code
        == 200
    )
    # admin'in tek kararı yeterli (override) — kalan adım varsa da karar verilir.
    kalan = client.get("/test-ui/api/leave-requests?scope=pending").json()["data"]
    for kayit in kalan:
        if kayit["id"] == talep_id:
            _post(
                client,
                f"/test-ui/api/leave-requests/{talep_id}/decision",
                {"decision": "approved"},
            )

    panel = client.get("/test-ui/api/dashboard").json()["data"]
    onaylananlar = [t for t in panel["decided_requests"] if t["employee_no"] == "MUH-1"]
    assert onaylananlar, panel["decided_requests"]
    assert onaylananlar[0]["status"] in {"approved", "pending"}
    assert panel["status_counts"]["pending"] + panel["status_counts"]["approved"] == 1

    # Kapsam süzgeci: İK (sibling) çalışanı için karara bağlanan kayıt müdüre görünmez.
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    kardes_panel = client.get("/test-ui/api/dashboard").json()["data"]
    assert all(t["employee_no"] != "IK-1" for t in kardes_panel["decided_requests"])

# =============================================================================
# Bildirim (2. tur): departman adı değiştirme, rol yetki düzenleme,
# yaklaşan izinler, sabit üst bar
# =============================================================================


def test_ui_departman_adlandirma_calisir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetkili kullanıcı departmanın adını/kodunu değiştirir; ağaç ve denetim güncellenir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    muhasebe = lot["birimler"]["muhasebe"]
    yanit = _post(
        client,
        f"/test-ui/api/departments/{muhasebe.pk}/update",
        {"name": "Muhasebe ve Finans", "code": "MUH-FIN", "reason": "birleştirme"},
    )
    assert yanit.status_code == 200
    assert yanit.json()["data"]["name"] == "Muhasebe ve Finans"

    agac = {d["code"]: d["name"] for d in client.get("/test-ui/api/departments").json()["data"]}
    assert agac["MUH-FIN"] == "Muhasebe ve Finans"
    # Kapsam/ağaç ilişkisi korunur.
    assert (
        client.get(f"/test-ui/api/departments?moves_for={muhasebe.pk}").json()["meta"]["moves_for"]
        == str(muhasebe.pk)
    )
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="org.unit.update").exists()


def test_ui_departman_adlandirma_yetkisiz_ve_gecersiz(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetkisiz kullanıcı adlandıramaz; boş ad reddedilir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    yasak = _post(
        client, f"/test-ui/api/departments/{lot['birimler']['muhasebe'].pk}/update",
        {"name": "Olmamalı"},
    )
    assert yasak.status_code == 403

    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    bos = _post(
        client, f"/test-ui/api/departments/{lot['birimler']['muhasebe'].pk}/update", {"name": "  "}
    )
    assert bos.status_code == 400


def test_ui_rol_yetkileri_okunur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetki düzenleme ekranı: modül grupları, Türkçe etiketler, seçili/erişilebilir bilgisi."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    rol_id = next(
        r["id"]
        for r in client.get("/test-ui/api/roles").json()["data"]
        if r["code"] == "dept_manager"
    )
    veri = client.get(f"/test-ui/api/roles/{rol_id}/permissions").json()["data"]
    assert veri["role"]["code"] == "dept_manager"
    assert veri["role"]["scope_type_label"] == "Seçili birim + alt birimleri"
    assert veri["can_edit"] is True
    modul_adlari = [m["module_label"] for m in veri["modules"]]
    assert "İzin" in modul_adlari and "Organizasyon (departman/unvan)" in modul_adlari
    # Platform izinleri listelenmez (OD3).
    kodlar = [p["code"] for m in veri["modules"] for p in m["permissions"]]
    assert kodlar and not any(k.startswith("platform.") for k in kodlar)
    secili = [p["code"] for m in veri["modules"] for p in m["permissions"] if p["secili"]]
    assert "leave.request.approve" in secili and "employee.read" in secili


def test_ui_rol_yetkisi_degistirilir_ve_aninda_etki_eder(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetki ekleme/kaldırma kaydedilir; kullanıcının etkin izinleri anında değişir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    dm = next(
        r for r in client.get("/test-ui/api/roles").json()["data"] if r["code"] == "dept_manager"
    )
    veri = client.get(f"/test-ui/api/roles/{dm['id']}/permissions").json()["data"]
    kodlar = [p["code"] for m in veri["modules"] for p in m["permissions"] if p["secili"]]
    assert "leave.balance.read" in kodlar

    # 1) Yetki KALDIR: bakiye görme yetkisi çıkarılsın.
    yeni_kume = [k for k in kodlar if k != "leave.balance.read"]
    yanit = _post(
        client,
        f"/test-ui/api/roles/{dm['id']}/permissions",
        {"permission_codes": yeni_kume, "reason": "bakiye görme kaldırıldı"},
    )
    assert yanit.status_code == 200
    with tenant_context(tenant_a.id):
        assert not user_has_permission(lot["yonetici_user"], "leave.balance.read")

    # 2) Yetki EKLE: tekrar verilsin.
    geri = _post(
        client,
        f"/test-ui/api/roles/{dm['id']}/permissions",
        {"permission_codes": kodlar, "reason": "geri alındı"},
    )
    assert geri.status_code == 200
    with tenant_context(tenant_a.id):
        assert user_has_permission(lot["yonetici_user"], "leave.balance.read")
        assert AuditLog.objects.filter(action="role.permissions.update").count() >= 2

    # 3) Arayüz de güncel görür.
    liste = {r["code"]: r for r in client.get("/test-ui/api/roles").json()["data"]}
    assert liste["dept_manager"]["permission_count"] == len(kodlar)


def test_ui_rol_yetkisi_guardlari(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Kendi rolü, platform izni, boş küme ve bilinmeyen kod reddedilir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    roller = client.get("/test-ui/api/roles").json()["data"]
    dm = next(r for r in roller if r["code"] == "dept_manager")
    admin_rolu = next(r for r in roller if r["code"] == "tenant_admin")

    # Kendi rolü: okuma can_edit=false, yazma 403 (self-elevation yasağı).
    kendi = client.get(f"/test-ui/api/roles/{admin_rolu['id']}/permissions").json()["data"]
    assert kendi["can_edit"] is False
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{admin_rolu['id']}/permissions",
            {"permission_codes": ["employee.read"]},
        ).status_code
        == 403
    )

    platform = _post(
        client,
        f"/test-ui/api/roles/{dm['id']}/permissions",
        {"permission_codes": ["platform.tenant.read"]},
    )
    assert platform.status_code == 400
    assert "Platform" in platform.json()["error"]["message"]

    bilinmeyen = _post(
        client,
        f"/test-ui/api/roles/{dm['id']}/permissions",
        {"permission_codes": ["uydurma.izin"]},
    )
    assert bilinmeyen.status_code == 400

    bos = _post(
        client, f"/test-ui/api/roles/{dm['id']}/permissions", {"permission_codes": []}
    )
    assert bos.status_code == 400
    assert "en az bir izin" in bos.json()["error"]["message"].lower()

    # Yetkisiz kullanıcı (dept_manager): `role.read` yok → rol kataloğu ve yetki
    # ekranı **403**; yazma da 403 (bildirilen "çalışan yetki düzenleyebiliyor" hatası).
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/roles").status_code == 403
    assert client.get(f"/test-ui/api/roles/{dm['id']}/permissions").status_code == 403
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{dm['id']}/permissions",
            {"permission_codes": ["employee.read"]},
        ).status_code
        == 403
    )


def test_ui_sahip_olunmayan_izin_dagitilamaz(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """§4.3: aktör sahip olmadığı izni dağıtamaz (servis katmanı, HTTP 403)."""
    kadro(tenant_a, user_a)  # kiracı kurulumu (roller + izinler)
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        # `role.manage` içeren ama `employee.sensitive.read` içermeyen özel bir rol.
        kisitli = Role.objects.create(
            tenant=tenant_a,
            code="kisitli_yonetici",
            name="Kısıtlı Yönetici",
            role_type="custom",
            scope_type="TENANT",
            is_assignable=True,
        )
        for kod in ("role.manage", "role.read", "employee.read"):
            RolePermission.objects.create(
                tenant=tenant_a, role=kisitli, permission=Permission.objects.get(code=kod)
            )
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=kisitli)

    with tenant_context(tenant_a.id):
        assert user_has_permission(user_a, "role.manage")
        with pytest.raises(RoleAssignmentError, match="Sahip olmadığınız"):
            update_role_permissions(
                actor=user_a,
                role=roller["employee"],
                permission_codes=["employee.read", "employee.sensitive.read"],
            )
        # Kaldırma serbest: sahip olmadığı bir izni **kaldırabilir**.
        update_role_permissions(
            actor=user_a, role=roller["employee"], permission_codes=["employee.read"]
        )


def test_ui_panel_yaklasan_izinler(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Panelde onaylı ve gelecekteki izinler görünür; bekleyen/kapsam dışı görünmez."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    baslangic = timezone.localdate() + timedelta(days=25)
    talep_id = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(lot["turler"]["annual_leave"].pk),
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=2)).isoformat(),
            "reason": "Yaklaşan izin",
            "submit": True,
        },
    ).json()["data"]["id"]

    # Bekleyen talep yaklaşan izinlerde görünmez.
    assert client.get("/test-ui/api/dashboard").json()["data"]["upcoming_requests"] == []
    # 1. adım birincil yönetici, 2. adım kiracı geneli yetkili (M14 onay sırası).
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    assert (
        _post(client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"})
        .status_code
        == 200
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    nihai = _post(
        client, f"/test-ui/api/leave-requests/{talep_id}/decision", {"decision": "approved"}
    )
    assert nihai.status_code == 200 and nihai.json()["data"]["status"] == "approved"
    yaklasan = client.get("/test-ui/api/dashboard").json()["data"]["upcoming_requests"]
    assert [t["employee_no"] for t in yaklasan] == ["MUH-1"]
    assert yaklasan[0]["start_date"] == baslangic.isoformat()

    # Kapsam süzgeci: müdür yalnız alt ağacı görür.
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["kardes_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/dashboard").json()["data"]["upcoming_requests"] == []


def test_ui_ust_bar_sabit_ve_sekmeler_sabit_alanda(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Başlık ve sekmeler kaydırmada sabit kalan tek bir üst bar içinde."""
    assert _giris(client, tenant_a, user_a.email, PAROLA_A).status_code == 200
    govde = client.get("/test-ui/").content.decode()
    assert ".topbar { position: sticky; top: 0;" in govde
    # Sekmeler üst barın İÇİNDE gelir (ayrı blok).
    ust_bar = govde.index('class="topbar"')
    sekmeler = govde.index('class="tabs"')
    kapanis = govde.index("</div>", sekmeler)
    assert ust_bar < sekmeler < kapanis
    assert "Yetki Yönetimi" in govde

# =============================================================================
# Bildirim (3. tur): çalışan yetki düzenleyemez, şirket dizini (PII'siz),
# izin/bakiye detayları yalnız yetkiyle
# =============================================================================


def test_ui_calisan_rol_listesi_ve_yetki_ekranina_erisemez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """(bildirilen hata) Çalışan ne rol kataloğunu görür ne de yetki düzenleyebilir."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/roles").status_code == 403
    rol_id = str(Role.objects.get(tenant=tenant_a, code="employee").pk)
    assert client.get(f"/test-ui/api/roles/{rol_id}/permissions").status_code == 403
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{rol_id}/permissions",
            {"permission_codes": ["role.manage"]},
        ).status_code
        == 403
    )
    # Başkasına rol atayamaz: kapsam dışı çalışan **404** (varlık sızdırılmaz),
    # kendi kaydı üzerinden **403** (self-elevation yasağı).
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['kardes'].pk}/roles",
            {"role_id": rol_id},
        ).status_code
        == 404
    )
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['personel'].pk}/roles",
            {"role_id": rol_id},
        ).status_code
        == 403
    )
    detay = client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"]
    assert detay["yetki_gorunur"] is False
    assert "roles" not in detay or detay.get("roles", []) == [] or detay["yetki_gorunur"] is False


def test_ui_calisan_yalniz_kendini_gorur(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """
    Bildirim: "normal bir çalışan sadece kendini görsün; diğer çalışanları ve
    numaralarını görmesine gerek yok" → liste yalnız kendi kaydını döner,
    başka çalışanın detayı **404** (dizin kaydı kaldırıldı).
    """
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    veri = client.get("/test-ui/api/employees").json()
    assert veri["meta"]["self_only"] is True
    assert [c["employee_no"] for c in veri["data"]] == ["MUH-1"]  # yalnız kendisi
    assert len(veri["data"]) == 1  # başka çalışan kaydı dönmez

    # Kendi kaydı tam: izin bakiyesi görünür.
    kendi = veri["data"][0]
    assert kendi.get("kendi_kaydim") is True
    assert kendi["annual"]["hakedis_var"] is True

    # Başka çalışanın detayı ve bakiyesi: "yok" gibi 404 (yetki mesajı da yok).
    assert client.get(f"/test-ui/api/employees/{lot['kardes'].pk}").status_code == 404
    assert client.get(f"/test-ui/api/employees/{lot['kardes'].pk}/balances").status_code == 404


def test_ui_calisan_baskalarinin_izinlerini_gormez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Panel/onay listeleri yalnız kendi kayıtlarını gösterir (kapsam süzgeci)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    baslangic = timezone.localdate() + timedelta(days=40)
    for calisan, gerekce in ((lot["kardes"], "kardes izni"), (lot["personel"], "personel izni")):
        assert (
            _post(
                client,
                "/test-ui/api/leave-requests",
                {
                    "employee_id": str(calisan.pk),
                    "leave_type_id": str(lot["turler"]["annual_leave"].pk),
                    "start_date": baslangic.isoformat(),
                    "end_date": (baslangic + timedelta(days=1)).isoformat(),
                    "reason": gerekce,
                    "submit": True,
                },
            ).status_code
            == 201
        )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    panel = client.get("/test-ui/api/dashboard").json()["data"]
    hepsi = panel["upcoming_requests"] + panel["decided_requests"] + panel["pending_requests"]
    assert all(t["employee_no"] == "MUH-1" for t in hepsi), hepsi
    havuz = client.get("/test-ui/api/leave-requests?scope=pending").json()["data"]
    assert all(t["employee_no"] == "MUH-1" for t in havuz)


def test_ui_departman_agaci_calisana_acik_ama_detaylar_degil(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan organizasyon şemasını görür; başka çalışanlar, yetki listesi ve bakiyeler kapalı."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    agac = client.get("/test-ui/api/departments").json()
    assert len(agac["data"]) >= 4
    assert agac["meta"]["can_manage"] is False
    # Panel: kendi kaydı ve kendi bakiyesi görünür.
    panel = client.get("/test-ui/api/dashboard").json()["data"]
    assert panel["employee"]["employee_no"] == "MUH-1"
    assert panel["annual"]["hakedis_var"] is True
    # Çalışan listesi yalnız kendi kaydı (bildirim: başkalarını/nolarını görmesin).
    no_lar = [c["employee_no"] for c in client.get("/test-ui/api/employees").json()["data"]]
    assert no_lar == ["MUH-1"]
    # Rol kataloğu ve yetki ekranı kapalı (role.read yok).
    assert client.get("/test-ui/api/roles").status_code == 403

# =============================================================================
# Bildirim (4. tur): Bilgilerim kartı, hesap açma (parola), kişisel veriler,
# rol oluşturma/düzenleme, izin türü şablonları
# =============================================================================


def test_ui_panel_bilgilerim_karti(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Her kullanıcı kendi panelinde kendi bilgilerini görür."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    profil = client.get("/test-ui/api/dashboard").json()["data"]["profile"]
    assert profil["email"] == lot["personel_user"].email
    assert profil["tenant"] == tenant_a.name
    assert "position" in profil  # unvan alanı her zaman döner (boş olabilir)
    assert profil["department"] == "Muhasebe"  # birim
    assert profil["employee_no"] == "MUH-1"
    assert profil["annual"]["hakedis_var"] is True
    assert profil["name"]  # ad soyad (hesap kaydından)

    # Çalışan kaydı olmayan kullanıcı da kendi bilgilerini görür (alanlar boş).
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    admin_profil = client.get("/test-ui/api/dashboard").json()["data"]["profile"]
    assert admin_profil["email"] == lot["admin"].email
    assert admin_profil["tenant"] == tenant_a.name
    assert admin_profil["employee_no"] is None


def test_ui_calisan_hesabi_acilir_ve_giris_yapar(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Çalışan eklerken e-posta + parola verilir; hesap açılır ve giriş çalışır."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yanit = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "YENI-20",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "email": "yeni.personel@ornek.test",
            "password": "Yeni-Parola-1234",
            "first_name": "Yeni",
            "last_name": "Personel",
        },
    )
    assert yanit.status_code == 201, yanit.content
    assert yanit.json()["data"]["user_email"] == "yeni.personel@ornek.test"
    # Parola yanıtta DÖNMEZ.
    assert "Yeni-Parola-1234" not in yanit.content.decode()

    # Denetim kaydında parola geçmez.
    with tenant_context(tenant_a.id):
        kayit = AuditLog.objects.filter(action="user.create").order_by("-created_at").first()
        assert kayit is not None
        assert set(kayit.changes) == {"email"}  # parola alanı YOK
        assert not AuditLog.objects.filter(changes__icontains="Yeni-Parola").exists()

    # Gerçek giriş: yeni hesap çalışıyor.
    cikis = client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    assert cikis.status_code == 204
    giris = _giris(client, tenant_a, "yeni.personel@ornek.test", "Yeni-Parola-1234")
    assert giris.status_code == 200
    assert client.get("/test-ui/api/me").json()["data"]["user"]["email"] == (
        "yeni.personel@ornek.test"
    )


def test_ui_hesap_acma_kurallari(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Zayıf parola ve tekrarlanan e-posta reddedilir; yetkisiz kullanıcı açamaz."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    govde = {
        "employee_no": "YENI-21",
        "department_id": str(lot["birimler"]["muhasebe"].pk),
        "hire_date": timezone.localdate().isoformat(),
        "first_name": "Test",
        "last_name": "Kişi",
    }
    zayif = _post(
        client, "/test-ui/api/employees", {**govde, "email": "zayif@ornek.test", "password": "123"}
    )
    assert zayif.status_code == 400
    assert "Parola" in zayif.json()["error"]["message"]

    tekrar = _post(
        client,
        "/test-ui/api/employees",
        {
            **govde,
            "employee_no": "YENI-22",
            "email": lot["admin"].email,
            "password": "Yeni-Parola-1234",
        },
    )
    assert tekrar.status_code == 400
    assert "zaten var" in tekrar.json()["error"]["message"]

    # `user.manage` olmayan kullanıcı hesap açamaz.
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    yasak = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "YENI-23",
            "department_id": str(lot["birimler"]["finans"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "email": "olmaz@ornek.test",
            "password": "Yeni-Parola-1234",
        },
    )
    assert yasak.status_code == 403


def test_ui_kisisel_veri_yetkiyle_gorunur_ve_yazilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Kişisel veriler yalnız `employee.sensitive.read` ile okunur/yazılır."""
    lot = kadro(tenant_a, user_a)
    personel_id = str(lot["personel"].pk)

    # Çalışan ve departman yöneticisi: kişisel veri YOK.
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert client.get(f"/test-ui/api/employees/{personel_id}/personal").status_code == 403
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE)
    assert client.get(f"/test-ui/api/employees/{personel_id}/personal").status_code == 403
    assert (
        _post(
            client, f"/test-ui/api/employees/{personel_id}/personal", {"first_name": "Olmaz"}
        ).status_code
        == 403
    )
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # Şirket yöneticisi: yazar ve okur.
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    kaydet = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {
            "first_name": "Ayşe",
            "last_name": "Demir",
            "birth_date": "1992-04-03",
            "personal_email": "ayse@ornek.test",
            "personal_phone": "05551234567",
            "address": "Kadıköy / İstanbul",
            "emergency_contact_phone": "05559876543",
            "emergency_contact_relation": "Eş",
            "citizenship": "TR",
        },
    )
    assert kaydet.status_code == 200, kaydet.content
    veri = client.get(f"/test-ui/api/employees/{personel_id}/personal").json()["data"]
    assert veri["first_name"] == "Ayşe"
    assert veri["birth_date"] == "1992-04-03"
    assert veri["emergency_contact_relation"] == "Eş"

    # Denetim: alan ADLARI yazılır, DEĞERLER yazılmaz (kayıt yeni ise `create`).
    with tenant_context(tenant_a.id):
        kayit = (
            AuditLog.objects.filter(action="employee.personal_data.update").first()
            or AuditLog.objects.filter(action="employee.personal_data.create").first()
        )
        assert kayit is not None
        assert "Kadıköy" not in str(kayit.changes)
        assert "alanlar" in kayit.changes


def test_ui_yabanci_uyruklu_calisanda_izin_zorunlu(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """TR dışı vatandaşlıkta oturma/çalışma izni zorunlu; verilince kaydedilir."""
    lot = kadro(tenant_a, user_a)
    personel_id = str(lot["personel"].pk)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    eksik = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {"first_name": "Ali", "last_name": "Yabancı", "citizenship": "DE"},
    )
    assert eksik.status_code == 400
    assert "izni" in eksik.json()["error"]["message"].lower()

    tam = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {
            "first_name": "Ali",
            "last_name": "Yabancı",
            "citizenship": "DE",
            "work_permit_type": "Çalışma izni",
            "work_permit_valid_until": "2027-12-31",
        },
    )
    assert tam.status_code == 200
    assert tam.json()["data"]["work_permit_type"] == "Çalışma izni"
    assert tam.json()["data"]["work_permit_valid_until"] == "2027-12-31"


def test_ui_rol_olusturma_ve_duzenleme(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Şirket yöneticisi özel rol oluşturur, adını/kapsamını düzenler."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)

    yeni = _post(
        client,
        "/test-ui/api/roles/create",
        {"name": "Vardiya Sorumlusu", "code": "vardiya_sorumlusu", "scope_type": "UNIT"},
    )
    assert yeni.status_code == 201, yeni.content
    rol_id = yeni.json()["data"]["id"]
    liste = {r["code"]: r for r in client.get("/test-ui/api/roles").json()["data"]}
    assert liste["vardiya_sorumlusu"]["role_type"] == "custom"
    assert liste["vardiya_sorumlusu"]["permission_count"] == 0
    assert liste["vardiya_sorumlusu"]["can_edit_meta"] is True

    # İzin verilebilir ve etki eder.
    assert (
        _post(
            client,
            f"/test-ui/api/roles/{rol_id}/permissions",
            {"permission_codes": ["employee.read", "leave.request.read"]},
        ).status_code
        == 200
    )
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['personel'].pk}/roles",
            {"role_id": rol_id, "scope_org_unit_id": str(lot["birimler"]["muhasebe"].pk)},
        ).status_code
        == 201
    )
    with tenant_context(tenant_a.id):
        assert user_has_permission(lot["personel_user"], "leave.request.read")

    # Yeniden adlandırma serbest; kapsam, etkin atama varken değiştirilemez.
    ad = _post(client, f"/test-ui/api/roles/{rol_id}/update", {"name": "Vardiya Şefi"})
    assert ad.status_code == 200
    assert ad.json()["data"]["name"] == "Vardiya Şefi"
    kapsam = _post(
        client, f"/test-ui/api/roles/{rol_id}/update", {"scope_type": "TENANT"}
    )
    assert kapsam.status_code == 400
    assert "ataması var" in kapsam.json()["error"]["message"]

    # Atama kaldırılınca kapsam değişir.
    with tenant_context(tenant_a.id):
        atama_id = str(
            UserRole.objects.get(user=lot["personel_user"], role_id=rol_id).pk
        )
    assert (
        _post(
            client, f"/test-ui/api/role-assignments/{atama_id}/revoke", {"reason": "deneme"}
        ).status_code
        == 200
    )
    son = _post(client, f"/test-ui/api/roles/{rol_id}/update", {"scope_type": "TENANT"})
    assert son.status_code == 200
    assert son.json()["data"]["scope_type"] == "TENANT"

    # Guard'lar: varsayılan kod, tekrar eden kod, platform kapsamı.
    assert (
        _post(
            client,
            "/test-ui/api/roles/create",
            {"name": "Kopya", "code": "dept_manager"},
        ).status_code
        == 400
    )
    assert (
        _post(
            client,
            "/test-ui/api/roles/create",
            {"name": "Tekrar", "code": "vardiya_sorumlusu"},
        ).status_code
        == 400
    )
    assert (
        _post(
            client,
            "/test-ui/api/roles/create",
            {"name": "Platform", "code": "platform_rol", "scope_type": "SYS"},
        ).status_code
        == 400
    )
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="role.create").exists()
        assert AuditLog.objects.filter(action="role.update").exists()


def test_ui_yetki_sekmesi_gorunurlugu_ve_sablonlar(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yetki sekmesi bayrağı ve izin türü şablonları yalnız yetkiliye/tümüne döner."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/me").json()["data"]["menu"]["roles_manage"] is True
    lookups = client.get("/test-ui/api/lookups").json()
    assert lookups["meta"]["can_create_users"] is True
    assert lookups["meta"]["can_see_pii"] is True
    assert len(lookups["data"]["leave_type_presets"]) >= 4
    sablon = lookups["data"]["leave_type_presets"][0]
    assert {"ad", "code", "category", "uses_balance", "requires_document"} <= set(sablon)

    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))
    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/me").json()["data"]["menu"]["roles_manage"] is False
    calisan_lookups = client.get("/test-ui/api/lookups").json()["meta"]
    assert calisan_lookups["can_create_users"] is False
    assert calisan_lookups["can_see_pii"] is False


# =============================================================================
# Bildirim (6. tur): izin durumu noktası, yönetici seçimi, onay ekranında bakiye,
# eksi bakiye politikası, yaklaşan izin süzgeci, banka bilgileri
# =============================================================================


def test_ui_bakiye_yetersizken_talep_politika_ile_acilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """
    Bildirim: "izin bakiyesi 0 olsa bile izin talebi oluşturulabilsin".
    Anahtar politikadır (`allow_negative_balance`): kapalıyken 400, açıkken 201.
    """
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    politika = client.get("/test-ui/api/leave-policies").json()["data"][0]
    assert politika["allow_negative_balance"] is False  # kurgu varsayılanı

    baslangic = timezone.localdate() + timedelta(days=30)
    govde = {
        "employee_id": str(lot["personel"].pk),
        "leave_type_id": str(lot["turler"]["annual_leave"].pk),
        "start_date": baslangic.isoformat(),
        "end_date": (baslangic + timedelta(days=19)).isoformat(),  # 20 gün > 14 hakediş
        "reason": "Bakiye üstü talep",
        "submit": True,
    }
    kapali = _post(client, "/test-ui/api/leave-requests", govde)
    assert kapali.status_code == 409, kapali.content
    assert "Yetersiz izin bakiyesi" in kapali.json()["error"]["message"]

    # Politikayı aç → talep oluşturulabilir (kararı onaylayan verir).
    assert (
        _post(
            client,
            f"/test-ui/api/leave-policies/{politika['id']}/update",
            {"allow_negative_balance": True, "reason": "Test: eksi bakiyeye izin"},
        ).status_code
        == 200
    )
    assert _post(client, "/test-ui/api/leave-requests", govde).status_code == 201


def test_ui_izin_politikasi_yetkisi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Politika ucu `leave.type.manage` ister; yetkisizde 404 (bölüm hiç görünmez)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/leave-policies").status_code == 200
    assert client.get("/test-ui/api/lookups").json()["meta"]["can_manage_policy"] is True
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    assert client.get("/test-ui/api/leave-policies").status_code == 404
    assert client.get("/test-ui/api/lookups").json()["meta"]["can_manage_policy"] is False
    # Güncelleme denemesi de reddedilir (izin kararı serviste).
    politika_id = str(LeavePolicy.objects.filter(deleted_at__isnull=True).first().pk)
    assert (
        _post(
            client,
            f"/test-ui/api/leave-policies/{politika_id}/update",
            {"allow_negative_balance": True, "reason": "yetkisiz deneme"},
        ).status_code
        in (403, 404)
    )


def test_ui_calisan_izin_durumu_isaretleri(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """İzinde olan çalışanda yeşil, bitişe 7 günden az kalanlarda sarı nokta."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    with tenant_context(tenant_a.id):
        istek = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun,
            end_date=bugun + timedelta(days=9),  # 10 gün: bakiyeye sığar, bitişe 9 gün
            reason="Yeşil nokta testi",
            submit=True,
        )
        decide_approval_step(
            actor=lot["admin"], request=istek, decision="approved", override=True,
            reason="Test: yeşil nokta",
        )

    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    durumlar = {
        c["employee_no"]: c["izin_durumu"]
        for c in client.get("/test-ui/api/employees").json()["data"]
    }
    assert durumlar["MUH-1"]["izinde"] is True
    assert durumlar["MUH-1"]["isaret"] == "yesil"  # bitişe 20 gün
    assert durumlar["IK-1"]["izinde"] is False and durumlar["IK-1"]["isaret"] == "yok"


def test_ui_izin_durumu_sari_isaret(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Bitişe 7 günden az kaldıysa sarı işaret döner."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    with tenant_context(tenant_a.id):
        istek = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun,
            end_date=bugun + timedelta(days=3),
            reason="Sarı nokta testi",
            submit=True,
        )
        decide_approval_step(
            actor=lot["admin"], request=istek, decision="approved", override=True,
            reason="Test: sarı nokta",
        )
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    kayit = next(
        c for c in client.get("/test-ui/api/employees").json()["data"]
        if c["employee_no"] == "MUH-1"
    )
    assert kayit["izin_durumu"]["isaret"] == "sari"
    assert kayit["izin_durumu"]["kalan_gun"] == 3


def test_ui_onay_kuyrugunda_bakiye_ve_kendi_talebi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """
    Onay ekranında talep sahibinin bakiyesi görünür; kullanıcı kendi talebini
    onaylayamaz (`can_approve=false`, `kendi_talebi=true`) ama listede görür.
    """
    lot = kadro(tenant_a, user_a)
    baslangic = timezone.localdate() + timedelta(days=25)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert (
        _post(
            client,
            "/test-ui/api/leave-requests",
            {
                "employee_id": str(lot["personel"].pk),
                "leave_type_id": str(lot["turler"]["annual_leave"].pk),
                "start_date": baslangic.isoformat(),
                "end_date": (baslangic + timedelta(days=2)).isoformat(),
                "reason": "Onay kuyruğu testi",
                "submit": True,
            },
        ).status_code
        == 201
    )
    kuyruk = client.get("/test-ui/api/leave-requests?scope=pending").json()["data"]
    kayit = next(t for t in kuyruk if t["employee_no"] == "MUH-1")
    assert kayit["talep_sahibi_bakiye"]["hakedis_var"] is True  # onaylayan bakiyeyi görür
    assert kayit["talep_sahibi_bakiye"]["available_days"] is not None
    assert kayit["kendi_talebi"] is False  # admin'in kendi talebi değil

    # Kuyrukta bakiye alanı her satırda dolu (talep sahibinin kendi görünümü hariç).
    assert all("talep_sahibi_bakiye" in t for t in kuyruk)


def test_ui_kendi_talebi_onaylanamaz_ama_iptal_edilebilir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Talep sahibi kendi talebini onaylayamaz; iptal edebilir (backend kararı)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    istek = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(lot["turler"]["annual_leave"].pk),
            "start_date": (timezone.localdate() + timedelta(days=60)).isoformat(),
            "end_date": (timezone.localdate() + timedelta(days=61)).isoformat(),
            "reason": "Kendi talebi testi",
            "submit": True,
        },
    )
    talep_id = istek.json()["data"]["id"]
    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    _giris(client, tenant_a, lot["personel_user"].email, PAROLA_LEAVE)
    kuyruk = client.get("/test-ui/api/leave-requests?scope=pending").json()["data"]
    kendi = next(t for t in kuyruk if t["id"] == talep_id)
    assert kendi["kendi_talebi"] is True
    assert kendi["can_approve"] is False  # SEPARATE_ACTOR
    assert kendi["can_cancel"] is True
    # Onay denemesi: aynı aktör (izin yok) → 403.
    assert (
        _post(
            client,
            f"/test-ui/api/leave-requests/{talep_id}/decision",
            {"decision": "approved"},
        ).status_code
        == 403
    )


def test_ui_yaklasan_izinler_gecmisi_gostermez(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Aralığı geçmiş onaylı izin "yaklaşan izinler" kartında listelenmez."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    with tenant_context(tenant_a.id):
        gecmis = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun - timedelta(days=30),
            end_date=bugun - timedelta(days=25),
            reason="Geçmiş izin testi",
            submit=True,
        )
        decide_approval_step(
            actor=lot["admin"], request=gecmis, decision="approved", override=True,
            reason="Test: geçmiş izin",
        )
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    yaklasan = client.get("/test-ui/api/dashboard").json()["data"]["upcoming_requests"]
    assert all(t["id"] != str(gecmis.pk) for t in yaklasan)


def test_ui_calisan_olusturmada_yonetici_secimi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Yeni çalışan formunda bağlı olacağı yönetici seçilebilir (bildirim)."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    secenekler = client.get("/test-ui/api/lookups").json()["data"]["employees"]
    assert any(e["employee_no"] == "FIN-M1" for e in secenekler)

    yanit = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "MUH-2",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "manager_employee_id": str(lot["yonetici"].pk),
        },
    )
    assert yanit.status_code == 201, yanit.content
    yeni_id = yanit.json()["data"]["id"]
    detay = client.get(f"/test-ui/api/employees/{yeni_id}").json()["data"]
    assert detay["manager"]["employee_no"] == "FIN-M1"

    # Bilinmeyen yönetici → 400 (kayıt yarım kalmaz: önce doğrulanır).
    hatali = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "MUH-3",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": timezone.localdate().isoformat(),
            "manager_employee_id": str(uuid4()),
        },
    )
    assert hatali.status_code == 400


def test_ui_kisisel_veri_banka_bilgisi(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Banka bilgileri (banka + IBAN) kişisel veri kapısından geçer."""
    lot = kadro(tenant_a, user_a)
    _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE)
    assert (
        _post(
            client,
            f"/test-ui/api/employees/{lot['personel'].pk}/personal",
            {
                "first_name": "Ayşe",
                "last_name": "Demir",
                "bank_name": "Örnek Bankası",
                "iban": "TR33 0006 1005 1978 6457 8413 26",
            },
        ).status_code
        == 200
    )
    veri = client.get(f"/test-ui/api/employees/{lot['personel'].pk}/personal").json()["data"]
    assert veri["bank_name"] == "Örnek Bankası"
    assert veri["iban"].startswith("TR33")
    # Denetim: değerler değil, alan adları yazılır.
    with tenant_context(tenant_a.id):
        kayit = (
            AuditLog.objects.filter(action="employee.personal_data.update").first()
            or AuditLog.objects.filter(action="employee.personal_data.create").first()
        )
        assert "Örnek Bankası" not in str(kayit.changes)
        assert "iban" in kayit.changes["alanlar"]


# =============================================================================
# Bildirim (8. tur): çalışan kaydı bütün bilgileri ister — kan grubu (K3) dahil
# =============================================================================


def test_ui_kayit_formu_tum_kisisel_verileri_ister(client, tenant_a, user_a) -> None:
    """Yeni çalışan formu K2 alanlarının tamamını + kan grubunu (K3) sorar."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    sayfa = client.get("/test-ui/").content.decode()
    for alan_id in (
        "emp-ad",
        "emp-soyad",
        "emp-kimlik",
        "emp-dogum",
        "emp-kan",
        "emp-pmail",
        "emp-ptel",
        "emp-adres",
        "emp-yakin-tel",
        "emp-yakin-derece",
        "emp-uyruk",
        "emp-izin",
        "emp-izin-tarih",
        "emp-banka",
        "emp-iban",
    ):
        assert f'id="{alan_id}"' in sayfa, alan_id
    # Kan grubu kapalı kümesi sunucudan gelir (tek kaynak: model BloodType).
    kan = client.get("/test-ui/api/lookups").json()["data"]["blood_types"]
    assert [b["value"] for b in kan] == ["A+", "A-", "B+", "B-", "AB+", "AB-", "0+", "0-"]
    assert {b["label"] for b in kan} >= {"0 Rh+", "AB Rh-"}


def test_ui_kayit_formu_bilgileri_tek_adimda_yazar(client, tenant_a, user_a) -> None:
    """Formun gönderdiği tam gövde: çalışan + bütün kişisel veri tek istekte yazılır."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    yanit = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "MUH-9",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "position_id": None,
            "hire_date": "2026-01-05",
            "manager_employee_id": str(lot["yonetici"].pk),
            "first_name": "Ayşe",
            "last_name": "Demir",
            "national_id": "12345678901",
            "birth_date": "1992-04-03",
            "blood_type": "0+",
            "personal_email": "ayse@ornek.test",
            "personal_phone": "05551234567",
            "address": "Kadıköy / İstanbul",
            "emergency_contact_phone": "05559876543",
            "emergency_contact_relation": "Eş",
            "citizenship": "TR",
            "work_permit_type": "",
            "work_permit_valid_until": None,
            "bank_name": "Örnek Bankası",
            "iban": "TR33 0006 1005 1978 6457 8413 26",
        },
    )
    assert yanit.status_code == 201, yanit.content
    yeni_id = yanit.json()["data"]["id"]
    veri = client.get(f"/test-ui/api/employees/{yeni_id}/personal").json()["data"]
    assert veri["first_name"] == "Ayşe"
    assert veri["national_id"] == "12345678901"
    assert veri["blood_type"] == "0+"
    assert veri["blood_type_label"] == "0 Rh+"
    assert veri["address"] == "Kadıköy / İstanbul"
    assert veri["iban"].startswith("TR33")


def test_ui_kan_grubu_sifreli_saklanir_ve_denetlenir(client, tenant_a, user_a) -> None:
    """Kan grubu (K3) veritabanında şifreli durur; denetimde yalnız alan adı yazılır."""
    lot = kadro(tenant_a, user_a)
    personel_id = str(lot["personel"].pk)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    kaydet = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {"first_name": "Ayşe", "last_name": "Demir", "blood_type": "AB-"},
    )
    assert kaydet.status_code == 200, kaydet.content
    assert kaydet.json()["data"]["blood_type"] == "AB-"
    assert kaydet.json()["data"]["blood_type_label"] == "AB Rh-"

    # Veritabanı: şifreli kolon düz metin taşımaz (Fernet).
    with tenant_context(tenant_a.id), connection.cursor() as cur:
        cur.execute(
            "SELECT blood_type FROM hr.employee_personal_data WHERE employee_id = %s",
            [personel_id],
        )
        ham = cur.fetchone()[0]
    assert ham is not None
    # Fernet jetonu (sürüm ön eki `gAAAAA`) — düz metin değil. Kısa değerlerde
    # şifreli metin tesadüfen aynı harfleri içerebilir; bu yüzden ön ek ölçülür.
    assert ham.startswith("gAAAAA")
    assert ham != "AB-"

    # Denetim: değer değil, K3 alan adı yazılır.
    with tenant_context(tenant_a.id):
        kayit = (
            AuditLog.objects.filter(action="employee.personal_data.update").first()
            or AuditLog.objects.filter(action="employee.personal_data.create").first()
        )
        assert kayit.changes["k3_alanlar"] == ["blood_type"]
        assert "AB-" not in str(kayit.changes)


def test_ui_kan_grubu_gecersiz_ve_yetkisiz_reddedilir(client, tenant_a, user_a) -> None:
    """Kan grubu kapalı kümeden olmalı; yetkisiz kullanıcı yazamaz (fail-closed)."""
    lot = kadro(tenant_a, user_a)
    personel_id = str(lot["personel"].pk)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200

    kotu = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {"first_name": "A", "last_name": "B", "blood_type": "XX"},
    )
    assert kotu.status_code == 400
    assert "kan grubu" in kotu.json()["error"]["message"].lower()

    # Boş değer = "bilinmiyor": alan temizlenebilir.
    temizle = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {"first_name": "A", "last_name": "B", "blood_type": "0+"},
    )
    assert temizle.status_code == 200
    bos = _post(client, f"/test-ui/api/employees/{personel_id}/personal", {"blood_type": ""})
    assert bos.status_code == 200
    assert bos.json()["data"]["blood_type"] is None

    client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=_csrf(client))

    # Departman yöneticisinde `employee.sensitive.read` yok → 403 ve yazılmaz.
    assert _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE).status_code == 200
    yasak = _post(
        client,
        f"/test-ui/api/employees/{personel_id}/personal",
        {"first_name": "Olmaz", "last_name": "Olmaz", "blood_type": "A+"},
    )
    assert yasak.status_code == 403
    with tenant_context(tenant_a.id), connection.cursor() as cur:
        cur.execute(
            "SELECT blood_type FROM hr.employee_personal_data WHERE employee_id = %s",
            [personel_id],
        )
        assert cur.fetchone()[0] is None


def test_ui_kullanicisiz_calisan_lookups_ve_listeyi_kirmaz(client, tenant_a, user_a) -> None:
    """
    (regresyon) Hesabı bağlı OLMAYAN çalışan `/lookups` ucunu kırmamalı.

    Bildirilen hata: yönetici seçimi için üretilen çalışan etiketi doğrudan
    `employee.user.first_name` okuyordu; `user_id` NULL olan bir çalışan
    oluşturulduğunda `/test-ui/api/lookups` `AttributeError` ile 500 döndü ve
    panel/çalışanlar sekmeleri **boş** kaldı (arayüzün tamamı lookups'a bağlı).
    """
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200

    # Hesabı bağlı olmayan çalışan (kullanıcı alanı boş) — kayıt ucu bunu destekler.
    yeni = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "KONTROL-1",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": "2026-03-01",
            "first_name": "Hesapsız",
            "last_name": "Çalışan",
        },
    )
    assert yeni.status_code == 201, yeni.content
    assert yeni.json()["data"]["user_email"] is None

    # 1) lookups 200 ve etiket çalışan numarasına düşer (ad/e-posta yok).
    yanit = client.get("/test-ui/api/lookups")
    assert yanit.status_code == 200, yanit.content
    etiketler = {e["employee_no"]: e["label"] for e in yanit.json()["data"]["employees"]}
    assert etiketler["KONTROL-1"] == "KONTROL-1"
    # Hesabı olan çalışanın etiketi ad-soyad'dır.
    beklenen = (
        f"{lot['yonetici_user'].first_name} {lot['yonetici_user'].last_name}".strip()
    )
    assert etiketler["FIN-M1"] == beklenen

    # 2) Çalışan listesi ve panel de çalışmaya devam eder.
    assert client.get("/test-ui/api/employees").status_code == 200
    assert client.get("/test-ui/api/dashboard").status_code == 200


def test_ui_kullanicisiz_calisan_detayi_bos_listelerle_doner(client, tenant_a, user_a) -> None:
    """
    (regresyon) Hesabı bağlı olmayan çalışanın detayı **200** döner ve rol
    alanları **boş liste** olarak gelir (arayüz `yetki_gorunur` true iken
    `.map` çağırıp çöktüğü için şekil sözleşmesi sabit tutulur).
    """
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    yeni = _post(
        client,
        "/test-ui/api/employees",
        {
            "employee_no": "KONTROL-2",
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": "2026-03-01",
            "first_name": "Hesapsız",
            "last_name": "İkinci",
        },
    )
    assert yeni.status_code == 201
    yeni_id = yeni.json()["data"]["id"]

    yanit = client.get(f"/test-ui/api/employees/{yeni_id}")
    assert yanit.status_code == 200, yanit.content
    veri = yanit.json()["data"]
    # Şirket yöneticisi rol bölümünü görür ama alanlar boş listedir (çökme yok).
    assert veri["yetki_gorunur"] is True
    assert veri["user_email"] is None
    assert veri["roles"] == []
    assert veri["revoked_assignments"] == []
    assert veri["permission_overrides"] == []
    assert veri["effective_permissions"] == []
    assert veri["assignable_roles"] == []


# =============================================================================
# Bildirim (9. tur): UX/fonksiyon düzeltmeleri — otomatik çalışan no, detay
# çekmecesi, liste kolonları, yerleşim standardı, izin türü özellikleri
# =============================================================================


def test_ui_calisan_no_sunucu_tarafindan_otomatik_uretilir(client, tenant_a, user_a) -> None:
    """Çalışan numarası istemciden İSTENMEZ: sunucu `EMP-000001` biçiminde üretir."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    yanit = _post(
        client,
        "/test-ui/api/employees",
        {
            # `employee_no` GÖNDERİLMEZ (form artık göndermiyor).
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": "2026-04-01",
            "first_name": "Otomatik",
            "last_name": "Numara",
        },
    )
    assert yanit.status_code == 201, yanit.content
    numara = yanit.json()["data"]["employee_no"]
    assert numara.startswith("EMP-"), numara
    assert len(numara) == len("EMP-000000")
    assert numara[4:].isdigit()

    # İkinci kayıt farklı numara alır ve liste ikisini de gösterir.
    ikinci = _post(
        client,
        "/test-ui/api/employees",
        {
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": "2026-04-02",
            "first_name": "İkinci",
            "last_name": "Numara",
        },
    )
    assert ikinci.status_code == 201
    assert ikinci.json()["data"]["employee_no"] != numara
    numaralar = {c["employee_no"] for c in client.get("/test-ui/api/employees").json()["data"]}
    assert {numara, ikinci.json()["data"]["employee_no"]} <= numaralar


def test_calisan_no_uretiminde_cakisma_olmaz(db) -> None:  # noqa: ANN001, ARG001
    """
    Ardışık üretimde aynı numara iki kez verilmez; sıra **atomik sequence**'ten gelir.

    Not: Gerçek **eşzamanlı** ölçüm (iki paralel HTTP kaydı → farklı numara)
    `scripts/verify_fresh_install.sh` adım **6c-9**'da yapılır; burada mekanizmanın
    tekilliği ve monotonluğu (çakışmasızlığın temeli) ölçülür.
    """
    from apps.organization.services import (
        EMPLOYEE_NO_DIGITS,
        EMPLOYEE_NO_PREFIX,
        _sequence_degeri,
    )

    numaralar = [
        f"{EMPLOYEE_NO_PREFIX}-{_sequence_degeri():0{EMPLOYEE_NO_DIGITS}d}" for _ in range(5)
    ]
    assert len(set(numaralar)) == 5, numaralar   # çakışma YOK
    assert numaralar == sorted(numaralar)        # monoton artan


def test_ui_calisan_no_birim_tasininca_degismez(client, tenant_a, user_a) -> None:
    """Birim değişse de çalışan numarası sabit kalır (numara kimlik değildir ama kararlıdır)."""
    from apps.organization.services import move_employee

    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    yeni = _post(
        client,
        "/test-ui/api/employees",
        {
            "department_id": str(lot["birimler"]["muhasebe"].pk),
            "hire_date": "2026-05-01",
            "first_name": "Taşıma",
            "last_name": "Testi",
        },
    )
    assert yeni.status_code == 201
    veri = yeni.json()["data"]
    eski_no, calisan_id = veri["employee_no"], veri["id"]

    with tenant_context(tenant_a.id):
        from apps.organization.models import Employee

        calisan = Employee.objects.get(pk=calisan_id)
        move_employee(
            actor=lot["admin"],
            employee=calisan,
            department=lot["birimler"]["ik"],
            reason="numara kararlılığı testi",
        )
        calisan.refresh_from_db()
        assert calisan.employee_no == eski_no
        assert calisan.department_id == lot["birimler"]["ik"].pk

    liste = {c["employee_no"]: c for c in client.get("/test-ui/api/employees").json()["data"]}
    assert liste[eski_no]["department"] == "İnsan Kaynakları"


def test_ui_detay_cekmecesi_sozlesmesi(client, tenant_a, user_a) -> None:
    """
    Detay çekmecesi (drawer) sözleşmesi: sağdan açılan panel + tek çekmece kuralı.

    Arayüz davranışı tarayıcıda JS ile çalışır; burada **şablon sözleşmesi** ölçülür:
    panel/backdrop düğümleri, "Detay → Detayı kapat" metni, aynı çalışana tekrar
    tıklamada kapatma kararı ve liste satırında `aria-expanded` taşıyan düğme.
    """
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    sayfa = client.get("/test-ui/").content.decode()

    # 1) Çekmece düğümleri ve kapatma denetimleri var.
    assert 'id="detail-panel"' in sayfa
    assert 'class="detail-panel"' in sayfa
    assert 'id="detail-backdrop"' in sayfa
    assert 'id="detail-panel-kapat"' in sayfa
    # 2) Aynı çalışana tekrar tıklayınca KAPATMA kararı (saf fonksiyon) var.
    assert "function cekmeceKarari(" in sayfa
    assert "acikId && acikId === tiklananId ? null : tiklananId" in sayfa
    # 3) Düğme metni duruma göre değişir.
    assert "Detayı kapat" in sayfa
    assert 'b.textContent = secili ? "Detayı kapat" : "Detay";' in sayfa
    # 4) Satırda erişilebilirlik: aria-expanded + aria-controls.
    assert 'aria-expanded="false" aria-controls="detail-panel"' in sayfa
    # 5) Escape ile kapanma (base.html) bağlı.
    assert "cekmeceKapat" in sayfa


def test_ui_calisan_listesi_kolonlari_ve_yerlesim_standardi(client, tenant_a, user_a) -> None:
    """Liste kolonları (unvan/yönetici/kalan izin) + yerleşim standardı sınıfları."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    sayfa = client.get("/test-ui/").content.decode()

    # Liste kolon başlıkları: unvan ve rol AYRI kavramlar olarak yan yana.
    for baslik in ("Çalışan", "Çalışan no", "Unvan", "Birim", "Yönetici", "Rol (yetki)",
                   "Kalan izin", "Durum", "İşlem"):
        assert f">{baslik}</th>" in sayfa, baslik
    assert "class=\"sayi\"" in sayfa          # sayısal hizalama
    assert 'class="aksiyon"' in sayfa         # aksiyonlar sağda
    # Yerleşim standardı: geniş sayfa kapsayıcı + çok kolonlu grid + tablo kaydırma.
    assert "--page-max: 1400px" in sayfa
    assert "main.page-container" in sayfa
    for sinif in (".grid-2 {", ".grid-3 {", ".split {", ".form-grid {",
                  ".data-table td.aksiyon", ".detail-panel {", ".action-bar {",
                  ".tablo-kaydir {", ".card.ic {"):
        assert sinif in sayfa, sinif


def test_ui_izin_turu_ozellikleri_backend_degerlerini_tasir(client, tenant_a, user_a) -> None:
    """İzin türü özellik kutuları doğru alan adlarını ve değerlerini gönderir."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200

    # Arayüz açıklamaları ve kutuları hizalı kartlar olarak sunulur.
    sayfa = client.get("/test-ui/").content.decode()
    for kutu in ("lt-paid", "lt-balance", "lt-approval", "lt-doc"):
        assert f'id="{kutu}"' in sayfa, kutu
    assert 'class="option-card"' in sayfa            # checkbox + açıklama aynı kartta
    assert 'class="option-grid"' in sayfa
    # Checkbox'lar backend alan adlarıyla birebir eşlenir.
    for alan in ("is_paid:", "uses_balance:", "requires_approval:", "requires_document:"):
        assert alan in sayfa, alan

    # API: gönderilen özellikler kalıcı olur (unpaid + belge zorunlu + onay yok).
    olustur = _post(
        client,
        "/test-ui/api/leave-types",
        {
            "code": "ux_test_izni",
            "name": "UX Test İzni",
            "category": "other",
            "is_paid": False,
            "uses_balance": False,
            "requires_approval": False,
            "requires_document": True,
        },
    )
    assert olustur.status_code == 201, olustur.content
    veri = olustur.json()["data"]
    assert veri["is_paid"] is False
    assert veri["uses_balance"] is False
    assert veri["requires_approval"] is False
    assert veri["requires_document"] is True


def test_ui_yetki_ekrani_rol_listesi_ve_izin_gruplari(client, tenant_a, user_a) -> None:
    """Yetki Yönetimi: solda rol listesi (unvan/rol ayrımı), sağda izin grupları."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    sayfa = client.get("/test-ui/").content.decode()
    assert 'class="rol-kart"' in sayfa           # rol listesi kartları
    assert 'class="split"' in sayfa              # sol liste + sağ editör
    assert "Yetkileri düzenle" in sayfa
    assert "Unvan ≠ rol" in sayfa
    # İzin grupları: accordion + "n/m seçili" sayacı + teknik kod ikinci planda.
    assert 'class="modul-izinler"' in sayfa
    assert 'class="modul-sayac"' in sayfa
    assert 'class="izin-kutu"' in sayfa
    assert "izin-tumu', " in sayfa or 'id="izin-tumu"' in sayfa
    assert 'id="izin-hicbiri"' in sayfa
    assert 'id="izin-geri"' in sayfa

    # İzin grubu ölçümü: modül/izin sayıları API'den gelir ve kod+ad içerir.
    roller = client.get("/test-ui/api/roles").json()["data"]
    rol_id = next(r["id"] for r in roller if r["code"] == "tenant_admin")
    izinler = client.get(f"/test-ui/api/roles/{rol_id}/permissions").json()["data"]
    assert izinler["modules"], "izin grupları boş"
    for modul in izinler["modules"]:
        assert modul["module_label"]
        for p in modul["permissions"]:
            assert p["code"] and p["name"]      # insan okunabilir ad + teknik kod


def test_ui_calisan_listesinde_kalan_izin_backend_degeri(client, tenant_a, user_a) -> None:
    """Listedeki 'Kalan izin' değeri backend bakiye kaydından gelir (arayüz hesap yapmaz)."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["admin"].email, PAROLA_LEAVE).status_code == 200
    kayitlar = {c["employee_no"]: c for c in client.get("/test-ui/api/employees").json()["data"]}
    kendi = kayitlar[lot["yonetici"].employee_no]
    assert kendi["annual"]["hakedis_var"] is True
    # Liste satırında kullanılan alanlar: available_days / entitled_days.
    bakiye = client.get(f"/test-ui/api/employees/{lot['yonetici'].pk}/balances").json()["data"]
    assert bakiye, "bakiye kaydı yok"
    assert str(bakiye[0]["available_days"]) == str(kendi["annual"]["available_days"])


def test_ui_scope_disi_bakiye_gorunmez(client, tenant_a, user_a) -> None:
    """Departman yöneticisi kapsam dışı çalışanın bakiyesini göremez (404/403)."""
    lot = kadro(tenant_a, user_a)
    assert _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_LEAVE).status_code == 200
    # İK çalışanı finans müdürünün kapsamı dışındadır.
    assert client.get(f"/test-ui/api/employees/{lot['kardes'].pk}").status_code == 404
    liste = client.get("/test-ui/api/employees").json()["data"]
    assert lot["kardes"].employee_no not in {c["employee_no"] for c in liste}


def test_ui_sablon_yorumu_sayfaya_sizmaz(client, tenant_a, user_a) -> None:
    """
    (regresyon) Django şablon yorumları sayfaya **metin olarak** sızmamalı.

    Bildirilen hata: çok satırlı `{# ... #}` yorumu (Django'da `{# #}` yalnızca
    tek satırdır) çalışan listesinin altında ham metin olarak göründü; aynı
    şekilde `{% comment %}`/`{% endcomment %}` dışındaki kapanmamış etiketler de
    sayfada ham kalır.
    """
    admin = kadro(tenant_a, user_a)["admin"]
    assert _giris(client, tenant_a, admin.email, PAROLA_LEAVE).status_code == 200
    sayfa = client.get("/test-ui/").content.decode()
    assert "{#" not in sayfa, "ham şablon yorumu sızmış"
    assert "{%" not in sayfa and "%}" not in sayfa, "ham şablon etiketi sızmış"
    assert "endcomment" not in sayfa
    assert admin.email in sayfa  # sayfa gerçekten panel
