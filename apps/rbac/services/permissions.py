"""
Yetki çözümleme servisi.

Kural: **varsayılan reddet.** Açık izin yoksa işlem yapılmaz
(docs/DEVELOPMENT_RULES.md, docs/AUTHORIZATION.md).
"""

from django.db.models import Q
from django.utils import timezone

from apps.rbac.models import PermissionEffect, RolePermission, UserPermissionOverride, UserRole


def _active_role_ids(user) -> list:
    """Süresi geçmemiş roller."""
    now = timezone.now()
    return list(
        UserRole.objects.filter(user=user)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values_list("role_id", flat=True)
    )


def effective_permission_codes(user) -> set[str]:
    """
    Kullanıcının etkin izin kodları.

    (rollerin izinleri) + (allow istisnaları) - (deny istisnaları)
    """
    if user is None or not user.is_authenticated:
        return set()

    role_ids = _active_role_ids(user)
    role_codes = set(
        RolePermission.objects.filter(role_id__in=role_ids).values_list(
            "permission__code", flat=True
        )
    )
    overrides = UserPermissionOverride.objects.filter(user=user).values_list(
        "permission__code", "effect"
    )
    allowed = {code for code, effect in overrides if effect == PermissionEffect.ALLOW}
    denied = {code for code, effect in overrides if effect == PermissionEffect.DENY}
    return (role_codes | allowed) - denied


def user_has_permission(user, code: str) -> bool:
    """Tek izin kontrolü. `deny` istisnası varsa false döner."""
    if user is None or not user.is_authenticated:
        return False
    return code in effective_permission_codes(user)


def user_has_any_permission(user) -> bool:
    """Kullanıcının en az bir izni var mı (yönetim paneli kapısı için)."""
    return bool(effective_permission_codes(user))
