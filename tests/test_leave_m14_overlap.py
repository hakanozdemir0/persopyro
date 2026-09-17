"""
M14 — izin talebi **tarih çakışması (overlap)** testleri.

Kural: aynı **tenant** + aynı **çalışan** için `pending` / `approved` / `taken`
durumdaki mevcut taleple tarih aralığı çakışan yeni talep reddedilir
(kapsayıcı formül; sınır günler dahil). `rejected`, `canceled` ve `draft`
engellemez; başka çalışanın/kiracının kaydı bu çalışanı engellemez.

Aralık kapıları:

    existing.start_date <= new.end_date  AND  existing.end_date >= new.start_date
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.leave.models import ApprovalStepStatus, LeaveRequest, LeaveRequestStatus
from apps.leave.services import (
    LeaveStateError,
    cancel_leave_request,
    create_leave_request,
    decide_approval_step,
    mark_leave_taken,
    submit_leave_request,
)
from apps.tenancy.context import tenant_context
from tests.leave_helpers import birim, calisan, kadro, rol_ver, turler


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN001, ANN202
    """`tenant_a` kullanan testler onun kiracı bağlamında çalışır (FORCE RLS)."""
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiracı = request.getfixturevalue("tenant_a")
    with tenant_context(kiracı.id):
        yield


def _talep(  # noqa: ANN001, ANN202
    lot, *,
    baslangic: date,
    bitis: date | None = None,
    tur: str = "annual_leave",
    submit: bool = True,
    employee=None,  # noqa: ANN001
    actor=None,  # noqa: ANN001
) -> LeaveRequest:
    """Lot'taki çalışan için talep açar (varsayılan: onaya sunulmuş = `pending`)."""
    hedef = employee if employee is not None else lot["personel"]
    with tenant_context(hedef.tenant_id):
        return create_leave_request(
            actor=actor if actor is not None else lot["personel_user"],
            employee=hedef,
            leave_type=lot["turler"][tur],
            start_date=baslangic,
            end_date=bitis or baslangic,
            reason="Çakışma testi",
            submit=submit,
        )


def _onayla(request: LeaveRequest, actor) -> LeaveRequest:  # noqa: ANN001
    """İki adımlı onay akışını tamamlar (nihai onay)."""
    with tenant_context(request.tenant_id):
        decide_approval_step(
            actor=actor, request=request, decision=ApprovalStepStatus.APPROVED
        )
        return decide_approval_step(
            actor=actor, request=request, decision=ApprovalStepStatus.APPROVED
        )


# --- 1–5: çakışan aralıklar reddedilir ---------------------------------------


@pytest.mark.parametrize(
    ("yeni_baslangic", "yeni_bitis", "senaryo"),
    [
        (7, 12, "A_birebir_ayni"),
        (9, 10, "B_icerde_kalan"),
        (5, 12, "C_soldan_kesen"),
        (11, 14, "D_sagdan_kesen"),
        (5, 14, "E_tamamen_kapsayan"),
    ],
)
def test_overlap_cakisan_talep_reddedilir(  # noqa: ANN001
    tenant_a, user_a, yeni_baslangic, yeni_bitis, senaryo
) -> None:
    """(1–5) Mevcut 10–15 Ekim talebiyle çakışan yeni talep reddedilir."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    mevcut = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    assert mevcut.status == LeaveRequestStatus.PENDING

    with pytest.raises(LeaveStateError, match="bekleyen veya onaylanmış"):
        _talep(
            lot,
            baslangic=bugun + timedelta(days=yeni_baslangic),
            bitis=bugun + timedelta(days=yeni_bitis),
        )
    with tenant_context(tenant_a.id):
        assert LeaveRequest.objects.filter(employee=lot["personel"]).count() == 1


def test_overlap_sadece_baslangic_gunu_ayni_reddedilir(tenant_a, user_a) -> None:  # noqa: ANN001
    """Sınır günü (aynı başlangıç) da çakışmadır: 10–12 ile 12–14 çakışır."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    _talep(lot, baslangic=bugun + timedelta(days=10), bitis=bugun + timedelta(days=12))
    with pytest.raises(LeaveStateError, match="bekleyen veya onaylanmış"):
        _talep(lot, baslangic=bugun + timedelta(days=12), bitis=bugun + timedelta(days=14))


# --- Kabul edilen durumlar ----------------------------------------------------


def test_overlap_tamamen_farkli_tarihler_kabul_edilir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(6) Aralık kesişmiyorsa yeni talep kabul edilir."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    yeni = _talep(lot, baslangic=bugun + timedelta(days=20), bitis=bugun + timedelta(days=21))
    assert yeni.status == LeaveRequestStatus.PENDING


def test_overlap_reddedilmis_talep_engellemez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(7) `rejected` mevcut talep yeni talebi engellemez."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    mevcut = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    with tenant_context(tenant_a.id):
        reddedilen = decide_approval_step(
            actor=lot["yonetici_user"],
            request=mevcut,
            decision=ApprovalStepStatus.REJECTED,
            reason="Test: red",
        )
    assert reddedilen.status == LeaveRequestStatus.REJECTED

    yeni = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    assert yeni.status == LeaveRequestStatus.PENDING


def test_overlap_iptal_edilmis_talep_engellemez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(8) `canceled` mevcut talep yeni talebi engellemez."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    mevcut = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    with tenant_context(tenant_a.id):
        iptal = cancel_leave_request(
            actor=lot["personel_user"], request=mevcut, reason="Planım değişti"
        )
    assert iptal.status == LeaveRequestStatus.CANCELED

    yeni = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    assert yeni.status == LeaveRequestStatus.PENDING


def test_overlap_baska_calisanin_talebi_engellemez(tenant_a, user_a) -> None:  # noqa: ANN001
    """(9) Aynı tarihli izin **başka** çalışana aitse bu çalışanı engellemez."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))

    yeni = _talep(
        lot,
        baslangic=bugun + timedelta(days=7),
        bitis=bugun + timedelta(days=12),
        tur="administrative_leave",
        employee=lot["kardes"],
        actor=lot["kardes_user"],
    )
    assert yeni.status == LeaveRequestStatus.PENDING
    assert yeni.employee_id == lot["kardes"].pk


def test_overlap_baska_tenant_engellemez_ve_veri_sizmaz(  # noqa: ANN001
    tenant_a, tenant_b, user_a, user_b
) -> None:
    """(10) Başka kiracının aynı tarihli talebi engellemez; sorgu kiracıya kapalıdır."""
    lot_a = kadro(tenant_a, user_a)
    bugun = timezone.localdate()

    with tenant_context(tenant_b.id):
        rol_ver(tenant_b, user_b, "tenant_admin")
        turler_b = turler(tenant_b, user_b)
        birim_b = birim(tenant_b, "TB1", "B Birimi")
        calisan_b = calisan(tenant_b, birim_b, no="TB-1", user=user_b)
        mevcut_b = create_leave_request(
            actor=user_b,
            employee=calisan_b,
            leave_type=turler_b["administrative_leave"],
            start_date=bugun + timedelta(days=7),
            end_date=bugun + timedelta(days=12),
            reason="B kiracısı izni",
            submit=True,
        )
    assert mevcut_b.status == LeaveRequestStatus.PENDING

    yeni_a = _talep(
        lot_a, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12)
    )
    assert yeni_a.status == LeaveRequestStatus.PENDING

    with tenant_context(tenant_a.id):
        assert LeaveRequest.objects.filter(tenant_id=tenant_b.id).count() == 0
        assert LeaveRequest.objects.filter(employee=lot_a["personel"]).count() == 1


# --- İzin türünden bağımsızlık ve onaya sunma kapısı -------------------------


def test_overlap_farkli_izin_turu_de_engellenir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(§6) Yıllık izin varken aynı döneme hastalık izni açılamaz."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=12))
    with pytest.raises(LeaveStateError, match="bekleyen veya onaylanmış"):
        _talep(
            lot,
            baslangic=bugun + timedelta(days=9),
            bitis=bugun + timedelta(days=10),
            tur="sick_leave",
        )


def test_overlap_taslak_aktif_talep_sayilmaz_ve_kendi_kaydi_engellemez(  # noqa: ANN001
    tenant_a, user_a
) -> None:
    """`draft` aktif sayılmaz; taslak onaya sunulurken çakışma yeniden kontrol edilir."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()

    taslak = _talep(
        lot,
        baslangic=bugun + timedelta(days=7),
        bitis=bugun + timedelta(days=12),
        submit=False,
    )
    assert taslak.status == LeaveRequestStatus.DRAFT

    # Taslak (aktif değil) onay bekleyen talebi engellemez.
    bekleyen = _talep(lot, baslangic=bugun + timedelta(days=7), bitis=bugun + timedelta(days=8))
    assert bekleyen.status == LeaveRequestStatus.PENDING
    onayli = _onayla(bekleyen, lot["admin"])
    assert onayli.status == LeaveRequestStatus.APPROVED

    # Taslak onaya sunulurken çakışma görülür → reddedilir (kendi kaydı hariç tutulur).
    with pytest.raises(LeaveStateError, match="bekleyen veya onaylanmış"):
        with tenant_context(tenant_a.id):
            submit_leave_request(actor=lot["personel_user"], request=taslak)
    with tenant_context(tenant_a.id):
        taslak.refresh_from_db()
    assert taslak.status == LeaveRequestStatus.DRAFT

    # Kendi onaylı kaydı kendisini engellemez: iptal edilebilir (exclude çalışıyor).
    with tenant_context(tenant_a.id):
        assert (
            LeaveRequest.objects.filter(
                employee=lot["personel"], status=LeaveRequestStatus.APPROVED
            ).count()
            == 1
        )


def test_overlap_kullanilmis_izin_de_engeller(tenant_a, user_a) -> None:  # noqa: ANN001
    """`taken` (onaylanmış + kullanılmış) dönem yeni talebi engeller."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()
    mevcut = _talep(lot, baslangic=bugun, bitis=bugun + timedelta(days=2))
    onayli = _onayla(mevcut, lot["admin"])
    assert onayli.status == LeaveRequestStatus.APPROVED
    with tenant_context(tenant_a.id):
        kullanildi = mark_leave_taken(actor=lot["admin"], request=onayli)
    assert kullanildi.status == LeaveRequestStatus.TAKEN

    with pytest.raises(LeaveStateError, match="bekleyen veya onaylanmış"):
        _talep(lot, baslangic=bugun + timedelta(days=1), bitis=bugun + timedelta(days=3))
