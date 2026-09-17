"""
İzin (leave) modülü modelleri — M14 (docs/DATABASE.md).

Kaynak kararlar: **OD21** (soft delete + kısmi benzersizlik), **OD24** (organizasyon
ağacı / onay yönlendirmesi), **OD5** (izin ∧ kapsam), **OD27** (tek tüzel kişilik),
**OD19** (auth_* yetki kaynağı değildir).

Veri modeli (docs/DATABASE.md §B.4: `hr.leave_*`, `hr.approval_steps`):

    hr.leave_types ──< hr.leave_policies ──< hr.leave_policy_rules
          │                    (FIXED | TENURE_BASED hakediş)
          └──< hr.leave_balances ──< hr.leave_balance_entries   (defter/olay kaydı)
                     │
    hr.employees ──< hr.leave_requests ──< hr.approval_steps (genel onay adımları)

Tasarım kararları (ölçümle raporlanır):

  * **Bakiye tek kaynak:** `leave_balances` güncel durumdur; `available_days`
    **DB tarafından üretilen** (generated) kolondur → türetilmiş değer ikinci bir
    source-of-truth olarak saklanmaz. `leave_balance_entries` ise **append-only**
    olay defteridir (hakediş, rezervasyon, kullanım, iade, düzeltme); bakiye ile
    aynı işlemde yazılır, sessiz değişim olmaz.
  * **`approval_steps` geneldir** (docs: `subject_kind` + `subject_id`): izin dışındaki
    onay akışları (vardiya düzeltmesi, erişim talebi) aynı tabloyu kullanır.
    `subject_id` **FK değildir** (polimorfik hedef) — sahte FK üretilmez.
  * **Belge entegrasyon noktası:** `leave_requests.document_ref` (UUID, FK **yok**)
    M16 `hr.documents` gelene kadar geçici referanstır; belge modülü hazır olmadan
    gerçek FK **eklenmez** (docs M16 planı).
  * Yetki **üretilmez**: bu tablolar izin/kapsam kararının verisidir; karar
    `apps/leave/services/authorization.py` içinde `izin ∧ kapsam` ile verilir.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.expressions import CombinedExpression

from apps.common.models import TenantScopedModel, TimeStampedModel, UUIDModel
from apps.leave.leave_type_catalog import AccrualMethod, LeaveCategory
from apps.organization.models import Employee, RecordStatus


class LeaveType(TenantScopedModel):
    """
    İzin türü (kiracı bazlı).

    `is_system=True` olanlar kiracı kurulumunda oluşturulan **sistem** türleridir:
    kodu değiştirilemez ve soft-delete edilemez (rezerve kod); pasifleştirilebilir.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_types",
        verbose_name="kiracı",
    )
    code = models.CharField("kod", max_length=60)
    name = models.CharField("ad", max_length=120)
    category = models.CharField(
        "kategori",
        max_length=40,
        choices=LeaveCategory.choices,
        default=LeaveCategory.OTHER,
    )
    is_paid = models.BooleanField("ücretli", default=True)
    uses_balance = models.BooleanField(
        "bakiye kullanır",
        default=False,
        help_text="True ise hakediş/bakiye (leave_balances) gerekir.",
    )
    is_time_based = models.BooleanField("saat tabanlı", default=False)
    workday_minutes = models.PositiveIntegerField(
        "1 gün dakika", null=True, blank=True,
        help_text="Saat tabanlı izinlerde bir tam günün karşılığı.",
    )
    requires_approval = models.BooleanField("onay gerektirir", default=True)
    requires_document = models.BooleanField("belge gerektirir", default=False)
    min_advance_notice_days = models.PositiveIntegerField("asgari ön bildirim (gün)", default=0)
    max_consecutive_days = models.DecimalField(
        "azami kesintisiz gün",
        max_digits=6,
        decimal_places=1,
        null=True,
        blank=True,
    )
    is_system = models.BooleanField(
        "sistem türü",
        default=False,
        help_text="Kod rezervedir: değiştirilemez, silinemez (yalnızca pasifleştirilir).",
    )
    status = models.CharField(
        "durum",
        max_length=20,
        choices=RecordStatus.choices,
        default=RecordStatus.ACTIVE,
    )

    class Meta:
        db_table = "leave_types"
        verbose_name = "izin türü"
        verbose_name_plural = "izin türleri"
        constraints = [
            # OD21: aktif (silinmemiş) tür kodları kiracı içinde benzersizdir.
            models.UniqueConstraint(
                fields=("tenant", "code"),
                condition=Q(deleted_at__isnull=True),
                name="leave_types_tenant_code_uniq",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[c.value for c in RecordStatus]),
                name="leave_types_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(max_consecutive_days__isnull=True) | Q(max_consecutive_days__gt=0),
                name="leave_types_max_consecutive_positive",
            ),
            models.CheckConstraint(
                condition=Q(is_time_based=False) | Q(workday_minutes__gt=0),
                name="leave_types_time_based_workday_positive",
            ),
        ]
        indexes = [
            models.Index(fields=("tenant", "status"), name="leave_types_tenant_status_idx"),
        ]
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} ({self.name})"


class LeavePolicy(TenantScopedModel):
    """
    Yıllık izin hakediş politikası (tür başına **tek** yaşayan politika).

    `accrual_method='fixed'` → `fixed_days`; `accrual_method='tenure_based'` → kurallar.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_policies",
        verbose_name="kiracı",
    )
    leave_type = models.ForeignKey(
        "leave.LeaveType",
        on_delete=models.PROTECT,
        related_name="policies",
        verbose_name="izin türü",
    )
    accrual_method = models.CharField(
        "hakediş yöntemi",
        max_length=20,
        choices=AccrualMethod.choices,
        default=AccrualMethod.FIXED,
    )
    fixed_days = models.DecimalField(
        "sabit gün (yıllık)",
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
    )
    allow_negative_balance = models.BooleanField(
        "negatif bakiye izni",
        default=False,
        help_text="True değilse negatif bakiye DB guard'ı ile reddedilir.",
    )
    allow_refund_after_approval = models.BooleanField(
        "onay sonrası iade",
        default=True,
        help_text="Onaylanmış talebin iptalinde bakiyenin geri yüklenmesine izin verir (M14 §12).",
    )
    status = models.CharField(
        "durum",
        max_length=20,
        choices=RecordStatus.choices,
        default=RecordStatus.ACTIVE,
    )

    class Meta:
        db_table = "leave_policies"
        verbose_name = "izin politikası"
        verbose_name_plural = "izin politikaları"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "leave_type"),
                condition=Q(deleted_at__isnull=True),
                name="leave_policies_tenant_type_uniq",
            ),
            models.CheckConstraint(
                condition=Q(accrual_method__in=[c.value for c in AccrualMethod]),
                name="leave_policies_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(accrual_method=AccrualMethod.FIXED, fixed_days__isnull=False)
                | Q(accrual_method=AccrualMethod.TENURE_BASED, fixed_days__isnull=True),
                name="leave_policies_fixed_days_consistency",
            ),
            models.CheckConstraint(
                condition=Q(fixed_days__isnull=True) | Q(fixed_days__gte=0),
                name="leave_policies_fixed_days_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[c.value for c in RecordStatus]),
                name="leave_policies_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.leave_type.code}:{self.accrual_method}"


class LeavePolicyRule(TenantScopedModel):
    """
    Kıdeme göre hakediş kuralı: `[min_service_months, max_service_months)`.

    Çakışan aralıklar **veritabanında** reddedilir
    (`trg_leave_policy_rules_overlap`, bkz. `0004_leave_guards`).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_policy_rules",
        verbose_name="kiracı",
    )
    policy = models.ForeignKey(
        "leave.LeavePolicy",
        on_delete=models.CASCADE,
        related_name="rules",
        verbose_name="politika",
    )
    min_service_months = models.PositiveIntegerField("asgari hizmet (ay)", default=0)
    max_service_months = models.PositiveIntegerField(
        "azami hizmet (ay)",
        null=True,
        blank=True,
        help_text="Boş ise üst sınır yoktur (açık uçlu kural).",
    )
    entitlement_days = models.DecimalField("hakediş (gün)", max_digits=5, decimal_places=1)

    class Meta:
        db_table = "leave_policy_rules"
        verbose_name = "izin politikası kuralı"
        verbose_name_plural = "izin politikası kuralları"
        constraints = [
            models.CheckConstraint(
                condition=Q(max_service_months__isnull=True)
                | Q(max_service_months__gt=F("min_service_months")),
                name="leave_policy_rules_range_valid",
            ),
            models.CheckConstraint(
                condition=Q(entitlement_days__gte=0),
                name="leave_policy_rules_days_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("policy", "min_service_months"), name="leave_policy_rules_policy_idx"
            ),
        ]
        ordering = ["min_service_months"]

    def __str__(self) -> str:
        return f"{self.policy_id}:{self.min_service_months}-{self.max_service_months}"


class LeaveBalance(TenantScopedModel):
    """
    Çalışan/yıl/tür bazında izin bakiyesi (güncel durum).

    `available_days` **veritabanının ürettiği** kolondur
    (`entitled + carried_forward + adjustment - used - reserved`) → türetilmiş
    değer ayrı bir source-of-truth olarak tutulmaz. Değişiklikler yalnızca servis
    katmanından ve `leave_balance_entries` ile **aynı işlemde** yapılır.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_balances",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leave_balances",
        verbose_name="çalışan",
    )
    leave_type = models.ForeignKey(
        "leave.LeaveType",
        on_delete=models.PROTECT,
        related_name="balances",
        verbose_name="izin türü",
    )
    period_year = models.PositiveSmallIntegerField("dönem (yıl)")
    entitled_days = models.DecimalField("hakediş", max_digits=8, decimal_places=4, default=0)
    carried_forward_days = models.DecimalField(
        "devreden", max_digits=8, decimal_places=4, default=0
    )
    adjustment_days = models.DecimalField(
        "düzeltme", max_digits=8, decimal_places=4, default=0
    )
    used_days = models.DecimalField("kullanılan", max_digits=8, decimal_places=4, default=0)
    reserved_days = models.DecimalField(
        "rezerve (onay bekleyen)", max_digits=8, decimal_places=4, default=0
    )
    available_days = models.GeneratedField(
        expression=CombinedExpression(
            CombinedExpression(
                CombinedExpression(
                    CombinedExpression(F("entitled_days"), "+", F("carried_forward_days")),
                    "+",
                    F("adjustment_days"),
                ),
                "-",
                F("used_days"),
            ),
            "-",
            F("reserved_days"),
        ),
        output_field=models.DecimalField(max_digits=8, decimal_places=4),
        db_persist=True,
    )

    class Meta:
        db_table = "leave_balances"
        verbose_name = "izin bakiyesi"
        verbose_name_plural = "izin bakiyeleri"
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "leave_type", "period_year"),
                condition=Q(deleted_at__isnull=True),
                name="leave_balances_employee_type_period_uniq",
            ),
            models.CheckConstraint(
                condition=Q(entitled_days__gte=0)
                & Q(carried_forward_days__gte=0)
                & Q(used_days__gte=0)
                & Q(reserved_days__gte=0),
                name="leave_balances_components_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "employee", "period_year"),
                name="leave_balances_tenant_emp_idx",
            ),
        ]
        ordering = ["-period_year"]

    def __str__(self) -> str:
        return f"{self.employee_id}/{self.leave_type_id}/{self.period_year}"


class BalanceEntryKind(models.TextChoices):
    """Defter olay türleri (M14 §3): her olay ilgili bakiye kolonunu değiştirir."""

    ACCRUAL = "accrual", "Hakediş"
    CARRYOVER = "carryover", "Devreden"
    ADJUSTMENT = "adjustment", "Manuel düzeltme"
    RESERVATION = "reservation", "Rezervasyon (onay bekliyor)"
    RESERVATION_RELEASE = "reservation_release", "Rezervasyon iptali"
    USAGE = "usage", "Kullanım (onaylandı)"
    REFUND = "refund", "İade (iptal)"
    TIME_OFF_CREDIT = "time_off_credit", "Fazla mesai serbest zaman kredisi"


#: Olay türü → değiştirdiği bakiye kolonu (defter kaydının anlamı).
BALANCE_ENTRY_COLUMNS: dict[str, str] = {
    BalanceEntryKind.ACCRUAL: "entitled_days",
    BalanceEntryKind.CARRYOVER: "carried_forward_days",
    BalanceEntryKind.ADJUSTMENT: "adjustment_days",
    BalanceEntryKind.RESERVATION: "reserved_days",
    BalanceEntryKind.RESERVATION_RELEASE: "reserved_days",
    BalanceEntryKind.USAGE: "used_days",
    BalanceEntryKind.REFUND: "used_days",
    BalanceEntryKind.TIME_OFF_CREDIT: "adjustment_days",
}


class LeaveBalanceEntry(UUIDModel):
    """
    Bakiye olay defteri — **append-only**.

    * Bakiye ile aynı işlemde yazılır; bakiye tek başına değişmez.
    * `entry_kind='accrual'` için dönem başına **tek** kayıt (idempotent hakediş).
    * Talep kaynaklı olaylarda (`reservation`/`usage`/`refund`/`reservation_release`)
      talep başına tür başına **tek** kayıt → çift tüketim DB'de engellenir.
    * `adjustment` için gerekçe **zorunludur** (DB `CHECK`).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_balance_entries",
        verbose_name="kiracı",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    balance = models.ForeignKey(
        LeaveBalance,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="bakiye",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leave_balance_entries",
        verbose_name="çalışan",
    )
    leave_type = models.ForeignKey(
        "leave.LeaveType",
        on_delete=models.PROTECT,
        related_name="balance_entries",
        verbose_name="izin türü",
    )
    period_year = models.PositiveSmallIntegerField("dönem (yıl)")
    entry_kind = models.CharField("olay türü", max_length=20, choices=BalanceEntryKind.choices)
    delta_days = models.DecimalField("değişim (gün)", max_digits=8, decimal_places=4)
    leave_request = models.ForeignKey(
        "leave.LeaveRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="balance_entries",
        verbose_name="izin talebi",
    )
    overtime_request = models.ForeignKey(
        "overtime.OvertimeRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_off_balance_entries",
        verbose_name="fazla mesai talebi",
    )
    reason = models.TextField("gerekçe", blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leave_balance_entries",
        verbose_name="işlemi yapan",
    )

    class Meta:
        db_table = "leave_balance_entries"
        verbose_name = "bakiye olay kaydı"
        verbose_name_plural = "bakiye olay kayıtları"
        constraints = [
            models.CheckConstraint(
                condition=Q(entry_kind__in=[c.value for c in BalanceEntryKind]),
                name="leave_balance_entries_kind_valid",
            ),
            models.CheckConstraint(
                condition=~Q(entry_kind=BalanceEntryKind.ADJUSTMENT) | ~Q(reason=""),
                name="leave_balance_entries_adjustment_reason",
            ),
            # İdempotans: dönem başına tek hakediş kaydı.
            models.UniqueConstraint(
                fields=("balance", "entry_kind", "period_year"),
                condition=Q(entry_kind=BalanceEntryKind.ACCRUAL),
                name="leave_balance_entries_accrual_uniq",
            ),
            # Çift tüketim engeli: talep başına olay türü başına tek kayıt.
            models.UniqueConstraint(
                fields=("leave_request", "entry_kind"),
                condition=Q(leave_request__isnull=False),
                name="leave_balance_entries_request_uniq",
            ),
            models.UniqueConstraint(
                fields=("overtime_request", "entry_kind"),
                condition=Q(overtime_request__isnull=False),
                name="leave_balance_entries_overtime_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "employee", "period_year"),
                name="leave_balance_entries_emp_idx",
            ),
        ]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.entry_kind}:{self.delta_days}"


class LeaveRequestStatus(models.TextChoices):
    """
    İzin talebi durumu (kapalı küme).

    docs'ta kapalı küme **tanımlı değildir**; adlar referans tasarımdan
    (`hr.leave_status`: pending/approved/rejected/cancelled/taken) alınmış ve
    kullanıcı isteğindeki `draft` durumu eklenmiştir. "submitted" ayrı bir durum
    **değildir**: onaya sunulmuş talep `pending`'dir. Yazım proje standardına
    uydurulmuştur (`canceled`; `accounts.TenantStatus` ile tutarlı).
    """

    DRAFT = "draft", "Taslak"
    PENDING = "pending", "Onay bekliyor"
    APPROVED = "approved", "Onaylandı"
    REJECTED = "rejected", "Reddedildi"
    CANCELED = "canceled", "İptal edildi"
    TAKEN = "taken", "Kullanıldı"


#: İzin talebi durum geçişleri (OD21: sessiz/joker geçiş yok) — servis ve DB guard'ı.
LEAVE_REQUEST_TRANSITIONS: dict[str, frozenset[str]] = {
    LeaveRequestStatus.DRAFT: frozenset({LeaveRequestStatus.PENDING, LeaveRequestStatus.CANCELED}),
    LeaveRequestStatus.PENDING: frozenset(
        {
            LeaveRequestStatus.APPROVED,
            LeaveRequestStatus.REJECTED,
            LeaveRequestStatus.CANCELED,
        }
    ),
    LeaveRequestStatus.APPROVED: frozenset(
        {LeaveRequestStatus.CANCELED, LeaveRequestStatus.TAKEN}
    ),
    LeaveRequestStatus.REJECTED: frozenset(),
    LeaveRequestStatus.CANCELED: frozenset(),
    LeaveRequestStatus.TAKEN: frozenset(),
}


class LeaveRequest(TenantScopedModel):
    """
    İzin talebi.

    * `days_requested` **sunucu tarafında** hesaplanır (istemci değeri yalnızca
      doğrulanır — `services/requests.py::calculate_requested_days`).
    * Hafta sonu/resmi tatil hesabı **yapılmaz**: çalışma takvimi modülü (M15)
      gelene kadar gün sayısı **takvim günüdür**; sahte kesinlik üretilmez.
    * `document_ref`: M16 `hr.documents` entegrasyon noktası (FK **yok**).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_requests",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leave_requests",
        verbose_name="çalışan",
    )
    leave_type = models.ForeignKey(
        "leave.LeaveType",
        on_delete=models.PROTECT,
        related_name="requests",
        verbose_name="izin türü",
    )
    start_date = models.DateField("başlangıç")
    end_date = models.DateField("bitiş")
    start_time = models.TimeField("başlangıç saati", null=True, blank=True)
    end_time = models.TimeField("bitiş saati", null=True, blank=True)
    is_half_day = models.BooleanField("yarım gün", default=False)
    days_requested = models.DecimalField("istenen gün", max_digits=8, decimal_places=4)
    reason = models.TextField("gerekçe", blank=True)
    status = models.CharField(
        "durum",
        max_length=20,
        choices=LeaveRequestStatus.choices,
        default=LeaveRequestStatus.DRAFT,
    )
    submitted_at = models.DateTimeField("onaya sunulma", null=True, blank=True)
    decided_at = models.DateTimeField("karar zamanı", null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_leave_requests",
        verbose_name="karar veren",
    )
    canceled_at = models.DateTimeField("iptal zamanı", null=True, blank=True)
    cancel_reason = models.TextField("iptal gerekçesi", blank=True)
    document_ref = models.UUIDField(
        "belge referansı",
        null=True,
        blank=True,
        help_text=(
            "M16 `hr.documents` entegrasyon noktası — belge modülü gelince gerçek FK bağlanır."
        ),
    )

    class Meta:
        db_table = "leave_requests"
        verbose_name = "izin talebi"
        verbose_name_plural = "izin talepleri"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="leave_requests_date_order",
            ),
            models.CheckConstraint(
                condition=Q(days_requested__gt=0),
                name="leave_requests_days_positive",
            ),
            models.CheckConstraint(
                condition=~Q(is_half_day=True) | Q(start_date=F("end_date")),
                name="leave_requests_half_day_single_date",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[c.value for c in LeaveRequestStatus]),
                name="leave_requests_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "status", "start_date"),
                name="leave_requests_status_idx",
            ),
            models.Index(
                fields=("tenant", "employee", "start_date"),
                name="leave_requests_tenant_emp_idx",
            ),
        ]
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return f"{self.employee_id}:{self.start_date}..{self.end_date}/{self.status}"


class LeaveDecisionSnapshot(UUIDModel, TimeStampedModel):
    """
    **Karar anındaki izin bakiyesi** — immutable (append-only) snapshot.

    Neden (bildirim): karara bağlanmış bir izin talebinin detayında "bugünkü"
    bakiye gösteriliyordu; bu, karar anındaki durumu **yanlış** temsil ediyordu.
    Karar anında alınan değerler buraya yazılır ve **asla** güncellenmez
    (DB tetikleyicisi `UPDATE`/`DELETE`'i reddeder).

    * Bir talep için **en fazla 1** snapshot vardır (`leave_request_id` benzersiz).
    * Alan adları ve anlamı `leave_balances` ile **aynıdır**; yeni bir bakiye
      kavramı üretilmez (`available_days` = hak − kullanılan − rezerve).
    * Talep anında bakiye satırı yoksa (ör. bakiye kullanmayan tür) değerler
      **NULL** kalır; `uses_balance` karar anındaki tür davranışını dondurur.
    * Eski kararlar için **backfill yapılmaz**: bugünkü bakiye geçmiş gibi
      yazılamaz → snapshot yoksa arayüz "Karar anı bakiyesi kayıtlı değil" der.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="leave_decision_snapshots",
        verbose_name="kiracı",
    )
    leave_request = models.OneToOneField(
        LeaveRequest,
        on_delete=models.CASCADE,
        related_name="decision_snapshot",
        verbose_name="izin talebi",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leave_decision_snapshots",
        verbose_name="çalışan",
    )
    leave_type = models.ForeignKey(
        "leave.LeaveType",
        on_delete=models.PROTECT,
        related_name="decision_snapshots",
        verbose_name="izin türü",
    )
    period_year = models.PositiveSmallIntegerField("dönem (yıl)")
    decision_status = models.CharField(
        "karar", max_length=20, choices=LeaveRequestStatus.choices
    )
    #: Karar anında tür bakiye kullanıyor muydu (tür sonradan değişse bile donar).
    uses_balance = models.BooleanField("bakiye kullanır", default=True)
    entitled_days = models.DecimalField(
        "hakediş", max_digits=6, decimal_places=1, null=True, blank=True
    )
    carried_forward_days = models.DecimalField(
        "devreden", max_digits=6, decimal_places=1, null=True, blank=True
    )
    adjustment_days = models.DecimalField(
        "düzeltme", max_digits=6, decimal_places=1, null=True, blank=True
    )
    used_days = models.DecimalField(
        "kullanılan", max_digits=6, decimal_places=1, null=True, blank=True
    )
    reserved_days = models.DecimalField(
        "rezerve (onay bekleyen)", max_digits=6, decimal_places=1, null=True, blank=True
    )
    available_days = models.DecimalField(
        "kalan", max_digits=6, decimal_places=1, null=True, blank=True
    )
    snapshot_at = models.DateTimeField("snapshot zamanı")

    class Meta:
        db_table = "leave_decision_snapshots"
        verbose_name = "karar anı izin bakiyesi"
        verbose_name_plural = "karar anı izin bakiyeleri"
        constraints = [
            # Yalnız **nihai karar** durumları snapshot'lanır (approved/rejected);
            # pending/draft/canceled için karar anı bakiyesi yoktur.
            models.CheckConstraint(
                condition=Q(
                    decision_status__in=[
                        LeaveRequestStatus.APPROVED,
                        LeaveRequestStatus.REJECTED,
                    ]
                ),
                name="leave_decision_snapshots_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "employee", "snapshot_at"),
                name="leave_dec_snap_emp_idx",
            ),
        ]
        ordering = ["-snapshot_at"]

    def __str__(self) -> str:
        return f"{self.leave_request_id}:{self.decision_status}/{self.available_days}"


class ApprovalSubjectKind(models.TextChoices):
    """
    Onay adımı konusu (genel `approval_steps` tablosu).

    P5a: fazla mesai talepleri aynı tabloyu kullanır (`overtime_request`) — ikinci bir
    onay tablosu açılmaz (OD39: onay sırası izin modülüyle aynı altyapıdadır).
    """

    LEAVE_REQUEST = "leave_request", "İzin talebi"
    OVERTIME_REQUEST = "overtime_request", "Fazla mesai talebi"


class ApprovalStepStatus(models.TextChoices):
    """Onay adımı durumu."""

    PENDING = "pending", "Karar bekliyor"
    APPROVED = "approved", "Onaylandı"
    REJECTED = "rejected", "Reddedildi"
    SKIPPED = "skipped", "Atlandı"
    CANCELED = "canceled", "İptal edildi"


class ApprovalStep(TenantScopedModel):
    """
    Onay adımı (genel; `subject_kind` + `subject_id`).

    * Adım sırası **atlanamaz**: `step_no` küçük olan adım karara bağlanmadan
      sonraki adım karar veremez (DB guard'ı + servis kontrolü).
    * Adım ya bir **kişiye** (`approver_user`) ya da bir **rol/kapsama**
      (`approver_role_code`) bağlanır (docs/ORGANIZATION.md §5).
    * `subject_id` **FK değildir** (polimorfik hedef); hedefin kiracıya ait olduğu
      servis katmanında doğrulanır.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="approval_steps",
        verbose_name="kiracı",
    )
    subject_kind = models.CharField(
        "konu türü",
        max_length=30,
        choices=ApprovalSubjectKind.choices,
        default=ApprovalSubjectKind.LEAVE_REQUEST,
    )
    subject_id = models.UUIDField("konu kimliği")
    step_no = models.PositiveSmallIntegerField("adım")
    approver_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_steps",
        verbose_name="onaycı (kişi)",
    )
    approver_role_code = models.CharField("onaycı rol kodu", max_length=60, blank=True)
    status = models.CharField(
        "durum",
        max_length=20,
        choices=ApprovalStepStatus.choices,
        default=ApprovalStepStatus.PENDING,
    )
    decided_at = models.DateTimeField("karar zamanı", null=True, blank=True)
    decision_reason = models.TextField("karar gerekçesi", blank=True)
    delegated_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="devreden",
    )

    class Meta:
        db_table = "approval_steps"
        verbose_name = "onay adımı"
        verbose_name_plural = "onay adımları"
        constraints = [
            models.UniqueConstraint(
                fields=("subject_kind", "subject_id", "step_no"),
                condition=Q(deleted_at__isnull=True),
                name="approval_steps_subject_step_uniq",
            ),
            models.CheckConstraint(
                condition=Q(approver_user__isnull=False) | ~Q(approver_role_code=""),
                name="approval_steps_approver_present",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[c.value for c in ApprovalStepStatus]),
                name="approval_steps_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "approver_user", "status"),
                name="approval_steps_approver_idx",
            ),
            models.Index(fields=("subject_kind", "subject_id"), name="approval_steps_subject_idx"),
        ]
        ordering = ["subject_kind", "subject_id", "step_no"]

    def __str__(self) -> str:
        return f"{self.subject_kind}:{self.step_no}:{self.status}"
