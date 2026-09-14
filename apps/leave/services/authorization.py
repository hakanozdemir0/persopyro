"""
İzin modülü yetkilendirmesi — **izin ∧ kapsam** (yeni sistem kurulmaz).

Kullanılan mevcut katman (`apps/rbac/services`, `apps/organization/services`):
  * `user_has_permission(user, code)` — etkin izin çözümlemesi (kapsam dahil)
  * `active_role_assignments(user)` — geçerli rol atamaları (rol kodu kontrolü)
  * `object_in_org_scope(user, department_id=..., owner_user_id=...)` — kapsam ekseni
  * `can_access_employee(user, employee, code)` — çalışan hedefi için birleşik karar

Koşullar (docs/AUTHORIZATION.md §5.4 kapalı kümesi):
  * `SELF` — kayıt isteği yapanın kendi çalışan kaydı
  * `ASSIGNED` — aktör, onay **adımına** atanmış (kişi ya da rol kodu + kapsam)
  * `SEPARATE_ACTOR` — işlemi yapan, talebi oluşturandan farklı
  * `UNIT_SUBTREE` — kaydın birimi aktörün kapsamı içinde
"""

from __future__ import annotations

from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    LeaveRequest,
    LeaveRequestStatus,
)
from apps.organization.models import Employee, EmployeeStatus
from apps.rbac.services import active_role_assignments, user_has_permission
from apps.rbac.services.scope import object_in_org_scope, user_org_scope


def _ayni_kiracida(user, tenant_id) -> bool:
    """
    Aktör ile nesne **aynı kiracıda** mı?

    RLS zaten satır görünürlüğünü sınırlar; ancak yetki kararı **kapsam** çözümlemesine
    dayandığı için kiracı eşitliği burada açıkça doğrulanır (fail-closed): başka
    kiracının nesnesi hiçbir koşulda izinli sayılmaz.
    """
    if user is None or tenant_id is None:
        return False
    return getattr(user, "tenant_id", None) == tenant_id


def user_employee(user) -> Employee | None:
    """Kullanıcının kendi çalışan kaydı (yoksa None)."""
    if user is None or getattr(user, "pk", None) is None:
        return None
    return (
        Employee.objects.filter(user_id=user.pk, deleted_at__isnull=True)
        .order_by("created_at")
        .first()
    )


def _aktif_calisan(employee: Employee | None) -> bool:
    """Çalışan talep **açabilir** mi (silinmemiş + yalnız `active`)?

    OD42/OD43 (P1-B): `suspended` çalışan giriş yapamaz ve yeni izin talebi
    **açamaz** (geçmiş talepler okunabilir kalır). `terminated`/`archived`
    zaten kapalıdır. P1-A'daki `_aktif_calisan` adı korunur; küme daraltıldı.
    """
    if employee is None or employee.deleted_at is not None:
        return False
    return employee.status == EmployeeStatus.ACTIVE


def employee_in_scope(user, employee: Employee | None) -> bool:
    """Çalışan, aktörün etkin organizasyon kapsamında mı (UNIT/UNIT_DIRECT/ASSIGNED)."""
    if employee is None:
        return False
    return object_in_org_scope(
        user,
        department_id=employee.department_id,
        owner_user_id=employee.user_id,
    )


def is_own_employee(user, employee: Employee | None) -> bool:
    """`SELF` koşulu: kayıt, aktörün kendi çalışan kaydı mı."""
    return (
        employee is not None
        and employee.user_id is not None
        and employee.user_id == getattr(user, "pk", None)
    )


def _rol_kodu_var(user, code: str) -> bool:
    """Aktörde verilen rol kodu geçerli bir atamayla var mı (silinmemiş rol)."""
    if not code:
        return False
    for atama in active_role_assignments(user):
        rol = atama.role
        if rol is not None and rol.deleted_at is None and rol.code == code:
            return True
    return False


def can_manage_leave_types(actor) -> bool:
    """İzin türü/politika yönetimi: `leave.type.manage` (TENANT kapsam)."""
    return user_has_permission(actor, "leave.type.manage")


def can_create_leave_request_for(actor, employee: Employee | None) -> bool:
    """
    `leave.request.create` ∧ kapsam: kendi adına (SELF) veya kapsamındaki çalışan adına.
    """
    if not _ayni_kiracida(actor, getattr(employee, "tenant_id", None)):
        return False
    if not user_has_permission(actor, "leave.request.create"):
        return False
    if not _aktif_calisan(employee):
        return False
    return is_own_employee(actor, employee) or employee_in_scope(actor, employee)


def can_view_leave_request(actor, request: LeaveRequest | None) -> bool:
    """`leave.request.read` ∧ aynı kiracı ∧ (SELF ∨ kapsam ∨ atanmış onay adımı)."""
    if request is None or not _ayni_kiracida(actor, request.tenant_id):
        return False
    if not user_has_permission(actor, "leave.request.read"):
        return False
    if is_own_employee(actor, request.employee):
        return True
    if employee_in_scope(actor, request.employee):
        return True
    # `ASSIGNED`: aktör **geçerli adımı** karara bağlayabiliyorsa görür (izin + kapsam
    # + adım sahipliği birlikte). Rolü geri alınmış bir kullanıcı yalnızca geçmiş
    # adım kaydına dayanarak görünürlük kazanamaz (fail-closed).
    return can_approve_step(actor, current_step(request), request)


def can_cancel_leave_request(actor, request: LeaveRequest | None) -> bool:
    """
    `leave.request.cancel` ∧ kapsam.

    Çalışan kendi talebini **onay öncesi** iptal edebilir (docs §6.2E: `SELF, onay
    öncesi`); onaylanmış talebin iptali kapsam/izin sahibine aittir.
    """
    if request is None or not _ayni_kiracida(actor, request.tenant_id):
        return False
    if not user_has_permission(actor, "leave.request.cancel"):
        return False
    if is_own_employee(actor, request.employee):
        return request.status in (LeaveRequestStatus.DRAFT, LeaveRequestStatus.PENDING)
    return employee_in_scope(actor, request.employee)


def can_read_balances(actor, employee: Employee | None) -> bool:
    """`leave.balance.read` ∧ aynı kiracı ∧ (SELF ∨ kapsam)."""
    if not _ayni_kiracida(actor, getattr(employee, "tenant_id", None)):
        return False
    if not user_has_permission(actor, "leave.balance.read"):
        return False
    return is_own_employee(actor, employee) or employee_in_scope(actor, employee)


def can_manage_balances(actor, employee: Employee | None) -> bool:
    """`leave.balance.manage` ∧ aynı kiracı ∧ kapsam (manuel düzeltme; gerekçe zorunlu)."""
    if not _ayni_kiracida(actor, getattr(employee, "tenant_id", None)):
        return False
    if not user_has_permission(actor, "leave.balance.manage"):
        return False
    return is_own_employee(actor, employee) or employee_in_scope(actor, employee)


def is_assigned_to_step(actor, step: ApprovalStep | None, request: LeaveRequest | None) -> bool:
    """
    `ASSIGNED` koşulu: adım ya aktöre atanmıştır ya da aktörün taşıdığı role bağlıdır.

    Rol kodu bağlı adımlarda ayrıca organizasyon kapsamı gerekir (`employee_in_scope`),
    yani "rol kodu taşımak" tek başına yetki üretmez.
    """
    if step is None or actor is None:
        return False
    if step.approver_user_id is not None and step.approver_user_id == getattr(actor, "pk", None):
        return True
    if step.approver_role_code and _rol_kodu_var(actor, step.approver_role_code):
        return employee_in_scope(actor, request.employee if request is not None else None)
    return False


def is_assigned_to_request(actor, request: LeaveRequest | None) -> bool:
    """Aktör, talebin herhangi bir onay adımının sahibi mi (görünürlük için)."""
    if request is None:
        return False
    adimlar = list(ApprovalStep.objects.filter(subject_id=request.pk, deleted_at__isnull=True))
    return any(is_assigned_to_step(actor, adim, request) for adim in adimlar)


def current_step(request: LeaveRequest | None) -> ApprovalStep | None:
    """Karar bekleyen **en küçük** adım (sıra atlanamaz — M14 §8)."""
    if request is None:
        return None
    return (
        ApprovalStep.objects.filter(
            subject_id=request.pk,
            status=ApprovalStepStatus.PENDING,
            deleted_at__isnull=True,
        )
        .order_by("step_no")
        .first()
    )


def can_approve_step(actor, step: ApprovalStep | None, request: LeaveRequest | None) -> bool:
    """
    `leave.request.approve` ∧ (ASSIGNED ∨ TENANT kapsamı) ∧ SEPARATE_ACTOR ∧ kapsam.

    * Adım sahibi (kişi ya da rol kodu + kapsam) onaylayabilir (§6.2E: ŞYT ◐ ASSIGNED,
      DY ◐ UNIT + ASSIGNED).
    * **Kiracı geneli** onay yetkisi olan rol (§6.2E: ŞİRKET YÖN. ● TENANT) geçerli
      adımı atanmış olmasa da karara bağlayabilir — sıra kuralı yine geçerlidir.
    * Primary manager olmak tek başına yetki **üretmez**: izin kodu ve kapsam da
      gerekir (docs/ORGANIZATION.md §5.5).
    """
    if step is None or request is None:
        return False
    if not _ayni_kiracida(actor, request.tenant_id):
        return False
    if not user_has_permission(actor, "leave.request.approve"):
        return False
    atanmis = is_assigned_to_step(actor, step, request)
    kiracı_geneli = user_org_scope(actor).tenant_wide
    if not (atanmis or kiracı_geneli):
        return False
    if is_own_employee(actor, request.employee):
        return False  # SEPARATE_ACTOR: kendi talebini onaylayamaz
    return employee_in_scope(actor, request.employee)


def can_override_approval(actor) -> bool:
    """`leave.request.approve.override` (kayıtlı gerekçe zorunlu)."""
    return user_has_permission(actor, "leave.request.approve.override")


__all__ = [
    "can_approve_step",
    "can_cancel_leave_request",
    "can_create_leave_request_for",
    "can_manage_balances",
    "can_manage_leave_types",
    "can_override_approval",
    "can_read_balances",
    "can_view_leave_request",
    "current_step",
    "employee_in_scope",
    "is_assigned_to_request",
    "is_assigned_to_step",
    "is_own_employee",
    "user_employee",
]
