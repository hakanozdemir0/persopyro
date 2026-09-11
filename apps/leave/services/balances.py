"""
Bakiye, olay defteri, hakediş (accrual) ve manuel düzeltme (M14 §3–§4).

Tasarım:
  * `leave_balances` **güncel durumdur**; `available_days` DB tarafından üretilir.
  * Her değişiklik `leave_balance_entries` (**append-only**) ile aynı işlemde yazılır
    → sessiz bakiye değişimi olmaz; olay türleri: düzeltme, hakediş, devir,
    rezervasyon, rezervasyon iptali, kullanım, iade.
  * **İdempotans:** hakediş, bakiye+dönem başına tek `accrual` kaydıyla korunur;
    yeniden çalıştırma değer değişmediyse **hiçbir şey yazmaz**. Değer değişirse
    (ör. işe giriş tarihi düzeltildi) fark, gerekçeli **düzeltme** kaydı olarak
    yazılır — sessiz üzerine yazma yok.
  * **Negatif bakiye:** tenant politikası açıkça izin vermiyorsa hem servis hem
    DB guard'ı reddeder (fail-closed).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.leave.models import (
    BalanceEntryKind,
    LeaveBalance,
    LeaveBalanceEntry,
    LeavePolicy,
    LeaveType,
    RecordStatus,
)
from apps.leave.services.authorization import can_manage_balances
from apps.leave.services.base import (
    LeaveAuthorizationError,
    LeaveBalanceError,
    LeaveError,
    require_permission,
)
from apps.leave.services.policies import entitlement_for
from apps.organization.models import Employee, EmployeeStatus

SIFIR = Decimal("0.0")


def period_year_for(tarih: date | None = None) -> int:
    """Verilen tarihin (varsayılan: bugün) hakediş dönemi (yıl)."""
    return (tarih or timezone.localdate()).year


def service_months(hire_date: date, as_of: date) -> int:
    """
    Tam hizmet ayı sayısı (`hire_date` → `as_of`); gelecek tarihte 0 döner.

    Kıdem kuralı aralıkları **[min, max)** yarı açıktır: örnek "0–5 yıl → 14 gün,
    5+ yıl → 21 gün" için 60. ayda ikinci kural geçerlidir.
    """
    if hire_date is None or as_of < hire_date:
        return 0
    aylar = (as_of.year - hire_date.year) * 12 + (as_of.month - hire_date.month)
    if as_of.day < hire_date.day:
        aylar -= 1
    return max(aylar, 0)


def get_or_create_balance(
    *, employee: Employee, leave_type: LeaveType, period_year: int
) -> LeaveBalance:
    """Çalışan/tür/dönem bakiyesini getirir, yoksa oluşturur (sıfır değerlerle)."""
    if employee.deleted_at is not None:
        raise LeaveBalanceError("Silinmiş çalışan için bakiye oluşturulamaz.")
    if leave_type.deleted_at is not None:
        raise LeaveBalanceError("Silinmiş izin türü için bakiye oluşturulamaz.")
    balance, _ = LeaveBalance.objects.get_or_create(
        employee=employee,
        leave_type=leave_type,
        period_year=period_year,
        deleted_at__isnull=True,
        defaults={"tenant_id": employee.tenant_id},
    )
    return balance


def _defter_yaz(
    *,
    balance: LeaveBalance,
    entry_kind: str,
    delta: Decimal,
    request=None,
    actor=None,
    reason: str = "",
) -> LeaveBalanceEntry:
    """Olay defterine kayıt yazar (append-only; idempotans kısıtları DB'de)."""
    return LeaveBalanceEntry.objects.create(
        tenant_id=balance.tenant_id,
        balance=balance,
        employee_id=balance.employee_id,
        leave_type_id=balance.leave_type_id,
        period_year=balance.period_year,
        entry_kind=entry_kind,
        delta_days=delta,
        leave_request=request,
        actor=actor,
        reason=reason,
    )


def _aktif_politika(leave_type: LeaveType) -> LeavePolicy | None:
    return (
        LeavePolicy.objects.filter(
            leave_type=leave_type, deleted_at__isnull=True, status=RecordStatus.ACTIVE
        )
        .order_by("-created_at")
        .first()
    )


# --- Hakediş (accrual) --------------------------------------------------------


def accrue_leave_entitlements(
    *,
    actor=None,
    tenant,
    period_year: int,
    employees=None,
    as_of: date | None = None,
) -> list[dict]:
    """
    Dönem hakedişini hesaplar ve **yazar** (idempotent).

    * Bakiye kullanan (`uses_balance=True`) aktif izin türleri ve **aktif** çalışanlar
      için çalışır; politikası olmayan tür atlanır (politika yoksa hakediş yoktur).
    * Hakediş = politika × hizmet süresi (işe giriş tarihinden `as_of`'a).
    * Değer değişmediyse **hiçbir kayıt yazılmaz** (tekrar çalıştırma güvenli).
    * Aktör verilirse `leave.balance.accrual` izni ve denetim kaydı uygulanır
      (management command için izin `--actor-email` ile verilir).
    """
    if actor is not None:
        require_permission(actor, "leave.balance.manage")

    referans = as_of or date(period_year, 12, 31)
    sonuclar: list[dict] = []

    turler = list(
        LeaveType.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            status=RecordStatus.ACTIVE,
            uses_balance=True,
        ).order_by("code")
    )
    if employees is None:
        calisanlar = list(
            Employee.objects.filter(
                tenant=tenant,
                deleted_at__isnull=True,
                status__in=[EmployeeStatus.ACTIVE, EmployeeStatus.SUSPENDED],
            ).order_by("employee_no")
        )
    else:
        calisanlar = list(employees)

    for tur in turler:
        policy = _aktif_politika(tur)
        if policy is None:
            sonuclar.append({"leave_type": tur.code, "durum": "politika_yok"})
            continue
        for calisan in calisanlar:
            if calisan.deleted_at is not None or calisan.status not in (
                EmployeeStatus.ACTIVE,
                EmployeeStatus.SUSPENDED,
            ):
                continue
            aylar = service_months(calisan.hire_date, referans)
            hedef = entitlement_for(policy, aylar)

            with transaction.atomic():
                balance = LeaveBalance.objects.select_for_update().get(
                    pk=get_or_create_balance(
                        employee=calisan, leave_type=tur, period_year=period_year
                    ).pk
                )
                fark = hedef - Decimal(balance.entitled_days)
                if fark == SIFIR:
                    sonuclar.append(
                        {
                            "employee_no": calisan.employee_no,
                            "leave_type": tur.code,
                            "durum": "degisiklik_yok",
                            "gun": str(hedef),
                        }
                    )
                    continue

                mevcut_hakedis = LeaveBalanceEntry.objects.filter(
                    balance=balance, entry_kind=BalanceEntryKind.ACCRUAL
                ).exists()
                balance.entitled_days = hedef
                balance.save(update_fields=["entitled_days", "updated_at"])

                if mevcut_hakedis:
                    # Dönem hakedişi zaten yazılmış; fark **düzeltme** olarak kaydedilir.
                    _defter_yaz(
                        balance=balance,
                        entry_kind=BalanceEntryKind.ADJUSTMENT,
                        delta=fark,
                        actor=actor,
                        reason=f"{period_year} hakediş yeniden hesaplama düzeltmesi",
                    )
                    durum = "duzeltme"
                    eylem = "leave.balance.adjustment"
                else:
                    _defter_yaz(
                        balance=balance,
                        entry_kind=BalanceEntryKind.ACCRUAL,
                        delta=fark,
                        actor=actor,
                        reason=f"{period_year} hakediş ({policy.accrual_method}, {aylar} ay)",
                    )
                    durum = "yazildi"
                    eylem = "leave.balance.accrual"

                log_action(
                    tenant_id=tenant.pk,
                    action=eylem,
                    user=actor,
                    table_name="leave_balances",
                    record_id=balance.pk,
                    changes={
                        "employee_no": calisan.employee_no,
                        "leave_type": tur.code,
                        "period_year": period_year,
                        "service_months": aylar,
                        "entitled_days": str(hedef),
                        "delta": str(fark),
                    },
                )
                sonuclar.append(
                    {
                        "employee_no": calisan.employee_no,
                        "leave_type": tur.code,
                        "durum": durum,
                        "gun": str(hedef),
                        "delta": str(fark),
                    }
                )
    return sonuclar


# --- Manuel düzeltme ----------------------------------------------------------


def adjust_leave_balance(
    *,
    actor,
    employee: Employee,
    leave_type: LeaveType,
    period_year: int,
    delta_days: Decimal,
    reason: str,
) -> LeaveBalance:
    """
    Bakiye düzeltmesi — **gerekçe zorunludur** ve işlem denetim kaydına yazılır.

    `leave.balance.manage` izni + kapsam gerekir; negatif bakiyeye izin yoksa
    DB guard'ı da reddeder.
    """
    if not can_manage_balances(actor, employee):
        raise LeaveAuthorizationError(
            "Bakiye düzeltmesi için 'leave.balance.manage' izni ve kapsam gerekir."
        )
    gerekce = (reason or "").strip()
    if len(gerekce) < 5:
        raise LeaveError("Manuel düzeltme için anlamlı bir gerekçe zorunludur.")
    delta = Decimal(delta_days)
    if delta == SIFIR:
        raise LeaveError("Düzeltme miktarı sıfır olamaz.")

    with transaction.atomic():
        balance = LeaveBalance.objects.select_for_update().get(
            pk=get_or_create_balance(
                employee=employee, leave_type=leave_type, period_year=period_year
            ).pk
        )
        balance.adjustment_days = Decimal(balance.adjustment_days) + delta
        if Decimal(balance.entitled_days) + Decimal(balance.carried_forward_days) + Decimal(
            balance.adjustment_days
        ) - Decimal(balance.used_days) - Decimal(balance.reserved_days) < SIFIR:
            policy = _aktif_politika(leave_type)
            if policy is None or not policy.allow_negative_balance:
                raise LeaveBalanceError(
                    "Düzeltme negatif bakiye üretirdi; politika negatif bakiyeye izin vermiyor."
                )
        balance.save(update_fields=["adjustment_days", "updated_at"])
        _defter_yaz(
            balance=balance,
            entry_kind=BalanceEntryKind.ADJUSTMENT,
            delta=delta,
            actor=actor,
            reason=gerekce,
        )
        log_action(
            tenant_id=balance.tenant_id,
            action="leave.balance.adjustment",
            user=actor,
            table_name="leave_balances",
            record_id=balance.pk,
            changes={
                "employee_no": employee.employee_no,
                "leave_type": leave_type.code,
                "period_year": period_year,
                "delta": str(delta),
                "has_reason": True,
            },
        )
    return balance


__all__ = [
    "accrue_leave_entitlements",
    "adjust_leave_balance",
    "get_or_create_balance",
    "period_year_for",
    "service_months",
]
