"""
İşten çıkış arayüz akışı — `/test-ui/api/employees/<id>/lifecycle/terminate`.

Kapsam (bu tur): çıkış formunun gönderdiği yükün gerçek HTTP yoluyla doğrulanması
(ISO tarih, machine code, gerekçe), güvenli 400 sözleşmesi, planlı çıkış
regresyonu ve şablonda ham `prompt()` kullanılmadığının kilitlenmesi.

Kaynak kararlar: OD41–OD43 (lifecycle), OD59 (auth_version), OD60 (atama).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.organization.models import Department, Employee, EmployeeStatus
from apps.tenancy.context import tenant_context
from tests.leave_helpers import rol_ver

pytestmark = pytest.mark.django_db

PAROLA_A = "Guclu-Parola-123"
SABLON = Path(__file__).resolve().parents[1] / "apps/testui/templates/testui/app.html"


@pytest.fixture(autouse=True)
def _test_ui_ayarlari(settings) -> None:  # noqa: ANN001
    """Arayüz açık; CSRF çerezi test istemcisinde http üzerinden taşınabilsin."""
    settings.TEST_UI_ENABLED = True
    settings.CSRF_COOKIE_SECURE = False


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiraci = request.getfixturevalue("tenant_a")
    with tenant_context(kiraci.id):
        yield


@pytest.fixture
def client():  # noqa: ANN201
    from django.test import Client  # noqa: PLC0415

    return Client(enforce_csrf_checks=True)


@pytest.fixture
def hr(tenant_a, user_a):
    """TENANT kapsamlı `hr_manager` (employee.terminate izni)."""
    rol_ver(tenant_a, user_a, "hr_manager")
    return user_a


def _csrf(client) -> str:  # noqa: ANN001
    client.get("/test-ui/login/")
    return client.cookies["csrftoken"].value


def _giris(client, tenant, email):  # noqa: ANN001
    return client.post(
        "/api/v1/auth/login",
        data={"tenant": tenant.slug, "email": email, "password": PAROLA_A},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=_csrf(client),
    )


def _post(client, yol: str, govde: dict):  # noqa: ANN001
    return client.post(
        yol, data=govde, content_type="application/json", HTTP_X_CSRFTOKEN=_csrf(client)
    )


def _calisan_kur(tenant, no: str = "C-T1"):  # noqa: ANN001
    with tenant_context(tenant.id):
        birim = Department.objects.create(tenant=tenant, code="GM", name="Genel Müdürlük")
        return birim, Employee.objects.create(
            tenant=tenant, department=birim, employee_no=no, hire_date="2026-01-01"
        )


def _yol(calisan) -> str:  # noqa: ANN001
    return f"/test-ui/api/employees/{calisan.pk}/lifecycle/terminate"


# --- 1) geçerli yük ---------------------------------------------------------


def test_t1_gecerli_yuk_terminate_eder(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Form yükü (ISO tarih + machine code + gerekçe) 200 döner ve durum yazılır."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    _, calisan = _calisan_kur(tenant_a)
    bugun = timezone.localdate()
    yanit = _post(
        client,
        _yol(calisan),
        {
            "termination_date": bugun.isoformat(),
            "termination_reason_code": "resignation",
            "reason": "istifa dilekçesi teslim alındı",
        },
    )
    assert yanit.status_code == 200
    calisan.refresh_from_db()
    assert calisan.status == EmployeeStatus.TERMINATED
    with tenant_context(tenant_a.id):
        kayitlar = AuditLog.objects.filter(action="employee.terminate", record_id=calisan.pk)
        assert kayitlar.count() == 1


# --- 2) ISO tarih ayrıştırma ------------------------------------------------


def test_t2_iso_tarih_dogru_parse_edilir(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """`<input type="date">` değeri ISO'dur; backend aynı günü yazar."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    _, calisan = _calisan_kur(tenant_a, "C-T2")
    tarih = timezone.localdate() - timedelta(days=3)
    yanit = _post(
        client,
        _yol(calisan),
        {
            "termination_date": tarih.isoformat(),
            "termination_reason_code": "employer_termination",
            "reason": "fesih bildirimi tebliğ edildi",
        },
    )
    assert yanit.status_code == 200
    calisan.refresh_from_db()
    assert calisan.termination_date == tarih
    assert yanit.json()["data"]["termination_date"] == tarih.isoformat()


# --- 3) geçersiz yük → güvenli 400 -----------------------------------------


@pytest.mark.parametrize(
    ("govde", "beklenen"),
    [
        ({"termination_date": "16.09.2026", "termination_reason_code": "resignation",
          "reason": "x"}, "ISO"),
        ({"termination_date": "", "termination_reason_code": "resignation", "reason": "x"},
         "zorunludur"),
        ({"termination_date": "2026-09-16", "termination_reason_code": "istifa", "reason": "x"},
         "Geçersiz çıkış nedeni"),
        ({"termination_date": "2026-09-16", "termination_reason_code": "resignation",
          "reason": ""}, "Gerekçe zorunludur"),
    ],
)
def test_t3_gecersiz_yuk_guvenli_400(client, tenant_a, user_a, hr, govde, beklenen) -> None:  # noqa: ANN001
    """Hatalı girdi 400 + `bad_request` + insan-okunur, iç ayrıntı sızdırmayan mesaj."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    _, calisan = _calisan_kur(tenant_a, "C-T3")
    yanit = _post(client, _yol(calisan), govde)
    assert yanit.status_code == 400
    hata = yanit.json()["error"]
    assert hata["code"] == "bad_request"
    assert beklenen in hata["message"]
    # Ham traceback/istisna sınıfı adı sızmaz.
    assert "Traceback" not in hata["message"]
    assert "apps." not in hata["message"]


# --- 4) machine code korunur ------------------------------------------------


def test_t4_cikis_kodu_backend_machine_code_olarak_korunur(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """UI Türkçe etiket gösterse de backend'e **code** gider ve code saklanır."""
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    _, calisan = _calisan_kur(tenant_a, "C-T4")
    yanit = _post(
        client,
        _yol(calisan),
        {
            "termination_date": timezone.localdate().isoformat(),
            "termination_reason_code": "mutual_agreement",
            "reason": "karşılıklı anlaşma protokolü",
        },
    )
    assert yanit.status_code == 200
    calisan.refresh_from_db()
    assert calisan.termination_reason_code == "mutual_agreement"
    from apps.organization.models import TerminationReason  # noqa: PLC0415

    assert {k for k, _ in TerminationReason.choices} == {
        "resignation", "employer_termination", "mutual_agreement",
        "contract_end", "retirement", "death", "other",
    }


# --- 5) planlı çıkış regresyonu --------------------------------------------


def test_t5_gelecek_tarih_planli_cikis_regresyonu(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """Gelecek tarih: çalışan aktif kalır, çıkış süreci görünür; bugün: terminated."""
    from apps.organization.lifecycle import notice_etiketi  # noqa: PLC0415

    assert _giris(client, tenant_a, user_a.email).status_code == 200
    birim, planli = _calisan_kur(tenant_a, "C-T5")
    gelecek = timezone.localdate() + timedelta(days=30)
    yanit = _post(
        client,
        _yol(planli),
        {
            "termination_date": gelecek.isoformat(),
            "termination_reason_code": "resignation",
            "reason": "ihbar süreci başlatıldı",
        },
    )
    assert yanit.status_code == 200
    planli.refresh_from_db()
    assert planli.status == EmployeeStatus.ACTIVE  # durum değişmez
    assert planli.termination_date == gelecek
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(
            action="employee.termination.schedule", record_id=planli.pk
        ).count() == 1
        etiket = notice_etiketi(planli, bugun=timezone.localdate())
    assert etiket is not None and etiket["label"] == "Çıkış sürecinde"

    # Bugünün tarihi: anında çıkış (immediate termination) korunur.
    with tenant_context(tenant_a.id):
        ani = Employee.objects.create(
            tenant=tenant_a, department=birim, employee_no="C-T5B", hire_date="2026-01-01"
        )
    yanit = _post(
        client,
        _yol(ani),
        {
            "termination_date": timezone.localdate().isoformat(),
            "termination_reason_code": "other",
            "reason": "derhal çıkış",
        },
    )
    assert yanit.status_code == 200
    ani.refresh_from_db()
    assert ani.status == EmployeeStatus.TERMINATED


# --- 6) şablon kilidi: ham browser penceresi yok ---------------------------


def test_t7_cikis_sureci_listesinde_cikis_tarihi_dolu(client, tenant_a, user_a, hr) -> None:  # noqa: ANN001
    """`Çalışanlar → İşten Çıkış / Çıkış Süreci` satırı çıkış tarihini gösterir.

    Liste satırında `lifecycle` bulunmaz; tarih liste özetinden gelir (ISO +
    okunur biçim). Regresyon: sütun "—" göstermemeli.
    """
    assert _giris(client, tenant_a, user_a.email).status_code == 200
    _, planli = _calisan_kur(tenant_a, "C-T7")
    gelecek = timezone.localdate() + timedelta(days=15)
    assert _post(
        client,
        _yol(planli),
        {
            "termination_date": gelecek.isoformat(),
            "termination_reason_code": "resignation",
            "reason": "ihbar süreci",
        },
    ).status_code == 200

    liste = client.get("/test-ui/api/employees")
    assert liste.status_code == 200
    satir = next(r for r in liste.json()["data"] if r["employee_no"] == "C-T7")
    assert satir["termination_date"] == gelecek.isoformat()
    assert satir["termination_date_display"] == gelecek.strftime("%d.%m.%Y")


def test_t6_sablonda_ham_prompt_ve_iso_date_picker() -> None:
    """Çıkış formu: `prompt()` yok, date picker + Türkçe etiketler var."""
    govde = SABLON.read_text(encoding="utf-8")
    assert 'prompt("Son çalışma günü' not in govde
    assert "Çıkış nedeni kodu (resignation" not in govde
    assert '<input id="lc-cikis-tarih" type="date"' in govde
    assert "Çalışanı İşten Çıkar" in govde
    assert "İşten çıkış kodu" in govde
    assert "İşten Çıkışı Onayla" in govde
    assert "İşten çıkış işlemi kaydedildi." in govde
    assert "İşten çıkış işlemi kaydedilemedi." in govde
    # Türkçe etiketler; ham enum değerleri yalnız `value` olarak
    for kod, etiket in (
        ("resignation", "İstifa"),
        ("employer_termination", "İşveren tarafından fesih"),
        ("mutual_agreement", "Karşılıklı anlaşma"),
        ("contract_end", "Sözleşme bitişi"),
        ("retirement", "Emeklilik"),
        ("death", "Vefat"),
        ("other", "Diğer"),
    ):
        assert f'["{kod}", "{etiket}"]' in govde
