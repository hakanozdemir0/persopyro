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
    "create_overtime_request",
    "current_step",
    "decide_overtime_request",
    "gecerli_gecis",
    "require_active",
    "require_permission",
    "talep_kapsamda",
    "user_employee",
]
