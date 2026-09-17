"""
İzin türleri (M14 §1).

* Sistem türleri katalogdan oluşturulur (`is_system=True`): **kodu değiştirilemez**,
  soft-delete edilemez (rezerve kod), yalnızca pasifleştirilebilir → kiracının kendi
  türleri sistem kodlarıyla çakışamaz.
* OD21: benzersizlik kısmidir (`WHERE deleted_at IS NULL`) → silinen türün kodu
  yeniden kullanılabilir.
* Tüm işlemler denetim kaydına yazılır (`leave.type.*`); değer içerikleri K0'dır.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.leave.leave_type_catalog import DEFAULT_LEAVE_TYPES, SYSTEM_LEAVE_TYPE_CODES
from apps.leave.models import LeaveType, RecordStatus
from apps.leave.services.base import LeaveError, require_permission


def create_default_leave_types(*, actor, tenant) -> list[LeaveType]:
    """
    Kiracı için **sistem** izin türlerini oluşturur (idempotent).

    Var olan kod atlanır; mevcut türün bayrakları **ezilmez** (kiracı değişikliği
    korunur). Denetim kaydı yalnızca gerçekten oluşturulan türler için yazılır.
    """
    require_permission(actor, "leave.type.manage")
    olusturulan: list[LeaveType] = []
    with transaction.atomic():
        for tanim in DEFAULT_LEAVE_TYPES:
            tur, yeni = LeaveType.objects.get_or_create(
                tenant=tenant,
                code=tanim["code"],
                deleted_at__isnull=True,
                defaults={
                    "name": tanim["name"],
                    "category": tanim["category"],
                    "is_paid": tanim["is_paid"],
                    "uses_balance": tanim["uses_balance"],
                    "requires_approval": tanim["requires_approval"],
                    "requires_document": tanim["requires_document"],
                    "is_time_based": tanim.get("is_time_based", False),
                    "workday_minutes": tanim.get("workday_minutes"),
                    "is_system": True,
                    "status": RecordStatus.ACTIVE,
                },
            )
            if yeni:
                olusturulan.append(tur)
                log_action(
                    tenant_id=tenant.pk,
                    action="leave.type.create",
                    user=actor,
                    table_name="leave_types",
                    record_id=tur.pk,
                    changes={"code": tur.code, "is_system": True},
                )
    return olusturulan


def create_leave_type(
    *,
    actor,
    tenant,
    code: str,
    name: str,
    category: str,
    is_paid: bool = True,
    uses_balance: bool = False,
    requires_approval: bool = True,
    requires_document: bool = False,
    min_advance_notice_days: int = 0,
    max_consecutive_days=None,
    is_time_based: bool = False,
    workday_minutes: int | None = None,
) -> LeaveType:
    """Kiracıya özel izin türü oluşturur (sistem kodları rezervedir)."""
    require_permission(actor, "leave.type.manage")
    kod = (code or "").strip().lower()
    if not kod:
        raise LeaveError("İzin türü kodu zorunludur.")
    if kod in SYSTEM_LEAVE_TYPE_CODES:
        raise LeaveError(
            f"'{kod}' sistem izin türü kodudur (rezerve) — farklı bir kod seçin."
        )
    if is_time_based and (workday_minutes is None or int(workday_minutes) <= 0):
        raise LeaveError("Saat tabanlı izin için 1 gün dakika değeri pozitif olmalıdır.")
    with transaction.atomic():
        tur = LeaveType.objects.create(
            tenant=tenant,
            code=kod,
            name=name.strip(),
            category=category,
            is_paid=is_paid,
            uses_balance=uses_balance,
            requires_approval=requires_approval,
            requires_document=requires_document,
            min_advance_notice_days=min_advance_notice_days,
            max_consecutive_days=max_consecutive_days,
            is_time_based=is_time_based,
            workday_minutes=workday_minutes,
            is_system=False,
        )
        log_action(
            tenant_id=tenant.pk,
            action="leave.type.create",
            user=actor,
            table_name="leave_types",
            record_id=tur.pk,
            changes={"code": tur.code, "category": tur.category, "uses_balance": uses_balance},
        )
    return tur


def update_leave_type(
    *,
    actor,
    leave_type: LeaveType,
    name: str | None = None,
    category: str | None = None,
    is_paid: bool | None = None,
    uses_balance: bool | None = None,
    requires_approval: bool | None = None,
    requires_document: bool | None = None,
    min_advance_notice_days: int | None = None,
    max_consecutive_days=None,
    is_time_based: bool | None = None,
    workday_minutes: int | None = None,
    workday_hours: Decimal | str | None = None,
) -> LeaveType:
    """
    İzin türünü günceller.

    **Sistem türünde yalnızca anlamlı alanlar** değişir; `code`, `is_system` ve
    `deleted_at` **değiştirilemez** (rezerve kod / silme yasağı).
    """
    require_permission(actor, "leave.type.manage")
    degisim: dict = {}
    for alan, deger in (
        ("name", name),
        ("category", category),
        ("is_paid", is_paid),
        ("uses_balance", uses_balance),
        ("requires_approval", requires_approval),
        ("requires_document", requires_document),
        ("min_advance_notice_days", min_advance_notice_days),
        ("is_time_based", is_time_based),
    ):
        if deger is not None and getattr(leave_type, alan) != deger:
            setattr(leave_type, alan, deger)
            degisim[alan] = str(deger)
    if max_consecutive_days is not None:
        leave_type.max_consecutive_days = max_consecutive_days
        degisim["max_consecutive_days"] = str(max_consecutive_days)
    if workday_hours is not None:
        try:
            saat = Decimal(str(workday_hours).replace(",", "."))
        except (InvalidOperation, TypeError, ValueError) as hata:
            raise LeaveError("1 gün saat karşılığı sayı olmalıdır.") from hata
        dakika = saat * Decimal("60")
        if saat <= 0 or dakika != dakika.to_integral_value():
            raise LeaveError("1 gün saat karşılığı pozitif ve dakika olarak tam olmalıdır.")
        workday_minutes = int(dakika)
    if workday_minutes is not None:
        if int(workday_minutes) <= 0:
            raise LeaveError("1 gün dakika değeri pozitif olmalıdır.")
        leave_type.workday_minutes = int(workday_minutes)
        degisim["workday_minutes"] = str(workday_minutes)
    if leave_type.is_time_based and not leave_type.workday_minutes:
        raise LeaveError("Saat tabanlı izin için 1 gün dakika değeri zorunludur.")

    if not degisim:
        return leave_type

    with transaction.atomic():
        leave_type.save(update_fields=[*degisim.keys(), "updated_at"])
        log_action(
            tenant_id=leave_type.tenant_id,
            action="leave.type.update",
            user=actor,
            table_name="leave_types",
            record_id=leave_type.pk,
            changes=degisim,
        )
    return leave_type


def deactivate_leave_type(*, actor, leave_type: LeaveType, reason: str = "") -> LeaveType:
    """
    İzin türünü **pasifleştirir** (soft delete değil — OD21: tür silinmez).

    Pasif tür yeni talep için **kullanılamaz**; mevcut talepler etkilenmez.
    """
    require_permission(actor, "leave.type.manage")
    if leave_type.status == RecordStatus.ARCHIVED:
        raise LeaveError("İzin türü zaten arşivlenmiş.")
    if leave_type.status == RecordStatus.INACTIVE and not reason:
        raise LeaveError("Pasifleştirme için gerekçe gerekir.")
    with transaction.atomic():
        leave_type.status = RecordStatus.INACTIVE
        leave_type.save(update_fields=["status", "updated_at"])
        log_action(
            tenant_id=leave_type.tenant_id,
            action="leave.type.deactivate",
            user=actor,
            table_name="leave_types",
            record_id=leave_type.pk,
            changes={
                "status": RecordStatus.INACTIVE,
                "has_reason": bool(reason),
                "at": timezone.now().isoformat(),
            },
        )
    return leave_type


def activate_leave_type(*, actor, leave_type: LeaveType, reason: str = "") -> LeaveType:
    """Pasif izin türünü yeniden aktifleştirir; geçmiş taleplere dokunmaz."""
    require_permission(actor, "leave.type.manage")
    if leave_type.deleted_at is not None:
        raise LeaveError("Silinmiş izin türü aktifleştirilemez.")
    if leave_type.status == RecordStatus.ARCHIVED:
        raise LeaveError("Arşivlenmiş izin türü aktifleştirilemez.")
    if leave_type.status == RecordStatus.ACTIVE:
        return leave_type
    gerekce = (reason or "").strip()
    if len(gerekce) < 5:
        raise LeaveError("Aktifleştirme için anlamlı bir gerekçe zorunludur.")
    with transaction.atomic():
        leave_type.status = RecordStatus.ACTIVE
        leave_type.save(update_fields=["status", "updated_at"])
        log_action(
            tenant_id=leave_type.tenant_id,
            action="leave.type.activate",
            user=actor,
            table_name="leave_types",
            record_id=leave_type.pk,
            changes={"status": RecordStatus.ACTIVE, "has_reason": True},
        )
    return leave_type


__all__ = [
    "create_default_leave_types",
    "create_leave_type",
    "deactivate_leave_type",
    "activate_leave_type",
    "update_leave_type",
]
