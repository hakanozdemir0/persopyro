"""İzin (leave) servis katmanı — M14.

Alt modüller:
  * `base`          — hatalar ve ortak guard'lar (aktif aktör, izin kontrolü)
  * `types`         — izin türleri (sistem kodları rezerve, OD21 soft delete)
  * `policies`      — hakediş politikası + kıdem kuralları + entitlement hesabı
  * `balances`      — bakiye, olay defteri, hakediş (accrual) ve manuel düzeltme
  * `requests`      — izin talebi, gün hesabı, onay akışı, iptal
  * `authorization` — `izin ∧ kapsam` kararları (yeni sistem kurulmaz; mevcut
    `apps.rbac.services` katmanı genişletilir)

Genel kural (docs/AUTHORIZATION.md §5.5): yetki kararı **her zaman** RBAC'ten gelir
(izin kodu + kapsam + koşul). Organizasyon kapsamı izin **üretmez**; yalnızca hangi
kayıtlara erişilebileceğini daraltır. Kiracı bağlamı kurulmamışsa izin çözümlemesi
boş küme döner → **fail-closed** (reddeder).
"""

from apps.leave.services.authorization import (
    can_approve_step,
    can_cancel_leave_request,
    can_create_leave_request_for,
    can_manage_balances,
    can_manage_leave_types,
    can_read_balances,
    can_view_leave_request,
    current_step,
    is_assigned_to_step,
    user_employee,
)
from apps.leave.services.balances import (
    accrue_leave_entitlements,
    adjust_leave_balance,
    get_or_create_balance,
    next_accrual_date,
    period_year_for,
    service_months,
)
from apps.leave.services.base import (
    LeaveAuthorizationError,
    LeaveBalanceError,
    LeaveError,
    LeavePolicyError,
    LeaveStateError,
)
from apps.leave.services.policies import (
    create_leave_policy,
    entitlement_for,
    update_leave_policy,
)
from apps.leave.services.requests import (
    calculate_requested_days,
    cancel_leave_request,
    create_leave_request,
    decide_approval_step,
    mark_leave_taken,
    submit_leave_request,
)
from apps.leave.services.types import (
    create_default_leave_types,
    create_leave_type,
    deactivate_leave_type,
    update_leave_type,
)

__all__ = [
    "LeaveAuthorizationError",
    "LeaveBalanceError",
    "LeaveError",
    "LeavePolicyError",
    "LeaveStateError",
    "accrue_leave_entitlements",
    "adjust_leave_balance",
    "next_accrual_date",
    "calculate_requested_days",
    "can_approve_step",
    "can_cancel_leave_request",
    "can_create_leave_request_for",
    "can_manage_balances",
    "can_manage_leave_types",
    "can_read_balances",
    "can_view_leave_request",
    "cancel_leave_request",
    "create_default_leave_types",
    "deactivate_leave_type",
    "create_leave_policy",
    "create_leave_request",
    "create_leave_type",
    "current_step",
    "decide_approval_step",
    "entitlement_for",
    "get_or_create_balance",
    "is_assigned_to_step",
    "mark_leave_taken",
    "period_year_for",
    "service_months",
    "submit_leave_request",
    "update_leave_policy",
    "update_leave_type",
    "user_employee",
]
