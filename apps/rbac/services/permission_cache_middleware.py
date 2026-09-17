"""
P1-B performans — request-scope izin önbelleği middleware'i.

`effective_permission_codes` aynı istek içinde aynı kullanıcı için tekrar tekrar
DB'ye sorgu göndermemek için contextvars tabanlı önbellek kullanır
(`apps/rbac/services/permissions.py`). Bu middleware önbelleği:

* istek başında **temizler** (önceki istekten değer kalmaz),
* istek sonunda **temizler** (bellek sızması olmaz).

Böylece önbelleğin yaşam döngüsü tam olarak **tek istektir**; RBAC satırları
request dışında değişse bile (admin işlemi, job) yeni istek taze DB'den okur.

Kullanıcı/tenant izolasyonu: önbellek user.pk anahtarlıdır ve contextvars
task-safe'tir; farklı kullanıcı/tenant arasında paylaşım oluşmaz.
"""

from apps.rbac.services.permissions import (
    invalidate_permission_cache,
    set_request_scope_permission_cache,
)


class PermissionCacheMiddleware:
    """Her istek başında/sonunda izin önbelleğini sıfırlar (request-scope garanti)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        invalidate_permission_cache()
        set_request_scope_permission_cache(True)
        try:
            return self.get_response(request)
        finally:
            set_request_scope_permission_cache(False)
            invalidate_permission_cache()
