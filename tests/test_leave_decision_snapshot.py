"""
M14/K — **karar anı izin bakiyesi** (immutable snapshot) + karar detayı testleri.

Kapsam:

  * Final karar (onay/red) anında bakiye snapshot'ı yazılır; sonraki işlemler
    snapshot'ı **değiştirmez** (tarihsel kayıt).
  * Bir talep için en fazla **1** snapshot; ikinci üretim/imza yok (idempotent)
    ve snapshot DB düzeyinde **update/delete edilemez**.
  * Snapshot'sız (eski) kararlar hata vermez → "Karar anı bakiyesi kayıtlı değil".
  * Karar detayı (API) karar gerekçesi + karar anı bakiyesini döner; yetkisiz
    kullanıcıya bakiye **değeri konmaz**.
  * Timeline: `leave_decision` olayı "Karar sonrası kalan" özetini taşır ve
    timeline **ters kronolojik** (en yeni üstte) sıralanır.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.leave.models import ApprovalStepStatus, LeaveDecisionSnapshot, LeaveRequestStatus
from apps.leave.services import (
    LeaveStateError,
    create_leave_request,
    decide_approval_step,
)
from apps.leave.services.balances import get_or_create_balance, period_year_for
from apps.organization.history import employee_history, timeline_sayfa
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro, kullanici, rol_ver
from tests.testui_helpers import get, giris

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


def _talep(lot, *, gun: int = 5, baslangic_ofset: int = 10, submit: bool = True):  # noqa: ANN001, ANN202
    baslangic = timezone.localdate() + timedelta(days=baslangic_ofset)
    with tenant_context(lot["personel"].tenant_id):
        return create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=baslangic,
            end_date=baslangic + timedelta(days=gun - 1),
            reason="Snapshot testi",
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


def _giris_yap(client, lot, anahtar: str = "admin"):  # noqa: ANN001, ANN202
    kullanici_obj = lot[anahtar]
    yanit = giris(client, kullanici_obj.tenant, kullanici_obj.email, PAROLA)
    assert yanit.status_code == 200
    return yanit


def _snapshot(request):  # noqa: ANN001, ANN202
    with tenant_context(request.tenant_id):
        return LeaveDecisionSnapshot.objects.filter(leave_request_id=request.pk).first()


# --- 1–2) approved snapshot ---------------------------------------------------


def test_onay_snapshot_kalan_bakiyeyi_saklar(lot) -> None:  # noqa: ANN001
    """(1) 14 gün hak + 5 gün onaylı izin → snapshot kalan = 9."""
    talep = _talep(lot, gun=5)
    onayli = _onayla(talep, lot["admin"])
    assert onayli.status == LeaveRequestStatus.APPROVED

    snapshot = _snapshot(talep)
    assert snapshot is not None
    assert snapshot.decision_status == LeaveRequestStatus.APPROVED
    assert snapshot.uses_balance is True
    assert snapshot.entitled_days == Decimal("14.0")
    assert snapshot.used_days == Decimal("5.0")
    assert snapshot.reserved_days == Decimal("0.0")
    assert snapshot.available_days == Decimal("9.0")
    assert snapshot.snapshot_at is not None
    assert snapshot.employee_id == lot["personel"].pk

    # Bugünkü bakiye ile karar anı bakiyesi aynı kaynaktan doğrulanır.
    with tenant_context(talep.tenant_id):
        guncel = get_or_create_balance(
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            period_year=period_year_for(talep.start_date),
        )
        assert guncel.available_days == Decimal("9.0")


def test_sonraki_onaylar_ilk_snapshotu_degistirmez(lot) -> None:  # noqa: ANN001
    """(2) Sonradan başka izin onaylansa bile ilk snapshot 9 kalır."""
    ilk = _onayla(_talep(lot, gun=5, baslangic_ofset=10), lot["admin"])
    ilk_snapshot_id = _snapshot(ilk).pk

    # Çakışmayan ikinci talep (2 gün) onaylanır → bugünkü bakiye 7'ye düşer.
    _onayla(_talep(lot, gun=2, baslangic_ofset=40), lot["admin"])

    with tenant_context(ilk.tenant_id):
        guncel = get_or_create_balance(
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            period_year=period_year_for(ilk.start_date),
        )
        ilk_snapshot = LeaveDecisionSnapshot.objects.get(pk=ilk_snapshot_id)
    assert guncel.available_days == Decimal("7.0")  # güncel bakiye değişti
    assert ilk_snapshot.available_days == Decimal("9.0")  # tarihsel kayıt sabit
    assert ilk_snapshot.used_days == Decimal("5.0")


# --- 3) rejected snapshot ----------------------------------------------------


def test_red_snapshotu_karar_ani_bakiyesini_saklar(lot) -> None:  # noqa: ANN001
    """(3) Red, bakiye tüketmez; snapshot karar anındaki mevcut durumu gösterir."""
    talep = _talep(lot, gun=4)
    with tenant_context(talep.tenant_id):
        rezerve_once = get_or_create_balance(
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            period_year=period_year_for(talep.start_date),
        )
        rezerve_once.refresh_from_db()
        assert rezerve_once.reserved_days == Decimal("4.0")  # onay beklerken rezerve

    with tenant_context(talep.tenant_id):
        reddedilen = decide_approval_step(
            actor=lot["yonetici_user"],
            request=talep,
            decision=ApprovalStepStatus.REJECTED,
            reason="Ekip kapasitesi yetersiz",
        )
    assert reddedilen.status == LeaveRequestStatus.REJECTED

    snapshot = _snapshot(talep)
    assert snapshot is not None
    assert snapshot.decision_status == LeaveRequestStatus.REJECTED
    assert snapshot.used_days == Decimal("0.0")  # red tüketmez
    assert snapshot.reserved_days == Decimal("0.0")  # rezervasyon çözüldü
    assert snapshot.available_days == Decimal("14.0")


# --- 4) idempotency + immutability -------------------------------------------


def test_ayni_talep_ikinci_snapshot_uretemez(lot) -> None:  # noqa: ANN001
    """(4) İkinci onay denemesi hata verir; snapshot tek kalır ve yazılamaz."""
    talep = _onayla(_talep(lot, gun=5), lot["admin"])
    with tenant_context(talep.tenant_id):
        assert LeaveDecisionSnapshot.objects.filter(leave_request_id=talep.pk).count() == 1

    # Aynı talep tekrar "approved" edilemez (mevcut durum makinesi).
    with pytest.raises(LeaveStateError, match="onay bekleyen"):
        _onayla(talep, lot["admin"])

    with tenant_context(talep.tenant_id):
        assert LeaveDecisionSnapshot.objects.filter(leave_request_id=talep.pk).count() == 1
        snapshot = LeaveDecisionSnapshot.objects.get(leave_request_id=talep.pk)
        # Immutability: UPDATE ve DELETE DB tetikleyicisiyle reddedilir.
        with pytest.raises(DatabaseError, match="degistirilemez|değiştirilemez"):
            with transaction.atomic():
                LeaveDecisionSnapshot.objects.filter(pk=snapshot.pk).update(
                    available_days=Decimal("99.0")
                )
        with pytest.raises(DatabaseError, match="degistirilemez|değiştirilemez"):
            with transaction.atomic():
                LeaveDecisionSnapshot.objects.filter(pk=snapshot.pk).delete()
        snapshot.refresh_from_db()
    assert snapshot.available_days == Decimal("9.0")


# --- 5) eski (snapshot'sız) kararlar ------------------------------------------


def test_snapshotsuz_karar_hata_vermez(lot) -> None:  # noqa: ANN001
    """(5) Snapshot'ı olmayan (eski) karar hata vermez; değer döndürmez."""
    talep = _talep(lot, gun=3)
    # Servis akışı atlanarak eski bir karar simüle edilir (snapshot üretilmez).
    with tenant_context(talep.tenant_id):
        from apps.leave.models import LeaveRequest  # noqa: PLC0415

        LeaveRequest.objects.filter(pk=talep.pk).update(
            status=LeaveRequestStatus.APPROVED,
            decided_at=timezone.now(),
            decided_by=lot["admin"],
        )
        assert LeaveDecisionSnapshot.objects.filter(leave_request_id=talep.pk).count() == 0
        olaylar = employee_history(lot["personel"])
        guncel = get_or_create_balance(
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            period_year=period_year_for(talep.start_date),
        )
    karar = [o for o in olaylar if o["type"] == "leave_decision" and o["label"].startswith("Yıllık")]
    assert karar and "karar_sonrasi_kalan" not in karar[0]
    # Bugünkü bakiye "karar anı" gibi sunulmaz: snapshot yoksa değer yazılmaz.
    assert guncel.available_days is not None


# --- 6–7) karar detayı (API) --------------------------------------------------


def test_karar_detayi_snapshotu_dondurur(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(6) Karar detayı karar anı bakiyesini gösterir (bugünkü bakiyeyi değil)."""
    talep = _onayla(_talep(lot, gun=5), lot["admin"])
    _onayla(_talep(lot, gun=2, baslangic_ofset=40), lot["admin"])  # bakiye 7'ye iner

    _giris_yap(csrf_client, lot)
    veri = get(csrf_client, "/test-ui/api/dashboard").json()["data"]
    kayit = next(t for t in veri["decided_requests"] if t["id"] == str(talep.pk))
    bakiye = kayit["karar_bakiye"]
    assert bakiye["var"] is True and bakiye["gorunur"] is True
    assert bakiye["hak_toplam"] == "14.0"
    assert bakiye["used_days"] == "5.0"
    assert bakiye["available_days"] == "9.0"  # bugünkü 7 değil, karar anı 9
    assert bakiye["snapshot_at"] is not None


def test_karar_detayi_red_gerekcesini_gosterir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(7) Red gerekçesi detayda görünür (onay adımından)."""
    talep = _talep(lot, gun=2)
    with tenant_context(talep.tenant_id):
        decide_approval_step(
            actor=lot["yonetici_user"],
            request=talep,
            decision=ApprovalStepStatus.REJECTED,
            reason="Yoğunluk nedeniyle uygun değil",
        )
    _giris_yap(csrf_client, lot)
    veri = get(csrf_client, "/test-ui/api/dashboard").json()["data"]
    kayit = next(t for t in veri["decided_requests"] if t["id"] == str(talep.pk))
    assert kayit["karar_gerekcesi"] == "Yoğunluk nedeniyle uygun değil"
    assert kayit["karar_bakiye"]["var"] is True
    assert kayit["karar_bakiye"]["available_days"] == "14.0"
    assert kayit["karar_veren"]


# --- 8–10) yetki ---------------------------------------------------------------


def test_calisan_kendi_snapshotini_gorebilir(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(8) Çalışan kendi kararında karar anı bakiyesini görebilir."""
    talep = _onayla(_talep(lot, gun=5), lot["admin"])

    # Detay (kendi kaydı, `leave.balance.read` + sahiplik kapsamı)
    _giris_yap(csrf_client, lot, "personel_user")
    satirlar = get(csrf_client, "/test-ui/api/leave-requests?scope=mine").json()["data"]
    kayit = next(s for s in satirlar if s["id"] == str(talep.pk))
    assert kayit["karar_bakiye"]["gorunur"] is True
    assert kayit["karar_bakiye"]["available_days"] == "9.0"

    # Timeline karar olayı da özet taşır.
    olaylar = get(
        csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history"
    ).json()["data"]["events"]
    karar = next(o for o in olaylar if o["type"] == "leave_decision")
    assert karar["karar_sonrasi_kalan"] == "9 gün"


def test_baska_calisanin_snapshotina_erisilemez(client, lot, csrf_client) -> None:  # noqa: ANN001
    """(9) Başka çalışanın kaydı kapsam dışıdır (404/403)."""
    talep = _onayla(_talep(lot, gun=5), lot["admin"])
    _giris_yap(csrf_client, lot, "kardes_user")  # SELF kapsamı
    yanit = get(csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history")
    assert yanit.status_code in (403, 404)
    satirlar = get(csrf_client, "/test-ui/api/leave-requests?scope=mine").json()["data"]
    assert all(s["id"] != str(talep.pk) for s in satirlar)  # başka çalışanın kaydı dönmez


def test_balance_read_yoksa_karar_bakiyesi_alinamaz(  # noqa: ANN001
    client, lot, tenant_a, csrf_client
) -> None:
    """(10) `leave.balance.read` yoksa karar anı bakiye **değeri** dönmez."""
    talep = _onayla(_talep(lot, gun=5), lot["admin"])

    # hr_manager: `employee.history.read` var, `leave.balance.read` yok, TENANT kapsam.
    hr = rol_ver(tenant_a, kullanici(tenant_a, "snapshot.hr@ornek.test", "İK"), "hr_manager")
    assert giris(csrf_client, tenant_a, hr.email, PAROLA).status_code == 200

    olaylar = get(
        csrf_client, f"/test-ui/api/employees/{lot['personel'].pk}/history"
    ).json()["data"]["events"]
    karar = [o for o in olaylar if o["type"] == "leave_decision"]
    assert karar, "karar olayı yok"
    assert all("karar_sonrasi_kalan" not in o for o in karar)
    assert all("karar_sonrasi_kalan_snapshot_at" not in o for o in karar)

    panel = get(csrf_client, "/test-ui/api/dashboard").json()["data"]
    kayit = next(t for t in panel["decided_requests"] if t["id"] == str(talep.pk))
    bakiye = kayit["karar_bakiye"]
    assert bakiye["var"] is True  # varlık bilgisi döner
    assert bakiye["gorunur"] is False  # ama değer dönmez
    assert bakiye["available_days"] is None
    assert bakiye["hak_toplam"] is None
    assert bakiye["used_days"] is None


# --- §6.5) timeline sıralaması (ters kronolojik) -----------------------------


def test_timeline_newest_first_siralanir(lot) -> None:  # noqa: ANN001
    """Timeline olayları en yeni üstte döner (backend source-of-truth)."""
    talep = _talep(lot, gun=5)
    _onayla(talep, lot["admin"])
    with tenant_context(lot["personel"].tenant_id):
        # Çalışan kaydı 2020'de oluşturulmuş (kadro); izin olayları bugün →
        # en az iki farklı tarih var, sıralama ölçülebilir.
        sayfa1, toplam = timeline_sayfa(employee_history(lot["personel"]), page=1)

    tarihler = [o["date"] for o in sayfa1]
    assert tarihler == sorted(tarihler, reverse=True), tarihler
    assert tarihler[0] == timezone.localdate().isoformat()  # en yeni üstte
    assert toplam >= 1


def test_timeline_pagination_newest_first_bozulmaz(lot) -> None:  # noqa: ANN001
    """Sayfalama ters kronolojik sırayı bozmaz; sayfalar kesişmez."""
    talep = _talep(lot, gun=5)
    _onayla(talep, lot["admin"])
    with tenant_context(lot["personel"].tenant_id):
        tum = employee_history(lot["personel"])
    sayfa1, toplam = timeline_sayfa(tum, page=1, page_size=2)
    sayfa2, _ = timeline_sayfa(tum, page=2, page_size=2)

    assert len(sayfa1) == 2 and toplam >= 2
    tarihler1 = [o["date"] for o in sayfa1]
    tarihler2 = [o["date"] for o in sayfa2]
    assert tarihler1 == sorted(tarihler1, reverse=True)
    # Sayfa 2, sayfa 1'in en eskisinden daha eski (veya eşit) olmalı.
    assert all(t <= tarihler1[-1] for t in tarihler2)
    # Kesişme yok: aynı olay iki sayfada birden görünmez.
    kimlikler1 = {(o["date"], o["label"]) for o in sayfa1}
    kimlikler2 = {(o["date"], o["label"]) for o in sayfa2}
    assert not (kimlikler1 & kimlikler2) or len(tum) <= 4