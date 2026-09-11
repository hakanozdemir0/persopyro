"""
İstek hattı (middleware).

SIRA KRİTİK: `TenantContextMiddleware`, Django'nun `AuthenticationMiddleware`
adımından ÖNCE çalışmalıdır. Aksi halde oturumdan kullanıcı yüklenemez, çünkü
RLS kiracı bağlamı olmadan satırı gizler (fail-closed).

Maliyet notu: Kiracı bağlamı işlem (transaction) ömrüne bağlı olduğu için istek
boyunca bir işlem açık tutulur. Bu bilinçli bir takastır: izolasyonun doğruluğu,
uzun işlem maliyetine tercih edilir. Ölçek büyüdüğünde (çok sunucu, bağlantı
havuzu) bu adım yeniden değerlendirilmelidir (docs/ARCHITECTURE.md bölüm 6.2).
"""

import uuid

from django.http import JsonResponse

from apps.tenancy.context import (
    current_tenant_id,
    tenant_context,
    tenant_id_by_domain,
    tenant_id_by_slug,
)

TENANT_SESSION_KEY = "tenant_id"


class RequestIdMiddleware:
    """Her isteğe bir kimlik verir; teknik loglarda ve denetim kaydında kullanılır."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request.request_id = incoming[:64] if incoming else uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        return response


class TenantContextMiddleware:
    """
    İstek boyunca kiracı bağlamını kurar.

    Çözümleme sırası:
      1. Oturumdaki kiracı (`tenant_id`).
      2. Alan adı (host) eşlemesi — giriş sayfası gibi oturumsuz istekler için.

    Güvenlik kuralı: İkisi de varsa ve FARKLIYSA istek reddedilir. Böylece bir
    kiracının oturumuyla başka kiracının alan adına yapılan istek (oturum
    taşıma/karıştırma) engellenir.

    Bağlam kurulamazsa istek bağlamsız devam eder; kiracıya bağlı hiçbir sorgu
    veri döndürmez (RLS) ve uygulama katmanı `require_tenant_id()` ile hata verir.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        oturum_tenant = (
            request.session.get(TENANT_SESSION_KEY) if hasattr(request, "session") else None
        )
        host_tenant = tenant_id_by_domain(_host(request))

        if oturum_tenant and host_tenant and str(oturum_tenant) != str(host_tenant):
            # Oturum ile alan adı uyuşmuyor: sessizce devam etmek yerine reddet.
            return JsonResponse(
                {
                    "error": {
                        "code": "tenant_mismatch",
                        "message": "Oturum bu alan adına ait değil.",
                    }
                },
                status=400,
            )

        tenant_id = oturum_tenant or host_tenant
        if tenant_id is None:
            return self.get_response(request)

        with tenant_context(tenant_id):
            response = self.get_response(request)
        return response


def _host(request) -> str:
    """İsteğin alan adı (port ve büyük harf ayıklanır)."""
    host = request.get_host().split(":")[0].strip().lower()
    return host


def set_session_tenant(request, tenant_id) -> None:
    """Giriş sonrası oturuma kiracıyı yazar (güvenlik: giriş anında sabitlenir)."""
    request.session[TENANT_SESSION_KEY] = str(tenant_id)


def clear_session_tenant(request) -> None:
    request.session.pop(TENANT_SESSION_KEY, None)


def resolve_request_tenant(request):
    """Aktif kiracı: önce uygulama bağlamı, sonra oturum."""
    return current_tenant_id() or request.session.get(TENANT_SESSION_KEY)


def resolve_tenant_id(identifier: str):  # noqa: ANN201
    """
    Kiracıyı slug, alan adı veya UUID ile çözer.

    Kimlik doğrulama öncesi çağrılır; veri döndürmez, yalnızca UUID çözer.
    """
    if not identifier:
        return None
    deger = identifier.strip().lower()
    try:
        return uuid.UUID(deger)
    except (ValueError, AttributeError):
        pass
    return tenant_id_by_slug(deger) or tenant_id_by_domain(deger)
