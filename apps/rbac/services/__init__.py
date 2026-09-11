"""
RBAC servisleri.

Alt modüller:
  * `permissions` — etkin izin çözümlemesi, istek düzeyinde yetki kararı
    (`authorized_permission_codes`) ve izin kontrolleri
  * `roles` — kiracı için varsayılan rollerin üretilmesi

Çağıranlar `from apps.rbac.services import ...` biçiminde kullanır.

NOT: Yetki kararının **tek merkezi** `authorized_permission_codes(request)`'tir
(OD26 / F8 + F11). DRF yetki sınıfları ve görünümler bunu kullanır; token kapsamı
ile kesişim yalnızca orada uygulanır.
"""

from apps.rbac.services.permissions import (
    authorized_permission_codes,
    effective_permission_codes,
    user_has_any_permission,
    user_has_permission,
)
from apps.rbac.services.roles import MissingPermissionError, create_default_roles

__all__ = [
    "MissingPermissionError",
    "authorized_permission_codes",
    "create_default_roles",
    "effective_permission_codes",
    "user_has_any_permission",
    "user_has_permission",
]
