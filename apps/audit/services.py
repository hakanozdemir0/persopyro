"""
Denetim kaydı yazma servisi.

Tek giriş noktası: `log_action(...)`. Böylece "log yazmayı unutma" riski azalır ve
kayıt biçimi tek yerde tutulur.

Kurallar:
  * Kiracı bağlamı kurulur (RLS nedeniyle zorunludur); kayıt kiracıya bağlanır.
  * `changes` içine kişisel veri DEĞERİ yazılmaz; yalnızca alan adı ve güvenli
    temsil (ör. "maskelendi" / değişim var/yok) yazılır.
  * Denetim tablosu yalnızca eklenebilir (UPDATE/DELETE yetkisi verilmez).
"""

import uuid

from apps.audit.models import AuditLog
from apps.tenancy.context import tenant_context


def log_action(
    *,
    tenant_id: uuid.UUID | str,
    action: str,
    user=None,
    table_name: str = "",
    record_id: uuid.UUID | str | None = None,
    changes: dict | None = None,
    request_id: str = "",
    ip_address: str | None = None,
) -> AuditLog:
    """Denetim kaydı oluşturur. `tenant_id` zorunludur."""
    if tenant_id is None:
        raise ValueError("Denetim kaydı kiracı olmadan yazılamaz.")

    with tenant_context(tenant_id):
        return AuditLog.objects.create(
            tenant_id=tenant_id,
            user=user,
            action=action,
            table_name=table_name,
            record_id=record_id,
            changes=changes or {},
            request_id=request_id,
            ip_address=ip_address,
        )


def masked_changes(before: dict, after: dict, masked_fields: set[str] | None = None) -> dict:
    """
    İki kayıt arasındaki farkı, kişisel veri DEĞERİ olmadan özetler.

    Değerler yerine yalnızca "hangi alan değişti" bilgisi tutulur; maskelenmesi
    gereken alanlar bu şekilde işaretlenir.
    """
    masked = masked_fields or set()
    result: dict[str, dict[str, str]] = {}
    for field in set(before) | set(after):
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if field in masked:
            result[field] = {"before": "[maskeli]", "after": "[maskeli]"}
        else:
            result[field] = {"before": _safe(old), "after": _safe(new)}
    return result


def _safe(value) -> str | None:
    if value is None:
        return None
    return str(value)[:120]
