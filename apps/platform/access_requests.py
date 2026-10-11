"""Platform personal-data access request validation (OD1/OD4/OD18)."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import connections, transaction
from django.utils import timezone

from apps.platform.permissions import effective_permissions
from apps.platform.services import record_platform_event
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

DEFAULT_ACCESS_TTL_MINUTES = 60
MAX_ACCESS_TTL_MINUTES = 60
MAX_ACCESS_RESOURCE_COUNT = 50
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
ACCESSIBLE_EMPLOYEE_FIELDS = frozenset(
    {
        "employee.name",
        "employee.work_contact",
        "employee.personal_contact",
        "employee.identity",
        "employee.birth_date",
        "employee.address",
        "employee.bank",
        "employee.emergency_contact",
        "employee.citizenship",
        "employee.work_permit",
        "employee.health",
        "employee.disability",
        "employee.biometric",
    }
)
EMPLOYEE_FIELD_LABELS = {
    "employee.name": "Ad ve soyad",
    "employee.work_contact": "İş e-postası ve telefonu",
    "employee.personal_contact": "Kişisel e-posta ve telefon",
    "employee.identity": "Kimlik numarası",
    "employee.birth_date": "Doğum tarihi",
    "employee.address": "Adres",
    "employee.bank": "IBAN ve banka",
    "employee.emergency_contact": "Acil kişi bilgileri",
    "employee.citizenship": "Vatandaşlık",
    "employee.work_permit": "Çalışma izni bilgileri",
    "employee.health": "Sağlık bilgileri (K3)",
    "employee.disability": "Engellilik bilgisi (K3)",
    "employee.biometric": "Biyometrik referans (K3)",
}
EMPLOYEE_FIELD_CHOICES = tuple(sorted(EMPLOYEE_FIELD_LABELS.items()))


class PlatformAccessRequestError(ValueError):
    """AccessRequest input violates its closed validation rules."""


def _validated_uuid(value, label: str) -> str:  # noqa: ANN001
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as error:
        raise PlatformAccessRequestError(f"{label} biçimi geçersiz.") from error


@dataclass(frozen=True)
class AccessRequestSpec:
    operation: str
    fields: tuple[str, ...]
    resources: tuple[str, ...]
    purpose: str
    ttl_minutes: int


def validate_access_request(
    *,
    operation: str,
    fields: Sequence[str],
    purpose: str,
    ttl_minutes: int | str | None = None,
    resources: Sequence[str] = (),
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
    unknown_fields = set(normalized_fields) - ACCESSIBLE_COMPANY_FIELDS - ACCESSIBLE_EMPLOYEE_FIELDS
    if unknown_fields:
        raise PlatformAccessRequestError("İstenen alan izin verilen listede değil.")
    if operation == "update" and set(normalized_fields) & ACCESSIBLE_EMPLOYEE_FIELDS:
        raise PlatformAccessRequestError(
            "Çalışan kişisel verisi için yalnızca okuma talep edilebilir."
        )
    employee_fields_requested = bool(set(normalized_fields) & ACCESSIBLE_EMPLOYEE_FIELDS)
    if isinstance(resources, (str, bytes)):
        raise PlatformAccessRequestError("Çalışan kayıtları liste biçiminde gönderilmelidir.")
    try:
        normalized_resources = tuple(_validated_uuid(item, "Çalışan kimliği") for item in resources)
    except TypeError as error:
        raise PlatformAccessRequestError("Çalışan kayıtları geçersiz.") from error
    if len(set(normalized_resources)) != len(normalized_resources):
        raise PlatformAccessRequestError("Aynı çalışan kaydı birden fazla seçilemez.")
    if employee_fields_requested and not normalized_resources:
        raise PlatformAccessRequestError("En az bir çalışan kaydı seçilmelidir.")
    if len(normalized_resources) > MAX_ACCESS_RESOURCE_COUNT:
        raise PlatformAccessRequestError(
            f"Bir erişim talebinde en fazla {MAX_ACCESS_RESOURCE_COUNT} çalışan seçilebilir."
        )
    if normalized_resources and not employee_fields_requested:
        raise PlatformAccessRequestError("Çalışan kaydı seçmek için çalışan alanı da seçilmelidir.")

    normalized_purpose = " ".join((purpose or "").split())
    if (
        len(normalized_purpose) < MIN_PURPOSE_LENGTH
        or len(normalized_purpose.split()) < MIN_PURPOSE_WORDS
    ):
        raise PlatformAccessRequestError(
            f"Gerekçe en az {MIN_PURPOSE_LENGTH} karakter ve {MIN_PURPOSE_WORDS} kelime olmalıdır."
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
        resources=normalized_resources,
        purpose=normalized_purpose,
        ttl_minutes=ttl,
    )


def json_array_value(value):
    """Normalize JSONB array values across psycopg driver versions."""
    parsed = json.loads(value) if isinstance(value, str) else value
    return parsed if isinstance(parsed, list) else []


@transaction.atomic(using="platform")
def create_access_request(
    *,
    requester_id,
    tenant_id,
    operation,
    fields,
    purpose,
    ttl_minutes=None,
    resources=(),
    ip_address="",
):
    """Persist a validated request. Access is not granted until two approvals."""
    permissions = effective_permissions(requester_id)
    if "platform.tenant.read" not in permissions:
        raise PlatformAccessRequestError("Bu kullanıcı platform erişim talebi açamaz.")
    spec = validate_access_request(
        operation=operation,
        fields=fields,
        purpose=purpose,
        ttl_minutes=ttl_minutes,
        resources=resources,
    )
    request_id = uuid.uuid4()
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM platform.tenant_registry WHERE tenant_id = %s AND deleted_at IS NULL",
            [str(tenant_id)],
        )
        if cursor.fetchone() is None:
            raise PlatformAccessRequestError("Etkin şirket kaydı bulunamadı.")
        if spec.resources:
            from apps.organization.models import Employee

            with tenant_context(tenant_id):
                found_resources = set(
                    str(value)
                    for value in Employee.objects.filter(
                        tenant_id=tenant_id,
                        pk__in=spec.resources,
                        deleted_at__isnull=True,
                    ).values_list("pk", flat=True)
                )
            if found_resources != set(spec.resources):
                raise PlatformAccessRequestError(
                    "Seçilen çalışan kayıtlarından biri bu şirket içinde etkin değil."
                )
        cursor.execute(
            """INSERT INTO platform.access_requests
                   (id, tenant_id, requester_id, operation, fields, resource_ids, purpose,
                    ttl_minutes)
                 VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
            [
                str(request_id),
                str(tenant_id),
                str(requester_id),
                spec.operation,
                json.dumps(spec.fields),
                json.dumps(spec.resources),
                spec.purpose,
                spec.ttl_minutes,
            ],
        )
    record_platform_event(
        action="platform.access.requested",
        actor_user_id=requester_id,
        tenant_id=tenant_id,
        details={
            "request_id": str(request_id),
            "operation": spec.operation,
            "fields": list(spec.fields),
            "resource_count": len(spec.resources),
            "purpose": spec.purpose,
            "ttl_minutes": spec.ttl_minutes,
        },
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
    request_id = _validated_uuid(request_id, "Talep kimliği")
    if "platform.tenant.manage" not in effective_permissions(approver_id):
        raise PlatformAccessRequestError("Bu kullanıcı erişim taleplerini karara bağlayamaz.")
    if decision == "reject" and len(" ".join((reason or "").split())) < 10:
        raise PlatformAccessRequestError("Red gerekçesi en az 10 karakter olmalıdır.")

    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT id, tenant_id, requester_id, operation, fields, purpose,
                      ttl_minutes, status, resource_ids
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
                           (request_id, tenant_id, operation, fields, resource_ids, purpose,
                            requester_id, starts_at, expires_at)
                         VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, now(),
                                 now() + (%s * interval '1 minute'))
                         RETURNING id""",
                    [
                        str(request_id),
                        str(row[1]),
                        row[3],
                        json.dumps(json_array_value(row[4])),
                        json.dumps(json_array_value(row[8])),
                        row[5],
                        str(row[2]),
                        row[6],
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
        actor_user_id=approver_id,
        tenant_id=row[1],
        details={
            "request_id": str(request_id),
            "decision": decision,
            "reason": " ".join((reason or "").split()),
            "grant_id": str(resulting_grant) if resulting_grant else None,
        },
        ip_address=ip_address,
    )
    return resulting_grant


def active_access_grant(*, grant_id, tenant_id, operation, fields):
    """Return the grant only when tenant, operation, fields and expiry all match."""
    grant_id = _validated_uuid(grant_id, "Erişim izni kimliği")
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT id, requester_id, purpose, fields, expires_at, resource_ids
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
        grant_id=grant_id,
        tenant_id=tenant_id,
        operation="read",
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
        action="platform.access.used",
        actor_user_id=requester_id,
        tenant_id=tenant_id,
        details={"grant_id": str(grant_id), "operation": "read", "fields": sorted(granted_fields)},
        ip_address=ip_address,
    )
    return result


def read_employee_people(*, grant_id, requester_id, tenant_id, ip_address=""):
    """Read only employee fields explicitly covered by the requester's active grant."""
    from django.contrib.auth import get_user_model
    from django.db.models import Prefetch

    from apps.organization.models import Employee, EmployeePersonalData

    if "platform.tenant.read" not in effective_permissions(requester_id):
        raise PlatformAccessRequestError("Bu kullanıcı çalışan verisi görüntüleyemez.")

    grant = active_access_grant(
        grant_id=grant_id,
        tenant_id=tenant_id,
        operation="read",
        fields=(),
    )
    if grant is None or str(grant[1]) != str(requester_id):
        raise PlatformAccessRequestError("Etkin çalışan verisi erişim izni bulunamadı.")
    granted_fields = set(json_array_value(grant[3])) & ACCESSIBLE_EMPLOYEE_FIELDS
    granted_resources = set(json_array_value(grant[5]))
    if not granted_fields or not granted_resources:
        raise PlatformAccessRequestError("İzin çalışan verisi kapsamıyor.")

    personal_column_by_scope = {
        "employee.name": ("first_name", "last_name"),
        "employee.personal_contact": ("personal_email", "personal_phone"),
        "employee.identity": ("national_id",),
        "employee.birth_date": ("birth_date",),
        "employee.address": ("address",),
        "employee.bank": ("iban", "bank_name"),
        "employee.emergency_contact": (
            "emergency_contact_phone",
            "emergency_contact_relation",
        ),
        "employee.citizenship": ("citizenship",),
        "employee.work_permit": ("work_permit_type", "work_permit_valid_until"),
        "employee.health": ("blood_type", "health_notes"),
        "employee.disability": ("disability_status",),
        "employee.biometric": ("biometric_ref",),
    }
    personal_columns = {
        column
        for code in granted_fields
        for column in personal_column_by_scope.get(code, ())
    }
    employee_query = Employee.objects.filter(
        tenant_id=tenant_id,
        deleted_at__isnull=True,
        pk__in=granted_resources,
    )
    employee_query = employee_query.only(
        "id", "tenant_id", "employee_no", "status", "hire_date", "termination_date", "user_id"
    )
    if "employee.name" in granted_fields or "employee.work_contact" in granted_fields:
        employee_query = employee_query.prefetch_related(
            Prefetch(
                "user",
                queryset=get_user_model().objects.only(
                    "id", "first_name", "last_name", "email", "phone"
                ),
            )
        )
    if personal_columns:
        employee_query = employee_query.prefetch_related(
            Prefetch(
                "personal_data",
                queryset=EmployeePersonalData.objects.only(
                    "employee_id", "tenant_id", *sorted(personal_columns)
                ),
            )
        )

    with tenant_context(tenant_id):
        employees = list(employee_query.order_by("employee_no"))
        result = []
        for employee in employees:
            if personal_columns:
                try:
                    personal = employee.personal_data
                except Employee.personal_data.RelatedObjectDoesNotExist:
                    personal = None
            else:
                personal = None
            user = (
                employee.user
                if employee.user_id
                and ("employee.name" in granted_fields or "employee.work_contact" in granted_fields)
                else None
            )
            details = {}
            for code in granted_fields:
                if code == "employee.name":
                    details[code] = (
                        f"{personal.first_name} {personal.last_name}".strip()
                        if personal
                        else f"{user.first_name} {user.last_name}".strip() if user else ""
                    )
                elif code == "employee.work_contact":
                    details[code] = {
                        "email": user.email if user else "",
                        "phone": user.phone if user else "",
                    }
                elif code == "employee.personal_contact":
                    details[code] = {
                        "email": personal.personal_email if personal else "",
                        "phone": personal.personal_phone if personal else "",
                    }
                elif code == "employee.identity":
                    details[code] = personal.national_id if personal else ""
                elif code == "employee.birth_date":
                    value = personal.birth_date if personal else None
                    details[code] = value.isoformat() if value else ""
                elif code == "employee.address":
                    details[code] = personal.address if personal else ""
                elif code == "employee.bank":
                    details[code] = {
                        "iban": personal.iban if personal else "",
                        "bank": personal.bank_name if personal else "",
                    }
                elif code == "employee.emergency_contact":
                    details[code] = {
                        "phone": personal.emergency_contact_phone if personal else "",
                        "relation": personal.emergency_contact_relation if personal else "",
                    }
                elif code == "employee.citizenship":
                    details[code] = personal.citizenship if personal else ""
                elif code == "employee.work_permit":
                    details[code] = {
                        "type": personal.work_permit_type if personal else "",
                        "valid_until": (
                            personal.work_permit_valid_until.isoformat()
                            if personal and personal.work_permit_valid_until
                            else ""
                        ),
                    }
                elif code == "employee.health":
                    details[code] = {
                        "blood_type": personal.blood_type if personal else "",
                        "notes": personal.health_notes if personal else "",
                    }
                elif code == "employee.disability":
                    details[code] = personal.disability_status if personal else ""
                elif code == "employee.biometric":
                    details[code] = personal.biometric_ref if personal else ""
            result.append(
                {
                    "employee_no": employee.employee_no,
                    "status": employee.get_status_display(),
                    "hire_date": employee.hire_date.isoformat(),
                    "termination_date": (
                        employee.termination_date.isoformat() if employee.termination_date else None
                    ),
                    "details": details,
                }
            )

    record_platform_event(
        action="platform.access.used",
        actor_user_id=requester_id,
        tenant_id=tenant_id,
        details={
            "grant_id": str(grant_id),
            "operation": "read",
            "fields": sorted(granted_fields),
            "employee_count": len(result),
        },
        ip_address=ip_address,
    )
    return result


def list_tenant_access_requests(*, tenant_id, requester_id, include_pending=False):
    """List the requester's history and, for approvers, pending requests only."""
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT r.id, r.requester_id, r.operation, r.fields, r.purpose, r.status,
                      r.ttl_minutes, r.created_at,
                      (SELECT count(*) FROM platform.access_approvals a
                        WHERE a.request_id = r.id),
                      g.id, g.expires_at, g.revoked_at, r.resource_ids
                 FROM platform.access_requests r
                 LEFT JOIN platform.access_grants g ON g.request_id = r.id
                WHERE r.tenant_id = %s
                  AND (r.requester_id = %s OR (%s AND r.status = 'pending'))
                ORDER BY r.created_at DESC LIMIT 50""",
            [str(tenant_id), str(requester_id), bool(include_pending)],
        )
        rows = cursor.fetchall()
    return [
        {
            "id": str(row[0]),
            "requester_id": str(row[1]),
            "operation": row[2],
            "fields": json_array_value(row[3]),
            "purpose": row[4],
            "status": row[5],
            "ttl_minutes": row[6],
            "created_at": row[7],
            "approval_count": row[8],
            "grant_id": str(row[9]) if row[9] else "",
            "expires_at": row[10],
            "revoked_at": row[11],
            "resources": json_array_value(row[12]),
            "grant_active": bool(row[10] and row[10] > timezone.now() and row[11] is None),
        }
        for row in rows
    ]


def revoke_access_grant(*, grant_id, actor_id, tenant_id, ip_address=""):
    """Revoke an active grant by its requester or an access manager."""
    grant_id = _validated_uuid(grant_id, "Erişim izni kimliği")
    permissions = effective_permissions(actor_id)
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """UPDATE platform.access_grants
                  SET revoked_at = now(), revoked_by_id = %s
                WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL
                  AND expires_at > now()
                  AND (requester_id = %s OR %s)
                RETURNING id, operation, fields""",
            [
                str(actor_id),
                str(grant_id),
                str(tenant_id),
                str(actor_id),
                "platform.tenant.manage" in permissions,
            ],
        )
        row = cursor.fetchone()
    if row is None:
        raise PlatformAccessRequestError("Etkin erişim izni bulunamadı veya iptal edilemez.")
    record_platform_event(
        action="platform.access.revoked",
        actor_user_id=actor_id,
        tenant_id=tenant_id,
        details={
            "grant_id": str(row[0]),
            "operation": row[1],
            "fields": json_array_value(row[2]),
        },
        ip_address=ip_address,
    )
