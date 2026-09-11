"""
RBAC servisleri.

Alt modüller:
  * `permissions` — etkin izin çözümlemesi ve izin kontrolleri
  * `roles` — kiracı için varsayılan rollerin üretilmesi

Çağıranlar `from apps.rbac.services import ...` biçiminde kullanır.
"""

from apps.rbac.services.permissions import (
    effective_permission_codes,
    user_has_any_permission,
    user_has_permission,
)
from apps.rbac.services.roles import MissingPermissionError, create_default_roles

__all__ = [
    "MissingPermissionError",
    "create_default_roles",
    "effective_permission_codes",
    "user_has_any_permission",
    "user_has_permission",
]
