"""
Arayüz pencere (modal) akışları — askıya alma/geri alma/arşivleme ve izin kararı.

Kapsam (bu tur — kullanıcı bildirimi):

  * **Askıya alma çalışmıyor / işten çıkış sonrası geri alınamıyor:** gerekçe ham
    `prompt()` ile alınıyordu; tarayıcının "bu sayfanın ek pencere açmasını
    engelle" seçeneği işaretlenince `prompt()` sessizce `null` döner ve istek
    **hiç gönderilmez**. Artık pencere içi form kullanılır (tek adım, ikinci
    onay penceresi yok) ve aynı uç noktalar aynı yükle çalışır.
  * **İzin reddi:** red gerekçesi de pencere içi formdan alınır; satır aksiyonları
    kendi kapsayıcısına bağlanır (global seçici aynı düğmeye ikinci dinleyici
    ekliyordu → çift pencere/çift istek).

Kaynak kararlar: OD41–OD43 (lifecycle), OD59 (auth_version), M14 §8 (onay adımları).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.leave.models import LeaveRequestStatus
from apps.organization.models import Department, Employee, EmployeeStatus
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro, rol_ver

pytestmark = pytest.mark.django_db

PAROLA_A = "Guclu-Parola-123"
#: `tests/leave_helpers.kullanici` hesap parolası (kadro kullanıcıları).
PAROLA_KADRO = "Gizli-Parola-123"
SABLON = Path(__file__).resolve().parents[1] / "apps/testui/templates/testui/app.html"


@pytest.fixture(autouse=True)
def _test_ui_ayarlari(settings) -> None:  # noqa: ANN001
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


@pytest.fixture
def client():  # noqa: ANN201
    from django.test import Client  # noqa: PLC0415

    return Client(enforce_csrf_checks=True)


@pytest.fixture
def hr(tenant_a, user_a):
    """TENANT kapsamlı `hr_manager` (suspend/restore/terminate izinleri)."""
    rol_ver(tenant_a, user_a, "hr_manager")
    return user_a


def _csrf(client) -> str:  # noqa: ANN001
    client.get("/test-ui/login/")
    return client.cookies["csrftoken"].value


def _giris(client, tenant, email: str, parola: str = PAROLA_A):  # noqa: ANN001
    return client.post(
        "/api/v1/auth/login",
        data={"tenant": tenant.slug, "email": email, "password": parola},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )


def _post(client, yol: str, govde: dict):  # noqa: ANN001
    return client.post(
        yol, data=govde, content_type="application/json", HTTP_X_CSRFTOKEN=_csrf(client)
    )


def _calisan_kur(tenant, no: str = "C-M1") -> Employee:  # noqa: ANN001
    with tenant_context(tenant.id):
        birim = Department.objects.create(tenant=tenant, code="GM", name="Genel Müdürlük")
        return Employee.objects.create(
            tenant=tenant, department=birim, employee_no=no, hire_date="2026-01-01"
        )


def _yol(calisan, islem: str) -> str:  # noqa: ANN001
    return f"/test-ui/api/employees/{calisan.pk}/lifecycle/{islem}"


# --- 1-2) askıya alma / geri alma --------------------------------------------


def test_suspend_ve_restore_pencere_yukuyle_calisir(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Pencere formu gerekçeyi gönderir: askıya al → geri al (aynı oturum)."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    calisan = _calisan_kur(tenant_a)

    aski = _post(client, _yol(calisan, "suspend"), {"reason": "disiplin süreci"})
    assert aski.status_code == 200, aski.content
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.SUSPENDED

    geri = _post(client, _yol(calisan, "restore"), {"reason": "süreç kapandı"})
    assert geri.status_code == 200, geri.content
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="employee.suspend").count() == 1
        assert AuditLog.objects.filter(action="employee.restore").count() == 1


def test_isten_cikis_sonrasi_restore_calisir(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Bildirilen akış: terminate → geri al (restore) artık çalışıyor."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    calisan = _calisan_kur(tenant_a, "C-M2")

    cikis = _post(
        client,
        _yol(calisan, "terminate"),
        {
            "termination_date": timezone.localdate().isoformat(),
            "termination_reason_code": "resignation",
            "reason": "istifa dilekçesi teslim alındı",
        },
    )
    assert cikis.status_code == 200, cikis.content
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.TERMINATED

    geri = _post(client, _yol(calisan, "restore"), {"reason": "kayıt düzeltmesi"})
    assert geri.status_code == 200, geri.content
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.ACTIVE


def test_gerekcesiz_lifecycle_400_ve_mesaj_guvenli(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Boş gerekçe sessiz kalmaz: güvenli 400 mesajı döner."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    calisan = _calisan_kur(tenant_a, "C-M3")
    yanit = _post(client, _yol(calisan, "suspend"), {"reason": ""})
    assert yanit.status_code == 400
    assert yanit.json()["error"]["message"]
    for yasak in ("Traceback", "psycopg", "SELECT "):
        assert yasak not in yanit.json()["error"]["message"]


# --- 3) izin kararı (red gerekçesi) ------------------------------------------


def test_red_karari_gerekceyle_http_yolunda_calisir(client, tenant_a, user_a) -> None:  # noqa: ANN001
    """Pencere formu ile red: karar gerçekten yazılır (sessiz no-op yok)."""
    with tenant_context(tenant_a.id):
        lot = kadro(tenant_a, user_a)
    # Talebi personel açar (kendi kaydı) → onaya sunulur.
    assert (
        _giris(client, tenant_a, lot["personel_user"].email, PAROLA_KADRO).status_code == 200
    )
    baslangic = timezone.localdate() + timedelta(days=10)
    olustur = _post(
        client,
        "/test-ui/api/leave-requests",
        {
            "employee_id": str(lot["personel"].pk),
            "leave_type_id": str(lot["turler"]["annual_leave"].pk),
            "start_date": baslangic.isoformat(),
            "end_date": (baslangic + timedelta(days=1)).isoformat(),
            "reason": "Modal testi",
            "submit": True,
        },
    )
    assert olustur.status_code == 201, olustur.content
    talep_id = olustur.json()["data"]["id"]

    # Birincil yönetici reddeder (red gerekçesi pencere formundan gelir).
    assert (
        _giris(client, tenant_a, lot["yonetici_user"].email, PAROLA_KADRO).status_code == 200
    )
    karar = _post(
        client,
        f"/test-ui/api/leave-requests/{talep_id}/decision",
        {"decision": "rejected", "reason": "ekip kapasitesi yetersiz"},
    )
    assert karar.status_code == 200, karar.content
    assert karar.json()["data"]["status"] == LeaveRequestStatus.REJECTED
    assert karar.json()["data"]["karar_gerekcesi"] == "ekip kapasitesi yetersiz"
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="leave.request.reject").count() == 1


# --- 4) şablon garantileri (ham diyalog yok, bağlama kapsamlı) ----------------


def test_sablonda_ham_dialog_yok_ve_baglamalar_kapsamli() -> None:
    """`prompt()`/`confirm()` yok; satır aksiyonları kapsayıcıya bağlanır."""
    govde = SABLON.read_text(encoding="utf-8")
    # Yorumlar hariç **kod**: ham tarayıcı diyalogları (gerekçe alma) kaldırıldı.
    kod = "\n".join(satir.split("//")[0] for satir in govde.splitlines())
    assert "prompt(" not in kod
    assert "confirm(" not in kod
    # Yeniden kullanılabilir gerekçe penceresi ve çağrı yerleri.
    assert "function gerekceModali(" in govde
    assert 'suspend: "Çalışanı askıya al"' in govde
    assert 'gerekceModali("Talebi reddet"' in govde
    assert 'gerekceModali("İzni iptal et"' in govde
    assert '"Rol atamasını onayla"' in govde
    assert '"Rol atamasını geri al"' in govde
    # Askıya alma/geri alma: ikinci onay penceresi sorulmaz (tek adım).
    assert "İşlem onayı" not in govde
    # Satır aksiyonları kendi kapsayıcısına bağlanır (çift dinleyici engeli).
    assert 'panelKok.querySelectorAll("[data-ok]")' in govde
    assert 'panelKok.querySelectorAll("[data-no]")' in govde
    assert 'onayKok.querySelectorAll("[data-ok]")' in govde
    assert 'onayKok.querySelectorAll("[data-no]")' in govde
    assert 'document.querySelectorAll("[data-ok]")' not in govde
    assert 'document.querySelectorAll("[data-no]")' not in govde
    # Kullanıcıya görünen başarı mesajları korunur.
    assert "Çalışan askıya alındı; erişimi kesildi." in govde
    assert "Çalışan aktifleştirildi; yeni giriş zorunlu." in govde


def test_izin_talebi_olusturma_uygulama_ici_onay_kullanir() -> None:
    """İzin talebi gönderilmeden önce modal onayı alınır."""
    govde = SABLON.read_text(encoding="utf-8")
    baslangic = govde.index('document.getElementById("lr-create").addEventListener')
    bitis = govde.index("\n    const saatAlanlariniGoster", baslangic)
    akis = govde[baslangic:bitis]
    assert "await onayModali(" in akis
    assert '"İzin talebini onayla"' in akis
    assert '"Talebi sun"' in akis
    assert "if (!onay) return;" in akis
    assert akis.index("if (!onay) return;") < akis.index('PERSOPYRO_UI.cagir("/test-ui/api/leave-requests"')


def test_kisisel_veri_kaydetme_uygulama_ici_onay_kullanir() -> None:
    """Kişisel veri POST'u confirmation olmadan gönderilmez."""
    govde = SABLON.read_text(encoding="utf-8")
    baslangic = govde.index('document.getElementById("pii-kaydet").addEventListener')
    bitis = govde.index("\n  async function rolAta", baslangic)
    akis = govde[baslangic:bitis]
    assert '"Kişisel verileri kaydet"' in akis
    assert "await onayModali(" in akis
    assert "if (!onay) return;" in akis
    assert akis.index("if (!onay) return;") < akis.index("/personal")


def test_rol_yetkileri_kaydetme_uygulama_ici_onay_kullanir() -> None:
    """Rol izinleri POST edilmeden önce değişiklik özeti onaylanır."""
    govde = SABLON.read_text(encoding="utf-8")
    baslangic = govde.index('const kaydet = document.getElementById("yetki-kaydet")')
    bitis = govde.index("\n    sayaclariGuncelle();", baslangic)
    akis = govde[baslangic:bitis]
    assert "await onayModali(" in akis
    assert '"Rol yetkilerini güncelle"' in akis
    assert "eklenen.length" in akis and "cikarilan.length" in akis
    assert "if (!onay) return;" in akis
    assert akis.index("if (!onay) return;") < akis.index("/permissions")


def test_izin_politikasi_ve_serbest_zaman_ayari_uygulama_ici_onay_kullanir() -> None:
    """Bakiye politikası ve serbest zaman gün ayarı POST öncesi onay ister."""
    govde = SABLON.read_text(encoding="utf-8")
    politika = govde[govde.index('document.querySelectorAll("[data-pol]")'):]
    politika = politika[:politika.index("\n  async function yukleIzinTurleri()")]
    assert 'await onayModali(' in politika
    assert '"İzin politikasını güncelle"' in politika
    assert politika.index("if (!onay) return;") < politika.index("/leave-policies/")

    saat = govde[govde.index('document.querySelectorAll("[data-tur-saat]")'):]
    saat = saat[:saat.index("\n  async function yukleIzinTaleplerim()")]
    assert 'await onayModali(' in saat
    assert '"Serbest zaman gün ayarı"' in saat
    assert saat.index("if (!onay) return;") < saat.index("/leave-types/")
