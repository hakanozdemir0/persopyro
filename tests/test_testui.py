"""
Manuel test arayüzü (test UI) testleri — `/test-ui/`.

Kapsanan istek senaryoları: 1–14 (giriş sayfası, geçerli/hatalı giriş, çalışanın
yalnız kendi talepleri, dept_manager alt ağaç görünürlüğü, sibling DENY, onay,
yetkisiz onay DENY, onay sonrası bakiye, red sonrası bakiye, çıkış, `/admin/` 404,
askıdaki kiracı, cross-tenant görünürlük) + arayüz kapalıyken 404 güvenlik testi.

Önemli: testler arayüzü **gerçek HTTP yoluyla** dener (Django test istemcisi +
CSRF çerezi/başlığı), servisleri doğrudan çağırmaz. Böylece "arayüz güvenliği
atlamıyor" iddiası ölçülmüş olur.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.leave.models import LeaveBalance
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro, rol_ver

#: `tests/conftest.py` içindeki `user_a` parolası; `leave_helpers` kullanıcıları
#: ayrı bir parola kullanır (aşağıda `PAROLA_LEAVE`).
PAROLA_A = "Guclu-Parola-123"
PAROLA_B = "Guclu-Parola-456"
PAROLA_LEAVE = "Gizli-Parola-123"


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
