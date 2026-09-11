"""
RBAC servisleri.

Alt modüller:
  * `permissions` — etkin izin çözümlemesi, istek düzeyinde yetki kararı
    (`authorized_permission_codes`) ve izin kontrolleri
  * `scope` — OD5 organizasyon kapsamı çözümlemesi (M10 sonrası gerçek alt ağaç;
    recursive CTE) + "izin ∧ kapsam" nesne kararı
  * `roles` — kiracı için varsayılan rollerin üretilmesi + guard'lı rol ataması

Çağıranlar `from apps.rbac.services import ...` biçiminde kullanır.

NOT: Yetki kararının **tek merkezi** `authorized_permission_codes(request)`'tir
(OD26 / F8 + F11). DRF yetki sınıfları ve görünümler bunu kullanır; token kapsamı
ile kesişim yalnızca orada uygulanır.
"""

from apps.rbac.services.permissions import (
    active_role_assignments,
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
    MAX_DEPTH,
    OrgScope,
    assignment_grants_permissions,
    assignment_org_scope,
    invalidate_org_scope_cache,
    object_in_org_scope,
    org_scope_required,
    resolve_assignment_org_scope,
    resolve_department_scope,
    user_org_scope,
)

__all__ = [
    "MAX_DEPTH",
    "MissingPermissionError",
    "OrgScope",
    "RoleAssignmentError",
    "active_role_assignments",
    "assign_role_to_user",
    "assignment_grants_permissions",
    "assignment_org_scope",
    "authorized_permission_codes",
    "can_assign_role",
    "create_default_roles",
    "declared_permission_codes",
    "effective_permission_codes",
    "invalidate_org_scope_cache",
    "object_in_org_scope",
    "org_scope_required",
    "resolve_assignment_org_scope",
    "resolve_department_scope",
    "user_has_any_permission",
    "user_has_permission",
    "user_org_scope",
]
