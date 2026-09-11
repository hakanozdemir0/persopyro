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
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.leave.models import LeaveBalance
from apps.organization.services import create_position
from apps.rbac.models import Role
from apps.rbac.services import (
    RoleAssignmentError,
    assign_role_to_user,
    create_default_roles,
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

    for kullanici, parola in (
        (lot["yonetici_user"], PAROLA_LEAVE),
        (lot["kardes_user"], PAROLA_LEAVE),
    ):
        _giris(client, tenant_a, kullanici.email, parola)
        roller = client.get("/test-ui/api/roles").json()
        assert roller["meta"]["can_manage"] is False
        assert all(r["atanabilir"] is False for r in roller["data"])
        assert (
            _post(
                client,
                f"/test-ui/api/employees/{lot['personel'].pk}/roles",
                {"role_id": roller["data"][0]["id"]},
            ).status_code
            == 403
        )
        assert (
            client.get(f"/test-ui/api/employees/{lot['personel'].pk}").json()["data"][
                "yetki_gorunur"
            ]
            is False
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
