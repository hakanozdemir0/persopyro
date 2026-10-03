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

from datetime import date, datetime, time, timedelta
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
    LeaveDecisionSnapshot,
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
    direct_reports,
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
    start_time: time | None = None,
    end_time: time | None = None,
    workday_minutes: int | None = None,
) -> Decimal:
    """
    İstenen gün sayısını **sunucu tarafında** hesaplar (takvim günü, kapsayıcı).

    Hafta sonu/resmi tatil **hesaba katılmaz** (çalışma takvimi modülü M15 gelene
    kadar); bu yüzden sonuç "takvim günü"dür ve arayüzde öyle gösterilmelidir.
    """
    if end_date < start_date:
        raise LeaveError("Bitiş tarihi başlangıç tarihinden önce olamaz.")
    if workday_minutes is not None:
        if start_date != end_date:
            raise LeaveError("Saat tabanlı serbest zaman izni tek gün seçilerek alınmalıdır.")
        if workday_minutes <= 0:
            raise LeaveError("1 gün dakika değeri pozitif olmalıdır.")
        # Kullanıcı yalnızca tek gün seçtiğinde, o gün doğrudan tenant'ın
        # serbest zaman çalışma günü olarak sayılır; saat alanı zorunlu değildir.
        if start_time is None and end_time is None:
            return Decimal("1")
        if start_time is None or end_time is None:
            raise LeaveError("Saat tabanlı izin için başlangıç ve bitiş birlikte girilmelidir.")
        dakika = int((datetime.combine(start_date, end_time) - datetime.combine(start_date, start_time)).total_seconds() // 60)
        if dakika <= 0:
            raise LeaveError("Bitiş saati başlangıç saatinden sonra olmalıdır.")
        if dakika % 30:
            raise LeaveError(
                "Serbest zaman kullanımı yalnızca 30 dakikalık aralıklarla girilebilir "
                "(ör. 1, 1.5 veya 2 saat)."
            )
        return (Decimal(dakika) / Decimal(workday_minutes)).quantize(Decimal("0.0001"))
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
    # OD42 (P1-B): yalnız `active` çalışan talep açar; suspended/terminated/archived kapalı.
    if employee.status != EmployeeStatus.ACTIVE:
        raise LeaveStateError(
            f"'{employee.status}' durumundaki çalışan yeni izin talebi oluşturamaz."
        )


#: Yeni talebi **bloklayan** mevcut talep durumları (aynı çalışan/aynı dönem).
#: * `pending`: onay sürecinde (onaya sunulmuş talep `pending`'dir).
#: * `approved`: onaylanmış.
#: * `taken`: onaylanmış iznin kullanılmış hâli — dönem fiilen tüketilmiştir.
#: `draft` (henüz onaya sunulmadı), `rejected` ve `canceled` **bloklamaz**.
AKTIF_TALEP_DURUMLARI = (
    LeaveRequestStatus.PENDING,
    LeaveRequestStatus.APPROVED,
    LeaveRequestStatus.TAKEN,
)

#: Kullanıcıya gösterilen çakışma mesajı (ham SQL/exception ayrıntısı sızdırılmaz).
CAKISMA_MESAJI = (
    "Bu tarih aralığında bekleyen veya onaylanmış başka bir izin talebiniz bulunuyor."
)


def _cakisan_aktif_talep(
    *,
    employee: Employee,
    start_date: date,
    end_date: date,
    exclude_pk=None,  # noqa: ANN001
) -> LeaveRequest | None:
    """
    Aynı **kiracı** ve aynı **çalışan** için tarih aralığı çakışan aktif talebi bulur.

    Çakışma (kapsayıcı, sınır günler dahil)::

        existing.start_date <= new.end_date AND existing.end_date >= new.start_date

    Kapsam tesadüfen değil **açıkça** daraltılır: `tenant_id` + `employee_id`. Farklı
    kiracının/çalışanın kaydı bu sorguya girmez (RLS ek güvencedir).
    """
    sorgu = LeaveRequest.objects.filter(
        tenant_id=employee.tenant_id,
        employee_id=employee.pk,
        status__in=AKTIF_TALEP_DURUMLARI,
        deleted_at__isnull=True,
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    if exclude_pk is not None:
        # Kendi kaydı (taslak onaya sunulurken) çakışma sayılmaz.
        sorgu = sorgu.exclude(pk=exclude_pk)
    return sorgu.order_by("start_date").first()


def _cakisma_guard(
    *,
    employee: Employee,
    start_date: date,
    end_date: date,
    exclude_pk=None,  # noqa: ANN001
) -> None:
    """Çakışan aktif talep varsa **fail-closed** reddeder (izin türünden bağımsız)."""
    if _cakisan_aktif_talep(
        employee=employee,
        start_date=start_date,
        end_date=end_date,
        exclude_pk=exclude_pk,
    ):
        raise LeaveStateError(CAKISMA_MESAJI)


def _fazla_mesai_cakismasi_var(*, employee: Employee, start_date: date, end_date: date) -> bool:
    """Aynı dönemde gönderilmiş/onaylanmış fazla mesai var mı?"""
    from apps.overtime.models import OvertimeRequest, OvertimeStatus

    return OvertimeRequest.objects.filter(
        tenant_id=employee.tenant_id, employee_id=employee.pk,
        deleted_at__isnull=True,
        status__in=(OvertimeStatus.PENDING, OvertimeStatus.APPROVED),
        work_date__gte=start_date, work_date__lte=end_date,
    ).exists()


def _vardiya_cakismasi_var(
    *, employee: Employee, start_date: date, end_date: date,
    start_time=None, end_time=None,
) -> bool:
    """İzin aralığıyla kesişen aktif vardiya ataması var mı?"""
    from apps.shifts.models import ShiftAssignment  # noqa: PLC0415

    izin_baslangici = datetime.combine(start_date, start_time or time.min)
    izin_bitisi = datetime.combine(end_date, end_time or time.max)
    atamalar = ShiftAssignment.objects.filter(
        tenant_id=employee.tenant_id,
        employee_id=employee.pk,
        deleted_at__isnull=True,
        work_date__gte=start_date - timedelta(days=1),
        work_date__lte=end_date,
    ).select_related("shift")
    for atama in atamalar:
        vardiya_baslangici = datetime.combine(atama.work_date, atama.shift.start_time)
        vardiya_bitis_tarihi = (
            atama.work_date + timedelta(days=1) if atama.shift.crosses_midnight else atama.work_date
        )
        vardiya_bitisi = datetime.combine(vardiya_bitis_tarihi, atama.shift.end_time)
        if vardiya_baslangici < izin_bitisi and izin_baslangici < vardiya_bitisi:
            return True
    return False


def _calisani_kilitle(employee_id) -> None:  # noqa: ANN001
    """
    Çalışan satırını kilitler (uygulama düzeyi yarış çözümü).

    Aynı çalışan için eşzamanlı iki "kontrol → create" çağrısının ikisinin de
    çakışmasız görüp duplicate üretmesini engeller: ikinci işlem, birincinin
    commit'ini bekler ve ardından çakışmayı görür. Şema/migration gerekmez.
    """
    Employee.objects.select_for_update().filter(pk=employee_id).first()


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
    if employee.pk not in direct_reports(actor):
        raise LeaveAuthorizationError(
            "'leave.request.create' izniyle yalnızca kendiniz veya size bağlı çalışanlar "
            "adına izin talebi oluşturabilirsiniz."
        )

    gun = calculate_requested_days(
        start_date=start_date, end_date=end_date, is_half_day=is_half_day,
        start_time=start_time, end_time=end_time,
        workday_minutes=leave_type.workday_minutes if leave_type.is_time_based else None,
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
        # Yarış güvencesi: aynı çalışan için eşzamanlı talep oluşturma serileştirilir.
        _calisani_kilitle(employee.pk)
        _cakisma_guard(employee=employee, start_date=start_date, end_date=end_date)
        if _vardiya_cakismasi_var(
            employee=employee, start_date=start_date, end_date=end_date,
            start_time=start_time, end_time=end_time,
        ):
            raise LeaveStateError(
                "İzin tarih/saat aralığında planlanmış vardiya bulunduğundan izin oluşturulamaz."
            )
        if _fazla_mesai_cakismasi_var(employee=employee, start_date=start_date, end_date=end_date):
            raise LeaveStateError(
                "Bu tarih aralığında bekleyen veya onaylanmış fazla mesai bulunduğundan izin oluşturulamaz."
            )
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
    # Onaya sunma (taslak → pending) de çakışma kontrolünden geçer: taslak
    # oluşturulduktan sonra başka bir talep onaylanmış olabilir. Kendi kaydı hariç.
    _calisani_kilitle(request.employee_id)
    _cakisma_guard(
        employee=request.employee,
        start_date=request.start_date,
        end_date=request.end_date,
        exclude_pk=request.pk,
    )
    if _vardiya_cakismasi_var(
        employee=request.employee, start_date=request.start_date, end_date=request.end_date,
        start_time=request.start_time, end_time=request.end_time,
    ):
        raise LeaveStateError(
            "İzin tarih/saat aralığında planlanmış vardiya bulunduğundan izin onaya gönderilemez."
        )
    if _fazla_mesai_cakismasi_var(
        employee=request.employee, start_date=request.start_date, end_date=request.end_date
    ):
        raise LeaveStateError(
            "Bu tarih aralığında bekleyen veya onaylanmış fazla mesai bulunduğundan izin onaya gönderilemez."
        )
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


def _karar_snapshot_yaz(request: LeaveRequest, *, karar: str) -> LeaveDecisionSnapshot:
    """
    Karar anındaki izin bakiyesini **immutable** snapshot olarak yazar (M14/K).

    * Bir talep için **en fazla 1** snapshot: mevcut kayıt varsa tekrar yazılmaz
      (idempotent; `leave_request_id` benzersizliği DB'de de korur) ve mevcut
      kayıt **güncellenmez** (DB tetikleyicisi UPDATE/DELETE'i reddeder).
    * Çağrı sırası önemlidir: onayda `_tuket` **sonrasında**, redde rezervasyon
      çözüldükten sonra çağrılır → snapshot kararın **etkisi uygulandıktan
      sonraki** bakiyeyi gösterir.
    * Alan adları `leave_balances` ile aynıdır; bakiye satırı yoksa (bakiye
      kullanmayan tür) değerler NULL kalır — bugünkü bakiye geçmiş gibi yazılmaz.
    """
    mevcut = LeaveDecisionSnapshot.objects.filter(leave_request_id=request.pk).first()
    if mevcut is not None:
        return mevcut

    donem = period_year_for(request.start_date)
    bakiye = None
    if request.leave_type.uses_balance:
        bakiye = LeaveBalance.objects.filter(
            tenant_id=request.tenant_id,
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_id,
            period_year=donem,
            deleted_at__isnull=True,
        ).first()

    def deger(alan: str):  # noqa: ANN202
        return getattr(bakiye, alan) if bakiye is not None else None

    return LeaveDecisionSnapshot.objects.create(
        tenant_id=request.tenant_id,
        leave_request=request,
        employee_id=request.employee_id,
        leave_type_id=request.leave_type_id,
        period_year=donem,
        decision_status=karar,
        uses_balance=request.leave_type.uses_balance,
        entitled_days=deger("entitled_days"),
        carried_forward_days=deger("carried_forward_days"),
        adjustment_days=deger("adjustment_days"),
        used_days=deger("used_days"),
        reserved_days=deger("reserved_days"),
        available_days=deger("available_days"),
        snapshot_at=timezone.now(),
    )


def _nihai_onay(request: LeaveRequest, *, actor, otomatik: bool = False) -> LeaveRequest:
    """Nihai onay: bakiye **tek kez** tüketilir, talep `approved` olur."""
    # Fazla mesai talebi aynı gün onaylanmış veya onay bekliyor olabilir.
    # İzin onayını da burada kapatmak, iki farklı onay akışının yarışında
    # sonradan geçersiz bir kombinasyon oluşmasını engeller.
    from apps.overtime.models import OvertimeRequest, OvertimeStatus

    if OvertimeRequest.objects.filter(
        tenant_id=request.tenant_id,
        employee_id=request.employee_id,
        deleted_at__isnull=True,
        status__in=(OvertimeStatus.PENDING, OvertimeStatus.APPROVED),
        work_date__gte=request.start_date,
        work_date__lte=request.end_date,
    ).exists():
        raise LeaveStateError(
            "Aynı gün için bekleyen veya onaylanmış fazla mesai bulunduğundan izin onaylanamaz."
        )
    if _vardiya_cakismasi_var(
        employee=request.employee, start_date=request.start_date, end_date=request.end_date,
        start_time=request.start_time, end_time=request.end_time,
    ):
        raise LeaveStateError(
            "İzin tarih/saat aralığında planlanmış vardiya bulunduğundan izin onaylanamaz."
        )
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
    # Karar anı bakiyesi: tüketim **uygulandıktan sonra** dondurulur.
    _karar_snapshot_yaz(request, karar=LeaveRequestStatus.APPROVED)
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
    # Redde bakiye tüketilmez; rezervasyon çözüldükten sonraki durum dondurulur.
    _karar_snapshot_yaz(request, karar=LeaveRequestStatus.REJECTED)
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
            "Kendi talebiniz için 'leave.request.create'; başkasının talebi için "
            "'leave.request.cancel' izni ve organizasyon kapsamı gerekir."
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


def revise_leave_request(
    *, actor, request: LeaveRequest, leave_type: LeaveType, start_date: date,
    end_date: date, reason: str = "", is_half_day: bool = False,
    start_time=None, end_time=None, requested_days=None,
) -> LeaveRequest:
    """Mevcut talebi iptal edip yeni içeriği tek işlemde yeniden onaya sunar."""
    require_active(actor)
    if not can_cancel_leave_request(actor, request):
        raise LeaveAuthorizationError("İzin talebini düzenlemek için talep sahibi yetkisi gerekir.")
    with transaction.atomic():
        kilitli = LeaveRequest.objects.select_for_update().get(pk=request.pk)
        if kilitli.status not in (
            LeaveRequestStatus.DRAFT, LeaveRequestStatus.PENDING, LeaveRequestStatus.APPROVED
        ):
            raise LeaveStateError(f"'{kilitli.status}' durumundaki talep düzenlenemez.")
        cancel_leave_request(actor=actor, request=kilitli, reason="Talep düzenleme için yenilendi")
        return create_leave_request(
            actor=actor, employee=kilitli.employee, leave_type=leave_type,
            start_date=start_date, end_date=end_date, reason=reason,
            is_half_day=is_half_day, start_time=start_time, end_time=end_time,
            requested_days=requested_days, submit=True,
        )


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
