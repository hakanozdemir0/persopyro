"""
İzin talebi, gün hesabı ve onay akışı (M14 §5–§9, §11–§12).

Gün hesabı (§6):
  * İstenen gün **sunucu tarafında** hesaplanır; istemciden gelen değer yalnızca
    **doğrulanır** (uyuşmazsa reddedilir).
  * Hafta sonu/resmi tatil **hariç tutulmaz**: çalışma takvimi modülü (M15,
    `work_schedules`) gelene kadar gün sayısı **takvim günüdür**. Sahte kesinlik
    üretilmez; bu sınır docs'ta açıkça belirtilir.
  * `is_half_day=True` yalnızca tek gün için geçerlidir → 0,5 gün.

Onay akışı (§8–§9):
  * Adımlar `hr.approval_steps` (genel tablo) üzerindedir ve **sıra atlanamaz**
    (DB guard'ı + servis kontrolü).
  * Sıra (docs'ta kesin sıra tanımlı değildir → güvenli v1):
    1) **birincil yönetici** (`employee_manager`, varsa; `SEPARATE_ACTOR` ihlali ise atlanır)
    2) **yetkili rol** (`hr_manager` rol kodu + kapsam)
  * Primary manager olmak yetki **üretmez**: onay için `leave.request.approve` izni,
    kapsam, `ASSIGNED` ve `SEPARATE_ACTOR` koşulları birlikte aranır.

Yarış/idempotans (§11): talep satırı `select_for_update()` ile kilitlenir; nihai
onay **tek** kez olur ve bakiye hareketleri olay defterinin benzersiz kısıtlarıyla
(`leave_request_id`, `entry_kind`) korunur → çift tüketim DB düzeyinde engellenir.

Bakiye (§12): talep onaya sunulduğunda **rezerve** edilir (kalıcı tüketim değil);
`approved` → rezervasyon çözülür + kullanım yazılır; `rejected`/iptal → rezervasyon
çözülür (bakiye tüketilmez); onaylı talebin iptalinde iade **politika izin veriyorsa**
açık `refund` kaydı ve denetimle yapılır.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.leave.models import (
    ApprovalStep,
    ApprovalStepStatus,
    ApprovalSubjectKind,
    BalanceEntryKind,
    LeaveBalance,
    LeaveRequest,
    LeaveRequestStatus,
    LeaveType,
    RecordStatus,
)
from apps.leave.services.authorization import (
    can_approve_step,
    can_cancel_leave_request,
    can_create_leave_request_for,
    can_override_approval,
    current_step,
    employee_in_scope,
    is_own_employee,
)
from apps.leave.services.balances import _aktif_politika, get_or_create_balance, period_year_for
from apps.leave.services.base import (
    LeaveAuthorizationError,
    LeaveBalanceError,
    LeaveError,
    LeavePolicyError,
    LeaveStateError,
    require_active,
)
from apps.organization.models import Employee, EmployeeManager, EmployeeStatus, ManagerRelationType
from apps.rbac.services import user_has_permission
from apps.rbac.services.scope import object_in_org_scope

SIFIR = Decimal("0.0")

#: Onay akışındaki **yetkili rol** adımı (docs: role/kapsama bağlı adım).
HR_APPROVER_ROLE_CODE = "hr_manager"


# --- Gün hesabı ---------------------------------------------------------------


def calculate_requested_days(
    *,
    start_date: date,
    end_date: date,
    is_half_day: bool = False,
) -> Decimal:
    """
    İstenen gün sayısını **sunucu tarafında** hesaplar (takvim günü, kapsayıcı).

    Hafta sonu/resmi tatil **hesaba katılmaz** (çalışma takvimi modülü M15 gelene
    kadar); bu yüzden sonuç "takvim günü"dür ve arayüzde öyle gösterilmelidir.
    """
    if end_date < start_date:
        raise LeaveError("Bitiş tarihi başlangıç tarihinden önce olamaz.")
    if is_half_day:
        if start_date != end_date:
            raise LeaveError("Yarım gün izni yalnızca tek gün için tanımlanabilir.")
        return Decimal("0.5")
    return Decimal((end_date - start_date).days + 1)


def _dogrula_istenen_gun(hesaplanan: Decimal, istemci_degeri) -> None:
    """İstemciden gelen gün değeri sunucu hesabıyla **birebir** uyuşmalıdır."""
    if istemci_degeri is None:
        return
    try:
        verilen = Decimal(str(istemci_degeri))
    except Exception as hata:  # noqa: BLE001
        raise LeaveError("İstenen gün sayısı sayısal değil.") from hata
    if verilen != hesaplanan:
        raise LeaveError(
            f"İstenen gün sayısı sunucu hesabıyla uyuşmuyor: bildirilen {verilen}, "
            f"hesaplanan {hesaplanan} (takvim günü)."
        )


# --- Talep oluşturma / onaya sunma -------------------------------------------


def _tur_kullanilabilir(leave_type: LeaveType) -> None:
    if leave_type.deleted_at is not None:
        raise LeaveStateError("Silinmiş izin türü kullanılamaz (OD21).")
    if leave_type.status != RecordStatus.ACTIVE:
        raise LeaveStateError("Yalnızca aktif izin türleri için talep oluşturulabilir.")


def _calisan_kullanilabilir(employee: Employee) -> None:
    if employee.deleted_at is not None:
        raise LeaveStateError("Silinmiş çalışan izin talebi oluşturamaz (OD21).")
    if employee.status not in (EmployeeStatus.ACTIVE, EmployeeStatus.SUSPENDED):
        raise LeaveStateError(
            f"'{employee.status}' durumundaki çalışan yeni izin talebi oluşturamaz."
        )


def create_leave_request(
    *,
    actor,
    employee: Employee,
    leave_type: LeaveType,
    start_date: date,
    end_date: date,
    reason: str = "",
    is_half_day: bool = False,
    start_time=None,
    end_time=None,
    requested_days=None,
    submit: bool = False,
) -> LeaveRequest:
    """
    İzin talebi oluşturur (varsayılan `draft`; `submit=True` ile onaya sunar).

    * `leave.request.create` izni + kapsam (`SELF` veya kapsamdaki çalışan) gerekir.
    * Gün sayısı sunucu tarafında hesaplanır; istemci değeri doğrulanır.
    * Ön bildirim (`min_advance_notice_days`) ve azami kesintisiz gün
      (`max_consecutive_days`) kuralları uygulanır.
    """
    if not user_has_permission(actor, "leave.request.create"):
        raise LeaveAuthorizationError(
            "İzin talebi oluşturmak için 'leave.request.create' izni gerekir."
        )
    _calisan_kullanilabilir(employee)
    _tur_kullanilabilir(leave_type)
    if not (is_own_employee(actor, employee) or employee_in_scope(actor, employee)):
        raise LeaveAuthorizationError(
            "Bu çalışan adına izin talebi oluşturmak için 'leave.request.create' izni "
            "ve organizasyon kapsamı (ya da kayıt sahipliği) gerekir."
        )

    gun = calculate_requested_days(
        start_date=start_date, end_date=end_date, is_half_day=is_half_day
    )
    _dogrula_istenen_gun(gun, requested_days)

    bugun = timezone.localdate()
    if leave_type.min_advance_notice_days:
        asgari_tarih = bugun + timedelta(days=leave_type.min_advance_notice_days)
        if start_date < asgari_tarih:
            raise LeaveError(
                f"'{leave_type.code}' için en az {leave_type.min_advance_notice_days} gün "
                f"önceden talep açılmalıdır (en erken başlangıç: {asgari_tarih})."
            )
    if (
        leave_type.max_consecutive_days is not None
        and gun > Decimal(leave_type.max_consecutive_days)
    ):
        raise LeaveError(
            f"'{leave_type.code}' için azami kesintisiz süre "
            f"{leave_type.max_consecutive_days} gündür (istenen: {gun})."
        )

    with transaction.atomic():
        request = LeaveRequest.objects.create(
            tenant_id=employee.tenant_id,
            employee=employee,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            start_time=start_time,
            end_time=end_time,
            is_half_day=is_half_day,
            days_requested=gun,
            reason=reason,
            status=LeaveRequestStatus.DRAFT,
        )
        log_action(
            tenant_id=employee.tenant_id,
            action="leave.request.create",
            user=actor,
            table_name="leave_requests",
            record_id=request.pk,
            changes={
                "employee_no": employee.employee_no,
                "leave_type": leave_type.code,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "days": str(gun),
            },
        )
        if submit:
            request = _onaya_sun(request, actor=actor)
    return request


def submit_leave_request(*, actor, request: LeaveRequest) -> LeaveRequest:
    """Taslak talebi onaya sunar (adımlar kurulur, bakiye rezerve edilir)."""
    with transaction.atomic():
        kilitli = LeaveRequest.objects.select_for_update().get(pk=request.pk)
        if not can_create_leave_request_for(actor, kilitli.employee):
            raise LeaveAuthorizationError(
                "Talebi onaya sunmak için 'leave.request.create' izni ve kapsam gerekir."
            )
        return _onaya_sun(kilitli, actor=actor)


def _onaya_sun(request: LeaveRequest, *, actor) -> LeaveRequest:
    """Taslağı `pending` durumuna geçirir: adımlar + rezervasyon + denetim."""
    if request.status != LeaveRequestStatus.DRAFT:
        raise LeaveStateError(
            f"Yalnızca taslak talep onaya sunulabilir (mevcut durum: {request.status})."
        )
    _tur_kullanilabilir(request.leave_type)
    request.submitted_at = timezone.now()
    request.status = LeaveRequestStatus.PENDING
    request.save(update_fields=["submitted_at", "status", "updated_at"])

    _adimlari_olustur(request)
    if request.leave_type.uses_balance:
        _rezerve_et(request, actor=actor)

    log_action(
        tenant_id=request.tenant_id,
        action="leave.request.submit",
        user=actor,
        table_name="leave_requests",
        record_id=request.pk,
        changes={
            "employee_no": request.employee.employee_no,
            "leave_type": request.leave_type.code,
            "days": str(request.days_requested),
            "requires_approval": request.leave_type.requires_approval,
        },
    )

    if not request.leave_type.requires_approval:
        # Onay gerektirmeyen tür (ör. toplu yıllık izin): sistem otomatik onaylar.
        request = _nihai_onay(request, actor=actor, otomatik=True)
    return request


def _adimlari_olustur(request: LeaveRequest) -> list[ApprovalStep]:
    """
    Onay adımlarını kurar (sıra: birincil yönetici → yetkili rol).

    `SEPARATE_ACTOR`: talebi oluşturan kişi birincil yöneticisi ise o adım
    **kurulmaz** (kendi talebini onaylayamaz) → yetkili rol adımı devralır.
    """
    if not request.leave_type.requires_approval:
        return []

    adimlar: list[ApprovalStep] = []
    sira = 1

    iliski = (
        EmployeeManager.objects.filter(
            employee_id=request.employee_id,
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
        talep_sahibi_user_id = request.employee.user_id
        if yonetici_user_id is not None and yonetici_user_id != talep_sahibi_user_id:
            adimlar.append(
                ApprovalStep(
                    tenant_id=request.tenant_id,
                    subject_kind=ApprovalSubjectKind.LEAVE_REQUEST,
                    subject_id=request.pk,
                    step_no=sira,
                    approver_user_id=yonetici_user_id,
                    status=ApprovalStepStatus.PENDING,
                )
            )
            sira += 1

    adimlar.append(
        ApprovalStep(
            tenant_id=request.tenant_id,
            subject_kind=ApprovalSubjectKind.LEAVE_REQUEST,
            subject_id=request.pk,
            step_no=sira,
            approver_role_code=HR_APPROVER_ROLE_CODE,
            status=ApprovalStepStatus.PENDING,
        )
    )
    for adim in adimlar:
        adim.save()
    return adimlar


# --- Bakiye hareketleri (defter ile birlikte) --------------------------------


def _kilitli_bakiye(request: LeaveRequest) -> LeaveBalance | None:
    if not request.leave_type.uses_balance:
        return None
    balance = get_or_create_balance(
        employee=request.employee,
        leave_type=request.leave_type,
        period_year=period_year_for(request.start_date),
    )
    return LeaveBalance.objects.select_for_update().get(pk=balance.pk)


def _defter(*, balance: LeaveBalance, kind: str, delta: Decimal, request, actor, reason: str = ""):
    from apps.leave.models import LeaveBalanceEntry  # noqa: PLC0415

    return LeaveBalanceEntry.objects.create(
        tenant_id=balance.tenant_id,
        balance=balance,
        employee_id=balance.employee_id,
        leave_type_id=balance.leave_type_id,
        period_year=balance.period_year,
        entry_kind=kind,
        delta_days=delta,
        leave_request=request,
        actor=actor,
        reason=reason,
    )


def _kalan(balance: LeaveBalance) -> Decimal:
    return (
        Decimal(balance.entitled_days)
        + Decimal(balance.carried_forward_days)
        + Decimal(balance.adjustment_days)
        - Decimal(balance.used_days)
        - Decimal(balance.reserved_days)
    )


def _rezerve_et(request: LeaveRequest, *, actor) -> None:
    """Onaya sunulan talep için bakiyeyi **rezerve** eder (kalıcı tüketim değil)."""
    balance = _kilitli_bakiye(request)
    if balance is None:
        return
    gun = Decimal(request.days_requested)
    kalan = _kalan(balance)
    if kalan < gun:
        policy = _aktif_politika(request.leave_type)
        if policy is None or not policy.allow_negative_balance:
            raise LeaveBalanceError(
                f"Yetersiz izin bakiyesi: kalan {kalan} gün, istenen {gun} gün "
                f"({request.leave_type.code}, {balance.period_year})."
            )
    balance.reserved_days = Decimal(balance.reserved_days) + gun
    balance.save(update_fields=["reserved_days", "updated_at"])
    _defter(
        balance=balance,
        kind=BalanceEntryKind.RESERVATION,
        delta=gun,
        request=request,
        actor=actor,
        reason="Onaya sunuldu (rezervasyon)",
    )


def _rezervasyonu_coz(request: LeaveRequest, *, actor, reason: str) -> None:
    """Rezervasyonu çözer (red/iptal): bakiye **tüketilmez**."""
    balance = _kilitli_bakiye(request)
    if balance is None:
        return
    gun = Decimal(request.days_requested)
    if Decimal(balance.reserved_days) < gun:
        raise LeaveBalanceError("Rezervasyon tutarsız: bakiye rezervasyonu talepten küçük.")
    balance.reserved_days = Decimal(balance.reserved_days) - gun
    balance.save(update_fields=["reserved_days", "updated_at"])
    _defter(
        balance=balance,
        kind=BalanceEntryKind.RESERVATION_RELEASE,
        delta=-gun,
        request=request,
        actor=actor,
        reason=reason,
    )


def _tuket(request: LeaveRequest, *, actor) -> None:
    """Nihai onay: rezervasyon çözülür, **kullanım** yazılır (tek kez)."""
    balance = _kilitli_bakiye(request)
    if balance is None:
        return
    gun = Decimal(request.days_requested)
    if Decimal(balance.reserved_days) < gun:
        raise LeaveBalanceError(
            "Nihai onay sırasında rezervasyon bulunamadı (çift onay/yarış durumu)."
        )
    balance.reserved_days = Decimal(balance.reserved_days) - gun
    balance.used_days = Decimal(balance.used_days) + gun
    balance.save(update_fields=["reserved_days", "used_days", "updated_at"])
    _defter(
        balance=balance,
        kind=BalanceEntryKind.RESERVATION_RELEASE,
        delta=-gun,
        request=request,
        actor=actor,
        reason="Onaylandı (rezervasyon çözüldü)",
    )
    _defter(
        balance=balance,
        kind=BalanceEntryKind.USAGE,
        delta=gun,
        request=request,
        actor=actor,
        reason="Onaylanan izin kullanımı",
    )


def _iade(request: LeaveRequest, *, actor, reason: str) -> None:
    """Onaylı talebin iptalinde iade (politika izin veriyorsa; açık kayıt)."""
    balance = _kilitli_bakiye(request)
    if balance is None:
        return
    gun = Decimal(request.days_requested)
    if Decimal(balance.used_days) < gun:
        raise LeaveBalanceError("İade tutarsız: kullanılan gün talepten küçük.")
    balance.used_days = Decimal(balance.used_days) - gun
    balance.save(update_fields=["used_days", "updated_at"])
    _defter(
        balance=balance,
        kind=BalanceEntryKind.REFUND,
        delta=-gun,
        request=request,
        actor=actor,
        reason=reason,
    )


# --- Onay ---------------------------------------------------------------------


def decide_approval_step(
    *,
    actor,
    request: LeaveRequest,
    decision: str,
    reason: str = "",
    override: bool = False,
) -> LeaveRequest:
    """
    Geçerli onay adımı için karar verir (`approved` / `rejected`).

    * Talep satırı `select_for_update()` ile kilitlenir → aynı talebin **iki kez**
      onaylanması mümkün değildir (ikinci çağrı durum hatası alır).
    * Adım sırası atlanamaz; karar için izin + kapsam + `ASSIGNED` +
      `SEPARATE_ACTOR` birlikte aranır.
    * `override=True` (yalnız `leave.request.approve.override`): kayıtlı gerekçeyle
      adımlar atlanır ve talep nihai olarak onaylanır.
    """
    require_active(actor)
    if decision not in (ApprovalStepStatus.APPROVED, ApprovalStepStatus.REJECTED):
        raise LeaveError("Karar 'approved' veya 'rejected' olmalıdır.")

    with transaction.atomic():
        kilitli = LeaveRequest.objects.select_for_update().get(pk=request.pk)
        if kilitli.status != LeaveRequestStatus.PENDING:
            raise LeaveStateError(
                f"Yalnızca onay bekleyen talep karara bağlanabilir (mevcut: {kilitli.status})."
            )

        adim = current_step(kilitli)
        gerekce = (reason or "").strip()

        if override:
            if not can_override_approval(actor):
                raise LeaveAuthorizationError(
                    "Adım atlama (override) için 'leave.request.approve.override' izni gerekir."
                )
            if len(gerekce) < 5:
                raise LeaveError("Override için kayıtlı gerekçe zorunludur.")
            if not object_in_org_scope(
                actor,
                department_id=kilitli.employee.department_id,
                owner_user_id=kilitli.employee.user_id,
            ):
                raise LeaveAuthorizationError("Talep, aktörün organizasyon kapsamı dışında.")
            if decision == ApprovalStepStatus.REJECTED:
                return _reddet(kilitli, actor=actor, reason=gerekce, adim=adim)
            return _override_onayla(kilitli, actor=actor, reason=gerekce)

        if adim is None:
            raise LeaveStateError("Talebin karar bekleyen onay adımı yok.")

        if not can_approve_step(actor, adim, kilitli):
            raise LeaveAuthorizationError(
                "Onay için 'leave.request.approve' izni, kapsam, adım sahipliği "
                "ve 'farklı aktör' koşulu gerekir."
            )
        if adim.approver_user_id is not None and adim.approver_user_id == kilitli.employee.user_id:
            raise LeaveAuthorizationError("Onaylayan kendi talebini onaylayamaz.")

        simdi = timezone.now()
        adim.status = decision
        adim.decided_at = simdi
        adim.decision_reason = gerekce
        adim.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

        if decision == ApprovalStepStatus.REJECTED:
            return _reddet(kilitli, actor=actor, reason=gerekce, adim=adim)

        if current_step(kilitli) is None:
            return _nihai_onay(kilitli, actor=actor)

        log_action(
            tenant_id=kilitli.tenant_id,
            action="leave.request.approve_step",
            user=actor,
            table_name="approval_steps",
            record_id=adim.pk,
            changes={"step_no": adim.step_no, "request_id": str(kilitli.pk)},
        )
        return kilitli


def _nihai_onay(request: LeaveRequest, *, actor, otomatik: bool = False) -> LeaveRequest:
    """Nihai onay: bakiye **tek kez** tüketilir, talep `approved` olur."""
    if request.leave_type.requires_document and request.document_ref is None:
        raise LeaveStateError(
            f"'{request.leave_type.code}' belge zorunlu türdür: belge referansı olmadan "
            "talep onaylanamaz (M14 §7, fail-closed)."
        )
    _tuket(request, actor=actor)
    request.status = LeaveRequestStatus.APPROVED
    request.decided_at = timezone.now()
    request.decided_by = actor
    request.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    log_action(
        tenant_id=request.tenant_id,
        action="leave.request.approve",
        user=actor,
        table_name="leave_requests",
        record_id=request.pk,
        changes={
            "employee_no": request.employee.employee_no,
            "leave_type": request.leave_type.code,
            "days": str(request.days_requested),
            "otomatik": otomatik,
        },
    )
    return request


def _override_onayla(request: LeaveRequest, *, actor, reason: str) -> LeaveRequest:
    """Yetkili override: kalan adımlar atlanır (`skipped`), talep onaylanır."""
    simdi = timezone.now()
    atlanan = list(
        ApprovalStep.objects.filter(
            subject_id=request.pk, status=ApprovalStepStatus.PENDING, deleted_at__isnull=True
        )
    )
    for adim in atlanan:
        adim.status = ApprovalStepStatus.SKIPPED
        adim.decided_at = simdi
        adim.decision_reason = f"Yetkili override: {reason}"
        adim.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])
    onayli = _nihai_onay(request, actor=actor)
    log_action(
        tenant_id=request.tenant_id,
        action="leave.request.override",
        user=actor,
        table_name="leave_requests",
        record_id=request.pk,
        changes={"atlanan_adim": len(atlanan), "has_reason": bool(reason)},
    )
    return onayli


def _reddet(request: LeaveRequest, *, actor, reason: str, adim) -> LeaveRequest:
    """Red: kalan adımlar kapatılır, rezervasyon çözülür (bakiye tüketilmez)."""
    simdi = timezone.now()
    kalan_adimlar = list(
        ApprovalStep.objects.filter(
            subject_id=request.pk, status=ApprovalStepStatus.PENDING, deleted_at__isnull=True
        )
    )
    for kalan in kalan_adimlar:
        if adim is not None and kalan.pk == adim.pk:
            continue
        kalan.status = ApprovalStepStatus.CANCELED
        kalan.decided_at = simdi
        kalan.decision_reason = "Talep reddedildi"
        kalan.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

    if request.leave_type.uses_balance:
        _rezervasyonu_coz(request, actor=actor, reason="Talep reddedildi")

    request.status = LeaveRequestStatus.REJECTED
    request.decided_at = simdi
    request.decided_by = actor
    request.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    log_action(
        tenant_id=request.tenant_id,
        action="leave.request.reject",
        user=actor,
        table_name="leave_requests",
        record_id=request.pk,
        changes={
            "employee_no": request.employee.employee_no,
            "has_reason": bool(reason),
        },
    )
    return request


# --- İptal / kullanıldı -------------------------------------------------------


def cancel_leave_request(*, actor, request: LeaveRequest, reason: str) -> LeaveRequest:
    """
    Talebi iptal eder (M14 §12).

    * `draft` → iptal (bakiye etkisi yok)
    * `pending` → iptal (rezervasyon çözülür; bakiye **tüketilmez**)
    * `approved` → iptal; iade **yalnızca politika izin veriyorsa** (aksi halde
      fail-closed hata) ve **açık `refund` kaydı + denetim** ile yapılır.
    """
    require_active(actor)
    gerekce = (reason or "").strip()
    if len(gerekce) < 5:
        raise LeaveError("İptal için gerekçe zorunludur.")
    if not can_cancel_leave_request(actor, request):
        raise LeaveAuthorizationError(
            "İptal için 'leave.request.cancel' izni ve kapsam (ya da kayıt sahipliği) gerekir."
        )

    with transaction.atomic():
        kilitli = LeaveRequest.objects.select_for_update().get(pk=request.pk)
        durum = kilitli.status
        if durum not in (
            LeaveRequestStatus.DRAFT,
            LeaveRequestStatus.PENDING,
            LeaveRequestStatus.APPROVED,
        ):
            raise LeaveStateError(f"'{durum}' durumundaki talep iptal edilemez.")

        if durum == LeaveRequestStatus.PENDING and kilitli.leave_type.uses_balance:
            _rezervasyonu_coz(kilitli, actor=actor, reason=f"Talep iptal edildi: {gerekce}")

        if durum == LeaveRequestStatus.APPROVED and kilitli.leave_type.uses_balance:
            policy = _aktif_politika(kilitli.leave_type)
            if policy is None or not policy.allow_refund_after_approval:
                raise LeavePolicyError(
                    "Politika onay sonrası iadeye izin vermiyor (fail-closed): "
                    "talep iptal edilemez."
                )
            _iade(kilitli, actor=actor, reason=f"Onaylı talep iptali (iade): {gerekce}")

        simdi = timezone.now()
        adimlar = list(
            ApprovalStep.objects.filter(
                subject_id=kilitli.pk,
                status=ApprovalStepStatus.PENDING,
                deleted_at__isnull=True,
            )
        )
        for adim in adimlar:
            adim.status = ApprovalStepStatus.CANCELED
            adim.decided_at = simdi
            adim.decision_reason = "Talep iptal edildi"
            adim.save(update_fields=["status", "decided_at", "decision_reason", "updated_at"])

        kilitli.status = LeaveRequestStatus.CANCELED
        kilitli.canceled_at = simdi
        kilitli.cancel_reason = gerekce
        kilitli.save(update_fields=["status", "canceled_at", "cancel_reason", "updated_at"])
        log_action(
            tenant_id=kilitli.tenant_id,
            action="leave.request.cancel",
            user=actor,
            table_name="leave_requests",
            record_id=kilitli.pk,
            changes={"onceki_durum": durum, "has_reason": bool(gerekce)},
        )
    return kilitli


def mark_leave_taken(*, actor, request: LeaveRequest) -> LeaveRequest:
    """
    Onaylanmış talebi "kullanıldı" olarak işaretler (bakiye etkisi **yoktur**).

    İdari işlemdir: `leave.request.approve` izni + kapsam gerekir; izin dönemi
    başlamadan işaretlenemez.
    """
    require_active(actor)
    if not user_has_permission(actor, "leave.request.approve"):
        raise LeaveAuthorizationError("'leave.request.approve' izni gerekir.")
    with transaction.atomic():
        kilitli = LeaveRequest.objects.select_for_update().get(pk=request.pk)
        if kilitli.status != LeaveRequestStatus.APPROVED:
            raise LeaveStateError("Yalnızca onaylanmış talep 'kullanıldı' işaretlenebilir.")
        if kilitli.start_date > timezone.localdate():
            raise LeaveStateError("İzin dönemi başlamadan 'kullanıldı' işaretlenemez.")
        if not object_in_org_scope(
            actor,
            department_id=kilitli.employee.department_id,
            owner_user_id=kilitli.employee.user_id,
        ):
            raise LeaveAuthorizationError("Talep, aktörün organizasyon kapsamı dışında.")
        kilitli.status = LeaveRequestStatus.TAKEN
        kilitli.save(update_fields=["status", "updated_at"])
        log_action(
            tenant_id=kilitli.tenant_id,
            action="leave.request.taken",
            user=actor,
            table_name="leave_requests",
            record_id=kilitli.pk,
            changes={"employee_no": kilitli.employee.employee_no},
        )
    return kilitli


__all__ = [
    "HR_APPROVER_ROLE_CODE",
    "calculate_requested_days",
    "cancel_leave_request",
    "create_leave_request",
    "decide_approval_step",
    "mark_leave_taken",
    "submit_leave_request",
]
