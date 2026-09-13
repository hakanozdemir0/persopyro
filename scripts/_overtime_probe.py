"""
Fazla mesai (P5a V1) + izin hakkı sondası — fresh-install/uygulama doğrulaması.

`manage.py shell` içine stdin olarak verilir; **tek satır** özet basar:

    OVERTIME_PROBE hak_durum=hakedis_tanimlandi hak=14.0 kalan=14.0 accrual=1 \
        fm=approved adim=1 sure=90

İdempotenttir: aynı durumu ikinci çalıştırmada da üretir (var olan kayıt yeniden
üretilmez, yalnızca raporlanır). Sonda çalışanı (`PROBE-1`) ve hesabı
(`probe@persopyro.test`) sabit kimlikle oluşturulur; demo kiracısı kullanılır.
"""

import uuid
from datetime import date, time
from decimal import Decimal

from apps.accounts.models import User
from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    ApprovalSubjectKind,
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeaveType,
)
from apps.leave.services import (
    create_default_leave_types,
    create_leave_policy,
    set_leave_entitlement,
)
from apps.organization.models import Department, Employee, EmployeeStatus
from apps.overtime.models import OvertimeRequest, OvertimeStatus
from apps.overtime.services import (
    create_overtime_request,
    current_step,
    decide_overtime_request,
)
from apps.rbac.models import UserRole
from apps.rbac.services import create_default_roles
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.persopyro.local")
CALISAN_NO = "PROBE-1"
EPOSTA = "probe@persopyro.test"
IS_TARIHI = date(2026, 9, 13)
FM_ACIKLAMA = "OVERTIME_PROBE ay sonu kapanis"
HAK_GEREKCESI = "OVERTIME_PROBE baslangic hakki"

with tenant_context(TENANT_ID):
    kiracı = Tenant.objects.get(pk=TENANT_ID)
    yonetici = User.objects.get(tenant=kiracı, email="demo@persopyro.test")
    create_default_leave_types(actor=yonetici, tenant=kiracı)
    yillik = LeaveType.objects.get(code="annual_leave", deleted_at__isnull=True)

    # Politika (idempotent): yıllık izin sabit 14 gün.
    if not LeavePolicy.objects.filter(leave_type=yillik, deleted_at__isnull=True).exists():
        create_leave_policy(
            actor=yonetici,
            leave_type=yillik,
            accrual_method="fixed",
            fixed_days=Decimal("14.0"),
        )

    # Sonda çalışanı + hesabı (idempotent; parola yalnız hesap açılışında verilir).
    birim = Department.objects.filter(deleted_at__isnull=True).order_by("code").first()
    hesap = User.objects.filter(tenant=kiracı, email=EPOSTA).first()
    if hesap is None:
        hesap = User.objects.create_user(
            tenant=kiracı,
            email=EPOSTA,
            password=uuid.uuid4().hex,
            first_name="Sonda",
            last_name="Calisan",
        )
    calisan = Employee.objects.filter(employee_no=CALISAN_NO, deleted_at__isnull=True).first()
    if calisan is None:
        calisan = Employee.objects.create(
            tenant=kiracı,
            department=birim,
            user=hesap,
            employee_no=CALISAN_NO,
            hire_date=IS_TARIHI,
            status=EmployeeStatus.ACTIVE,
        )

    # Bildirim **kendi kaydı** için açılır (V1 sınırı) → sonda hesabına `employee`
    # rolü verilir (`overtime.request.create` bu şablondadır). İdempotenttir.
    roller = create_default_roles(kiracı)
    UserRole.objects.get_or_create(tenant=kiracı, user=hesap, role=roller["employee"])

    # 1) İzin hakkı: manuel başlangıç hakkı (dönem hakedişi defterine tek kayıt).
    sonuc = set_leave_entitlement(
        actor=yonetici,
        employee=calisan,
        leave_type=yillik,
        period_year=IS_TARIHI.year,
        days=Decimal("14.0"),
        reason=HAK_GEREKCESI,
    )
    bakiye = LeaveBalance.objects.get(employee=calisan, leave_type=yillik, period_year=2026)
    accrual_sayisi = LeaveBalanceEntry.objects.filter(
        balance=bakiye, entry_kind=BalanceEntryKind.ACCRUAL
    ).count()

    # 2) Fazla mesai bildirimi (idempotent: aynı açıklama varsa yeniden açılmaz).
    talep = OvertimeRequest.objects.filter(
        employee=calisan, description=FM_ACIKLAMA, deleted_at__isnull=True
    ).first()
    if talep is None:
        with tenant_context(TENANT_ID):
            talep = create_overtime_request(
                actor=hesap,
                employee=calisan,
                work_date=IS_TARIHI,
                start_time=time(18, 0),
                end_time=time(19, 30),
                description=FM_ACIKLAMA,
            )

    # 3) Onay: karar bekleyen adım kalmayana kadar yetkili (TENANT kapsam) karar verir.
    while talep.status == OvertimeStatus.PENDING:
        adim = current_step(talep)
        if adim is None:  # pragma: no cover - güvenlik freni
            break
        with tenant_context(TENANT_ID):
            talep = decide_overtime_request(
                actor=yonetici, talep=talep, decision=OvertimeStatus.APPROVED
            )

    # Çıktı **kararlı** olmalı (sonda idempotenttir): bu turda kaç karar verildiği değil,
    # talebin onaylanmış adım sayısı raporlanır.
    onayli_adim = ApprovalStep.objects.filter(
        tenant_id=TENANT_ID,
        subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
        subject_id=talep.pk,
        status=ApprovalStepStatus.APPROVED,
    ).count()

    print(
        f"OVERTIME_PROBE hak={bakiye.entitled_days} kalan={bakiye.available_days} "
        f"accrual={accrual_sayisi} "
        f"fm={talep.status} adim={onayli_adim} sure={talep.total_minutes}"
    )
