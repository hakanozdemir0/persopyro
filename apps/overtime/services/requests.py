"""
Fazla mesai talebi servisleri — oluşturma, karar (onay/ret) ve iptal (**P5a V1**).

Tasarım:
  * **Süre sunucuda:** `total_minutes` daima `end_time - start_time` ile hesaplanır;
    istemci değeri yalnızca **doğrulanır** (yazılmaz). Sıfır/negatif süre ve makul
    olmayan üst sınır reddedilir (`OVERTIME_MAX_MINUTES`); aynı kural DB `CHECK`'i
    ile de zorlanır (bkz. `overtime/0002_p5a_overtime_guards`).
  * **Onay altyapısı ortak:** adımlar `hr.approval_steps` tablosunda kurulur
    (`subject_kind='overtime_request'`); sıra atlanamaz (OD39). Adım **birincil
    yönetici** → **İK** sırasıyla kurulur; kaydı açan kişi birincil yöneticisi ise o
    adım kurulmaz (`SEPARATE_ACTOR`: kendi talebini onaylayamaz).
  * **Karşılık/katsayı YOK:** bu dilimde `compensation_type` yazılmaz (kural motoru
    P5a/P5b ile gelir); ücret veya izin karşılığı **hesaplanmaz**.
  * Her işlem `log_action` ile denetim kaydına yazılır (parola/PII yazılmaz).
"""

from __future__ import annotations

from datetime import datetime, time

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.leave.models import ApprovalStep, ApprovalStepStatus, ApprovalSubjectKind
from apps.leave.models import LeaveType, RecordStatus
from apps.leave.services.balances import credit_time_off_balance
from apps.leave.services.requests import HR_APPROVER_ROLE_CODE
from apps.organization.models import Employee, EmployeeManager, ManagerRelationType
from apps.overtime.models import (
    OVERTIME_MAX_MINUTES,
    OVERTIME_MIN_DESCRIPTION_LENGTH,
    OVERTIME_TRANSITIONS,
    OvertimeRequest,
    OvertimeRule,
    OvertimeStatus,
)
from apps.overtime.services.authorization import (
    aktif_calisan,
    can_approve_overtime,
    can_cancel_overtime,
    can_create_overtime_for,
    current_step,
)
from apps.overtime.services.base import (
    OvertimeAuthorizationError,
    OvertimeError,
    OvertimeStateError,
    OvertimeValidationError,
    require_active,
)
from apps.overtime.services.rules import calculate_time_off_days, resolve_overtime_rule


def calculate_total_minutes(*, start_time: time, end_time: time) -> int:
    """
    Süreyi dakika olarak hesaplar (sunucu tek kaynak).

    * Gece yarısını aşan kayıt **tek satırda** ifade edilemez (`end <= start` →
      reddedilir); iki ayrı bildirim ya da ileriki fazın vardiya/devam hesabı gerekir.
    * Sıfır veya negatif süre reddedilir; `OVERTIME_MAX_MINUTES` üstü makul sayılmaz.
    """
    if start_time is None or end_time is None:
        raise OvertimeValidationError("Başlangıç ve bitiş saati zorunludur.")
    fark = datetime.combine(datetime.min.date(), end_time) - datetime.combine(
        datetime.min.date(), start_time
    )
    dakika = int(fark.total_seconds() // 60)
    if dakika <= 0:
        raise OvertimeValidationError(
            "Bitiş saati başlangıçtan sonra olmalıdır (süre sıfır veya negatif olamaz)."
        )
    if dakika > OVERTIME_MAX_MINUTES:
        raise OvertimeValidationError(
            f"Tek kayıtta en fazla {OVERTIME_MAX_MINUTES // 60} saat fazla mesai "
            "bildirilebilir; lütfen kaydı günlere bölün."
        )
    return dakika


def _aciklama_dogrula(metin: str) -> str:
    """Açıklama zorunludur ve anlamlı uzunlukta olmalıdır (denetim izi; DB guard'ı ile aynı)."""
    temiz = (metin or "").strip()
    if len(temiz) < OVERTIME_MIN_DESCRIPTION_LENGTH:
        raise OvertimeValidationError(
            "Fazla mesai için açıklama zorunludur (en az "
            f"{OVERTIME_MIN_DESCRIPTION_LENGTH} karakter)."
        )
    return temiz


def _cakisan_talep_var(employee: Employee, work_date, start_time, end_time) -> bool:
    """
    Aynı gün için çakışan **açık/onaylı** kayıt var mı (kapalı aralıklar `[start, end)`).

    Reddedilen/iptal edilmiş kayıtlar engel değildir: düzeltme yeni kayıtla yapılır.
    """
    return (
        OvertimeRequest.objects.filter(
            employee=employee,
            work_date=work_date,
            deleted_at__isnull=True,
            status__in=(OvertimeStatus.PENDING, OvertimeStatus.APPROVED),
            start_time__lt=end_time,
            end_time__gt=start_time,
        )
        .exists()
    )


def _onayli_izin_var(employee: Employee, work_date) -> bool:
    """Aynı gün gönderilmiş/onaylı izin varsa fazla mesaiyi kapatır."""
    # Yerel import, izin servislerinin fazla mesai servislerini kullandığı
    # onay akışında modül döngüsü oluşmasını engeller.
    from apps.leave.models import LeaveRequest, LeaveRequestStatus

    return LeaveRequest.objects.filter(
        tenant_id=employee.tenant_id,
        employee_id=employee.pk,
        deleted_at__isnull=True,
        status__in=(LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED),
        start_date__lte=work_date,
        end_date__gte=work_date,
    ).exists()


def _adimlari_olustur(talep: OvertimeRequest) -> list[ApprovalStep]:
    """
    Onay adımlarını kurar: **birincil yönetici → İK** (OD39).

    Birincil yönetici yoksa ya da kaydı açanın kendisiyse yönetici adımı **kurulmaz**
    (kendi talebini onaylayamaz); adım İK rolüne düşer.
    """
    adimlar: list[ApprovalStep] = []
    sira = 1

    iliski = (
        EmployeeManager.objects.filter(
            employee_id=talep.employee_id,
            relation_type=ManagerRelationType.PRIMARY,
            effective_to__isnull=True,
            deleted_at__isnull=True,
        )
        .select_related("manager_employee")
        .order_by("-effective_from")
        .first()
    )
    if iliski is not None and iliski.manager_employee.deleted_at is None:
        yonetici_user_id = iliski.manager_employee.user_id
        sahibi_user_id = talep.employee.user_id
        if yonetici_user_id is not None and yonetici_user_id != sahibi_user_id:
            adimlar.append(
                ApprovalStep(
                    tenant_id=talep.tenant_id,
                    subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
                    subject_id=talep.pk,
                    step_no=sira,
                    approver_user_id=yonetici_user_id,
                    status=ApprovalStepStatus.PENDING,
                )
            )
            sira += 1

    adimlar.append(
        ApprovalStep(
            tenant_id=talep.tenant_id,
            subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
            subject_id=talep.pk,
            step_no=sira,
            approver_role_code=HR_APPROVER_ROLE_CODE,
            status=ApprovalStepStatus.PENDING,
        )
    )
    for adim in adimlar:
        adim.save()
    return adimlar


def create_overtime_request(
    *,
    actor,
    employee: Employee,
    work_date,
    start_time: time,
    end_time: time,
    description: str,
    client_total_minutes=None,
    overtime_rule: OvertimeRule | None = None,
) -> OvertimeRequest:
    """
    Fazla mesai bildirimi oluşturur ve **doğrudan onaya sunar** (`pending`).

    * Yetki: `overtime.request.create` ∧ **yalnız kendi çalışan kaydı** (V1 sınırı).
    * Çalışan `active`/`suspended` olmalıdır (işten çıkmış kayıt bildiremez).
    * Gelecek tarih için bildirim açılamaz (henüz çalışılmamış gün).
    * Aynı gün çakışan açık/onaylı kayıt varsa reddedilir.
    """
    require_active(actor)
    if not can_create_overtime_for(actor, employee):
        raise OvertimeAuthorizationError(
            "Fazla mesai bildirimi için 'overtime.request.create' izni gerekir ve "
            "bildirim yalnızca kendi çalışan kaydınız için açılabilir."
        )
    if not aktif_calisan(employee):
        raise OvertimeValidationError(
            "Yalnızca aktif/işten çıkmamış çalışan kaydı için bildirim açılabilir."
        )
    if work_date is None:
        raise OvertimeValidationError("Tarih zorunludur.")
    if work_date > timezone.localdate():
        raise OvertimeValidationError("Gelecek bir tarih için fazla mesai bildirilemez.")

    aciklama = _aciklama_dogrula(description)
    dakika = calculate_total_minutes(start_time=start_time, end_time=end_time)

    if overtime_rule is not None and (
        overtime_rule.tenant_id != employee.tenant_id
        or overtime_rule.deleted_at is not None
        or overtime_rule.effective_from > work_date
        or (
            overtime_rule.effective_until is not None
            and overtime_rule.effective_until < work_date
        )
        or overtime_rule.min_minutes > dakika
        or (
            overtime_rule.max_minutes is not None
            and overtime_rule.max_minutes <= dakika
        )
    ):
        raise OvertimeValidationError(
            "Seçilen fazla mesai kuralı bu tarih ve süre için geçerli değil."
        )

    # İstemci süre gönderdiyse **yalnızca doğrulanır** (yazılmaz).
    if client_total_minutes not in (None, ""):
        try:
            istemci = int(client_total_minutes)
        except (TypeError, ValueError) as hata:
            raise OvertimeValidationError("Gönderilen süre sayı olmalıdır.") from hata
        if istemci != dakika:
            raise OvertimeValidationError(
                "Gönderilen süre ile saat aralığı uyuşmuyor; süre sunucuda hesaplanır."
            )

    if _cakisan_talep_var(employee, work_date, start_time, end_time):
        raise OvertimeValidationError(
            "Bu tarih ve saat aralığı için açık/onaylı bir fazla mesai kaydı zaten var."
        )
    if _onayli_izin_var(employee, work_date):
        raise OvertimeStateError(
            "Bu gün için bekleyen veya onaylanmış izin bulunduğundan fazla mesai bildirilemez."
        )

    with transaction.atomic():
        talep = OvertimeRequest.objects.create(
            tenant_id=employee.tenant_id,
            employee=employee,
            work_date=work_date,
            start_time=start_time,
            end_time=end_time,
            total_minutes=dakika,
            description=aciklama,
            overtime_rule=overtime_rule,
            status=OvertimeStatus.PENDING,
            created_by=actor,
        )
        adimlar = _adimlari_olustur(talep)
        log_action(
            tenant_id=talep.tenant_id,
            action="overtime.request.create",
            user=actor,
            table_name="overtime_requests",
            record_id=talep.pk,
            changes={
                "employee_no": employee.employee_no,
                "work_date": str(work_date),
                "total_minutes": dakika,
                "adim_sayisi": len(adimlar),
                "karari_bekleyen": adimlar[0].step_no if adimlar else None,
            },
        )
    return talep


def decide_overtime_request(
    *, actor, talep: OvertimeRequest, decision: str, reason: str = ""
) -> OvertimeRequest:
    """
    Geçerli onay adımı için karar verir (`approved` / `rejected`).

    * Talep satırı `select_for_update()` ile kilitlenir → aynı talebin iki kez karara
      bağlanması mümkün değildir (ikinci çağrı durum hatası alır).
    * Son adım onaylanınca talep `approved`; herhangi bir adımda ret → `rejected`.
    * Ek kural yok: bu sürümde onay **hiçbir hesaplama tetiklemez** (ücret/izin
      karşılığı P5b/OD32).
    """
    require_active(actor)
    if decision not in (OvertimeStatus.APPROVED, OvertimeStatus.REJECTED):
        raise OvertimeValidationError("Karar 'approved' veya 'rejected' olmalıdır.")

    with transaction.atomic():
        kilitli = OvertimeRequest.objects.select_for_update().get(pk=talep.pk)
        if kilitli.status != OvertimeStatus.PENDING:
            raise OvertimeStateError(
                f"Yalnızca onay bekleyen talep karara bağlanabilir (mevcut: {kilitli.status})."
            )
        adim = current_step(kilitli)
        if adim is None:
            raise OvertimeStateError("Talebin karar bekleyen onay adımı yok.")
        if not can_approve_overtime(actor, kilitli):
            raise OvertimeAuthorizationError(
                "Karar için 'overtime.request.approve' izni, kapsam, adım sahipliği ve "
                "'farklı aktör' koşulu gerekir (kendi bildiriminizi onaylayamazsınız)."
            )

        gerekce = (reason or "").strip()
        simdi = timezone.now()
        adim.status = decision
        adim.decided_at = simdi
        adim.decision_reason = gerekce
        adim.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

        if decision == OvertimeStatus.REJECTED:
            return _reddet(kilitli, actor=actor, reason=gerekce, adim=adim)

        if current_step(kilitli) is None:
            return _nihai_onay(kilitli, actor=actor)

        log_action(
            tenant_id=kilitli.tenant_id,
            action="overtime.request.approve_step",
            user=actor,
            table_name="approval_steps",
            record_id=adim.pk,
            changes={
                "step_no": adim.step_no,
                "request_id": str(kilitli.pk),
                "sonraki_adim": getattr(current_step(kilitli), "step_no", None),
            },
        )
        return kilitli


def _nihai_onay(talep: OvertimeRequest, *, actor) -> OvertimeRequest:
    """Nihai onayda kural değerlerini immutable snapshot alanlarına alır."""
    if _onayli_izin_var(talep.employee, talep.work_date):
        raise OvertimeStateError(
            "Bu gün için bekleyen veya onaylanmış izin bulunduğundan fazla mesai onaylanamaz."
        )
    kural = talep.overtime_rule or resolve_overtime_rule(
        tenant_id=talep.tenant_id,
        work_date=talep.work_date,
        total_minutes=talep.total_minutes,
    )
    talep.status = OvertimeStatus.APPROVED
    talep.compensation_type = kural.compensation_type
    talep.compensation_type_snapshot = kural.compensation_type
    talep.multiplier_snapshot = kural.multiplier
    talep.decided_at = timezone.now()
    talep.decided_by = actor
    talep.save(update_fields=[
        "status", "compensation_type", "compensation_type_snapshot", "multiplier_snapshot",
        "decided_at", "decided_by", "updated_at",
    ])
    if kural.compensation_type == "time_off":
        izin_turu = LeaveType.objects.filter(
            tenant_id=talep.tenant_id, code="time_off", status=RecordStatus.ACTIVE,
            deleted_at__isnull=True,
        ).first()
        if izin_turu is None:
            raise OvertimeValidationError("Serbest zaman izin türü tanımlı değil.")
        credit_time_off_balance(
            employee=talep.employee, leave_type=izin_turu, overtime_request=talep,
            delta_days=calculate_time_off_days(
                total_minutes=talep.total_minutes, multiplier=kural.multiplier,
                workday_minutes=izin_turu.workday_minutes,
            ), actor=actor, reason="Onaylanan fazla mesai serbest zaman kredisi",
        )
    log_action(
        tenant_id=talep.tenant_id,
        action="overtime.request.approve",
        user=actor,
        table_name="overtime_requests",
        record_id=talep.pk,
        changes={
            "employee_no": talep.employee.employee_no,
            "work_date": str(talep.work_date),
            "total_minutes": talep.total_minutes,
            "compensation_type": talep.compensation_type_snapshot,
            "multiplier": str(talep.multiplier_snapshot),
        },
    )
    return talep


def _reddet(talep: OvertimeRequest, *, actor, reason: str, adim) -> OvertimeRequest:
    """Ret: kalan adımlar kapatılır, talep `rejected` olur."""
    simdi = timezone.now()
    kalanlar = list(
        ApprovalStep.objects.filter(
            subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
            subject_id=talep.pk,
            status=ApprovalStepStatus.PENDING,
            deleted_at__isnull=True,
        )
    )
    for kalan in kalanlar:
        if adim is not None and kalan.pk == adim.pk:
            continue
        kalan.status = ApprovalStepStatus.CANCELED
        kalan.decided_at = simdi
        kalan.decision_reason = "Talep reddedildi"
        kalan.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

    talep.status = OvertimeStatus.REJECTED
    talep.decided_at = simdi
    talep.decided_by = actor
    talep.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    log_action(
        tenant_id=talep.tenant_id,
        action="overtime.request.reject",
        user=actor,
        table_name="overtime_requests",
        record_id=talep.pk,
        changes={
            "employee_no": talep.employee.employee_no,
            "work_date": str(talep.work_date),
            "has_reason": bool(reason),
        },
    )
    return talep


def cancel_overtime_request(*, actor, talep: OvertimeRequest, reason: str) -> OvertimeRequest:
    """
    `pending` talebi iptal eder — **gerekçe zorunludur** (denetim izi).

    Karara bağlanmış (`approved`/`rejected`) kayıt iptal **edilemez**; düzeltme ayrı
    iş kalemidir (OD21: sessiz geri alma yok).
    """
    require_active(actor)
    gerekce = (reason or "").strip()
    if len(gerekce) < 5:
        raise OvertimeValidationError("İptal için anlamlı bir gerekçe zorunludur.")

    with transaction.atomic():
        kilitli = OvertimeRequest.objects.select_for_update().get(pk=talep.pk)
        if kilitli.status != OvertimeStatus.PENDING:
            raise OvertimeStateError(
                f"Yalnızca onay bekleyen bildirim iptal edilebilir (mevcut: {kilitli.status})."
            )
        if not can_cancel_overtime(actor, kilitli):
            raise OvertimeAuthorizationError(
                "İptal için 'overtime.request.cancel' izni ve kayıt sahipliği/kapsamı gerekir."
            )

        simdi = timezone.now()
        kalanlar = list(
            ApprovalStep.objects.filter(
                subject_kind=ApprovalSubjectKind.OVERTIME_REQUEST,
                subject_id=kilitli.pk,
                status=ApprovalStepStatus.PENDING,
                deleted_at__isnull=True,
            )
        )
        for adim in kalanlar:
            adim.status = ApprovalStepStatus.CANCELED
            adim.decided_at = simdi
            adim.decision_reason = "Bildirim iptal edildi"
            adim.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

        kilitli.status = OvertimeStatus.CANCELED
        kilitli.canceled_at = simdi
        kilitli.cancel_reason = gerekce
        kilitli.save(update_fields=["status", "canceled_at", "cancel_reason", "updated_at"])
        log_action(
            tenant_id=kilitli.tenant_id,
            action="overtime.request.cancel",
            user=actor,
            table_name="overtime_requests",
            record_id=kilitli.pk,
            changes={
                "employee_no": kilitli.employee.employee_no,
                "work_date": str(kilitli.work_date),
                "has_reason": True,
            },
        )
    return kilitli


def gecerli_gecis(mevcut: str, hedef: str) -> bool:
    """Durum geçişi kapalı kümede izinli mi (servis + DB guard'ı ile aynı tablo)."""
    return hedef in OVERTIME_TRANSITIONS.get(mevcut, frozenset())


__all__ = [
    "OVERTIME_MAX_MINUTES",
    "OvertimeError",
    "calculate_total_minutes",
    "cancel_overtime_request",
    "create_overtime_request",
    "decide_overtime_request",
    "gecerli_gecis",
]
