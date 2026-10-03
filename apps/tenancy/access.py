"""
Kiracı erişilebilirliği (bulgu F11 — kapalı).

KURAL: bir kiracının verisi yalnızca kiracı **uygun durumdayken** erişilebilir.

  * İzin verilen durumlar: `active`, `trial`
    * Reddedilen durumlar: başlangıç tarihi gelmemiş, bitiş tarihi geçmiş,
    `suspended`, `canceled` veya `deleted_at IS NOT NULL` şirket.

NEDEN UYGULAMA KATMANI: RLS politikaları yalnızca `tenant_id` eşitliğine bakar,
kiracının **durumuna** bakmaz. Askıya alınmış bir kiracının oturumu/token'ı bu
yüzden teknik olarak çalışmaya devam ediyordu (ölçüm: `TenantStatus` hiçbir kodda
okunmuyordu). Durum kontrolü burada, kimlik doğrulama ve istek yetkilendirme
katmanında yapılır; **RLS gevşetilmez ve `BYPASSRLS` kullanılmaz.**

Tek kaynak: `tenant_access_allowed()`. Şu yollar bu fonksiyonu kullanır:
  * giriş (parola doğrulamasından önce, `apps/accounts/views.py`)
  * istek hattı / oturum (`apps/tenancy/middleware.py`)
  * API token doğrulaması (`apps/accounts/authentication.py`)
  * istek düzeyinde yetki kararı (`apps/rbac/services/permissions.py`)

Fail-closed: kiracı satırı okunamıyorsa (yoksa) erişim **kapalıdır**.
"""

import uuid

from django.db import connection
from django.utils import timezone

from apps.tenancy.context import tenant_context

ALLOWED_TENANT_STATUSES = frozenset({"active", "trial"})

_TENANT_STATE_SQL = (
    "SELECT status, deleted_at, valid_from, valid_until "
    "FROM hr.tenants WHERE id = %s"
)


def tenant_state(tenant_id: uuid.UUID | str | None):  # noqa: ANN201
    """
    Kiracının durum/soft-delete/kullanım tarihlerini okur; kiracı yoksa `None`.

    Kiracı tablosu RLS + FORCE ile korunduğu için okuma kiracı bağlamı içinde
    yapılır (politika `id = current_tenant_id()`).
    """
    if tenant_id is None:
        return None
    with tenant_context(tenant_id):
        with connection.cursor() as cur:
            cur.execute(_TENANT_STATE_SQL, [str(tenant_id)])
            satir = cur.fetchone()
    return (satir[0], satir[1], satir[2], satir[3]) if satir else None


def tenant_access_allowed(tenant_id: uuid.UUID | str | None) -> bool:
    """
    Kiracı erişime açık mı?

    `tenant_id` None ise `True` döner: kiracı bağlamı yoksa kiracıya bağlı veri
    zaten erişilemez (RLS fail-closed); bu durum çağıranı engellemez.
    """
    if tenant_id is None:
        return True

    durum = tenant_state(tenant_id)
    if durum is None:
        return False  # kiracı bulunamadı → fail-closed

    status, deleted_at, valid_from, valid_until = durum
    return (
        deleted_at is None
        and status in ALLOWED_TENANT_STATUSES
        and (valid_from is None or valid_from <= timezone.localdate())
        and (valid_until is None or valid_until >= timezone.localdate())
    )


def tenant_block_reason(tenant_id: uuid.UUID | str | None) -> str:
    """Log/denetim için kısa gerekçe (kullanıcıya gösterilmez)."""
    if tenant_id is None:
        return "no_tenant"
    durum = tenant_state(tenant_id)
    if durum is None:
        return "tenant_not_found"
    status, deleted_at, valid_from, valid_until = durum
    if deleted_at is not None:
        return "tenant_deleted"
    if valid_from is not None and valid_from > timezone.localdate():
        return "tenant_not_started"
    if valid_until is not None and valid_until < timezone.localdate():
        return "tenant_expired"
    if status not in ALLOWED_TENANT_STATUSES:
        return f"tenant_{status}"
    return ""
