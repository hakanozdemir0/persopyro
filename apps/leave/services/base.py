"""İzin servisleri — ortak hatalar ve guard'lar."""

from __future__ import annotations

from apps.rbac.services import user_has_permission


class LeaveError(RuntimeError):
    """İzin modülü iş kuralı hatası (taban sınıf)."""


class LeaveAuthorizationError(LeaveError):
    """Aktörün izni/kapsamı işlem için yeterli değil (fail-closed)."""


class LeaveStateError(LeaveError):
    """Geçersiz durum/geçiş (OD21: sessiz durum değişimi yok)."""


class LeavePolicyError(LeaveError):
    """Politika/kural yapılandırması eksik veya tutarsız."""


class LeaveBalanceError(LeaveError):
    """Bakiye kuralı ihlali (ör. negatif bakiye, yetersiz kalan)."""


def require_active(actor) -> None:
    """Aktör aktif ve kimliği doğrulanmış olmalıdır."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise LeaveAuthorizationError("İşlem için oturum açmış bir kullanıcı gerekir.")
    if getattr(actor, "status", None) not in (None, "active"):
        raise LeaveAuthorizationError("Pasif kullanıcı izin işlemi yapamaz.")
    if getattr(actor, "deleted_at", None) is not None:
        raise LeaveAuthorizationError("Silinmiş kullanıcı izin işlemi yapamaz.")


def require_permission(actor, code: str) -> None:
    """
    Aktörde izin kodu bulunmalıdır.

    Kiracı bağlamı kurulmamışsa izin kümesi boş döner → **fail-closed** reddeder.
    """
    require_active(actor)
    if not user_has_permission(actor, code):
        raise LeaveAuthorizationError(f"Bu işlem için '{code}' izni gerekir.")
