"""Fazla mesai kuralı doğrulama ve oluşturma servisleri (P5b)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit.services import log_action
from apps.overtime.models import CompensationType, OvertimeRule
from apps.overtime.services.base import (
    OvertimeAuthorizationError,
    OvertimeValidationError,
    require_permission,
)


def validate_overtime_rule(
    *,
    code: str,
    name: str,
    min_minutes: int,
    max_minutes: int | None,
    multiplier: Decimal | str,
    compensation_type: str,
    effective_from: date,
    effective_until: date | None,
) -> dict:
    """Kural girdisini normalize eder; geçersiz değerleri fail-closed reddeder."""
    if not isinstance(code, str) or not code.strip():
        raise OvertimeValidationError("Fazla mesai kuralı kodu zorunludur.")
    if not isinstance(name, str) or not name.strip():
        raise OvertimeValidationError("Fazla mesai kuralı adı zorunludur.")

    try:
        min_minutes = int(min_minutes)
    except (TypeError, ValueError) as exc:
        raise OvertimeValidationError("Asgari dakika geçerli bir tam sayı olmalıdır.") from exc
    if min_minutes < 0:
        raise OvertimeValidationError("Asgari dakika negatif olamaz.")

    if max_minutes is not None:
        try:
            max_minutes = int(max_minutes)
        except (TypeError, ValueError) as exc:
            raise OvertimeValidationError("Azami dakika geçerli bir tam sayı olmalıdır.") from exc
        if max_minutes <= min_minutes:
            raise OvertimeValidationError("Azami dakika, asgari dakikadan büyük olmalıdır.")

    try:
        multiplier = Decimal(str(multiplier)).quantize(Decimal("0.001"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OvertimeValidationError("Katsayı geçerli bir sayı olmalıdır.") from exc
    if multiplier <= 0:
        raise OvertimeValidationError("Katsayı sıfırdan büyük olmalıdır.")

    if compensation_type not in {choice.value for choice in CompensationType}:
        raise OvertimeValidationError("Karşılık türü yalnızca ücret veya izin olabilir.")
    if not isinstance(effective_from, date):
        raise OvertimeValidationError("Kural başlangıç tarihi geçersizdir.")
    if effective_until is not None:
        if not isinstance(effective_until, date) or effective_until < effective_from:
            raise OvertimeValidationError(
                "Kural bitiş tarihi başlangıç tarihinden önce olamaz."
            )

    return {
        "code": code.strip(),
        "name": name.strip(),
        "min_minutes": min_minutes,
        "max_minutes": max_minutes,
        "multiplier": multiplier,
        "compensation_type": compensation_type,
        "effective_from": effective_from,
        "effective_until": effective_until,
    }


def _araliklar_cakisiyor(
    *,
    first_min: int,
    first_max: int | None,
    second_min: int,
    second_max: int | None,
) -> bool:
    """Yarı açık dakika aralıklarının kesişimini kontrol eder."""
    first_upper = float("inf") if first_max is None else first_max
    second_upper = float("inf") if second_max is None else second_max
    return first_min < second_upper and second_min < first_upper


def _tarihler_cakisiyor(
    *,
    first_from: date,
    first_until: date | None,
    second_from: date,
    second_until: date | None,
) -> bool:
    first_upper = date.max if first_until is None else first_until
    second_upper = date.max if second_until is None else second_until
    return first_from <= second_upper and second_from <= first_upper


def list_overtime_rules(*, actor):
    """Aktörün tenant'ındaki canlı kuralları yeni geçerlilik tarihinden itibaren döndürür."""
    require_permission(actor, "overtime.rule.read")
    tenant_id = getattr(actor, "tenant_id", None)
    if tenant_id is None:
        raise OvertimeAuthorizationError("Fazla mesai kuralları kiracı olmadan okunamaz.")
    return OvertimeRule.objects.filter(
        tenant_id=tenant_id,
        deleted_at__isnull=True,
    ).order_by("-effective_from", "min_minutes", "code")


def resolve_overtime_rule(*, tenant_id, work_date: date, total_minutes: int) -> OvertimeRule:
    """Onay anındaki tarih ve süre için tek, canlı tenant kuralını seçer."""
    if tenant_id is None or not isinstance(work_date, date) or total_minutes <= 0:
        raise OvertimeValidationError("Fazla mesai kuralı seçimi için geçerli tarih ve süre gerekir.")

    kurallar = list(
        OvertimeRule.objects.filter(
            tenant_id=tenant_id,
            deleted_at__isnull=True,
            effective_from__lte=work_date,
        )
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=work_date))
        .filter(min_minutes__lte=total_minutes)
        .filter(Q(max_minutes__isnull=True) | Q(max_minutes__gt=total_minutes))
        .order_by("-effective_from", "min_minutes", "code")
    )
    if len(kurallar) != 1:
        raise OvertimeValidationError("Fazla mesai için tek bir geçerli kural bulunamadı.")
    return kurallar[0]


def calculate_time_off_minutes(*, total_minutes: int, multiplier: Decimal | str) -> Decimal:
    """TIME_OFF karşılığını dakika olarak hesaplar; gün dönüşümü yapmaz."""
    if not isinstance(total_minutes, int) or total_minutes <= 0:
        raise OvertimeValidationError("Fazla mesai süresi pozitif tam sayı olmalıdır.")
    try:
        katsayi = Decimal(str(multiplier))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise OvertimeValidationError("TIME_OFF katsayısı geçersizdir.") from exc
    if katsayi <= 0:
        raise OvertimeValidationError("TIME_OFF katsayısı sıfırdan büyük olmalıdır.")
    return (Decimal(total_minutes) * katsayi).quantize(Decimal("0.001"))


def calculate_time_off_days(
    *, total_minutes: int, multiplier: Decimal | str, workday_minutes: int
) -> Decimal:
    """TIME_OFF dakikasını tenant çalışma günü uzunluğuna göre güne çevirir."""
    if not isinstance(workday_minutes, int) or workday_minutes <= 0:
        raise OvertimeValidationError("Tenant çalışma günü dakikası pozitif tam sayı olmalıdır.")
    return (calculate_time_off_minutes(total_minutes=total_minutes, multiplier=multiplier)
            / Decimal(workday_minutes)).quantize(Decimal("0.0001"))


def calculate_time_off_credit(*, total_minutes: int, multiplier: Decimal | str) -> dict:
    """TIME_OFF kredisini yarım gün adımına aşağı yuvarlar.

    Her 240 karşılık dakika 0,5 gün eder; tamamlanmayan bölüm sonraki krediye
    devredilmek üzere dakika olarak döner.
    """
    hesaplanan = calculate_time_off_minutes(
        total_minutes=total_minutes, multiplier=multiplier
    )
    tam_dakika = int(hesaplanan)
    birimler, kalan = divmod(tam_dakika, 240)
    return {
        "credited_days": (Decimal(birimler) / Decimal("2")).quantize(Decimal("0.1")),
        "remainder_minutes": kalan,
    }


@transaction.atomic
def create_overtime_rule(
    *,
    actor,
    code: str,
    name: str,
    min_minutes: int,
    max_minutes: int | None,
    multiplier: Decimal | str,
    compensation_type: str,
    effective_from: date,
    effective_until: date | None = None,
) -> OvertimeRule:
    """Yetkili aktör için çakışmayan fazla mesai kuralı oluşturur."""
    require_permission(actor, "overtime.rule.manage")
    veri = validate_overtime_rule(
        code=code,
        name=name,
        min_minutes=min_minutes,
        max_minutes=max_minutes,
        multiplier=multiplier,
        compensation_type=compensation_type,
        effective_from=effective_from,
        effective_until=effective_until,
    )
    tenant_id = getattr(actor, "tenant_id", None)
    if tenant_id is None:
        raise OvertimeValidationError("Fazla mesai kuralı kiracı olmadan oluşturulamaz.")

    mevcutlar = OvertimeRule.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True)
    for mevcut in mevcutlar:
        if not _tarihler_cakisiyor(
            first_from=mevcut.effective_from,
            first_until=mevcut.effective_until,
            second_from=veri["effective_from"],
            second_until=veri["effective_until"],
        ):
            continue
        if _araliklar_cakisiyor(
            first_min=mevcut.min_minutes,
            first_max=mevcut.max_minutes,
            second_min=veri["min_minutes"],
            second_max=veri["max_minutes"],
        ):
            raise OvertimeValidationError(
                "Fazla mesai kuralı dakika ve geçerlilik aralığında mevcut bir kuralla çakışıyor."
            )

    kural = OvertimeRule.objects.create(
        tenant_id=tenant_id,
        created_by=actor,
        **veri,
    )
    log_action(
        tenant_id=tenant_id,
        action="overtime.rule.create",
        user=actor,
        table_name="overtime_rules",
        record_id=kural.pk,
        changes={
            "code": kural.code,
            "name": kural.name,
            "min_minutes": kural.min_minutes,
            "max_minutes": kural.max_minutes,
            "multiplier": str(kural.multiplier),
            "compensation_type": kural.compensation_type,
            "effective_from": kural.effective_from.isoformat(),
            "effective_until": (
                None if kural.effective_until is None else kural.effective_until.isoformat()
            ),
        },
    )
    return kural


@transaction.atomic
def update_overtime_rule(*, actor, rule: OvertimeRule, **changes) -> OvertimeRule:
    """Yetkili aktörün canlı fazla mesai kuralını çakışma kontrolüyle günceller."""
    require_permission(actor, "overtime.rule.manage")
    if rule.tenant_id != getattr(actor, "tenant_id", None) or rule.deleted_at is not None:
        raise OvertimeValidationError("Fazla mesai kuralı bulunamadı.")
    veri = validate_overtime_rule(
        code=changes.get("code", rule.code), name=changes.get("name", rule.name),
        min_minutes=changes.get("min_minutes", rule.min_minutes),
        max_minutes=changes.get("max_minutes", rule.max_minutes),
        multiplier=changes.get("multiplier", rule.multiplier),
        compensation_type=changes.get("compensation_type", rule.compensation_type),
        effective_from=changes.get("effective_from", rule.effective_from),
        effective_until=changes.get("effective_until", rule.effective_until),
    )
    for mevcut in OvertimeRule.objects.filter(tenant_id=rule.tenant_id, deleted_at__isnull=True).exclude(pk=rule.pk):
        if _tarihler_cakisiyor(first_from=mevcut.effective_from, first_until=mevcut.effective_until,
                               second_from=veri["effective_from"], second_until=veri["effective_until"]) and _araliklar_cakisiyor(
            first_min=mevcut.min_minutes, first_max=mevcut.max_minutes,
            second_min=veri["min_minutes"], second_max=veri["max_minutes"]):
            raise OvertimeValidationError("Fazla mesai kuralı dakika ve geçerlilik aralığında mevcut bir kuralla çakışıyor.")
    for alan, deger in veri.items():
        setattr(rule, alan, deger)
    rule.save(update_fields=[*veri.keys(), "updated_at"])
    log_action(tenant_id=rule.tenant_id, action="overtime.rule.update", user=actor,
               table_name="overtime_rules", record_id=rule.pk, changes={k: str(v) for k, v in veri.items()})
    return rule


@transaction.atomic
def delete_overtime_rule(*, actor, rule: OvertimeRule, reason: str = "") -> OvertimeRule:
    """Kuralı soft delete eder; geçmiş taleplerin snapshot'ları korunur."""
    require_permission(actor, "overtime.rule.manage")
    if rule.tenant_id != getattr(actor, "tenant_id", None) or rule.deleted_at is not None:
        raise OvertimeValidationError("Fazla mesai kuralı bulunamadı.")
    rule.deleted_at = timezone.now()
    rule.save(update_fields=["deleted_at", "updated_at"])
    log_action(tenant_id=rule.tenant_id, action="overtime.rule.delete", user=actor,
               table_name="overtime_rules", record_id=rule.pk, changes={"has_reason": bool(reason)})
    return rule
