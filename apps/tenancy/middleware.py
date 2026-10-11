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

from apps.tenancy.access import tenant_access_allowed, tenant_block_reason
from apps.tenancy.context import (
    current_tenant_id,
    tenant_context,
    tenant_id_by_domain,
    tenant_id_by_slug,
)

TENANT_SESSION_KEY = "tenant_id"

# Kiracı durumu uygun olmadığında bile erişilebilen yollar: yalnızca sağlık uçları.
# Bunlar kiracı verisi döndürmez ve kimlik doğrulama gerektirmez (F11 istisnası).
TENANT_EXEMPT_PATHS = frozenset(
    {
        "/api/health",
        "/api/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/token",
        "/test-ui/login/",
    }
)


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
      2. `Authorization: Token ...` başlığındaki API token'ın kiracısı.
      3. Alan adı (host) eşlemesi — giriş sayfası gibi oturumsuz istekler için.

    NEDEN TOKEN (OD26 / F8 + F11): token ile gelen istekte kiracı bağlamı
    kurulmazsa yetki çözümlemesi (RLS'li RBAC tabloları) boş küme döner ve
    kesişim kuralı işlevsiz kalır; ayrıca kiracıya bağlı hiçbir sorgu veri
    gösteremez. Token'ın kiracısı bu yüzden istek hattında kurulur ve token kaydı
    `request._api_token` üzerine bırakılır (kimlik doğrulama ikinci sorgu yapmaz).

    Güvenlik kuralı: Kaynaklar (oturum / token / alan adı) birden fazla kiracı
    gösteriyorsa istek reddedilir. Böylece bir kiracının oturumu veya token'ıyla
    başka kiracının alan adına yapılan istek (oturum/bağlam karıştırma) engellenir.

    Bağlam kurulamazsa istek bağlamsız devam eder; kiracıya bağlı hiçbir sorgu
    veri döndürmez (RLS) ve uygulama katmanı `require_tenant_id()` ile hata verir.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Platform paneli kendi kimlik doğrulamasını ve kiracı bağlamını kurar.
        # Aynı tarayıcıdaki süresi dolmuş şirket oturumu platform operatörünü
        # engellememeli veya platform oturumunu düşürmemelidir.
        if request.path == "/platform" or request.path.startswith("/platform/"):
            return self.get_response(request)

        oturum_tenant = (
            request.session.get(TENANT_SESSION_KEY) if hasattr(request, "session") else None
        )
        host_tenant = tenant_id_by_domain(_host(request))
        token_tenant = _token_tenant(request)

        gosterilenler = {
            str(deger) for deger in (oturum_tenant, host_tenant, token_tenant) if deger
        }
        if len(gosterilenler) > 1:
            # Kaynaklar farklı kiracı gösteriyor: sessizce devam etmek yerine reddet.
            return JsonResponse(
                {
                    "error": {
                        "code": "tenant_mismatch",
                        "message": "Oturum bu alan adına ait değil.",
                    }
                },
                status=400,
            )

        tenant_id = oturum_tenant or token_tenant or host_tenant
        if tenant_id is None:
            return self.get_response(request)

        # F11 (kapalı): kiracı askıda/iptal/silinmişse erişim KESİLİR. Oturum
        # anahtarı temizlenir; böylece kullanıcı başka bir kiracıyla giriş
        # yapabilir. RLS gevşetilmez, `BYPASSRLS` kullanılmaz.
        if not tenant_access_allowed(tenant_id) and request.path not in TENANT_EXEMPT_PATHS:
            reason = tenant_block_reason(tenant_id)
            if oturum_tenant:
                for key in (
                    TENANT_SESSION_KEY,
                    "_auth_user_id",
                    "_auth_user_backend",
                    "_auth_user_hash",
                    "auth_version",
                ):
                    request.session.pop(key, None)
            else:
                clear_session_tenant(request)
            expired = reason == "tenant_expired"
            return JsonResponse(
                {
                    "error": {
                        "code": "tenant_expired" if expired else "tenant_unavailable",
                        "message": (
                            "Şirketinizin kullanım süresi sona erdi. Oturumunuz kapatılıyor."
                            if expired
                            else "Kiracının erişimi kapalı."
                        ),
                    }
                },
                status=403,
            )

        with tenant_context(tenant_id):
            response = self.get_response(request)
        return response


def _host(request) -> str:
    """İsteğin alan adı (port ve büyük harf ayıklanır)."""
    host = request.get_host().split(":")[0].strip().lower()
    return host


def _token_tenant(request):  # noqa: ANN001, ANN202
    """
    `Authorization: Token ...` başlığından kiracı kimliğini çözer.

    İptal edilmiş/süresi geçmiş/geçersiz token `None` döner: bağlam kurulmaz ve
    DRF kimlik doğrulaması isteği zaten `401` ile reddeder. Token kaydı
    `request._api_token` üzerine bırakılır; böylece aynı istekte ikinci kez
    sorgulanmaz (kimlik doğrulama tüm kontrolleri yine kendisi yapar).

    İçe aktarma fonksiyon içinde: `apps.accounts` → `apps.tenancy` yönünde modül
    düzeyinde döngüsel bağımlılık oluşmasın.
    """
    from rest_framework.authentication import get_authorization_header

    from apps.accounts.authentication import token_from_header

    try:
        baslik = get_authorization_header(request).decode()
    except UnicodeDecodeError:
        return None

    kayit = token_from_header(baslik)
    if kayit is None:
        return None

    request._api_token = kayit  # noqa: SLF001 - istek içi önbellek (bilinçli)
    return kayit.tenant_id


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
