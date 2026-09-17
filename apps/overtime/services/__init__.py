"""
Fazla mesai servisleri (P5a V1).

Alt modüller:
  * `base` — ortak hatalar (`OvertimeError` ailesi) ve guard'lar
  * `authorization` — `izin ∧ kapsam` kararları (mevcut RBAC/kapsam katmanı yeniden kullanılır)
  * `requests` — talep oluşturma (sunucu tarafı süre hesabı), karar (onay/ret) ve iptal

Çağıranlar `from apps.overtime.services import ...` biçiminde kullanır.
"""

from apps.overtime.services.authorization import (
    aktif_calisan,
    can_approve_overtime,
    can_cancel_overtime,
    can_create_overtime_for,
    can_view_overtime,
    current_step,
    talep_kapsamda,
    user_employee,
)
from apps.overtime.services.base import (
    OvertimeAuthorizationError,
    OvertimeError,
    OvertimeStateError,
    OvertimeValidationError,
    require_active,
    require_permission,
)
from apps.overtime.services.requests import (
    OVERTIME_MAX_MINUTES,
    calculate_total_minutes,
    cancel_overtime_request,
    create_overtime_request,
    decide_overtime_request,
    gecerli_gecis,
)
from apps.overtime.services.rules import (
    calculate_time_off_credit,
    calculate_time_off_minutes,
    calculate_time_off_days,
    create_overtime_rule,
    delete_overtime_rule,
    list_overtime_rules,
    resolve_overtime_rule,
    update_overtime_rule,
    validate_overtime_rule,
)

__all__ = [
    "OVERTIME_MAX_MINUTES",
    "OvertimeAuthorizationError",
    "OvertimeError",
    "OvertimeStateError",
    "OvertimeValidationError",
    "aktif_calisan",
    "calculate_total_minutes",
    "can_approve_overtime",
    "can_cancel_overtime",
    "can_create_overtime_for",
    "can_view_overtime",
    "cancel_overtime_request",
    "create_overtime_rule",
    "delete_overtime_rule",
    "calculate_time_off_minutes",
    "calculate_time_off_credit",
    "calculate_time_off_days",
    "create_overtime_request",
    "list_overtime_rules",
    "resolve_overtime_rule",
    "update_overtime_rule",
    "current_step",
    "decide_overtime_request",
    "gecerli_gecis",
    "require_active",
    "require_permission",
    "talep_kapsamda",
    "user_employee",
    "validate_overtime_rule",
]
