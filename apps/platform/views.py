import json
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import DomainNameValidator, validate_email, validate_slug
from django.db import IntegrityError, connection, connections, transaction
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
)
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods, require_POST

from apps.organization.models import Employee
from apps.platform.access_requests import json_array_value
from apps.platform.constants import TENANT_PLAN_CHOICES, TENANT_PROFILE_FIELDS
from apps.platform.permissions import effective_permissions, platform_user_row
from apps.platform.services import record_platform_event
from apps.rbac.models import UserRole
from apps.rbac.services.roles import create_default_roles
from apps.tenancy.context import tenant_context
from apps.tenancy.file_policy import (
    DEFAULT_FILE_EXTENSIONS,
    DEFAULT_FILE_MAX_MB,
    FILE_EXTENSION_CHOICES,
    tenant_file_usage_bytes,
)
from apps.tenancy.models import Tenant

SESSION_KEY = "platform_user_id"
MAX_LOGIN_FAILURES = 5
LOCKOUT_DURATION = timedelta(minutes=15)


def _current_platform_user(request):  # noqa: ANN001, ANN202
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    row = platform_user_row(user_id)
    if row is None or row[3] != "active" or not row[4] or row[5] is not None:
        request.session.flush()
        return None
    return row


def _plan_choices():
    return [(row[0], row[1]) for row in _plan_specs()]


def _plan_specs():
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT code, label, max_users, max_storage_mb, max_active_employees, "
            "max_employee_records FROM platform.company_plans WHERE active "
            "ORDER BY sort_order, label"
        )
        rows = cursor.fetchall()
    return rows or [(code, label, 10, 500, 0, 0) for code, label in TENANT_PLAN_CHOICES]


@require_http_methods(["GET", "POST"])
def platform_login(request):  # noqa: ANN001, ANN201
    if request.method == "GET":
        if _current_platform_user(request):
            return redirect("platform-dashboard")
        return render(request, "platform/login.html")

    email = request.POST.get("email", "").strip().lower()
    password = request.POST.get("password", "")
    with transaction.atomic(using="platform"):
        with connections["platform"].cursor() as cursor:
            cursor.execute(
                """SELECT id, password, status, is_active, deleted_at,
                          failed_login_count, lockout_until
                     FROM platform.users WHERE email = %s FOR UPDATE""",
                [email],
            )
            user = cursor.fetchone()
            now = timezone.now()
            valid = bool(
                user
                and user[2] == "active"
                and user[3]
                and user[4] is None
                and (user[6] is None or user[6] <= now)
                and check_password(password, user[1])
            )
            if user and user[4] is None and user[2] == "active" and user[3]:
                if valid:
                    cursor.execute(
                        "UPDATE platform.users SET failed_login_count = 0, lockout_until = NULL, "
                        "updated_at = now() WHERE id = %s",
                        [str(user[0])],
                    )
                elif user[6] is None or user[6] <= now:
                    failures = user[5] + 1
                    lockout = now + LOCKOUT_DURATION if failures >= MAX_LOGIN_FAILURES else None
                    cursor.execute(
                        "UPDATE platform.users SET failed_login_count = %s, lockout_until = %s, "
                        "updated_at = now() WHERE id = %s",
                        [failures, lockout, str(user[0])],
                    )

    record_platform_event(
        action="platform.login.succeeded" if valid else "platform.login.failed",
        actor_user_id=user[0] if user else None,
        details={"result": "success" if valid else "invalid_credentials"},
        ip_address=request.META.get("REMOTE_ADDR", ""),
    )
    if not valid:
        return render(
            request,
            "platform/login.html",
            {"error": "Giriş bilgileri geçersiz."},
            status=401,
        )

    request.session.cycle_key()
    request.session[SESSION_KEY] = str(user[0])
    return redirect("platform-dashboard")


@require_http_methods(["GET"])
def platform_dashboard(request):  # noqa: ANN001, ANN201
    user = _current_platform_user(request)
    if user is None:
        return redirect("platform-login")
    permissions = effective_permissions(user[0])
    if "platform.tenant.read" not in permissions:
        return HttpResponseForbidden("Bu panele erişim yetkiniz yok.")
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT tenant_id, slug, domain, status, max_users, plan_code, created_at
                 FROM platform.tenant_registry WHERE deleted_at IS NULL ORDER BY slug"""
        )
        tenants = [
            (*row[:5], dict(TENANT_PLAN_CHOICES).get(row[5], row[5]), *row[6:])
            for row in cursor.fetchall()
        ]
    record_platform_event(
        action="platform.tenant.list.read",
        actor_user_id=user[0],
        details={"count": len(tenants)},
        ip_address=request.META.get("REMOTE_ADDR", ""),
    )
    tab = request.GET.get("tab", "companies")
    return render(
        request,
        "platform/dashboard.html",
        {
            "email": user[2],
            "tenants": tenants,
            "can_create_tenant": "platform.tenant.create" in permissions,
            "can_manage_companies": "platform.settings.manage" in permissions,
            "plan_choices": _plan_choices(),
            "plan_specs": _plan_specs(),
            "profile_fields": TENANT_PROFILE_FIELDS,
            "tab": tab,
        },
    )


@require_http_methods(["GET", "POST"])
def platform_plans(request):  # noqa: ANN001, ANN201
    actor = _current_platform_user(request)
    if actor is None:
        return redirect("platform-login")
    if "platform.settings.manage" not in effective_permissions(actor[0]):
        return HttpResponseForbidden("Planları yönetme yetkiniz yok.")
    if request.method == "POST":
        code = request.POST.get("code", "").strip().lower()
        if request.POST.get("action") == "delete":
            with connections["platform"].cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM platform.tenant_registry WHERE plan_code = %s", [code]
                )
                usage_count = cursor.fetchone()[0]
                if usage_count:
                    return redirect(f"/platform/plans/?error=in_use&count={usage_count}#{code}")
                cursor.execute("DELETE FROM platform.company_plans WHERE code = %s", [code])
            record_platform_event(
                action="platform.plan.deleted",
                actor_user_id=actor[0],
                details={"plan_code": code},
                ip_address=request.META.get("REMOTE_ADDR", ""),
            )
            return redirect("/platform/plans/?removed=1")
        label = request.POST.get("label", "").strip()
        description = request.POST.get("description", "").strip()
        try:
            limits = [
                int(request.POST.get(key, "0"))
                for key in ("max_users", "max_active_employees", "max_employee_records")
            ]
            storage = Decimal(request.POST.get("max_storage_mb", "0"))
            if (
                not code
                or len(code) > 40
                or not code.replace("_", "").replace("-", "").isalnum()
                or not label
                or len(label) > 100
                or len(description) > 500
                or any(value < 0 for value in limits)
                or not storage.is_finite()
                or storage < 0
            ):
                raise ValueError
        except (ValueError, InvalidOperation):
            return HttpResponseBadRequest("Plan adı, kodu ve limitleri geçerli olmalıdır.")
        with connections["platform"].cursor() as cursor:
            cursor.execute(
                """INSERT INTO platform.company_plans
                (code, label, description, max_users, max_storage_mb,
                 max_active_employees, max_employee_records, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s,
                    COALESCE((SELECT max(sort_order)+1 FROM platform.company_plans), 1))
                ON CONFLICT (code) DO UPDATE SET
                    label=EXCLUDED.label, description=EXCLUDED.description,
                    max_users=EXCLUDED.max_users, max_storage_mb=EXCLUDED.max_storage_mb,
                    max_active_employees=EXCLUDED.max_active_employees,
                    max_employee_records=EXCLUDED.max_employee_records,
                    active=true, updated_at=now()""",
                [code, label, description, limits[0], storage, limits[1], limits[2]],
            )
        record_platform_event(
            action="platform.plan.saved",
            actor_user_id=actor[0],
            details={"plan_code": code},
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
        return redirect("/platform/plans/?saved=1")
    with connections["platform"].cursor() as cursor:
        cursor.execute("""SELECT p.code, p.label, p.description, p.max_users,
            p.max_storage_mb, p.max_active_employees, p.max_employee_records, p.active,
            (SELECT count(*) FROM platform.tenant_registry r WHERE r.plan_code = p.code)
            FROM platform.company_plans p ORDER BY p.sort_order, p.label""")
        plans = cursor.fetchall()
    return render(
        request,
        "platform/plans.html",
        {
            "email": actor[2],
            "plans": plans,
            "error": request.GET.get("error"),
            "error_count": request.GET.get("count", ""),
            "saved": request.GET.get("saved") == "1",
            "removed": request.GET.get("removed") == "1",
        },
    )


@require_POST
def platform_logout(request):  # noqa: ANN001, ANN201
    actor_user_id = request.session.get(SESSION_KEY)
    if actor_user_id:
        record_platform_event(
            action="platform.logout",
            actor_user_id=actor_user_id,
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
    request.session.flush()
    return redirect("platform-login")


@require_POST
def update_tenant_profile(request, tenant_id):  # noqa: ANN001, ANN201
    user = _current_platform_user(request)
    if user is None:
        return redirect("platform-login")
    if "platform.settings.manage" not in effective_permissions(user[0]):
        return HttpResponseForbidden("Şirket bilgilerini yönetme yetkiniz yok.")

    optional_fields = {code for code, _label in TENANT_PROFILE_FIELDS}
    profile_fields = set(optional_fields)
    if profile_fields - optional_fields:
        return HttpResponseBadRequest("Şirket profili alanı geçersiz.")

    values = {field: request.POST.get(field, "").strip() for field in optional_fields}
    values["name"] = request.POST.get("name", "").strip()
    if not values["name"] or len(values["name"]) > 200:
        return HttpResponseBadRequest("Şirket adı zorunludur (en fazla 200 karakter).")
    field_limits = {
        "legal_name": 250,
        "tax_number": 50,
        "email": 190,
        "phone": 30,
        "country": 100,
        "city": 100,
        "address": 5000,
    }
    for field, maximum in field_limits.items():
        if len(values[field]) > maximum:
            label = dict(TENANT_PROFILE_FIELDS)[field]
            return HttpResponseBadRequest(f"{label} en fazla {maximum} karakter olabilir.")
    required_fields = set()
    if values["email"]:
        try:
            validate_email(values["email"])
        except ValidationError:
            return HttpResponseBadRequest("Şirket e-posta adresi geçerli değil.")

    plan_choices = _plan_choices()
    plan_code = request.POST.get("plan_code", "").strip()
    if plan_code not in dict(plan_choices):
        return HttpResponseBadRequest("Plan seçimi geçersiz.")
    status = request.POST.get("status", "").strip()
    if status not in {value for value, _label in Tenant._meta.get_field("status").choices}:
        return HttpResponseBadRequest("Şirket durumu geçersiz.")

    raw_dates = {
        field: request.POST.get(field, "").strip() for field in ("valid_from", "valid_until")
    }
    try:
        date_values = {
            field: date.fromisoformat(value) if value else None
            for field, value in raw_dates.items()
        }
    except ValueError:
        return HttpResponseBadRequest("Tarihler YYYY-AA-GG biçiminde olmalıdır.")
    if (
        date_values["valid_from"]
        and date_values["valid_until"]
        and date_values["valid_from"] > date_values["valid_until"]
    ):
        return HttpResponseBadRequest("Kullanım başlangıcı bitiş tarihinden sonra olamaz.")

    try:
        limits = {
            field: int(request.POST.get(field, ""))
            for field in ("max_users", "max_active_employees", "max_employee_records")
        }
        storage = Decimal(request.POST.get("max_storage_mb", ""))
        if any(value < 0 for value in limits.values()) or not storage.is_finite() or storage < 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        return HttpResponseBadRequest(
            "Limitler sıfır veya daha büyük sayı olmalıdır; 0 sınırsızdır."
        )
    if storage.as_tuple().exponent < -2 or storage >= Decimal("10000000000"):
        return HttpResponseBadRequest(
            "Depolama limiti en fazla iki ondalık basamakla girilmelidir."
        )
    try:
        file_max_mb = Decimal(request.POST.get("employee_storage_max_mb", str(DEFAULT_FILE_MAX_MB)))
        if not file_max_mb.is_finite() or file_max_mb < Decimal("0.1") or file_max_mb > 50:
            raise ValueError
    except (ValueError, InvalidOperation):
        return HttpResponseBadRequest("Dosya boyutu sınırı 0,1 ile 50 MB arasında olmalıdır.")
    allowed_extensions = sorted(set(request.POST.getlist("employee_file_extensions")))
    if "employee_file_extensions_present" not in request.POST:
        allowed_extensions = list(DEFAULT_FILE_EXTENSIONS)
    if set(allowed_extensions) - set(DEFAULT_FILE_EXTENSIONS):
        return HttpResponseBadRequest("İzin verilen dosya türlerinden biri geçersiz.")

    try:
        custom_fields = json.loads(request.POST.get("custom_fields_json", "[]"))
        if not isinstance(custom_fields, list) or len(custom_fields) > 40:
            raise ValueError
        normalized_custom_fields, seen_codes = [], set()
        for item in custom_fields:
            label = str(item.get("label", "")).strip()
            code = str(item.get("code", "")).strip().lower()
            if not code and label:
                base_code = "custom_" + (slugify(label).replace("-", "_") or "alan")
                code = base_code
                suffix = 2
                while code in seen_codes:
                    code = f"{base_code}_{suffix}"
                    suffix += 1
            kind = str(item.get("type", "text"))
            required = False
            options = [
                str(value).strip() for value in item.get("options", []) if str(value).strip()
            ]
            if (
                not code.startswith("custom_")
                or not code[7:].replace("_", "").isalnum()
                or code in seen_codes
                or not label
                or len(label) > 100
                or kind not in {"text", "email", "number", "date", "select"}
                or (kind == "select" and not options)
            ):
                raise ValueError
            seen_codes.add(code)
            normalized_custom_fields.append(
                {
                    "code": code,
                    "label": label,
                    "type": kind,
                    "required": required,
                    "options": options if kind == "select" else [],
                }
            )
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return HttpResponseBadRequest("Özel şirket alanı tanımı geçersiz.")

    notify_company = request.POST.get("notify_company") == "on"
    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "SELECT plan_code, profile_fields FROM platform.tenant_registry WHERE tenant_id = %s",
            [str(tenant_id)],
        )
        registry_values = cursor.fetchone()
    if registry_values is None:
        return HttpResponseNotFound("Şirket bulunamadı.")
    metadata_changed = (
        registry_values[0] != plan_code
        or set(json_array_value(registry_values[1])) != profile_fields
    )

    try:
        with tenant_context(tenant_id):
            tenant = Tenant.objects.get(pk=tenant_id, deleted_at__isnull=True)
            if notify_company and not tenant.email:
                return HttpResponseBadRequest(
                    "Bildirim göndermek için şirket e-posta adresi gereklidir."
                )
            changed_fields = []
            update_values = {
                "name": values["name"],
                **{field: values[field] for field in profile_fields},
                **limits,
                "max_storage_mb": storage,
                **date_values,
                "status": status,
            }
            settings_value = dict(tenant.settings or {})
            settings_changed = False
            for key, value in (
                ("employee_storage_max_mb", float(file_max_mb)),
                ("employee_file_extensions", allowed_extensions),
            ):
                if settings_value.get(key, DEFAULT_FILE_MAX_MB if key.endswith("max_mb") else list(DEFAULT_FILE_EXTENSIONS)) != value:
                    settings_value[key] = value
                    changed_fields.append(key)
                    settings_changed = True
            custom_values = {}
            for item in normalized_custom_fields:
                value = request.POST.get(f"custom_value_{item['code']}", "").strip()
                if item["type"] == "email" and value:
                    try:
                        validate_email(value)
                    except ValidationError:
                        return HttpResponseBadRequest(
                            f"{item['label']} geçerli bir e-posta olmalıdır."
                        )
                if item["type"] == "select" and value and value not in item["options"]:
                    return HttpResponseBadRequest(f"{item['label']} seçeneği geçersiz.")
                custom_values[item["code"]] = value
            for key, value in (
                ("company_profile_values", custom_values),
                ("company_profile_fields", normalized_custom_fields),
                ("company_required_fields", sorted(required_fields)),
            ):
                if settings_value.get(key) != value:
                    settings_value[key] = value
                    changed_fields.append(key)
                    settings_changed = True
            tenant.settings = settings_value
            for field, value in update_values.items():
                current = getattr(tenant, field)
                if current != value:
                    changed_fields.append(field)
                    setattr(tenant, field, value)
            if changed_fields:
                update_fields = [field for field in changed_fields if field in update_values]
                if settings_changed:
                    update_fields.append("settings")
                tenant.save(update_fields=[*update_fields, "updated_at"])
    except Tenant.DoesNotExist:
        return HttpResponseNotFound("Şirket bulunamadı.")

    if metadata_changed:
        with connections["platform"].cursor() as cursor:
            cursor.execute(
                """UPDATE platform.tenant_registry
                      SET plan_code = %s, profile_fields = %s::jsonb, updated_at = now()
                    WHERE tenant_id = %s""",
                [plan_code, json.dumps(sorted(profile_fields)), str(tenant_id)],
            )
    changed_fields = sorted(
        set(changed_fields) | ({"plan_code", "profile_fields"} if metadata_changed else set())
    )
    if changed_fields:
        record_platform_event(
            action="platform.tenant.profile.updated",
            actor_user_id=user[0],
            tenant_id=tenant_id,
            details={"changed_fields": changed_fields},
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
    notice = "not-requested"
    if notify_company:
        try:
            sent = send_mail(
                subject="Şirket bilgileriniz güncellendi",
                message=(
                    "PersoPyro şirket bilgileriniz güncellendi. Ayrıntılar için "
                    "şirket yönetim panelinize giriş yapabilirsiniz."
                ),
                from_email=None,
                recipient_list=[tenant.email],
                fail_silently=False,
            )
            notice = "sent" if sent else "failed"
        except Exception:  # SMTP/provider errors must not undo the saved company profile.
            notice = "failed"
        record_platform_event(
            action=(
                "platform.tenant.notification.sent"
                if notice == "sent"
                else "platform.tenant.notification.failed"
            ),
            actor_user_id=user[0],
            tenant_id=tenant_id,
            details={"recipient_count": 1},
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )

    return redirect(
        f"/platform/tenants/{tenant_id}/?saved=1&changed={int(bool(changed_fields))}"
        f"&notification={notice}#company-edit"
    )


@require_POST
def create_tenant(request):  # noqa: ANN001, ANN201
    actor = _current_platform_user(request)
    if actor is None:
        return redirect("platform-login")
    if "platform.tenant.create" not in effective_permissions(actor[0]):
        return HttpResponseForbidden("Şirket oluşturma yetkiniz yok.")

    name = request.POST.get("name", "").strip()
    slug = request.POST.get("slug", "").strip().lower()
    domain = request.POST.get("domain", "").strip().lower() or None
    email = request.POST.get("admin_email", "").strip().lower()
    first_name = request.POST.get("admin_first_name", "").strip()
    last_name = request.POST.get("admin_last_name", "").strip()
    password = request.POST.get("admin_password", "")
    profile_field_codes = {code for code, _label in TENANT_PROFILE_FIELDS}
    profile_fields = set(profile_field_codes)
    required_profile_fields = set()
    profile_values = {field: request.POST.get(field, "").strip() for field in profile_field_codes}
    plan_code = request.POST.get("plan_code", "trial").strip() or "trial"
    try:
        custom_fields = json.loads(request.POST.get("custom_fields_json", "[]"))
        if not isinstance(custom_fields, list) or len(custom_fields) > 40:
            raise ValueError("En fazla 40 özel şirket alanı tanımlanabilir.")
        custom_definitions, custom_values, used_codes = [], {}, set()
        for item in custom_fields:
            label = str(item.get("label", "")).strip()
            kind = str(item.get("type", "text"))
            required = False
            options = [
                str(option).strip() for option in item.get("options", []) if str(option).strip()
            ]
            base = "custom_" + (slugify(label).replace("-", "_") or "alan")
            code, suffix = base, 2
            while code in used_codes:
                code, suffix = f"{base}_{suffix}", suffix + 1
            if (
                not label
                or len(label) > 100
                or kind not in {"text", "email", "number", "date", "select"}
                or (kind == "select" and not options)
            ):
                raise ValueError("Özel şirket alanı adı, türü veya seçenekleri geçersiz.")
            value = request.POST.get(f"custom_value_{code}", "").strip()
            if kind == "email" and value:
                validate_email(value)
            if kind == "select" and value and value not in options:
                raise ValueError(f"{label} için seçilen seçenek geçersiz.")
            used_codes.add(code)
            custom_definitions.append(
                {
                    "code": code,
                    "label": label,
                    "type": kind,
                    "required": required,
                    "options": options if kind == "select" else [],
                }
            )
            custom_values[code] = value
        max_users = int(request.POST.get("max_users", "10"))
        max_active_employees = int(request.POST.get("max_active_employees", "0"))
        max_employee_records = int(request.POST.get("max_employee_records", "0"))
        max_storage_mb = Decimal(request.POST.get("max_storage_mb", "500"))
        employee_file_max_mb = Decimal(request.POST.get("employee_storage_max_mb", str(DEFAULT_FILE_MAX_MB)))
        employee_file_extensions = sorted(set(request.POST.getlist("employee_file_extensions")))
        valid_from_raw = request.POST.get("valid_from", "").strip()
        valid_until_raw = request.POST.get("valid_until", "").strip()
        valid_from = date.fromisoformat(valid_from_raw) if valid_from_raw else None
        valid_until = date.fromisoformat(valid_until_raw) if valid_until_raw else None
        if profile_fields - profile_field_codes:
            raise ValueError("Şirket profili alanı geçersiz.")
        if plan_code not in dict(_plan_choices()):
            raise ValueError("Plan seçimi geçersiz.")
        if valid_from and valid_until and valid_from > valid_until:
            raise ValueError("Kullanım başlangıcı bitiş tarihinden sonra olamaz.")
        if any(value < 0 for value in (max_users, max_active_employees, max_employee_records)):
            raise ValueError("Limitler sıfır veya daha büyük olmalıdır.")
        if (
            not max_storage_mb.is_finite()
            or max_storage_mb < 0
            or max_storage_mb >= Decimal("10000000000")
        ):
            raise ValueError("Depolama limiti geçersiz.")
        if max_storage_mb.as_tuple().exponent < -2:
            raise ValueError("Depolama limiti en fazla iki ondalık basamak içerebilir.")
        if not employee_file_max_mb.is_finite() or employee_file_max_mb < Decimal("0.1") or employee_file_max_mb > 50:
            raise ValueError("Dosya boyutu sınırı 0,1 ile 50 MB arasında olmalıdır.")
        if set(employee_file_extensions) - set(DEFAULT_FILE_EXTENSIONS):
            raise ValueError("İzin verilen dosya türlerinden biri geçersiz.")
        lengths = {
            "legal_name": 250,
            "tax_number": 50,
            "email": 190,
            "phone": 30,
            "country": 100,
            "city": 100,
            "address": 5000,
        }
        for field, maximum in lengths.items():
            if len(profile_values[field]) > maximum:
                label = dict(TENANT_PROFILE_FIELDS)[field]
                raise ValueError(f"{label} en fazla {maximum} karakter olabilir.")
        if profile_values["email"]:
            validate_email(profile_values["email"])
        if not name or len(name) > 200:
            raise ValueError("Şirket adı zorunludur (en fazla 200 karakter).")
        if not slug or len(slug) > 60:
            raise ValueError("Şirket kodu zorunludur (en fazla 60 karakter).")
        validate_slug(slug)
        if domain:
            DomainNameValidator(accept_idna=True)(domain)
        validate_email(email)
        if not first_name or not last_name:
            raise ValueError("İlk şirket yöneticisinin adı ve soyadı zorunludur.")
        if len(first_name) > 150 or len(last_name) > 150:
            raise ValueError("Yönetici adı/soyadı en fazla 150 karakter olabilir.")
        if max_users < 0:
            raise ValueError("Kullanıcı limiti sıfır veya daha büyük olmalıdır; 0 sınırsızdır.")
        if not password:
            raise ValueError("İlk yöneticinin parolası zorunludur.")
        validate_password(password)
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError, ValidationError) as error:
        messages = getattr(error, "messages", [str(error)])
        return HttpResponseBadRequest(" ".join(messages))

    company_email = profile_values["email"]

    tenant_id = uuid.uuid4()
    try:
        with transaction.atomic():
            with tenant_context(tenant_id):
                tenant = Tenant.objects.create(
                    id=tenant_id,
                    name=name,
                    slug=slug,
                    domain=domain,
                    status="trial",
                    max_users=max_users,
                    max_storage_mb=max_storage_mb,
                    max_active_employees=max_active_employees,
                    max_employee_records=max_employee_records,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    settings={
                        "employee_storage_max_mb": float(employee_file_max_mb),
                        "employee_file_extensions": employee_file_extensions or list(DEFAULT_FILE_EXTENSIONS),
                        "company_required_fields": sorted(required_profile_fields),
                        "company_profile_fields": custom_definitions,
                        "company_profile_values": custom_values,
                    },
                    legal_name=profile_values["legal_name"],
                    tax_number=profile_values["tax_number"],
                    email=company_email,
                    phone=profile_values["phone"],
                    address=profile_values["address"],
                    country=profile_values["country"],
                    city=profile_values["city"],
                )
                roles = create_default_roles(tenant)
                user_model = get_user_model()
                admin = user_model.objects.create_user(
                    tenant=tenant,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                )
                UserRole.objects.create(
                    tenant=tenant,
                    user=admin,
                    role=roles["tenant_admin"],
                )
            record_platform_event(
                action="platform.tenant.created",
                actor_user_id=actor[0],
                tenant_id=tenant_id,
                details={
                    "slug": slug,
                    "admin_email": email,
                    "max_users": max_users,
                    "plan_code": plan_code,
                    "profile_fields": sorted(profile_fields),
                },
                ip_address=request.META.get("REMOTE_ADDR", ""),
            )
    except IntegrityError:
        return HttpResponseBadRequest(
            "Şirket kodu, alan adı veya yönetici e-postası zaten kullanımda."
        )

    with connections["platform"].cursor() as cursor:
        cursor.execute(
            "UPDATE platform.tenant_registry SET plan_code = %s, "
            "profile_fields = %s::jsonb WHERE tenant_id = %s",
            [plan_code, json.dumps(sorted(profile_fields)), str(tenant_id)],
        )

    return redirect("platform-dashboard")


@require_http_methods(["GET"])
def tenant_people(request, tenant_id):  # noqa: ANN001, ANN201
    actor = _current_platform_user(request)
    if actor is None:
        return redirect("platform-login")
    permissions = effective_permissions(actor[0])
    if "platform.tenant.read" not in permissions:
        return HttpResponseForbidden("Şirket çalışanlarını görme yetkiniz yok.")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM hr.tenant_directory WHERE tenant_id = %s",
            [str(tenant_id)],
        )
        if cursor.fetchone() is None:
            return HttpResponseNotFound("Şirket bulunamadı.")

    can_read_pii = "platform.tenant.pii.read" in permissions
    record_platform_event(
        action="platform.tenant.people.read",
        actor_user_id=actor[0],
        tenant_id=tenant_id,
        details={"pii": can_read_pii},
        ip_address=request.META.get("REMOTE_ADDR", ""),
    )

    people = []
    with tenant_context(tenant_id):
        employees = (
            Employee.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True)
            .select_related("user", "personal_data")
            .order_by("employee_no")
        )
        for employee in employees:
            item = {
                "employee_no": employee.employee_no,
                "status": employee.get_status_display(),
                "hire_date": employee.hire_date.isoformat(),
                "termination_date": (
                    employee.termination_date.isoformat() if employee.termination_date else None
                ),
                "has_account": employee.user_id is not None,
            }
            if can_read_pii:
                user = employee.user if employee.user_id else None
                personal = getattr(employee, "personal_data", None)
                item["user"] = (
                    {
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "phone": user.phone,
                        "status": user.status,
                    }
                    if user
                    else None
                )
                fields = (
                    "first_name",
                    "last_name",
                    "birth_date",
                    "national_id",
                    "address",
                    "personal_email",
                    "personal_phone",
                    "iban",
                    "bank_name",
                    "emergency_contact_phone",
                    "emergency_contact_relation",
                    "citizenship",
                    "work_permit_type",
                    "work_permit_valid_until",
                    "blood_type",
                    "health_notes",
                    "disability_status",
                    "biometric_ref",
                    "custom_fields",
                )
                item["personal_data"] = (
                    {
                        field: (value.isoformat() if hasattr(value, "isoformat") else value)
                        for field in fields
                        if (value := getattr(personal, field, None)) is not None
                    }
                    if personal
                    else None
                )
            people.append(item)
    return render(
        request,
        "platform/people.html",
        {"people": people, "can_read_pii": can_read_pii, "tenant_id": tenant_id},
    )


@require_http_methods(["GET"])
def tenant_detail(request, tenant_id):  # noqa: ANN001, ANN201
    actor = _current_platform_user(request)
    if actor is None:
        return redirect("platform-login")
    permissions = effective_permissions(actor[0])
    if "platform.tenant.read" not in permissions:
        return HttpResponseForbidden("Şirket ayrıntılarını görme yetkiniz yok.")

    with connections["platform"].cursor() as cursor:
        cursor.execute(
            """SELECT tenant_id, slug, domain, status, max_users, max_storage_mb,
                      plan_code, created_at, valid_from, valid_until,
                      max_active_employees, max_employee_records, profile_fields
                 FROM platform.tenant_registry
                WHERE tenant_id = %s AND deleted_at IS NULL""",
            [str(tenant_id)],
        )
        registry = cursor.fetchone()
        if registry is None:
            return HttpResponseNotFound("Şirket bulunamadı.")
        cursor.execute(
            """SELECT e.action, e.details, e.created_at,
                      COALESCE(u.full_name, u.email, 'SYSTEM')
                 FROM platform.audit_events e
                 LEFT JOIN platform.users u ON u.id = e.actor_user_id
                WHERE e.tenant_id = %s AND e.action IN (
                    'platform.tenant.profile.updated',
                    'platform.tenant.notification.sent',
                    'platform.tenant.notification.failed'
                ) ORDER BY e.created_at DESC LIMIT 50""",
            [str(tenant_id)],
        )
        event_rows = cursor.fetchall()

    with tenant_context(tenant_id):
        company = Tenant.objects.filter(pk=tenant_id, deleted_at__isnull=True).first()
        if company is None:
            return HttpResponseNotFound("Şirket bulunamadı.")
        employee_count = Employee.objects.filter(
            tenant_id=tenant_id, deleted_at__isnull=True
        ).count()
        active_employee_count = Employee.objects.filter(
            tenant_id=tenant_id, deleted_at__isnull=True, status="active"
        ).count()
        storage_used_bytes = tenant_file_usage_bytes(tenant_id)

    profile_labels = dict(TENANT_PROFILE_FIELDS)
    profile_fields = list(profile_labels)
    field_values = {"name": company.name}
    field_values.update({field: getattr(company, field) for field in profile_labels})
    settings_value = company.settings or {}
    custom_fields = settings_value.get("company_profile_fields", [])
    custom_values = settings_value.get("company_profile_values", {})
    for item in custom_fields:
        item["value"] = custom_values.get(item["code"], "")
    required_fields = settings_value.get("company_required_fields", [])
    profile_inputs = [
        {"field": field, "label": label, "value": field_values[field]}
        for field, label in TENANT_PROFILE_FIELDS
    ]
    audit_labels = {
        "name": "Şirket adı",
        "legal_name": "Tüzel unvan",
        "tax_number": "Vergi numarası",
        "email": "Şirket e-postası",
        "phone": "Telefon",
        "address": "Adres",
        "country": "Ülke",
        "city": "Şehir",
        "status": "Durum",
        "max_users": "Kullanıcı limiti",
        "max_storage_mb": "Depolama limiti",
        "employee_storage_max_mb": "Çalışan başı dosya kotası",
        "employee_file_extensions": "İzin verilen dosya biçimleri",
        "max_active_employees": "Aktif çalışan limiti",
        "max_employee_records": "Toplam çalışan kayıt limiti",
        "valid_from": "Kullanım başlangıcı",
        "valid_until": "Kullanım bitişi",
        "plan_code": "Plan",
        "profile_fields": "Şirket profili alanları",
    }
    events = []
    for action, details, created_at, actor_label in event_rows:
        events.append(
            {
                "action": (
                    "Şirket bilgileri güncellendi"
                    if action.endswith("updated")
                    else "E-posta bildirimi gönderildi"
                    if action.endswith("sent")
                    else "E-posta bildirimi gönderilemedi"
                ),
                "fields": [audit_labels.get(key, key) for key in details.get("changed_fields", [])],
                "actor": actor_label,
                "created_at": created_at,
            }
        )
    tenant = {
        "id": registry[0],
        "slug": registry[1],
        "domain": registry[2],
        "status": registry[3],
        "max_users": registry[4],
        "max_storage_mb": registry[5],
        "plan_code": registry[6],
        "created_at": registry[7],
        "valid_from": registry[8],
        "valid_until": registry[9],
        "max_active_employees": registry[10],
        "max_employee_records": registry[11],
        "employee_file_max_mb": (company.settings or {}).get("employee_storage_max_mb", DEFAULT_FILE_MAX_MB),
        "employee_file_extensions": (company.settings or {}).get("employee_file_extensions", list(DEFAULT_FILE_EXTENSIONS)),
        "storage_used_mb": round(storage_used_bytes / (1024 * 1024), 2),
    }
    can_manage = "platform.settings.manage" in permissions
    if can_manage:
        record_platform_event(
            action="platform.tenant.detail.read",
            actor_user_id=actor[0],
            tenant_id=tenant_id,
            ip_address=request.META.get("REMOTE_ADDR", ""),
        )
    return render(
        request,
        "platform/tenant_detail.html",
        {
            "email": actor[2],
            "tenant": tenant,
            "company": company,
            "company_values": field_values,
            "profile_fields": profile_fields,
            "profile_inputs": profile_inputs if can_manage else [],
            "profile_field_choices": TENANT_PROFILE_FIELDS,
            "file_extension_choices": FILE_EXTENSION_CHOICES,
            "plan_choices": _plan_choices(),
            "plan_specs": _plan_specs(),
            "status_choices": Tenant._meta.get_field("status").choices,
            "plan_label": dict(_plan_choices()).get(registry[6], registry[6]),
            "can_manage": can_manage,
            "events": events,
            "custom_fields": custom_fields,
            "custom_fields_json": json.dumps(custom_fields),
            "required_fields": required_fields,
            "employee_count": employee_count,
            "active_employee_count": active_employee_count,
            "saved": request.GET.get("saved") == "1",
            "changed": request.GET.get("changed") == "1",
            "notification_status": request.GET.get("notification", ""),
        },
    )
