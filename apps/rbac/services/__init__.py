"""
RBAC servisleri.

Alt modüller:
  * `permissions` — etkin izin çözümlemesi, istek düzeyinde yetki kararı
    (`authorized_permission_codes`) ve izin kontrolleri
  * `scope` — OD5 organizasyon kapsamı köprüsü (M9 kısmi: M10 öncesi fail-closed)
  * `roles` — kiracı için varsayılan rollerin üretilmesi + guard'lı rol ataması

Çağıranlar `from apps.rbac.services import ...` biçiminde kullanır.

NOT: Yetki kararının **tek merkezi** `authorized_permission_codes(request)`'tir
(OD26 / F8 + F11). DRF yetki sınıfları ve görünümler bunu kullanır; token kapsamı
ile kesişim yalnızca orada uygulanır.
"""

from apps.rbac.services.permissions import (
    authorized_permission_codes,
    declared_permission_codes,
    effective_permission_codes,
    user_has_any_permission,
    user_has_permission,
)
from apps.rbac.services.roles import (
    MissingPermissionError,
    RoleAssignmentError,
    assign_role_to_user,
    can_assign_role,
    create_default_roles,
)
from apps.rbac.services.scope import (
    assignment_grants_permissions,
    org_scope_required,
    resolve_assignment_org_scope,
)

__all__ = [
    "MissingPermissionError",
    "RoleAssignmentError",
    "assign_role_to_user",
    "assignment_grants_permissions",
    "authorized_permission_codes",
    "can_assign_role",
    "create_default_roles",
    "declared_permission_codes",
    "effective_permission_codes",
    "org_scope_required",
    "resolve_assignment_org_scope",
    "user_has_any_permission",
    "user_has_permission",
]
