"""İzin talepleri ve vardiya atamaları aynı zaman aralığını paylaşamaz."""

from datetime import time, timedelta

import pytest
from django.utils import timezone

from apps.leave.models import LeaveRequestStatus
from apps.leave.services import LeaveStateError, create_leave_request
from apps.shifts.services import ShiftError, assign_shifts, create_shift
from apps.tenancy.context import tenant_context
from tests.leave_helpers import kadro


@pytest.fixture(autouse=True)
def _kiraci_baglami(request):  # noqa: ANN001, ANN202
    if "tenant_a" not in request.fixturenames:
        yield
        return
    tenant = request.getfixturevalue("tenant_a")
    with tenant_context(tenant.id):
        yield


def _vardiya(tenant, actor):  # noqa: ANN001, ANN202
    with tenant_context(tenant.id):
        return create_shift(
            actor=actor,
            tenant=tenant,
            code="sabah",
            name="Sabah vardiyası",
            start_time=time(8, 0),
            end_time=time(17, 0),
        )


@pytest.mark.django_db
def test_leave_blocks_shift_assignment(tenant_a, user_a):
    lot = kadro(tenant_a, user_a)
    day = timezone.localdate() + timedelta(days=30)
    with tenant_context(tenant_a.id):
        izin = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["annual_leave"],
            start_date=day,
            end_date=day,
            reason="Yıllık izin",
            submit=True,
        )
        vardiya = _vardiya(tenant_a, lot["admin"])

        assert izin.status == LeaveRequestStatus.PENDING
        with pytest.raises(ShiftError, match="izni .* vardiya atanmasına engel"):
            assign_shifts(
                actor=lot["admin"], shift=vardiya,
                employee_ids=[lot["personel"].pk], dates=[day],
            )


@pytest.mark.django_db
def test_shift_blocks_leave_request(tenant_a, user_a):
    lot = kadro(tenant_a, user_a)
    day = timezone.localdate() + timedelta(days=30)
    vardiya = _vardiya(tenant_a, lot["admin"])
    with tenant_context(tenant_a.id):
        assign_shifts(
            actor=lot["admin"], shift=vardiya,
            employee_ids=[lot["personel"].pk], dates=[day],
        )

        with pytest.raises(LeaveStateError, match="planlanmış vardiya"):
            create_leave_request(
                actor=lot["personel_user"],
                employee=lot["personel"],
                leave_type=lot["turler"]["annual_leave"],
                start_date=day,
                end_date=day,
                reason="Yıllık izin",
            )


@pytest.mark.django_db
def test_time_leave_outside_shift_hours_does_not_conflict(tenant_a, user_a):
    lot = kadro(tenant_a, user_a)
    day = timezone.localdate() + timedelta(days=30)
    vardiya = _vardiya(tenant_a, lot["admin"])
    with tenant_context(tenant_a.id):
        assign_shifts(
            actor=lot["admin"], shift=vardiya,
            employee_ids=[lot["personel"].pk], dates=[day],
        )

        request = create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"]["time_off"],
            start_date=day,
            end_date=day,
            start_time=time(6, 0),
            end_time=time(7, 0),
            reason="Kısa serbest zaman",
        )

        assert request.start_time == time(6, 0)
