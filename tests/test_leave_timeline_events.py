"""
M14/P1-B — izin timeline olayları (Yaşananlar) + izin hakkı geri bildirimi testleri.

Kapsam:

  * Çalışanın izin olayları timeline'da görünür: oluşturuldu / onaylandı /
    reddedildi / iptal edildi / kullanıldı.
  * İzin hakkı (hakediş) ve bakiye düzeltmesi olayları timeline'da görünür.
  * İzolasyon: başka çalışanın olayı sızmaz (kaynak sorgu `employee` ile daraltılır).
  * Yetki: çalışan **kendi** timeline'ını görebilir; izin yoksa bakiye değerleri
    yanıta **konmaz** (maskelenir).
  * İzin hakkı işlemi sessiz kalmaz: başarı/hata geri bildirimi sunucudan döner.

Tarihsel bakiye anlık görüntüsü (karar anındaki bakiye) **yoktur**; bu testler
bugünkü bakiyeyi geçmiş kararın bakiyesi gibi doğrulamaz.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.leave.models import ApprovalStepStatus
from apps.leave.services import (
    adjust_leave_balance,
    cancel_leave_request,
    create_leave_request,
    decide_approval_step,
    mark_leave_taken,
)
from apps.organization.history import employee_history
from apps.tenancy.context import tenant_context
from tests.leave_helpers import calisan, kadro, kullanici, rol_ver
from tests.testui_helpers import get, giris, post

PAROLA = "Gizli-Parola-123"


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
def lot(tenant_a, user_a):  # noqa: ANN001, ANN201
    with tenant_context(tenant_a.id):
        return kadro(tenant_a, user_a)


def _talep(lot, *, gun: int = 5, submit: bool = True, baslangic: date | None = None):  # noqa: ANN001, ANN202
    baslangic = baslangic or (timezone.localdate() + timedelta(days=7))
    with tenant_context(lot["personel"].tenant_id):
        return create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=baslangic,
            end_date=baslangic + timedelta(days=gun - 1),
            reason="Timeline testi",
            submit=submit,
        )


def _onayla(request, actor):  # noqa: ANN001, ANN202
    with tenant_context(request.tenant_id):
        decide_approval_step(
            actor=actor, request=request, decision=ApprovalStepStatus.APPROVED
        )
        return decide_approval_step(
            actor=actor, request=request, decision=ApprovalStepStatus.APPROVED
        )


def _olaylar(lot, *, tip: str | None = None, etiket: str | None = None) -> list[dict]:  # noqa: ANN001, ANN202
    with tenant_context(lot["personel"].tenant_id):
        olaylar = employee_history(lot["personel"])
    if tip is not None:
        olaylar = [o for o in olaylar if o["type"] == tip]
    if etiket is not None:
        olaylar = [o for o in olaylar if o["label"] == etiket]
    return olaylar


def _giris_yap(client, lot, anahtar: str = "admin"):  # noqa: ANN001, ANN202
    kullanici = lot[anahtar]
    yanit = giris(client, kullanici.tenant, kullanici.email, PAROLA)
    assert yanit.status_code == 200
    return yanit


def _yeni_calisan(lot):  # noqa: ANN001, ANN202
    with tenant_context(lot["birimler"]["muhasebe"].tenant_id):
        return calisan(
            lot["birimler"]["muhasebe"].tenant,
            lot["birimler"]["muhasebe"],
            no="TL-1",
            hire_date=date(2026, 9, 13),
        )


# --- 1–4) izin talebi olayları ------------------------------------------------


def test_timeline_izin_olusturuldu_gorunur(lot) -> None:  # noqa: ANN001
    """(1) İzin talebi oluşturuldu olayı insan-okunur biçimde görünür."""
    talep = _talep(lot, submit=False)
    olaylar = _olaylar(lot, tip="leave_created")
    assert len(olaylar) == 1
    olay = olaylar[0]
    assert olay["label"] == "Yıllık İzin talebi oluşturuldu"
    assert olay["date"] == talep.created_at.date().isoformat()
    assert olay["detail"]["Tür"] == "Yıllık İzin"
    assert "· 5 gün" in olay["summary"]
    assert talep.start_date.strftime("%d.%m.%Y") in olay["summary"]


def test_timeline_izin_onaylandi_gorunur(lot) -> None:  # noqa: ANN001
    """(2) Onay olayı `decided_at` ile görünür; aktör iş kimliğiyle yazılır."""
    talep = _talep(lot)
    karar = _onayla(talep, lot["admin"])
    olay = _olaylar(lot, tip="leave_decision")[0]
    assert olay["label"] == "Yıllık İzin onaylandı"
    assert olay["date"] == karar.decided_at.date().isoformat()
    assert olay["detail"]["Tür"] == "Yıllık İzin"

    # Aktör eşlemesi: karar veren kullanıcının **çalışan kaydı** varsa çalışan no
    # gösterilir (ad/e-posta gibi K1 değerler timeline'a yazılmaz). Testte nihai
    # kararı `admin` verir; bu kullanıcının çalışan kaydı olmadığı için eşleme
    # ayrıca çalışan kaydı olan kullanıcı ile doğrulanır.
    with tenant_context(lot["personel"].tenant_id):
        from apps.leave.models import LeaveRequest  # noqa: PLC0415

        LeaveRequest.objects.filter(pk=talep.pk).update(decided_by=lot["yonetici_user"])
    olay = _olaylar(lot, tip="leave_decision")[0]
    assert olay["detail"]["Yetkili"] == lot["yonetici"].employee_no
    assert f"Yetkili: {lot['yonetici'].employee_no}" in olay["summary"]


def test_timeline_izin_reddedildi_gerekce_ile_gorunur(lot) -> None:  # noqa: ANN001
    """(3) Red olayı, onay adımındaki gerekçesiyle görünür."""
    talep = _talep(lot)
    with tenant_context(lot["personel"].tenant_id):
        reddedilen = decide_approval_step(
            actor=lot["yonetici_user"],
            request=talep,
            decision=ApprovalStepStatus.REJECTED,
            reason="Ekip kapasitesi yetersiz",
        )
    assert reddedilen.status == "rejected"
    olay = _olaylar(lot, tip="leave_decision")[0]
    assert olay["label"] == "Yıllık İzin reddedildi"
    assert olay["detail"]["Gerekçe"] == "Ekip kapasitesi yetersiz"
    assert olay["summary"].startswith("Gerekçe: Ekip kapasitesi yetersiz")


def test_timeline_izin_iptal_edildi_gorunur(lot) -> None:  # noqa: ANN001
    """(4) İptal/geri çekme olayı gerekçe ve iptal edenle görünür."""
    talep = _talep(lot)
    with tenant_context(lot["personel"].tenant_id):
        cancel_leave_request(
            actor=lot["personel_user"], request=talep, reason="Planım değişti"
        )
    olay = _olaylar(lot, tip="leave_canceled")[0]
    assert olay["label"] == "Yıllık İzin talebi iptal edildi"
    assert olay["detail"]["Gerekçe"] == "Planım değişti"
    assert olay["detail"]["İptal eden"] == lot["personel"].employee_no


def test_timeline_kullanilmis_izin_olayi(lot) -> None:  # noqa: ANN001
    """Onaylı izin 'kullanıldı' işaretlenince olay (denetim zamanı) görünür."""
    talep = _talep(lot, gun=2, baslangic=timezone.localdate())
    onayli = _onayla(talep, lot["admin"])
    with tenant_context(lot["personel"].tenant_id):
        mark_leave_taken(actor=lot["admin"], request=onayli)
    olay = _olaylar(lot, tip="leave_taken")[0]
    assert olay["label"] == "Yıllık İzin kullanıldı"
    assert olay["date"] == timezone.localdate().isoformat()
    assert olay["summary"].endswith("gün")


def test_timeline_kronolojik_siralanir(lot) -> None:  # noqa: ANN001
    """Timeline eski → yeni sıralıdır (karışık olay tipleri arasında)."""
    talep = _talep(lot)
    _onayla(talep, lot["admin"])
    with tenant_context(lot["personel"].tenant_id):
        olaylar = employee_history(lot["personel"])
    tarihler = [o["date"] for o in olaylar]
    assert tarihler == sorted(tarihler)


# --- 5–6) izin hakkı / bakiye olayları ----------------------------------------


def test_timeline_izin_hakki_tanimlandi_gorunur(lot) -> None:  # noqa: ANN001
    """(5) Hakediş (hak tanımı) olayı timeline'da görünür."""
    olaylar = _olaylar(lot, tip="leave_entitlement")
    assert olaylar, "hakediş olayı timeline'da yok"
    olay = olaylar[0]
    assert olay["label"] == "Yıllık İzin hakkı tanımlandı"
    assert "14 gün" in olay["summary"]
    assert olay["detail"]["Tür"] == "Yıllık İzin"
    assert olay["detail"]["Değişim"] == "14 gün"


def test_timeline_bakiye_duzeltmesi_gorunur(lot) -> None:  # noqa: ANN001
    """(6) Gerekçeli bakiye düzeltmesi olayı değişim/gerekçe/aktör ile görünür."""
    yil = timezone.localdate().year
    with tenant_context(lot["personel"].tenant_id):
        adjust_leave_balance(
            actor=lot["admin"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            period_year=yil,
            delta_days=Decimal("2.0"),
            reason="Kıdem düzeltmesi",
        )
        olaylar = employee_history(lot["personel"])
    duzeltmeler = [o for o in olaylar if o["type"] == "leave_balance"]
    assert len(duzeltmeler) == 1
    olay = duzeltmeler[0]
    assert olay["label"] == "Yıllık İzin bakiyesi düzeltildi"
    assert olay["summary"] == "+2 gün"
    assert olay["detail"]["Değişim"] == "+2 gün"
    assert olay["detail"]["Gerekçe"] == "Kıdem düzeltmesi"
    assert olay["detail"]["Yıl"] == str(yil)
    # Tarihsel bakiye anlık görüntüsü yok → "önceki/sonraki bakiye" gösterilmez.
    assert "Önceki bakiye" not in olay["detail"]
    assert "Sonraki bakiye" not in olay["detail"]


# --- 7) izolasyon --------------------------------------------------------------


def test_timeline_baska_calisanin_olayi_sizmaz(lot) -> None:  # noqa: ANN001
    """(7) Başka çalışanın izin olayı kendi timeline'ında görünmez."""
    talep = _talep(lot)
    with tenant_context(lot["personel"].tenant_id):
        kardes_olaylari = employee_history(lot["kardes"])

    tipler = {o["type"] for o in kardes_olaylari}
    assert "leave_created" not in tipler
    assert "leave_decision" not in tipler
    aralik = f"{talep.start_date.strftime('%d.%m.%Y')} – {talep.end_date.strftime('%d.%m.%Y')}"
    assert all(aralik not in str(o.get("summary", "")) for o in kardes_olaylari)
    assert all(talep.pk.hex not in str(o) for o in kardes_olaylari)


# --- 8–9) yetki / maskeleme ----------------------------------------------------


def test_timeline_calisan_kendi_gecmisini_gorebilir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(8) Çalışan kendi izin/timeline olaylarını görebilir (kendi kaydı)."""
    _talep(lot)
    _giris_yap(csrf_client, lot, "personel_user")
    yanit = get(csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history")
    assert yanit.status_code == 200, yanit.content
    veri = yanit.json()["data"]
    assert veri["employee_no"] == lot["personel"].employee_no
    assert any(o["type"] == "leave_created" for o in veri["events"])


def test_timeline_yetkisiz_ve_masked_deger(client, lot, tenant_a, user_a, csrf_client) -> None:  # noqa: ANN001
    """(8) Yetkisiz kullanıcı başka çalışanın timeline'ını alamaz."""
    _talep(lot)
    _giris_yap(csrf_client, lot, "kardes_user")  # kapsam: SELF
    yanit = get(csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history")
    assert yanit.status_code in (403, 404)

    # `employee.history.read` var ama `leave.balance.read` yok → bakiye değerleri
    # yanıta **konmaz** (maskelenir); izin talebi olayları normal döner.
    hr = rol_ver(tenant_a, kullanici(tenant_a, "timeline.hr@ornek.test", "İK"), "hr_manager")
    assert giris(csrf_client, tenant_a, hr.email, PAROLA).status_code == 200
    yanit = get(csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history")
    assert yanit.status_code == 200, yanit.content
    olaylar = yanit.json()["data"]["events"]
    bakiyeler = [o for o in olaylar if o["type"] == "leave_entitlement"]
    assert bakiyeler, "hakediş olayı yok"
    for olay in bakiyeler:
        assert olay["summary"] == "***"
        assert set(olay["detail"].values()) == {"***"}
        assert "14" not in str(olay["detail"])
    assert any(o["type"] == "leave_created" for o in olaylar)  # izin olayları döner


# --- 10-11) izin hakkı işlem geri bildirimi -----------------------------------


def test_entitlement_basarili_isllem_geri_bildirimi(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(9) Hak tanımlama başarısı açık mesajla döner (sessiz kalmaz)."""
    _giris_yap(csrf_client, lot)
    yeni = _yeni_calisan(lot)
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{yeni.pk}/entitlements",
        {"leave_type_id": str(tur.pk), "days": "14", "reason": "Başlangıç hakkı"},
    )
    assert yanit.status_code == 200, yanit.content
    govde = yanit.json()
    assert govde["data"]["durum"] == "hakedis_tanimlandi"
    assert govde["data"]["mesaj"] == "İzin hakkı başarıyla tanımlandı."

    # Aynı işlem timeline'a da yansır (olay üretimi ile geri bildirim tutarlı).
    with tenant_context(lot["personel"].tenant_id):
        olaylar = employee_history(yeni)
    assert any(o["type"] == "leave_entitlement" for o in olaylar)


def test_entitlement_hata_geri_bildirimi_guvenli(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(10) Hata durumunda güvenli mesaj döner (ham exception/SQL yok)."""
    _giris_yap(csrf_client, lot)
    with tenant_context(lot["personel"].tenant_id):
        tur = lot["turler"]["annual_leave"]
    yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{lot['personel'].pk}/entitlements",
        {"leave_type_id": str(tur.pk), "days": "14", "reason": "kısa"},
    )
    assert yanit.status_code == 400
    hata = yanit.json()["error"]
    assert hata["message"]
    assert "gerekçe" in hata["message"].lower()
    for yasak in ("Traceback", "SELECT ", "psycopg", "raise "):
        assert yasak not in hata["message"]
    # Yetkisiz kullanıcı da sessiz kalmaz: açık hata kodu döner.
    _giris_yap(csrf_client, lot, "kardes_user")
    yasak_yanit = post(
        csrf_client,
        f"/test-ui/api/employees/{lot['personel'].pk}/entitlements",
        {"leave_type_id": str(tur.pk), "days": "30", "reason": "Yetkisiz deneme"},
    )
    assert yasak_yanit.status_code in (403, 404)

# --- §7) reusable geri bildirim altyapısı --------------------------------------


def test_feedback_bolgesi_erisilebilir_ve_kapatilabilir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(§7) Geri bildirim bölgesi aria-live; hata kullanıcı kapatabilir."""
    _giris_yap(csrf_client, lot)
    yanit = csrf_client.get("/test-ui/")
    assert yanit.status_code == 200
    html = yanit.content.decode()
    assert 'id="global-msg"' in html
    assert 'aria-live="polite"' in html          # erişilebilirlik
    assert "MSG_BASARI_SURESI_MS" in html        # başarı 5 sn sonra kapanır
    assert "msg-kapat" in html                   # hata kapatma düğmesi
    assert "e.summary" in html                   # Yaşananlar insan-okunur özet satırı
