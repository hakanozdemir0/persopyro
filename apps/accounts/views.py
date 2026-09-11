"""
Kimlik doğrulama uç noktaları.

İki giriş yolu vardır ve ikisi de AYNI güvenlik adımlarından geçer
(parola → kilit kontrolü → MFA):

  * Oturum (tarayıcı): `POST /api/v1/auth/login`  → HttpOnly çerez + CSRF
  * Token (mobil/entegrasyon): `POST /api/v1/auth/token` → opak API token

Güvenlik notları:
  * Hata mesajları kullanıcı numaralandırmayı engellemek için GENEL tutulur;
    yalnızca parola doğruysa MFA'nın gerektiği bildirilir.
  * Oturum uçları CSRF korumalıdır (`csrf_protect`); token uçları çerez
    kullanmadığı için CSRF gerektirmez.
  * Giriş/çıkış olayları denetim kaydına yazılır.
  * Kullanıcı satırına dokunan HER işlem kiracı bağlamı içinde yapılır (RLS).
"""

from django.contrib.auth import authenticate, login, logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.mfa import (
    MfaConfigError,
    MfaInvalidError,
    MfaRequiredError,
    dogrula_totp,
)
from apps.accounts.models import ApiToken
from apps.accounts.serializers import GirisSerializer
from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.tenancy.context import tenant_context
from apps.tenancy.middleware import clear_session_tenant, resolve_tenant_id, set_session_tenant

GECERSIZ = {
    "error": {
        "code": "invalid_credentials",
        "message": "Giriş bilgileri geçersiz.",
    }
}
MFA_GEREKLI = {
    "error": {
        "code": "mfa_required",
        "message": "Çok faktörlü doğrulama kodu gerekli.",
    }
}
MFA_GECERSIZ = {
    "error": {
        "code": "mfa_invalid",
        "message": "Çok faktörlü doğrulama kodu geçersiz.",
    }
}


def _kullanici_ozeti(user) -> dict:  # noqa: ANN001
    """Yanıtta gösterilecek en az veri (kişisel veri minimizasyonu)."""
    return {
        "id": str(user.pk),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "tenant_id": str(user.tenant_id),
        "multi_factor_enabled": user.multi_factor_enabled,
    }


def _kimlik_dogrula(request, veri: dict, user_agent: str = ""):  # noqa: ANN001, ANN202
    """
    Kiracıyı çözer, parolayı doğrular ve MFA'yı uygular.

    Döner: (kullanici, tenant_id, hata_yaniti) — hata varsa kullanıcı None olur.
    """
    tenant_id = resolve_tenant_id(veri["tenant"])
    if tenant_id is None:
        # Kiracı bulunamadı: ayrıntı verilmez (numaralandırmayı önlemek için).
        return None, None, Response(GECERSIZ, status=status.HTTP_401_UNAUTHORIZED)

    kullanici = authenticate(
        request,
        username=veri["email"],
        password=veri["password"],
        tenant=tenant_id,
        user_agent=user_agent,
    )
    if kullanici is None:
        return None, tenant_id, Response(GECERSIZ, status=status.HTTP_401_UNAUTHORIZED)

    # MFA: parola doğrulandıktan SONRA uygulanır; böylece "MFA açık mı" bilgisi
    # parolayı bilmeyen birine sızmaz.
    try:
        dogrula_totp(kullanici, veri.get("totp_code"))
    except MfaRequiredError:
        return None, tenant_id, Response(MFA_GEREKLI, status=status.HTTP_401_UNAUTHORIZED)
    except MfaInvalidError:
        _mfa_denetim(tenant_id, kullanici, request, "mfa_gecersiz")
        return None, tenant_id, Response(MFA_GECERSIZ, status=status.HTTP_401_UNAUTHORIZED)
    except MfaConfigError:
        _mfa_denetim(tenant_id, kullanici, request, "mfa_yapilandirma_hatasi")
        return None, tenant_id, Response(GECERSIZ, status=status.HTTP_401_UNAUTHORIZED)

    return kullanici, tenant_id, None


def _mfa_denetim(tenant_id, user, request, reason: str) -> None:  # noqa: ANN001
    log_action(
        tenant_id=tenant_id,
        user=user,
        action=AuditAction.LOGIN_FAILED,
        table_name="users",
        record_id=getattr(user, "pk", None),
        changes={"reason": reason},
        request_id=getattr(request, "request_id", ""),
    )


class SessionLoginView(APIView):
    """Tarayıcı girişi: oturum çerezi (HttpOnly) + CSRF koruması."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @method_decorator(csrf_protect)
    def post(self, request):
        serializer = GirisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        veri = serializer.validated_data

        kullanici, tenant_id, hata = _kimlik_dogrula(request, veri)
        if hata is not None:
            return hata

        # Kiracı bağlamı ZORUNLUDUR: `login()` sinyali kullanıcı satırını
        # günceller (son giriş zamanı) ve RLS olmadan bu güncelleme 0 satır
        # etkiler → hata verir. Oturum anahtarı da burada döndürülür
        # (session fixation koruması).
        with tenant_context(tenant_id):
            login(request, kullanici)
            set_session_tenant(request, tenant_id)
        return Response({"data": _kullanici_ozeti(kullanici)}, status=status.HTTP_200_OK)


class SessionLogoutView(APIView):
    """Oturumu kapatır."""

    permission_classes = [IsAuthenticated]

    @method_decorator(csrf_protect)
    def post(self, request):
        kullanici = request.user
        tenant_id = kullanici.tenant_id
        log_action(
            tenant_id=tenant_id,
            user=kullanici,
            action=AuditAction.LOGOUT,
            table_name="users",
            record_id=kullanici.pk,
            request_id=getattr(request, "request_id", ""),
        )
        logout(request)
        clear_session_tenant(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TokenLoginView(APIView):
    """API istemcileri için token girişi (çerez yok, CSRF gerekmez)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        serializer = GirisSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        veri = serializer.validated_data

        kullanici, _tenant_id, hata = _kimlik_dogrula(
            request, veri, user_agent=request.META.get("HTTP_USER_AGENT", "")[:80]
        )
        if hata is not None:
            return hata

        kayit, token = ApiToken.issue(kullanici, label=veri.get("label", ""))
        return Response(
            {
                "data": {
                    "token": token,
                    "expires_at": kayit.expires_at,
                    "user": _kullanici_ozeti(kullanici),
                }
            },
            status=status.HTTP_201_CREATED,
        )


class TokenLogoutView(APIView):
    """Yalnızca isteği yapan token'ı iptal eder (diğer cihazlar etkilenmez)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.auth
        if token is not None:
            ApiToken.objects.filter(pk=token.pk).delete()
        log_action(
            tenant_id=request.user.tenant_id,
            user=request.user,
            action=AuditAction.LOGOUT,
            table_name="api_tokens",
            record_id=getattr(token, "pk", None),
            request_id=getattr(request, "request_id", ""),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
