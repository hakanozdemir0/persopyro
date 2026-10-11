"""
Kimlik doğrulama backend'i (uygulama katmanı).

Neden kendi backend'imiz var: e-posta KİRACI BAŞINA tekildir. Django'nun
`ModelBackend`'i global tekil e-posta varsayar ve `get_by_natural_key` ile tek
kayıt bekler; çok kiracılı yapıda bu yanlıştır ve birden çok kayıt bulabilir.

İlkeler:
  * **Varsayılan reddet:** kiracı belirlenemiyorsa kimlik doğrulama yapılmaz.
  * **Kiracı bağlamı zorunludur:** kullanıcı sorgusu ve güncellemeleri, RLS
    nedeniyle kiracı bağlamı KURULDUKTAN sonra yapılır. Bu adım atlanırsa
    hiçbir kullanıcı bulunamaz (RLS fail-closed davranır) ve giriş imkânsız olur.
  * **Zamanlama sızıntısı olmasın:** kullanıcı bulunamasa da parola doğrulaması
    yapılmış gibi eşit maliyetli bir işlem yapılır.
  * **Kilitlenme:** art arda başarısız denemede hesap geçici olarak kilitlenir.
  * **Denetim kaydı:** başarılı/başarısız giriş denemeleri kayıtlanır.

NOT (oturum ile kimlik doğrulama): `get_user(user_id)` de kiracı bağlamı ister.
Bu yüzden istek hattında (middleware) kiracı bağlamı, Django'nun
`AuthenticationMiddleware`'inden ÖNCE kurulmalıdır. Bu, faz 2'nin ikinci
diliminde eklenecek istek hattı işidir (docs/ARCHITECTURE.md bölüm 22).
"""

import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.tenancy.context import tenant_context

# Kullanıcı bulunamadığında da aynı maliyette parola doğrulaması yapmak için
# kullanılan sabit hash (gerçek bir parolaya karşılık gelmez).
_DUMMY_HASH = make_password("persopyro-dummy-password-for-timing-equalization")


class TenantScopedModelBackend:
    """Kiracı bağlamı zorunlu, e-posta + parola ile kimlik doğrulama."""

    def authenticate(  # noqa: PLR0911 - net olması için erken dönüşler tercih edildi
        self,
        request,
        username: str | None = None,
        password: str | None = None,
        tenant=None,
        **kwargs,
    ):
        email = (username or "").strip().lower()
        if not email or not password:
            return None

        tenant_id = _resolve_tenant_id(tenant)
        if tenant_id is None:
            # Kiracı belirlenemiyorsa kimlik doğrulama yapılmaz (fail-closed).
            return None

        # Kiracı bağlamı olmadan kullanıcı satırı GÖRÜNMEZ; bu yüzden tüm
        # kimlik doğrulama akışı bağlam içinde çalışır.
        with tenant_context(tenant_id):
            try:
                user = User.objects.get(tenant_id=tenant_id, email=email)
            except User.DoesNotExist:
                # Zamanlama eşitleme: kullanıcı yoksa da parola maliyeti ödenir.
                check_password(password, _DUMMY_HASH)
                _log_safe(
                    request, tenant_id, None, AuditAction.LOGIN_FAILED, {"reason": "kullanici_yok"}
                )
                return None

            # Kilit penceresi sona erdiyse eski deneme sayacını yeni pencereye
            # taşımayız; aksi halde tek bir yazım hatası hesabı yeniden kilitler.
            if user.lockout_until is not None and user.lockout_until <= timezone.now():
                user.failed_login_count = 0
                user.lockout_until = None
                user.save(update_fields=["failed_login_count", "lockout_until", "updated_at"])

            if user.is_locked:
                _log_safe(request, tenant_id, None, AuditAction.LOGIN_FAILED, {"reason": "kilitli"})
                return None

            if not user.is_active:
                _log_safe(request, tenant_id, None, AuditAction.LOGIN_FAILED, {"reason": "pasif"})
                return None

            if not user.check_password(password):
                _register_failed_attempt(user)
                _log_safe(request, tenant_id, None, AuditAction.LOGIN_FAILED, {"reason": "parola"})
                return None

            _register_successful_login(user)
            _log_safe(request, tenant_id, user, AuditAction.LOGIN, {})
            return user

    def get_user(self, user_id):
        """
        Oturumdan kullanıcıyı yükler.

        DİKKAT: RLS nedeniyle kiracı bağlamı kurulu olmalıdır; aksi halde None
        döner. İstek hattı, kimlik doğrulama ara yazılımından önce kiracı
        bağlamını kurmalıdır.
        """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


def _resolve_tenant_id(tenant) -> uuid.UUID | None:
    """Kiracı; Tenant nesnesi, UUID veya string olabilir."""
    if tenant is None:
        return None
    tenant_id = getattr(tenant, "pk", tenant)
    try:
        return uuid.UUID(str(tenant_id))
    except (TypeError, ValueError):
        return None


def _register_failed_attempt(user: User) -> None:
    user.failed_login_count += 1
    update_fields = ["failed_login_count", "updated_at"]
    if user.failed_login_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
        user.lockout_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
        update_fields.append("lockout_until")
    user.save(update_fields=update_fields)


def _register_successful_login(user: User) -> None:
    user.failed_login_count = 0
    user.lockout_until = None
    user.last_login_at = timezone.now()
    user.save(update_fields=["failed_login_count", "lockout_until", "last_login_at", "updated_at"])


def _log_safe(request, tenant_id, user, action: str, changes: dict) -> None:
    """Giriş denetimi yazılamazsa kimlik doğrulamayı fail-closed durdur."""
    ip_address = None
    request_id = ""
    if request is not None:
        ip_address = _client_ip(request)
        request_id = getattr(request, "request_id", "")
    log_action(
        tenant_id=tenant_id,
        user=user,
        action=action,
        table_name="users",
        record_id=getattr(user, "pk", None),
        changes=changes,
        request_id=request_id,
        ip_address=ip_address,
    )


def _client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
