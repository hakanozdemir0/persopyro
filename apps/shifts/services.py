"""Vardiya tanımı ve gün bazlı planlama servisleri."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.rbac.models import Permission, PermissionEffect, RolePermission, UserPermissionOverride
from apps.rbac.services.permissions import (
    active_role_assignments,
    invalidate_permission_cache,
    user_has_permission,
)
from apps.rbac.services.scope import object_in_org_scope, user_org_scope
from apps.shifts.models import Shift, ShiftAssignment


class ShiftError(ValueError):
    """Vardiya girdisi veya iş kuralı hatası."""


class ShiftConflictError(ShiftError):
    """Vardiya planı mevcut izin veya planla çakıştığında kullanılır."""


class ShiftAuthorizationError(PermissionError):
    """Vardiya yetkisi/kapsamı reddi."""


def can_manage_shifts(actor, permission: str) -> bool:  # noqa: ANN001
    """Şirket yöneticisi için eski rol verisiyle de uyumlu vardiya kapısı."""
    if user_has_permission(actor, permission):
        return True
    # Eski demo verilerinde vardiya izin bağları eksik kalmışsa, kiracı geneli
    # `role.manage` sahibi yönetici vardiya kapısında kilitlenmez.
    return user_has_permission(actor, "role.manage") and user_org_scope(actor).tenant_wide


def _repair_default_manager_shift_permissions(actor) -> None:  # noqa: ANN001
    """Eski kurulumlarda eksik kalan varsayılan vardiya bağlarını tamamlar.

    Yalnızca varsayılan yönetici rolleri için çalışır. Kullanıcıda açık bir
    `deny` istisnası varsa hiçbir bağ eklenmez; böylece yönetici tarafından
    bilinçli olarak kapatılan yetki geri açılmaz.
    """
    manager_codes = {
        "tenant_admin", "tenant_officer", "hr_manager", "dept_manager", "shift_manager"
    }
    atamalar = [a for a in active_role_assignments(actor) if a.role.code in manager_codes]
    if not atamalar:
        return
    denied = set(
        UserPermissionOverride.objects.filter(
            user=actor, deleted_at__isnull=True, effect=PermissionEffect.DENY,
            permission__code__in=("shift.read", "shift.assign", "shift.schedule.manage"),
        ).values_list("permission__code", flat=True)
    )
    kodlar = [kod for kod in ("shift.read", "shift.assign", "shift.schedule.manage") if kod not in denied]
    izinler = {p.code: p for p in Permission.objects.filter(code__in=kodlar)}
    repaired = False
    for atama in atamalar:
        role = atama.role
        expected_scope = "UNIT" if role.code in {"dept_manager", "shift_manager"} else "TENANT"
        if role.scope_type != expected_scope:
            role.scope_type = expected_scope
            role.save(update_fields=["scope_type", "updated_at"])
            repaired = True
        for code in kodlar:
            permission = izinler.get(code)
            if permission is None:
                continue
            _, created = RolePermission.objects.get_or_create(
                tenant_id=role.tenant_id, role_id=role.pk, permission_id=permission.pk,
            )
            repaired = repaired or created
    if repaired:
        invalidate_permission_cache()


def ensure_shift_manager_access(actor) -> None:  # noqa: ANN001
    """Eski/demo kurulumlarının vardiya yetkilerini istek öncesi tamamlar."""
    _repair_default_manager_shift_permissions(actor)


def _actor(actor, permission: str) -> None:  # noqa: ANN001
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise ShiftAuthorizationError("Aktif bir kullanıcı gerekli.")
    _repair_default_manager_shift_permissions(actor)
    if not can_manage_shifts(actor, permission):
        raise ShiftAuthorizationError(f"Bu işlem için '{permission}' izni gerekir.")


def _same_tenant(actor, obj) -> None:  # noqa: ANN001
    # `create_shift` kiracının kendisini (Tenant) alır; diğer vardiya
    # işlemleri kiracıya bağlı bir kayıt alır. Tenant modelinde `tenant_id`
    # değil `pk` bulunduğundan iki biçimi de karşılaştır.
    object_tenant_id = getattr(obj, "tenant_id", None)
    if object_tenant_id is None:
        object_tenant_id = getattr(obj, "pk", None)
    if str(object_tenant_id) != str(getattr(actor, "tenant_id", None)):
        raise ShiftAuthorizationError("Kayıt ve kullanıcı aynı kiracıda olmalıdır.")


def _valid_time_range(start_time: time, end_time: time, crosses_midnight: bool) -> None:
    if not isinstance(start_time, time) or not isinstance(end_time, time):
        raise ShiftError("Başlangıç ve bitiş saatleri geçerli olmalıdır.")
    if start_time == end_time:
        raise ShiftError("Başlangıç ve bitiş saati aynı olamaz.")
    if end_time < start_time and not crosses_midnight:
        raise ShiftError("Gece yarısını aşan vardiya için seçenek işaretlenmelidir.")


def _valid_code_name(code: str, name: str) -> tuple[str, str]:
    code = (code or "").strip().lower()
    name = (name or "").strip()
    if not code or not name:
        raise ShiftError("Vardiya kodu ve adı zorunludur.")
    if len(code) > 50 or len(name) > 120:
        raise ShiftError("Vardiya kodu en fazla 50, adı en fazla 120 karakter olabilir.")
    return code, name


@transaction.atomic
def create_shift(*, actor, tenant, code, name, start_time, end_time, break_minutes=0,
                 crosses_midnight=False, work_model="office", description=""):
    _actor(actor, "shift.schedule.manage")
    _same_tenant(actor, tenant)
    code, name = _valid_code_name(code, name)
    _valid_time_range(start_time, end_time, bool(crosses_midnight))
    try:
        break_minutes = int(break_minutes)
    except (TypeError, ValueError) as exc:
        raise ShiftError("Mola dakikası sayı olmalıdır.") from exc
    if break_minutes < 0 or break_minutes > 24 * 60:
        raise ShiftError("Mola dakikası 0 ile 1440 arasında olmalıdır.")
    if work_model not in {"office", "hybrid", "remote", "flex"}:
        raise ShiftError("Çalışma modeli geçersiz.")
    if Shift.objects.filter(tenant=tenant, code=code).exists():
        raise ShiftError(f"'{code}' koduyla etkin bir vardiya zaten var.")
    shift = Shift.objects.create(
        tenant=tenant, code=code, name=name, start_time=start_time, end_time=end_time,
        break_minutes=break_minutes, crosses_midnight=bool(crosses_midnight),
        work_model=work_model, description=(description or "").strip(),
    )
    log_action(tenant_id=tenant.pk, action="shift.create", user=actor, table_name="shifts",
               record_id=shift.pk, changes={"code": code, "name": name})
    return shift


@transaction.atomic
def update_shift(*, actor, shift, code=None, name=None, start_time=None, end_time=None,
                 break_minutes=None, crosses_midnight=None, work_model=None,
                 description=None, reason=""):
    _actor(actor, "shift.schedule.manage")
    _same_tenant(actor, shift)
    if shift.deleted_at is not None:
        raise ShiftError("Vardiya bulunamadı veya silinmiş.")
    yeni_baslangic = start_time if start_time is not None else shift.start_time
    yeni_bitis = end_time if end_time is not None else shift.end_time
    yeni_gece = bool(crosses_midnight) if crosses_midnight is not None else shift.crosses_midnight
    _valid_time_range(yeni_baslangic, yeni_bitis, yeni_gece)
    changes = {}
    if code is not None:
        code, _ = _valid_code_name(code, shift.name)
        if code != shift.code and Shift.objects.filter(tenant=shift.tenant, code=code).exists():
            raise ShiftError(f"'{code}' koduyla etkin bir vardiya zaten var.")
        changes["code"] = {"before": shift.code, "after": code}
        shift.code = code
    if name is not None:
        _, name = _valid_code_name(shift.code, name)
        changes["name"] = {"before": shift.name, "after": name}
        shift.name = name
    if start_time is not None or end_time is not None or crosses_midnight is not None:
        changes["hours"] = {"before": f"{shift.start_time}-{shift.end_time}", "after": f"{yeni_baslangic}-{yeni_bitis}"}
        shift.start_time, shift.end_time, shift.crosses_midnight = yeni_baslangic, yeni_bitis, yeni_gece
    if break_minutes is not None:
        break_minutes = int(break_minutes)
        if break_minutes < 0 or break_minutes > 24 * 60:
            raise ShiftError("Mola dakikası 0 ile 1440 arasında olmalıdır.")
        shift.break_minutes = break_minutes
    if work_model is not None:
        if work_model not in {"office", "hybrid", "remote", "flex"}:
            raise ShiftError("Çalışma modeli geçersiz.")
        shift.work_model = work_model
    if description is not None:
        shift.description = str(description).strip()
    if not changes and all(x is None for x in (break_minutes, work_model, description)):
        return shift
    shift.save()
    log_action(tenant_id=shift.tenant_id, action="shift.update", user=actor,
               table_name="shifts", record_id=shift.pk,
               changes={**changes, "reason": (reason or "")[:200]})
    return shift


@transaction.atomic
def delete_shift(*, actor, shift, reason=""):
    _actor(actor, "shift.schedule.manage")
    _same_tenant(actor, shift)
    if shift.deleted_at is not None:
        raise ShiftError("Vardiya bulunamadı veya silinmiş.")
    if ShiftAssignment.objects.filter(shift=shift, deleted_at__isnull=True).exists():
        raise ShiftError("Vardiya silinemez: etkin atamaları var. Önce planı kaldırın.")
    shift.deleted_at = timezone.now()
    shift.save(update_fields=["deleted_at", "updated_at"])
    log_action(tenant_id=shift.tenant_id, action="shift.delete", user=actor,
               table_name="shifts", record_id=shift.pk,
               changes={"code": shift.code, "reason": (reason or "")[:200]})
    return shift


def _dates_from_request(*, mode, dates=None, start_date=None, end_date=None, weekdays=None):
    if mode == "daily":
        sonuc = sorted({d for d in (dates or []) if isinstance(d, date)})
        if not sonuc:
            raise ShiftError("En az bir çalışma günü seçilmelidir.")
        return sonuc
    if not isinstance(start_date, date) or not isinstance(end_date, date) or end_date < start_date:
        raise ShiftError("Haftalık plan için geçerli başlangıç/bitiş tarihi gerekir.")
    if (end_date - start_date).days > 366:
        raise ShiftError("Tek seferde en fazla 1 yıllık plan oluşturulabilir.")
    gunler = {int(g) for g in (weekdays or [])}
    if not gunler or not gunler.issubset(set(range(7))):
        raise ShiftError("Haftalık plan için geçerli günler seçilmelidir.")
    return [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)
            if (start_date + timedelta(days=i)).weekday() in gunler]


def _leave_conflict(*, tenant_id, employees, shift, work_dates):  # noqa: ANN001
    """Return an active leave request whose time interval overlaps a shift."""
    from apps.leave.models import LeaveRequest, LeaveRequestStatus  # noqa: PLC0415

    dates = sorted(work_dates)
    if not dates:
        return None
    requests = LeaveRequest.objects.filter(
        tenant_id=tenant_id,
        employee_id__in=[employee.pk for employee in employees],
        status__in=(LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED, LeaveRequestStatus.TAKEN),
        deleted_at__isnull=True,
        start_date__lte=dates[-1] + timedelta(days=1),
        end_date__gte=dates[0],
    ).select_related("employee", "leave_type").order_by("start_date")
    for leave in requests:
        leave_start = datetime.combine(leave.start_date, leave.start_time or time.min)
        leave_end = datetime.combine(leave.end_date, leave.end_time or time.max)
        for work_date in dates:
            shift_start = datetime.combine(work_date, shift.start_time)
            shift_end_date = work_date + timedelta(days=1) if shift.crosses_midnight else work_date
            shift_end = datetime.combine(shift_end_date, shift.end_time)
            if shift_start < leave_end and leave_start < shift_end:
                return leave, work_date
    return None


@transaction.atomic
def assign_shifts(*, actor, shift, employee_ids, mode="daily", dates=None,
                  start_date=None, end_date=None, weekdays=None, note="",
                  replace_existing=False):
    _actor(actor, "shift.assign")
    _same_tenant(actor, shift)
    if shift.deleted_at is not None:
        raise ShiftError("Vardiya bulunamadı veya silinmiş.")
    employees = list(shift.tenant.employees.filter(
        pk__in=list(employee_ids), deleted_at__isnull=True
    ).select_related("department"))
    if len(employees) != len(set(str(x) for x in employee_ids)):
        raise ShiftError("Bir veya daha fazla çalışan bulunamadı.")
    for employee in employees:
        if not object_in_org_scope(actor, department_id=employee.department_id, owner_user_id=employee.user_id):
            raise ShiftAuthorizationError("Çalışan organizasyon kapsamınız dışında.")
    work_dates = _dates_from_request(mode=mode, dates=dates, start_date=start_date,
                                     end_date=end_date, weekdays=weekdays)
    leave_conflict = _leave_conflict(
        tenant_id=shift.tenant_id, employees=employees, shift=shift, work_dates=work_dates
    )
    if leave_conflict:
        leave, work_date = leave_conflict
        raise ShiftConflictError(
            f"{leave.employee.employee_no} için '{leave.leave_type.name}' izni "
            f"{work_date:%d.%m.%Y} tarihinde vardiya atanmasına engel."
        )
    existing = list(ShiftAssignment.objects.filter(
        tenant=shift.tenant, employee__in=employees, work_date__in=work_dates,
        deleted_at__isnull=True
    ))
    if existing and not replace_existing:
        raise ShiftError("Seçilen günlerden en az biri zaten planlı. Değiştirmek için mevcut planı değiştir seçeneğini kullanın.")
    mevcut_haritasi = {(a.employee_id, a.work_date): a for a in existing}
    now = timezone.now()
    if existing:
        ShiftAssignment.objects.filter(pk__in=[x.pk for x in existing]).update(
            deleted_at=now, updated_at=now
        )
    assignments = [
        ShiftAssignment(tenant=shift.tenant, employee=employee, shift=shift, work_date=day,
                        assigned_by=actor, assignment_source=mode, note=(note or "").strip())
        for employee in employees for day in work_dates
    ]
    ShiftAssignment.objects.bulk_create(assignments)
    for atama in assignments:
        onceki = mevcut_haritasi.get((atama.employee_id, atama.work_date))
        log_action(
            tenant_id=shift.tenant_id,
            action="shift.assign",
            user=actor,
            table_name="shift_assignments",
            record_id=atama.pk,
            changes={
                "employee_id": str(atama.employee_id),
                "event": "changed" if onceki else "assigned",
                "work_date": atama.work_date.isoformat(),
                "shift_name": shift.name,
                "old_shift_name": onceki.shift.name if onceki else None,
                "mode": mode,
            },
        )
    log_action(tenant_id=shift.tenant_id, action="shift.assign", user=actor,
               table_name="shift_assignments", record_id=shift.pk,
               changes={"employee_count": len(employees), "day_count": len(work_dates),
                        "mode": mode, "replace_existing": bool(replace_existing)})
    return assignments


@transaction.atomic
def remove_shift_assignment(*, actor, assignment: ShiftAssignment, reason="") -> ShiftAssignment:
    """Remove one active schedule entry while preserving its audit history."""
    _actor(actor, "shift.assign")
    _same_tenant(actor, assignment)
    if assignment.deleted_at is not None:
        raise ShiftError("Etkin vardiya ataması bulunamadı.")
    employee = assignment.employee
    if not object_in_org_scope(actor, department_id=employee.department_id, owner_user_id=employee.user_id):
        raise ShiftAuthorizationError("Çalışan organizasyon kapsamınız dışında.")
    assignment.deleted_at = timezone.now()
    assignment.save(update_fields=("deleted_at", "updated_at"))
    log_action(
        tenant_id=assignment.tenant_id,
        action="shift.assign",
        user=actor,
        table_name="shift_assignments",
        record_id=assignment.pk,
        changes={
            "employee_id": str(employee.pk),
            "work_date": assignment.work_date.isoformat(),
            "shift_name": assignment.shift.name,
            "event": "removed",
            "reason": (reason or "")[:200],
        },
    )
    return assignment


@transaction.atomic
def report_late_arrival(*, actor, assignment: ShiftAssignment, minutes: int) -> ShiftAssignment:
    """A direct manager records/corrects a late arrival for a scheduled shift."""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise ShiftAuthorizationError("Aktif bir kullanıcı gerekli.")
    if not user_has_permission(actor, "attendance.manage"):
        raise ShiftAuthorizationError("Geç kalış bildirmek için 'attendance.manage' izni gerekir.")
    _same_tenant(actor, assignment)
    if assignment.deleted_at is not None:
        raise ShiftError("Etkin vardiya ataması bulunamadı.")
    employee = assignment.employee
    if employee.user_id == actor.pk:
        raise ShiftAuthorizationError("Kendi geç kalışınızı yönetici olarak bildiremezsiniz.")
    if not object_in_org_scope(actor, department_id=employee.department_id, owner_user_id=employee.user_id):
        raise ShiftAuthorizationError("Çalışan organizasyon kapsamınız dışında.")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 1440:
        raise ShiftError("Geç kalış 0 ile 1440 dakika arasında olmalıdır.")

    onceki = assignment.late_minutes if assignment.late_reported_at else None
    assignment.late_minutes = minutes
    assignment.late_reported_by = actor
    assignment.late_reported_at = timezone.now()
    assignment.save(update_fields=("late_minutes", "late_reported_by", "late_reported_at", "updated_at"))
    log_action(
        tenant_id=assignment.tenant_id,
        action="attendance.manual_entry",
        user=actor,
        table_name="shift_assignments",
        record_id=assignment.pk,
        changes={
            "employee_id": str(assignment.employee_id),
            "work_date": assignment.work_date.isoformat(),
            "shift_name": assignment.shift.name,
            "old_late_minutes": onceki,
            "late_minutes": minutes,
        },
    )
    return assignment
