"""
API token'ı için kiracı bağlamı ara yazılımı.

NEDEN GEREKLİ: `TenantContextMiddleware` kiracıyı oturumdan veya alan adından
çözer. Token ile gelen (tarayıcı dışı) isteklerde ikisi de yoktur; kiracı
yalnızca token'ın kendisinden öğrenilebilir. Bu ara yazılım, token'ı çözüp
isteğin GERİ KALANI için kiracı bağlamını kurar. Aksi halde DRF'in kimlik
doğrulama adımı doğru kullanıcıyı bulsa bile, görünüm içindeki sorgular
bağlamsız kalır ve RLS fail-closed davranır (boş sonuç).

SIRA: `TenantContextMiddleware`'den SONRA, `AuthenticationMiddleware`'den ÖNCE
çalışmalıdır.

Bu ara yazılım yalnızca BAĞLAMI kurar; yetki kararı vermez. Doğrulama (süre,
kullanıcı durumu, iptal) `ApiTokenAuthentication` içinde yapılır.
"""

from apps.accounts.authentication import token_from_header
from apps.tenancy.context import current_tenant_id, tenant_context


class ApiTokenTenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if current_tenant_id() is not None:
            # Bağlam zaten kurulu (oturum veya alan adı).
            return self.get_response(request)

        kayit = token_from_header(request.META.get("HTTP_AUTHORIZATION", ""))
        if kayit is None:
            return self.get_response(request)

        # Doğrulama adımının aynı kaydı kullanması için isteğe iliştirilir.
        request._api_token = kayit
        with tenant_context(kayit.tenant_id):
            return self.get_response(request)
