"""
İzin (leave/M14) testleri için ortak yardımcılar.

`tests/conftest.py` kiracı/kullanıcı fixture'larını sağlar; bu modül izin modülüne
özel kurulum yardımcılarını toplar (departman/çalışan/rol/politika/talep).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.leave.models import ApprovalStep, LeaveRequest, LeaveType
from apps.leave.services import (
    accrue_leave_entitlements,
    create_default_leave_types,
    create_leave_policy,
    create_leave_request,
)
from apps.organization.models import (
    Department,
    Employee,
    EmployeeManager,
    EmployeeStatus,
    ManagerRelationType,
)
from apps.rbac.models import UserRole
from apps.rbac.services.roles import create_default_roles
from apps.tenancy.context import tenant_context

LEAVE_TABLOLARI = (
    "leave_types",
    "leave_policies",
    "leave_policy_rules",
    "leave_balances",
    "leave_balance_entries",
    "leave_requests",
    "approval_steps",
)


def kullanici(tenant, email: str, ad: str = "Test"):  # noqa: ANN001, ANN202
    user_model = get_user_model()
    with tenant_context(tenant.id):
        return user_model.objects.create_user(
            tenant=tenant,
            email=email,
            password="Gizli-Parola-123",
            first_name=ad,
            last_name="Kullanıcı",
        )


def rol_ver(tenant, user, rol_kodu: str, *, department: Department | None = None):  # noqa: ANN001, ANN202
    """Kullanıcıya varsayılan rolü atar; `UNIT` tipli rolde kapsam zorunludur."""
    with tenant_context(tenant.id):
        roller = create_default_roles(tenant)
        return UserRole.objects.create(
            tenant=tenant, user=user, role=roller[rol_kodu], scope_org_unit=department
        )


def birim(tenant, kod: str, ad: str | None = None, parent: Department | None = None) -> Department:
    with tenant_context(tenant.id):
        return Department.objects.create(
            tenant=tenant, code=kod, name=ad or f"Birim {kod}", parent=parent
        )


def calisan(
    tenant,
    department: Department,
    *,
    no: str = "C-001",
    user=None,  # noqa: ANN001
    hire_date: date | None = None,
    status: str = EmployeeStatus.ACTIVE,
    **ek,  # noqa: ANN003
) -> Employee:
    """Çalışan oluşturur; ek alanlar (`termination_date`,
    `termination_reason_code` …) geçirilebilir.

    P1-A/OD41: `status='terminated'` DB `CHECK`'i terminal alanları zorunlu kıldığı için
    bu alanları geçirebilmek gerekir.
    """
    with tenant_context(tenant.id):
        return Employee.objects.create(
            tenant=tenant,
            department=department,
            user=user,
            employee_no=no,
            hire_date=hire_date or date(2020, 1, 1),
            status=status,
            **ek,
        )


def yonetici_iliskisi(tenant, employee: Employee, manager: Employee) -> EmployeeManager:
    with tenant_context(tenant.id):
        return EmployeeManager.objects.create(
            tenant=tenant,
            employee=employee,
            manager_employee=manager,
            relation_type=ManagerRelationType.PRIMARY,
            effective_from=date(2024, 1, 1),
        )


def turler(tenant, actor) -> dict[str, LeaveType]:  # noqa: ANN001
    create_default_leave_types(actor=actor, tenant=tenant)
    with tenant_context(tenant.id):
        return {t.code: t for t in LeaveType.objects.filter(tenant=tenant)}


def yillik_politika(actor, tur: LeaveType, gun: str = "14.0"):  # noqa: ANN001, ANN202
    return create_leave_policy(
        actor=actor, leave_type=tur, accrual_method="fixed", fixed_days=Decimal(gun)
    )


def kadro(tenant, actor):  # noqa: ANN001, ANN202
    """
    Ortak kurulum: GM → Finans → Muhasebe, GM → İK; yönetici/personel/kardeş çalışan.

    * `admin` → `tenant_admin` (TENANT kapsam)
    * `yonetici_user` → `dept_manager` (Finans birimi kapsamı)
    * `personel_user` → `employee` (SELF); birincil yöneticisi `yonetici`
    * `kardes_user` → `employee` (İK biriminde, kapsam dışı)
    """
    gm = birim(tenant, "GM", "Genel Müdürlük")
    finans = birim(tenant, "FIN", "Finans", parent=gm)
    muhasebe = birim(tenant, "MUH", "Muhasebe", parent=finans)
    ik = birim(tenant, "IK", "İnsan Kaynakları", parent=gm)

    admin = kullanici(tenant, "admin@ornek.test", "Admin")
    rol_ver(tenant, admin, "tenant_admin")

    yonetici_user = kullanici(tenant, "finans.mudur@ornek.test", "Finans")
    rol_ver(tenant, yonetici_user, "dept_manager", department=finans)
    yonetici = calisan(tenant, finans, no="FIN-M1", user=yonetici_user)

    personel_user = kullanici(tenant, "personel@ornek.test", "Personel")
    rol_ver(tenant, personel_user, "employee")
    personel = calisan(tenant, muhasebe, no="MUH-1", user=personel_user)

    kardes_user = kullanici(tenant, "ik@ornek.test", "Kardeş")
    rol_ver(tenant, kardes_user, "employee")
    kardes = calisan(tenant, ik, no="IK-1", user=kardes_user)

    yonetici_iliskisi(tenant, personel, yonetici)

    izin_turleri = turler(tenant, admin)
    yillik_politika(admin, izin_turleri["annual_leave"], "14.0")
    # Dönem hakedişi: bakiye gerektiren türler için 14 gün yazılır (M14 §4).
    accrue_leave_entitlements(actor=admin, tenant=tenant, period_year=timezone.localdate().year)

    return {
        "birimler": {"gm": gm, "finans": finans, "muhasebe": muhasebe, "ik": ik},
        "admin": admin,
        "yonetici_user": yonetici_user,
        "yonetici": yonetici,
        "personel_user": personel_user,
        "personel": personel,
        "kardes_user": kardes_user,
        "kardes": kardes,
        "turler": izin_turleri,
    }


def talep(  # noqa: ANN001
    lot, *, gun: int = 3, submit: bool = False, tur_kodu: str = "annual_leave"
) -> LeaveRequest:
    """Personel için yıllık izin talebi açar (varsayılan: taslak)."""
    baslangic = timezone.localdate() + timedelta(days=7)
    with tenant_context(lot["personel"].tenant_id):
        return create_leave_request(
            actor=lot["personel_user"],
            employee=lot["personel"],
            leave_type=lot["turler"][tur_kodu],
            start_date=baslangic,
            end_date=baslangic + timedelta(days=gun - 1),
            reason="Yıllık izin planı",
            submit=submit,
        )


def sonuc(sonuclar: list[dict], tur_kodu: str) -> list[dict]:
    """`accrue_leave_entitlements` çıktısından tek bir izin türünün satırlarını süzer."""
    return [satir for satir in sonuclar if satir.get("leave_type") == tur_kodu]


def adimlar(request: LeaveRequest) -> list[ApprovalStep]:
    with tenant_context(request.tenant_id):
        return list(
            ApprovalStep.objects.filter(subject_id=request.pk, deleted_at__isnull=True).order_by(
                "step_no"
            )
        )
