"""
İzin modülü sondası (M14) — fresh-install/uygulama doğrulaması.

`manage.py shell` içine stdin olarak verilir; **tek satır** özet basar:

    IZIN_PROBE turler=7 politika=fixed hakedis=14.0 rezerve=0.0 kullanilan=3.0 \
        durum=approved adim=2 defter=3

İdempotenttir: aynı sonda talebi (`reason="IZIN_PROBE"`) varsa yeniden üretilmez,
yalnızca durum raporlanır. Demo kiracısı ve demo yöneticisi kullanılır; aktör
`tenant_admin` (TENANT kapsam) olduğu için onay adımlarını yürütebilir.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.leave.models import (
    ApprovalStep,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
)
from apps.leave.services import (
    accrue_leave_entitlements,
    create_default_leave_types,
    create_leave_policy,
    create_leave_request,
    decide_approval_step,
)
from apps.organization.models import Employee
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.persopyro.local")
SONDA_GEREKCESI = "IZIN_PROBE"
DURUMLAR = [LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED]


def _ozet(tur_sayisi: int, yontem: str, request: LeaveRequest, adim_sayisi: int) -> str:
    bakiye = LeaveBalance.objects.filter(
        employee_id=request.employee_id, leave_type_id=request.leave_type_id
    ).first()
    defter = LeaveBalanceEntry.objects.filter(leave_request_id=request.pk).count()
    return (
        f"IZIN_PROBE turler={tur_sayisi} politika={yontem} "
        f"hakedis={bakiye.entitled_days if bakiye else '-'} "
        f"rezerve={bakiye.reserved_days if bakiye else '-'} "
        f"kullanilan={bakiye.used_days if bakiye else '-'} "
        f"durum={request.status} adim={adim_sayisi} defter={defter}"
    )


with tenant_context(TENANT_ID):
    kiracı = Tenant.objects.get(pk=TENANT_ID)
    yonetici = User.objects.get(tenant=kiracı, email="demo@persopyro.test")

    # İzin türleri (idempotent) + yıllık hakediş politikası.
    create_default_leave_types(actor=yonetici, tenant=kiracı)
    tur_sayisi = LeaveType.objects.filter(deleted_at__isnull=True).count()
    yillik = LeaveType.objects.get(code="annual_leave", deleted_at__isnull=True)

    if not LeavePolicy.objects.filter(leave_type=yillik, deleted_at__isnull=True).exists():
        create_leave_policy(
            actor=yonetici,
            leave_type=yillik,
            accrual_method="fixed",
            fixed_days=Decimal("14.0"),
        )
    politika = LeavePolicy.objects.get(leave_type=yillik, deleted_at__isnull=True)

    # Dönem hakedişi (idempotent).
    accrue_leave_entitlements(
        actor=yonetici, tenant=kiracı, period_year=timezone.localdate().year
    )

    # SEPARATE_ACTOR: talebi oluşturan/onaylayan kişi kendi kaydı üzerinde işlem
    # yapamaz → sondada aktörün kendi çalışan kaydı **seçilmez**.
    calisan = (
        Employee.objects.filter(deleted_at__isnull=True)
        .exclude(user_id=yonetici.pk)
        .order_by("employee_no")
        .first()
    )
    sonda = LeaveRequest.objects.filter(
        employee=calisan, leave_type=yillik, reason=SONDA_GEREKCESI, status__in=DURUMLAR
    ).first()

    if sonda is None:
        baslangic = timezone.localdate()
        sonda = create_leave_request(
            actor=yonetici,
            employee=calisan,
            leave_type=yillik,
            start_date=baslangic,
            end_date=baslangic + timedelta(days=2),
            reason=SONDA_GEREKCESI,
            submit=True,
        )

    # Onay akışı: adımlar sırayla `tenant_admin` (TENANT kapsam) tarafından yürütülür.
    while sonda.status == LeaveRequestStatus.PENDING:
        sonda = decide_approval_step(
            actor=yonetici, request=sonda, decision="approved", reason="Sonda onayı"
        )

    adim_sayisi = ApprovalStep.objects.filter(subject_id=sonda.pk, deleted_at__isnull=True).count()
    # Özet, kiracı bağlamı İÇİNDE hesaplanır (FORCE RLS: bağlam yoksa satır görünmez).
    print(_ozet(tur_sayisi, politika.accrual_method, sonda, adim_sayisi))
