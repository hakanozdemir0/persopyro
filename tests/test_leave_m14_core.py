"""
M14 — izin modülü **çekirdek** testleri: türler, politika, hakediş, bakiye, talep doğrulama.

Kapsanan istek senaryoları: 1, 2, 3, 4, 5, 6, 7, 10, 21, 22 + manuel düzeltme ve
negatif bakiye guard'ları. Akış testleri (onay, iptal, kapsam, yarış, RLS)
`tests/test_leave_m14_flow.py` içindedir.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.leave.leave_type_catalog import DEFAULT_LEAVE_TYPES
from apps.leave.models import (
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeavePolicyRule,
    LeaveType,
    RecordStatus,
)
from apps.leave.services import (
    LeaveBalanceError,
    LeaveError,
    LeavePolicyError,
    LeaveStateError,
    accrue_leave_entitlements,
    adjust_leave_balance,
    calculate_requested_days,
    can_create_leave_request_for,
    create_leave_policy,
    create_leave_request,
    create_leave_type,
    get_or_create_balance,
    service_months,
    update_leave_policy,
)
from apps.organization.models import EmployeeStatus
from apps.tenancy.context import tenant_context
from tests.leave_helpers import (
    birim,
    calisan,
    kadro,
    sonuc,
    turler,
    yillik_politika,
)


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN001, ANN202
    """
    `tenant_a` kullanan testler varsayılan olarak onun **kiracı bağlamında** çalışır.

    FORCE RLS: izin/kapsam çözümlemesi ve yazımlar bağlam gerektirir. Çok kiracılı
    testler kendi bağlamlarını iç içe açabilir.
    """
    if "tenant_a" not in request.fixturenames:
        yield
        return
    kiracı = request.getfixturevalue("tenant_a")
    with tenant_context(kiracı.id):
        yield


# --- türler -------------------------------------------------------------------


def test_m14_tenant_leave_type_olusturulur(tenant_a, user_a) -> None:  # noqa: ANN001
    """(1) Kiracı bazlı izin türleri oluşturulur; varsayılanlar sistem türüdür."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    assert set(izin_turleri) >= {
        "annual_leave",
        "annual_leave_block",
        "work_from_home",
        "flex_time",
        "sick_leave",
        "administrative_leave",
        "paternity_leave",
    }
    assert izin_turleri["annual_leave"].uses_balance is True
    assert izin_turleri["sick_leave"].requires_document is True
    assert all(t.is_system for t in izin_turleri.values())
    assert izin_turleri["annual_leave"].tenant_id == tenant_a.id

    # Kiracı kendi türünü ekleyebilir (sistem kodu değilse).
    ozel = create_leave_type(
        actor=user_a,
        tenant=tenant_a,
        code="dogum_gunu",
        name="Doğum Günü İzni",
        category="other",
        uses_balance=False,
    )
    assert ozel.is_system is False


def test_m14_baska_tenant_leave_type_goremez(tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """(2) RLS: kiracı yalnızca kendi izin türlerini görür."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    turler(tenant_a, user_a)
    with tenant_context(tenant_b.id):
        rol_ver(tenant_b, user_b, "tenant_admin")
        turler(tenant_b, user_b)

    with tenant_context(tenant_a.id):
        assert LeaveType.objects.count() == len(DEFAULT_LEAVE_TYPES)
        assert set(LeaveType.objects.values_list("tenant_id", flat=True)) == {tenant_a.id}
        assert LeaveType.objects.filter(tenant_id=tenant_b.id).count() == 0
    with tenant_context(tenant_b.id):
        assert set(LeaveType.objects.values_list("tenant_id", flat=True)) == {tenant_b.id}


def test_m14_duplicate_aktif_leave_type_kodu_reddedilir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(3) OD21: aktif kod kiracı içinde benzersizdir (DB + kısmi indeks)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    with tenant_context(tenant_a.id):
        LeaveType.objects.create(tenant=tenant_a, code="ozel_izin", name="Özel İzin")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        LeaveType.objects.create(tenant=tenant_a, code="ozel_izin", name="Kopya")


def test_m14_sistem_izin_kodu_rezerve(tenant_a, user_a) -> None:  # noqa: ANN001
    """(3/4) Sistem kodları rezervedir; kiracı aynı kodu tanımlayamaz."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    turler(tenant_a, user_a)
    with pytest.raises(LeaveError, match="rezerve"):
        create_leave_type(
            actor=user_a,
            tenant=tenant_a,
            code="annual_leave",
            name="Kopya Yıllık",
            category="annual",
        )


def test_m14_silinmis_ve_pasif_tur_kullanilamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(21) Soft-deleted ve pasif izin türü yeni talep için kullanılamaz."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()

    with tenant_context(tenant_a.id):
        lot["turler"]["flex_time"].deleted_at = timezone.now()
        lot["turler"]["flex_time"].save(update_fields=["deleted_at"])
    with pytest.raises(LeaveStateError, match="Silinmiş izin türü"):
        create_leave_request(
            actor=lot["admin"],
            employee=lot["personel"],
            leave_type=lot["turler"]["flex_time"],
            start_date=bugun + timedelta(days=5),
            end_date=bugun + timedelta(days=5),
        )

    with tenant_context(tenant_a.id):
        lot["turler"]["administrative_leave"].status = RecordStatus.INACTIVE
        lot["turler"]["administrative_leave"].save(update_fields=["status"])
    with pytest.raises(LeaveStateError, match="aktif izin türleri"):
        create_leave_request(
            actor=lot["admin"],
            employee=lot["personel"],
            leave_type=lot["turler"]["administrative_leave"],
            start_date=bugun + timedelta(days=5),
            end_date=bugun + timedelta(days=5),
        )


# --- politika ve hakediş ------------------------------------------------------


def test_m14_fixed_policy_hakedis_hesaplanir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(4) FIXED politika: yıllık sabit gün hakedişi yazılır."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    calisan_kaydi = calisan(tenant_a, birim(tenant_a, "A1"), no="A-1")

    sonuclar = accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    assert [s["durum"] for s in sonuc(sonuclar, "annual_leave")] == ["yazildi"]
    with tenant_context(tenant_a.id):
        bakiye = LeaveBalance.objects.get(employee=calisan_kaydi, period_year=2026)
        assert bakiye.entitled_days == Decimal("14.0")
        assert bakiye.available_days == Decimal("14.0")
        assert (
            LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.ACCRUAL).count() == 1
        )


def test_m14_tenure_kurali_dogru_seciliyor(tenant_a, user_a) -> None:  # noqa: ANN001
    """(5) TENURE_BASED: [0,60) → 14 gün, [60,∞) → 21 gün (yarı açık aralık)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    create_leave_policy(
        actor=user_a,
        leave_type=izin_turleri["annual_leave"],
        accrual_method="tenure_based",
        rules=[
            {"min_service_months": 0, "max_service_months": 60, "entitlement_days": "14.0"},
            {"min_service_months": 60, "max_service_months": None, "entitlement_days": "21.0"},
        ],
    )
    ortak = birim(tenant_a, "A2")
    kidemli = calisan(tenant_a, ortak, no="A-2", hire_date=date(2016, 1, 1))
    yeni = calisan(tenant_a, ortak, no="A-3", hire_date=date(2023, 1, 1))

    assert service_months(date(2016, 1, 1), date(2026, 12, 31)) == 131
    assert service_months(date(2023, 1, 1), date(2026, 12, 31)) == 47

    accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    with tenant_context(tenant_a.id):
        assert (
            LeaveBalance.objects.get(employee=kidemli, period_year=2026).entitled_days
            == Decimal("21.0")
        )
        assert (
            LeaveBalance.objects.get(employee=yeni, period_year=2026).entitled_days
            == Decimal("14.0")
        )


def test_m14_cakisan_tenure_kurali_reddedilir(tenant_a, user_a) -> None:  # noqa: ANN001
    """(6) Çakışan hizmet süresi aralıkları servis ve DB guard'ı ile reddedilir."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)

    with pytest.raises(LeavePolicyError, match="çakış"):
        create_leave_policy(
            actor=user_a,
            leave_type=izin_turleri["annual_leave"],
            accrual_method="tenure_based",
            rules=[
                {"min_service_months": 0, "max_service_months": 60, "entitlement_days": "14.0"},
                {"min_service_months": 36, "max_service_months": None, "entitlement_days": "21.0"},
            ],
        )

    policy = create_leave_policy(
        actor=user_a,
        leave_type=izin_turleri["annual_leave"],
        accrual_method="tenure_based",
        rules=[{"min_service_months": 0, "max_service_months": 60, "entitlement_days": "14.0"}],
    )
    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="cakisiyor"),
        transaction.atomic(),
    ):
        LeavePolicyRule.objects.create(
            tenant=tenant_a,
            policy=policy,
            min_service_months=36,
            entitlement_days=Decimal("21.0"),
        )


def test_m14_tek_aktif_politika_garantisi(tenant_a, user_a) -> None:  # noqa: ANN001
    """(6) Tür başına tek yaşayan politika (servis + DB kısmi benzersizlik)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    with pytest.raises(LeavePolicyError, match="zaten var"):
        yillik_politika(user_a, izin_turleri["annual_leave"], "20.0")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        LeavePolicy.objects.create(
            tenant=tenant_a,
            leave_type=izin_turleri["annual_leave"],
            accrual_method="fixed",
            fixed_days=Decimal("20.0"),
        )


def test_m14_accrual_idempotent(tenant_a, user_a) -> None:  # noqa: ANN001
    """(7) Aynı dönem hakedişi tekrar çalıştırıldığında duplicate üretmez."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    calisan(tenant_a, birim(tenant_a, "B1"), no="B-1")

    ilk = accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    ikinci = accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    ucuncu = accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    assert [s["durum"] for s in sonuc(ilk, "annual_leave")] == ["yazildi"]
    assert [s["durum"] for s in sonuc(ikinci, "annual_leave")] == ["degisiklik_yok"]
    assert [s["durum"] for s in sonuc(ucuncu, "annual_leave")] == ["degisiklik_yok"]

    with tenant_context(tenant_a.id):
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.ACCRUAL).count() == 1
        assert LeaveBalance.objects.get(period_year=2026).entitled_days == Decimal("14.0")


def test_m14_accrual_deger_degisirse_duzeltme_yazar(tenant_a, user_a) -> None:  # noqa: ANN001
    """(7) Hakediş değeri değişirse fark **düzeltme** kaydı olarak yazılır (sessiz yazım yok)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    policy = yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    calisan(tenant_a, birim(tenant_a, "B2"), no="B-2")
    accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )

    update_leave_policy(
        actor=user_a, policy=policy, fixed_days=Decimal("18.0"), reason="sözleşme güncellendi"
    )
    sonuclar = accrue_leave_entitlements(
        actor=user_a, tenant=tenant_a, period_year=2026, as_of=date(2026, 12, 31)
    )
    assert [s["durum"] for s in sonuc(sonuclar, "annual_leave")] == ["duzeltme"]
    with tenant_context(tenant_a.id):
        assert LeaveBalance.objects.get(period_year=2026).entitled_days == Decimal("18.0")
        assert LeaveBalanceEntry.objects.filter(entry_kind=BalanceEntryKind.ACCRUAL).count() == 1
        duzeltme = LeaveBalanceEntry.objects.get(entry_kind=BalanceEntryKind.ADJUSTMENT)
        assert duzeltme.delta_days == Decimal("4.0")
        assert AuditLog.objects.filter(action="leave.balance.adjustment").count() == 1


# --- manuel düzeltme ve negatif bakiye ---------------------------------------


def test_m14_manuel_duzeltme_gerekce_zorunlu(tenant_a, user_a) -> None:  # noqa: ANN001
    """Manuel düzeltme: gerekçe zorunlu, denetim kaydı yazılır, izin gerekir."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    calisan_kaydi = calisan(tenant_a, birim(tenant_a, "B3"), no="B-3")

    with pytest.raises(LeaveError, match="gerekçe"):
        adjust_leave_balance(
            actor=user_a,
            employee=calisan_kaydi,
            leave_type=izin_turleri["annual_leave"],
            period_year=2026,
            delta_days=Decimal("2.0"),
            reason="kısa",
        )

    bakiye = adjust_leave_balance(
        actor=user_a,
        employee=calisan_kaydi,
        leave_type=izin_turleri["annual_leave"],
        period_year=2026,
        delta_days=Decimal("2.0"),
        reason="Geçmiş yıl devir düzeltmesi",
    )
    assert bakiye.adjustment_days == Decimal("2.0")
    with tenant_context(tenant_a.id):
        kayit = LeaveBalanceEntry.objects.get(entry_kind=BalanceEntryKind.ADJUSTMENT)
        assert kayit.reason.startswith("Geçmiş yıl")
        assert AuditLog.objects.filter(action="leave.balance.adjustment").count() == 1

    with pytest.raises(LeaveBalanceError):
        adjust_leave_balance(
            actor=user_a,
            employee=calisan_kaydi,
            leave_type=izin_turleri["annual_leave"],
            period_year=2026,
            delta_days=Decimal("-100.0"),
            reason="Aşırı düşürme denemesi",
        )


def test_m14_negatif_bakiye_tenant_politikasina_bagli(tenant_a, user_a) -> None:  # noqa: ANN001
    """Negatif bakiye yalnızca politika açıkça izin veriyorsa oluşur (fail-closed)."""
    from tests.leave_helpers import rol_ver

    rol_ver(tenant_a, user_a, "tenant_admin")
    izin_turleri = turler(tenant_a, user_a)
    yillik_politika(user_a, izin_turleri["annual_leave"], "14.0")
    calisan_kaydi = calisan(tenant_a, birim(tenant_a, "B4"), no="B-4")
    bakiye = get_or_create_balance(
        employee=calisan_kaydi, leave_type=izin_turleri["annual_leave"], period_year=2026
    )

    with (
        tenant_context(tenant_a.id),
        pytest.raises(DatabaseError, match="Negatif"),
        transaction.atomic(),
    ):
        LeaveBalance.objects.filter(pk=bakiye.pk).update(used_days=Decimal("20.0"))

    with tenant_context(tenant_a.id):
        policy = LeavePolicy.objects.get(leave_type=izin_turleri["annual_leave"])
    update_leave_policy(
        actor=user_a,
        policy=policy,
        allow_negative_balance=True,
        reason="avans izin onayı",
    )
    with tenant_context(tenant_a.id):
        LeaveBalance.objects.filter(pk=bakiye.pk).update(used_days=Decimal("20.0"))
        bakiye.refresh_from_db()
        assert bakiye.available_days == Decimal("-20.0")


# --- talep doğrulama ----------------------------------------------------------


def test_m14_gun_hesabi_sunucu_tarafinda(tenant_a, user_a) -> None:  # noqa: ANN001
    """(10) Gün hesabı sunucuda yapılır; istemci değeri doğrulanır (takvim günü)."""
    lot = kadro(tenant_a, user_a)
    bugun = timezone.localdate()

    assert calculate_requested_days(
        start_date=bugun, end_date=bugun + timedelta(days=4)
    ) == Decimal("5.0")
    assert (
        calculate_requested_days(start_date=bugun, end_date=bugun, is_half_day=True)
        == Decimal("0.5")
    )
    with pytest.raises(LeaveError, match="önce olamaz"):
        calculate_requested_days(start_date=bugun, end_date=bugun - timedelta(days=1))
    with pytest.raises(LeaveError, match="tek gün"):
        calculate_requested_days(
            start_date=bugun, end_date=bugun + timedelta(days=1), is_half_day=True
        )

    with pytest.raises(LeaveError, match="uyuşmuyor"):
        create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun + timedelta(days=5),
            end_date=bugun + timedelta(days=6),
            requested_days="5",
        )
    with pytest.raises(LeaveError, match="önce olamaz"):
        create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=bugun + timedelta(days=5),
            end_date=bugun + timedelta(days=1),
        )


def test_m14_deleted_ve_pasif_calisan_talep_olusturamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    """(22) Silinmiş/pasif çalışan yeni izin talebi oluşturamaz."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        lot["personel"].deleted_at = timezone.now()
        lot["personel"].save(update_fields=["deleted_at"])
    assert can_create_leave_request_for(lot["personel_user"], lot["personel"]) is False

    # P1-A/OD41: `status='terminated'` artık terminal alanları zorunlu kılar
    # (`termination_date` + `termination_reason_code` — DB CHECK).
    ayrilmis = calisan(
        tenant_a,
        lot["birimler"]["ik"],
        no="IK-9",
        status=EmployeeStatus.TERMINATED,
        termination_date=timezone.localdate(),
        termination_reason_code="resignation",
    )
    with pytest.raises(LeaveStateError, match="durumundaki çalışan"):
        create_leave_request(
            actor=lot["admin"],
            employee=ayrilmis,
            leave_type=lot["turler"]["annual_leave"],
            start_date=timezone.localdate() + timedelta(days=2),
            end_date=timezone.localdate() + timedelta(days=3),
        )


def test_m14_on_bildirim_ve_azami_sure_kurallari(tenant_a, user_a) -> None:  # noqa: ANN001
    """(10) `min_advance_notice_days` ve `max_consecutive_days` uygulanır."""
    lot = kadro(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        yillik = lot["turler"]["annual_leave"]
        yillik.min_advance_notice_days = 7
        yillik.max_consecutive_days = Decimal("10.0")
        yillik.save(update_fields=["min_advance_notice_days", "max_consecutive_days"])
    calisan(tenant_a, lot["birimler"]["ik"], no="IK-8")

    with pytest.raises(LeaveError, match="önceden talep"):
        create_leave_request(
            actor=lot["admin"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=timezone.localdate() + timedelta(days=2),
            end_date=timezone.localdate() + timedelta(days=3),
        )
    with pytest.raises(LeaveError, match="azami kesintisiz"):
        create_leave_request(
            actor=lot["admin"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=timezone.localdate() + timedelta(days=20),
            end_date=timezone.localdate() + timedelta(days=40),
        )
