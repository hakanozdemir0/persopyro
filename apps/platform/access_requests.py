"""Platform personal-data access request validation (OD1/OD4/OD18)."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import connections, transaction

from apps.platform.permissions import effective_permissions
from apps.platform.services import record_platform_event
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

DEFAULT_ACCESS_TTL_MINUTES = 60
MAX_ACCESS_TTL_MINUTES = 60
MIN_PURPOSE_LENGTH = 20
MIN_PURPOSE_WORDS = 3

ACCESS_OPERATIONS = frozenset({"read", "update"})
ACCESSIBLE_COMPANY_FIELDS = frozenset(
    {
        "tenant.name",
        "tenant.legal_name",
        "tenant.tax_number",
        "tenant.email",
        "tenant.phone",
        "tenant.address",
        "tenant.country",
        "tenant.city",
        "tenant.slug",
        "tenant.domain",
        "tenant.status",
        "tenant.max_users",
        "tenant.max_storage_mb",
        "tenant.valid_until",
        "registry.plan_code",
        "registry.billing_ref",
        "registry.internal_note",
    }
)
COMPANY_FIELD_LABELS = {
    "tenant.name": "Şirket adı",
    "tenant.legal_name": "Tüzel unvan",
    "tenant.tax_number": "Vergi numarası",
    "tenant.email": "Şirket e-postası",
    "tenant.phone": "Telefon",
    "tenant.address": "Adres",
    "tenant.country": "Ülke",
    "tenant.city": "Şehir",
    "tenant.slug": "Şirket kodu",
    "tenant.domain": "Alan adı",
    "tenant.status": "Şirket durumu",
    "tenant.max_users": "Kullanıcı limiti",
    "tenant.max_storage_mb": "Depolama limiti",
    "tenant.valid_until": "Kullanım bitişi",
    "registry.plan_code": "Plan kodu",
    "registry.billing_ref": "Faturalama referansı",
    "registry.internal_note": "İç not",
}


class PlatformAccessRequestError(ValueError):
    """AccessRequest input violates its closed validation rules."""


@dataclass(frozen=True)
class AccessRequestSpec:
    operation: str
    fields: tuple[str, ...]
    purpose: str
    ttl_minutes: int


def validate_access_request(
    *,
    operation: str,
    fields: Sequence[str],
    purpose: str,
    ttl_minutes: int | str | None = None,
) -> AccessRequestSpec:
    """Validate a field-limited operation and return its normalized request data."""
    if operation not in ACCESS_OPERATIONS:
        raise PlatformAccessRequestError("İşlem yalnızca 'read' veya 'update' olabilir.")

    if isinstance(fields, (str, bytes)) or not fields:
        raise PlatformAccessRequestError("En az bir şirket alanı seçilmelidir.")
    normalized_fields = tuple(str(field).strip() for field in fields)
    if any(not field for field in normalized_fields):
        raise PlatformAccessRequestError("Boş alan adı kabul edilmez.")
    if len(set(normalized_fields)) != len(normalized_fields):
        raise PlatformAccessRequestError("Aynı alan birden fazla seçilemez.")
    unknown_fields = set(normalized_fields) - ACCESSIBLE_COMPANY_FIELDS
    if unknown_fields:
        raise PlatformAccessRequestError("İstenen alan izin verilen listede değil.")

    normalized_purpose = " ".join((purpose or "").split())
    if (
        len(normalized_purpose) < MIN_PURPOSE_LENGTH
        or len(normalized_purpose.split()) < MIN_PURPOSE_WORDS
    ):
        raise PlatformAccessRequestError(
            f"Gerekçe en az {MIN_PURPOSE_LENGTH} karakter ve "
            f"{MIN_PURPOSE_WORDS} kelime olmalıdır."
        )

    if ttl_minutes in (None, ""):
        ttl = DEFAULT_ACCESS_TTL_MINUTES
    else:
        if isinstance(ttl_minutes, bool):
            raise PlatformAccessRequestError("Erişim süresi dakika cinsinden tam sayı olmalıdır.")
        try:
            ttl = int(ttl_minutes)
        except (TypeError, ValueError) as exc:
            raise PlatformAccessRequestError(
                "Erişim süresi dakika cinsinden tam sayı olmalıdır."
            ) from exc
        if str(ttl).strip() != str(ttl_minutes).strip():
            raise PlatformAccessRequestError("Erişim süresi dakika cinsinden tam sayı olmalıdır.")

    if ttl <= 0 or ttl > MAX_ACCESS_TTL_MINUTES:
        raise PlatformAccessRequestError(
            f"Erişim süresi 1–{MAX_ACCESS_TTL_MINUTES} dakika arasında olmalıdır."
        )

    return AccessRequestSpec(
        operation=operation,
        fields=normalized_fields,
        purpose=normalized_purpose,
        ttl_minutes=ttl,
    )


def json_array_value(value):
    """Normalize JSONB array values across psycopg driver versions."""
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


@transaction.atomic(using="platform")
def create_access_request(*, requester_id, tenant_id, operation, fields, purpose,
                          ttl_minutes=None, ip_address=""):
    """Persist a validated request. Access is not granted until two approvals."""
    permissions = effective_permissions(requester_id)
    if "platform.tenant.read" not in permissions:
        raise PlatformAccessRequestError("Bu kullanıcı platform erişim talebi açamaz.")
    spec = validate_access_request(
        operation=operation, fields=fields, purpose=purpose, ttl_minutes=ttl_minutes
    )
    request_id = uuid.uuid4()
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM platform.tenant_registry WHERE tenant_id = %s AND deleted_at IS NULL",
            [str(tenant_id)],
        )
        if cursor.fetchone() is None:
            raise PlatformAccessRequestError("Etkin şirket kaydı bulunamadı.")
        cursor.execute(
            """INSERT INTO platform.access_requests
                   (id, tenant_id, requester_id, operation, fields, purpose, ttl_minutes)
                 VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)""",
            [str(request_id), str(tenant_id), str(requester_id), spec.operation,
             json.dumps(spec.fields), spec.purpose, spec.ttl_minutes],
        )
    record_platform_event(
        action="platform.access.requested", actor_user_id=requester_id,
        tenant_id=tenant_id,
        details={"request_id": str(request_id), "operation": spec.operation,
                 "fields": list(spec.fields), "purpose": spec.purpose,
                 "ttl_minutes": spec.ttl_minutes},
        ip_address=ip_address,
    )
    return request_id


@transaction.atomic(using="platform")
def decide_access_request(
    *, approver_id, tenant_id, request_id, decision, reason="", ip_address=""
):
    """Record one distinct platform decision; the second approval creates a grant."""
    if decision not in {"approve", "reject"}:
        raise PlatformAccessRequestError("Karar approve veya reject olmalıdır.")
    if "platform.tenant.manage" not in effective_permissions(approver_id):
        raise PlatformAccessRequestError("Bu kullanıcı erişim taleplerini karara bağlayamaz.")
    if decision == "reject" and len(" ".join((reason or "").split())) < 10:
        raise PlatformAccessRequestError("Red gerekçesi en az 10 karakter olmalıdır.")

    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT id, tenant_id, requester_id, operation, fields, purpose,
                      ttl_minutes, status
                 FROM platform.access_requests WHERE id = %s FOR UPDATE""",
            [str(request_id)],
        )
        row = cursor.fetchone()
        if row is None:
            raise PlatformAccessRequestError("Erişim talebi bulunamadı.")
        if str(row[1]) != str(tenant_id):
            raise PlatformAccessRequestError("Talep bu şirkete ait değil.")
        if row[7] != "pending":
            raise PlatformAccessRequestError("Bu talep artık karara açık değil.")
        if str(row[2]) == str(approver_id):
            raise PlatformAccessRequestError("Talep sahibi kendi talebini onaylayamaz.")
        cursor.execute(
            "SELECT 1 FROM platform.access_approvals WHERE request_id = %s AND approver_id = %s",
            [str(request_id), str(approver_id)],
        )
        if cursor.fetchone() is not None:
            raise PlatformAccessRequestError("Bu kullanıcı bu talep için zaten karar verdi.")
        cursor.execute(
            """INSERT INTO platform.access_approvals
                   (request_id, approver_id, decision, reason)
                 VALUES (%s, %s, %s, %s)""",
            [str(request_id), str(approver_id), decision, " ".join((reason or "").split())],
        )
        if decision == "reject":
            cursor.execute(
                "UPDATE platform.access_requests SET status = 'rejected', "
                "decided_at = now() WHERE id = %s",
                [str(request_id)],
            )
            resulting_grant = None
        else:
            cursor.execute(
                "SELECT count(*) FROM platform.access_approvals "
                "WHERE request_id = %s AND decision = 'approve'",
                [str(request_id)],
            )
            if cursor.fetchone()[0] < 2:
                resulting_grant = None
            else:
                cursor.execute(
                    """INSERT INTO platform.access_grants
                           (request_id, tenant_id, operation, fields, purpose, requester_id,
                            starts_at, expires_at)
                         VALUES (%s, %s, %s, %s::jsonb, %s, %s, now(),
                                 now() + (%s * interval '1 minute'))
                         RETURNING id""",
                    [
                        str(request_id), str(row[1]), row[3],
                        json.dumps(json_array_value(row[4])), row[5], str(row[2]), row[6],
                    ],
                )
                resulting_grant = cursor.fetchone()[0]
                cursor.execute(
                    "UPDATE platform.access_requests SET status = 'approved', "
                    "decided_at = now() WHERE id = %s",
                    [str(request_id)],
                )
    record_platform_event(
        action="platform.access.rejected" if decision == "reject" else "platform.access.approved",
        actor_user_id=approver_id, tenant_id=row[1],
        details={"request_id": str(request_id), "decision": decision,
                 "reason": " ".join((reason or "").split()),
                 "grant_id": str(resulting_grant) if resulting_grant else None},
        ip_address=ip_address,
    )
    return resulting_grant


def active_access_grant(*, grant_id, tenant_id, operation, fields):
    """Return the grant only when tenant, operation, fields and expiry all match."""
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT id, requester_id, purpose, fields, expires_at
                 FROM platform.access_grants
                WHERE id = %s AND tenant_id = %s AND operation = %s
                  AND revoked_at IS NULL AND starts_at <= now() AND expires_at > now()""",
            [str(grant_id), str(tenant_id), operation],
        )
        row = cursor.fetchone()
    if row is None or not set(fields).issubset(set(json_array_value(row[3]))):
        return None
    return row


TENANT_PROFILE_FIELDS = {
    "tenant.name": "name",
    "tenant.legal_name": "legal_name",
    "tenant.tax_number": "tax_number",
    "tenant.email": "email",
    "tenant.phone": "phone",
    "tenant.address": "address",
    "tenant.country": "country",
    "tenant.city": "city",
    "tenant.slug": "slug",
    "tenant.domain": "domain",
    "tenant.status": "status",
    "tenant.max_users": "max_users",
    "tenant.max_storage_mb": "max_storage_mb",
    "tenant.valid_until": "valid_until",
}


def read_company_profile(*, grant_id, requester_id, tenant_id, ip_address=""):
    """Read only the canonical tenant fields covered by an active read grant."""
    grant = active_access_grant(
        grant_id=grant_id, tenant_id=tenant_id, operation="read",
        fields=(),
    )
    if grant is None or str(grant[1]) != str(requester_id):
        raise PlatformAccessRequestError("Etkin görüntüleme izni bulunamadı.")
    granted_fields = set(json_array_value(grant[3])) & TENANT_PROFILE_FIELDS.keys()
    with tenant_context(tenant_id):
        tenant = Tenant.objects.filter(pk=tenant_id, deleted_at__isnull=True).first()
        if tenant is None:
            raise PlatformAccessRequestError("Şirket bulunamadı.")
        result = {}
        for key in granted_fields:
            value = getattr(tenant, TENANT_PROFILE_FIELDS[key])
            result[key] = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    record_platform_event(
        action="platform.access.used", actor_user_id=requester_id,
        tenant_id=tenant_id,
        details={"grant_id": str(grant_id), "operation": "read",
                 "fields": sorted(granted_fields)},
        ip_address=ip_address,
    )
    return result
