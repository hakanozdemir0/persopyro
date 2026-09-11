"""
İzin politikası ve hakediş kuralları (M14 §2).

* Tür başına **tek yaşayan politika** (kısmi benzersizlik: `WHERE deleted_at IS NULL`).
* `FIXED`: yılda sabit gün · `TENURE_BASED`: `[min, max)` hizmet süresi kuralları.
* Çakışan kural aralıkları **veritabanında** reddedilir
  (`trg_leave_policy_rules_overlap`); servis de önceden doğrular (anlaşılır hata).
* Sabit gün/kural değerleri **kod içine gömülmez**; kiracı yapılandırır.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.leave.leave_type_catalog import AccrualMethod
from apps.leave.models import LeavePolicy, LeavePolicyRule, LeaveType, RecordStatus
from apps.leave.services.base import LeaveError, LeavePolicyError, require_permission


def _kural_dogrula(kurallar: list[dict]) -> None:
    """Kural aralıklarını doğrular: geçerli aralık + **çakışma yok** (yarı açık)."""
    if not kurallar:
        raise LeavePolicyError("Kıdeme göre politikada en az bir kural gerekir.")
    araliklar = []
    for kural in kurallar:
        alt = int(kural["min_service_months"])
        ust = kural.get("max_service_months")
        ust = None if ust is None else int(ust)
        if alt < 0:
            raise LeavePolicyError("Asgari hizmet süresi negatif olamaz.")
        if ust is not None and ust <= alt:
            raise LeavePolicyError(
                f"Kural aralığı geçersiz: [{alt}, {ust}) — üst sınır alt sınırdan büyük olmalı."
            )
        if Decimal(kural["entitlement_days"]) < 0:
            raise LeavePolicyError("Hakediş günü negatif olamaz.")
        araliklar.append((alt, ust))
    araliklar.sort()
    for (alt1, ust1), (alt2, _ust2) in zip(araliklar, araliklar[1:], strict=False):
        sinir1 = ust1 if ust1 is not None else float("inf")
        if alt2 < sinir1:
            raise LeavePolicyError(
                f"Hizmet süresi kuralları çakışıyor: [{alt1}, {ust1}) ile [{alt2}, ...)."
            )


def entitlement_for(policy: LeavePolicy, service_months: int) -> Decimal:
    """
    Politikaya göre hakediş günü.

    * `FIXED` → `fixed_days`
    * `TENURE_BASED` → hizmet süresine düşen kural (`[min, max)`); eşleşen kural
      yoksa **fail-closed**: politika eksik demektir, sessizce 0 dönülmez.
    """
    if policy.accrual_method == AccrualMethod.FIXED:
        if policy.fixed_days is None:
            raise LeavePolicyError("Sabit politikada `fixed_days` tanımlı değil.")
        return Decimal(policy.fixed_days)

    for kural in policy.rules.filter(deleted_at__isnull=True).order_by("min_service_months"):
        if service_months < kural.min_service_months:
            continue
        if kural.max_service_months is not None and service_months >= kural.max_service_months:
            continue
        return Decimal(kural.entitlement_days)

    raise LeavePolicyError(
        f"Politikada {service_months} aylık hizmet süresine uyan kural yok "
        f"(tür: {policy.leave_type_id})."
    )


def create_leave_policy(
    *,
    actor,
    leave_type: LeaveType,
    accrual_method: str,
    fixed_days: Decimal | None = None,
    rules: list[dict] | None = None,
    allow_negative_balance: bool = False,
    allow_refund_after_approval: bool = True,
) -> LeavePolicy:
    """Yeni hakediş politikası (tür başına tek yaşayan politika)."""
    require_permission(actor, "leave.type.manage")
    if leave_type.deleted_at is not None:
        raise LeaveError("Silinmiş izin türü için politika tanımlanamaz.")

    if accrual_method == AccrualMethod.FIXED:
        if fixed_days is None:
            raise LeavePolicyError("`fixed` yöntem için sabit gün zorunludur.")
        if rules:
            raise LeavePolicyError("`fixed` yöntemde kıdem kuralı tanımlanamaz.")
    elif accrual_method == AccrualMethod.TENURE_BASED:
        if fixed_days is not None:
            raise LeavePolicyError("`tenure_based` yöntemde sabit gün tanımlanamaz.")
        _kural_dogrula(rules or [])
    else:
        raise LeavePolicyError(f"Bilinmeyen hakediş yöntemi: {accrual_method}")

    if LeavePolicy.objects.filter(leave_type=leave_type, deleted_at__isnull=True).exists():
        raise LeavePolicyError(
            "Bu izin türü için yaşayan bir politika zaten var (güncelleyin veya silin)."
        )

    with transaction.atomic():
        policy = LeavePolicy.objects.create(
            tenant=leave_type.tenant,
            leave_type=leave_type,
            accrual_method=accrual_method,
            fixed_days=fixed_days,
            allow_negative_balance=allow_negative_balance,
            allow_refund_after_approval=allow_refund_after_approval,
        )
        for kural in rules or []:
            LeavePolicyRule.objects.create(
                tenant=leave_type.tenant,
                policy=policy,
                min_service_months=int(kural["min_service_months"]),
                max_service_months=(
                    None
                    if kural.get("max_service_months") is None
                    else int(kural["max_service_months"])
                ),
                entitlement_days=Decimal(kural["entitlement_days"]),
            )
        log_action(
            tenant_id=leave_type.tenant_id,
            action="leave.policy.create",
            user=actor,
            table_name="leave_policies",
            record_id=policy.pk,
            changes={
                "leave_type_id": str(leave_type.pk),
                "accrual_method": accrual_method,
                "fixed_days": None if fixed_days is None else str(fixed_days),
                "rules": len(rules or []),
                "allow_negative_balance": allow_negative_balance,
            },
        )
    return policy


def update_leave_policy(
    *,
    actor,
    policy: LeavePolicy,
    fixed_days: Decimal | None = None,
    rules: list[dict] | None = None,
    allow_negative_balance: bool | None = None,
    allow_refund_after_approval: bool | None = None,
    reason: str = "",
) -> LeavePolicy:
    """
    Politikayı günceller.

    `rules` verilirse mevcut kurallar **silinmez**, soft-delete edilir ve yenileri
    yazılır (denetim izi korunur, OD21).
    """
    require_permission(actor, "leave.type.manage")
    degisim: dict = {}
    with transaction.atomic():
        if fixed_days is not None:
            if policy.accrual_method != AccrualMethod.FIXED:
                raise LeavePolicyError("`tenure_based` politikada sabit gün değiştirilemez.")
            policy.fixed_days = Decimal(fixed_days)
            degisim["fixed_days"] = str(fixed_days)

        if rules is not None:
            if policy.accrual_method != AccrualMethod.TENURE_BASED:
                raise LeavePolicyError("`fixed` politikada kıdem kuralı tanımlanamaz.")
            _kural_dogrula(rules)
            # Mevcut kurallar SİLİNMEZ: soft-delete edilir (OD21, denetim izi korunur).
            policy.rules.filter(deleted_at__isnull=True).update(deleted_at=timezone.now())
            for kural in rules:
                LeavePolicyRule.objects.create(
                    tenant=policy.tenant,
                    policy=policy,
                    min_service_months=int(kural["min_service_months"]),
                    max_service_months=(
                        None
                        if kural.get("max_service_months") is None
                        else int(kural["max_service_months"])
                    ),
                    entitlement_days=Decimal(kural["entitlement_days"]),
                )
            degisim["rules"] = len(rules)

        if allow_negative_balance is not None:
            policy.allow_negative_balance = allow_negative_balance
            degisim["allow_negative_balance"] = allow_negative_balance
        if allow_refund_after_approval is not None:
            policy.allow_refund_after_approval = allow_refund_after_approval
            degisim["allow_refund_after_approval"] = allow_refund_after_approval

        if not degisim:
            return policy

        policy.save()
        log_action(
            tenant_id=policy.tenant_id,
            action="leave.policy.update",
            user=actor,
            table_name="leave_policies",
            record_id=policy.pk,
            changes={**degisim, "has_reason": bool(reason)},
        )
    return policy


def deactivate_leave_policy(*, actor, policy: LeavePolicy, reason: str) -> LeavePolicy:
    """Politikayı pasifleştirir (yeni hakediş üretmez; kural/geçmiş korunur)."""
    require_permission(actor, "leave.type.manage")
    with transaction.atomic():
        policy.status = RecordStatus.INACTIVE
        policy.save(update_fields=["status", "updated_at"])
        log_action(
            tenant_id=policy.tenant_id,
            action="leave.policy.deactivate",
            user=actor,
            table_name="leave_policies",
            record_id=policy.pk,
            changes={"status": RecordStatus.INACTIVE, "has_reason": bool(reason)},
        )
    return policy


__all__ = [
    "create_leave_policy",
    "deactivate_leave_policy",
    "entitlement_for",
    "update_leave_policy",
]
