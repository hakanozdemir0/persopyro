"""
Fazla mesai yetkilendirmesi — **izin ∧ kapsam** (yeni sistem kurulmaz).

Kullanılan mevcut katman:
  * `apps.rbac.services.user_has_permission` — etkin izin çözümlemesi
  * `apps.leave.services.authorization` — organizasyon kapsamı yardımcıları
    (`employee_in_scope`, `is_own_employee`, `user_employee`, `is_assigned_to_step`)
    ve genel `approval_steps` altyapısı (OD39: onay sırası leave ile aynı).

Koşullar (docs/AUTHORIZATION.md §5.4 kapalı kümesi):
  * `SELF`           — kayıt, aktörün kendi çalışan kaydı
  * `ASSIGNED`       — aktör, geçerli onay adımının sahibi (kişi ya da rol + kapsam)
  * `SEPARATE_ACTOR` — işlemi yapan, talebi oluşturandan farklı
  * `UNIT_SUBTREE`   — kaydın birimi aktörün kapsamı içinde

**V1 sınırı (bilinçli):** fazla mesai bildirimi yalnızca **kendi kaydı** için açılır
(`SELF`); bir çalışan başka bir çalışan adına kayıt açamaz. Yöneticinin ekibi adına
kayıt girmesi ürün kararı gerektirir ve bu turda **yoktur** (arayüzde de sunulmaz).
"""

from __future__ import annotations

from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    ApprovalSubjectKind,
)
from apps.leave.services.authorization import (
    employee_in_scope,
    is_assigned_to_step,
    is_own_employee,
    user_employee,
)
from apps.organization.models import Employee, EmployeeStatus
from apps.overtime.models import OvertimeRequest, OvertimeStatus
from apps.rbac.services import user_has_permission
from apps.rbac.services.scope import object_in_org_scope, user_org_scope


def _ayni_kiracida(user, tenant_id) -> bool:
    """Aktör ile nesne aynı kiracıda mı (RLS'e ek olarak açık doğrulama, fail-closed)."""
    if user is None or tenant_id is None:
        return False
    return getattr(user, "tenant_id", None) == tenant_id


def aktif_calisan(employee: Employee | None) -> bool:
    """Çalışan aktif mi (silinmemiş + işten çıkmış/arşivli değil)."""
    if employee is None or employee.deleted_at is not None:
        return False
    return employee.status in (EmployeeStatus.ACTIVE, EmployeeStatus.SUSPENDED)


def can_create_overtime_for(actor, employee: Employee | None) -> bool:
    """
    `overtime.request.create` ∧ **yalnız kendi kaydı** (V1) ∧ aktif çalışan.

    Başka bir çalışan adına kayıt açılamaz: kapsam yetkisi olsa bile (`UNIT`/`TENANT`)
    bu uç kendi kaydıyla sınırlıdır.
    """
    if not _ayni_kiracida(actor, getattr(employee, "tenant_id", None)):
        return False
    if not user_has_permission(actor, "overtime.request.create"):
        return False
    if not aktif_calisan(employee):
        return False
    return is_own_employee(actor, employee)


def current_step(talep: OvertimeRequest | None) -> ApprovalStep | None:
    """Karar bekleyen **en küçük** onay adımı (sıra atlanamaz — OD39/§11)."""
    if talep is None:
        return None
    return (
        ApprovalStep.objects.filter(
            subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
            subject_id=talep.pk,
            status=ApprovalStepStatus.PENDING,
            deleted_at__isnull=True,
        )
        .order_by("step_no")
        .first()
    )


def can_view_overtime(actor, talep: OvertimeRequest | None) -> bool:
    """`overtime.request.read` ∧ aynı kiracı ∧ (SELF ∨ kapsam ∨ karar bekleyen adım sahibi)."""
    if talep is None or not _ayni_kiracida(actor, talep.tenant_id):
        return False
    if not user_has_permission(actor, "overtime.request.read"):
        return False
    if is_own_employee(actor, talep.employee):
        return True
    if employee_in_scope(actor, talep.employee):
        return True
    # `ASSIGNED`: aktör geçerli adımın sahibi (rolü geri alınmış kullanıcı görünürlük
    # kazanmaz → fail-closed).
    return is_assigned_to_step(actor, current_step(talep), talep)


def can_cancel_overtime(actor, talep: OvertimeRequest | None) -> bool:
    """
    `overtime.request.cancel` ∧ kapsam.

    Çalışan **kendi** talebini yalnız `pending` durumdayken iptal eder (karar verilmiş
    kayıt geri alınmaz; düzeltme ayrı iş kalemidir). Kapsam sahibi de aynı kurala uyar;
    iptal gerekçesi zorunludur.
    """
    if talep is None or not _ayni_kiracida(actor, talep.tenant_id):
        return False
    if not user_has_permission(actor, "overtime.request.cancel"):
        return False
    if talep.status != OvertimeStatus.PENDING:
        return False
    if is_own_employee(actor, talep.employee):
        return True
    return employee_in_scope(actor, talep.employee)


def can_approve_overtime(actor, talep: OvertimeRequest | None) -> bool:
    """
    `overtime.request.approve` ∧ (ASSIGNED ∨ TENANT kapsamı) ∧ SEPARATE_ACTOR ∧ kapsam.

    * Adım sahibi (birincil yönetici ya da İK rol kodu + kapsam) karara bağlar.
    * Kiracı geneli onay yetkisi olan rol, adım atanmamış olsa da karara bağlayabilir
      (sıra kuralı yine geçerlidir).
    * **Kendi talebini onaylayamaz** (self-approval yasağı).
    """
    if talep is None or not _ayni_kiracida(actor, talep.tenant_id):
        return False
    if not user_has_permission(actor, "overtime.request.approve"):
        return False
    adim = current_step(talep)
    atanmis = is_assigned_to_step(actor, adim, talep)
    if not (atanmis or user_org_scope(actor).tenant_wide):
        return False
    if is_own_employee(actor, talep.employee):
        return False
    return employee_in_scope(actor, talep.employee)


def talep_kapsamda(actor, employee: Employee | None) -> bool:
    """Çalışan, aktörün organizasyon kapsamında mı (liste/karar sorguları için)."""
    if not _ayni_kiracida(actor, getattr(employee, "tenant_id", None)):
        return False
    return object_in_org_scope(
        actor, department_id=employee.department_id, owner_user_id=employee.user_id
    )


__all__ = [
    "aktif_calisan",
    "can_approve_overtime",
    "can_cancel_overtime",
    "can_create_overtime_for",
    "can_view_overtime",
    "current_step",
    "talep_kapsamda",
    "user_employee",
]
