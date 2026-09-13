"""Fazla mesai servisleri — ortak hatalar ve guard'lar."""

from __future__ import annotations

from apps.rbac.services import user_has_permission


class OvertimeError(RuntimeError):
    """Fazla mesai modülü iş kuralı hatası (taban sınıf)."""


class OvertimeAuthorizationError(OvertimeError):
    """
    Aktörün izni/kapsamı işlem için yeterli değil (fail-closed).

    HTTP katmanı bu sınıfı **403** olarak eşler; girdi hataları **400**
    (`OvertimeValidationError`) döner. Böylece "yetkiniz yok" ile "veri geçersiz"
    ayrımı istemciye doğru yansır.
    """


class OvertimeValidationError(OvertimeError):
    """Girdi doğrulaması başarısız (tarih/saat/açıklama/süre)."""


class OvertimeStateError(OvertimeError):
    """Geçersiz durum/geçiş (OD21: sessiz durum değişimi yok)."""


def require_active(actor) -> None:
    """Aktör aktif ve kimliği doğrulanmış olmalıdır."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise OvertimeAuthorizationError("İşlem için oturum açmış bir kullanıcı gerekir.")
    if getattr(actor, "status", None) not in (None, "active"):
        raise OvertimeAuthorizationError("Pasif kullanıcı fazla mesai işlemi yapamaz.")
    if getattr(actor, "deleted_at", None) is not None:
        raise OvertimeAuthorizationError("Silinmiş kullanıcı fazla mesai işlemi yapamaz.")


def require_permission(actor, code: str) -> None:
    """
    Aktörde izin kodu bulunmalıdır.

    Kiracı bağlamı kurulmamışsa izin kümesi boş döner → **fail-closed** reddeder.
    """
    require_active(actor)
    if not user_has_permission(actor, code):
        raise OvertimeAuthorizationError(f"Bu işlem için '{code}' izni gerekir.")


__all__ = [
    "OvertimeAuthorizationError",
    "OvertimeError",
    "OvertimeStateError",
    "OvertimeValidationError",
    "require_active",
    "require_permission",
]
