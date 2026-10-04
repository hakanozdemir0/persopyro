"""Shared navigation for authenticated platform panel pages."""

from apps.platform.permissions import can_view_platform_admins, effective_permissions


def platform_navigation(request):  # noqa: ANN001, ANN201
    user_id = request.session.get("platform_user_id")
    if not user_id:
        return {
            "platform_nav_permissions": frozenset(),
            "platform_nav_can_view_admins": False,
        }
    permissions = effective_permissions(user_id)
    return {
        "platform_nav_permissions": permissions,
        "platform_nav_can_view_admins": can_view_platform_admins(user_id, permissions),
    }
