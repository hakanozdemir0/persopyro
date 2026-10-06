"""
Fazla mesai (overtime) modülü modelleri — **P5a V1: talep + onay temeli**.

Kaynak kararlar: **OD39** (izinle aynı adım altyapısı; yönetici varsayılan, İK isteğe bağlı), **OD40** (`attendance`
hesaplar, talep onaylar; onay tek yerde), **OD21** (soft delete + kısmi benzersizlik),
**OD5** (izin ∧ kapsam), **OD41+** (ücret/katsayı ileriki faz).

Kapsam sınırı (bu sürüm):

  * **VAR:** talep kaydı (`hr.overtime_requests`), sunucu tarafı süre hesabı, kapalı
    durum kümesi, onay/ret/iptal ve denetim kaydı.
  * **YOK (bilinçli):** katsayı motoru (`hr.overtime_rules`), katsayı dilimleri
    (`hr.overtime_entries`), ücret hesabı (P5b), izin karşılığı yazımı (TIME_OFF),
    devam kaydı bağı (`attendance_record_id`; M15 tablosu henüz yok), yuvarlama kuralı
    (**OD44** açık). Bu alanlar `docs/HR_ROADMAP.md` §11'de tasarımdır; **uydurulmaz**
    ve bu sürümde üretilmez.

Tasarım kararları (ölçümle raporlanır):

  * **Süre tek kaynak:** `total_minutes` **sunucuda** `end_time - start_time` ile
    hesaplanır; istemciden gelen süre yalnızca doğrulanır (asla yazılmaz). Sıfır/negatif
    süre hem servis hem **DB `CHECK`** ile reddedilir.
  * **Saat dilimi:** kayıt `work_date` + yerel saat (`start_time`, `end_time`) tutar;
    hesaplama **iki saat arasındaki farktır** ve dönüşüm gerektirmez. Kiracı/işyeri
    bazlı saat dilimi alanı **henüz yoktur** (platform `TIME_ZONE` = Europe/Istanbul); eklendiğinde
    kayıt biçimi değişmez → şema uyumludur.
  * **Karşılık türü (`compensation_type`)**: kapalı küme (`pay` | `time_off`) alanıdır ve
    bu sürümde **kural motoru olmadığı için dolmaz** (P5a/P5b). Değer kümesi dışındaki
    "ayarlanmamış" durumu **boş dizedir** (DJ001: string alanlarda `null=True` kullanılmaz;
    bkz. `organization/0019_p1a_reason_not_null`). Arayüz desteklenmeyen sahte seçenek
    göstermez; alan yol haritası uyumu için durur.
  * **Onay sahibi veridir:** onay adımı (`hr.approval_steps`, `subject_kind='overtime_request'`)
    talebin oluşturulması sırasında **birincil yöneticiden** (yoksa yetkili rolden)
    kurulur; arayüz tahmin etmez (OD39/§7).
  * Yetki **üretilmez**: karar `apps/overtime/services/authorization.py` içinde
    `izin ∧ kapsam` ile verilir.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.common.models import TenantScopedModel
from apps.organization.models import Employee

#: Tek kayıtta azami fazla mesai süresi (16 saat) — makul üst sınır.
#: Aşılırsa hem servis hem DB `CHECK` reddeder (yanlış birim girişine karşı koruma).
OVERTIME_MAX_MINUTES = 16 * 60

#: Açıklama için asgari uzunluk (denetim izi: "neden" sorusu cevaplanabilmeli).
OVERTIME_MIN_DESCRIPTION_LENGTH = 5


class OvertimeStatus(models.TextChoices):
    """
    Fazla mesai talebi durumu — **kapalı küme**.

    Yazım proje standardıdır (`canceled`; `LeaveRequestStatus` ve
    `accounts.TenantStatus` ile tutarlı). `draft` bilinçli olarak **yoktur**: bu
    sürümde talep oluşturulduğu anda onaya sunulur (iki adımlı taslak akışı izin
    modülünde vardır; fazla mesai bildiriminde gereksiz karmaşıklık olurdu).
    """

    PENDING = "pending", "Onay bekliyor"
    APPROVED = "approved", "Onaylandı"
    REJECTED = "rejected", "Reddedildi"
    CANCELED = "canceled", "İptal edildi"


#: Durum geçişleri (OD21: sessiz/joker geçiş yok) — servis ve DB guard'ı.
OVERTIME_TRANSITIONS: dict[str, frozenset[str]] = {
    OvertimeStatus.PENDING: frozenset(
        {
            OvertimeStatus.APPROVED,
            OvertimeStatus.REJECTED,
            OvertimeStatus.CANCELED,
        }
    ),
    # Karara bağlanmış kayıt geri alınmaz: düzeltme yeni kayıt/ayrı iş kalemidir.
    OvertimeStatus.APPROVED: frozenset(),
    OvertimeStatus.REJECTED: frozenset(),
    OvertimeStatus.CANCELED: frozenset(),
}


class CompensationType(models.TextChoices):
    """
    Karşılık türü (P5b) — **kapalı küme, bu sürümde kural yoktur**.

    Yol haritası (§11) `pay` | `time_off` | `employee_choice` tanımlar. Bu sürümde
    yalnız ilk ikisi şemada tutulur; `employee_choice` kiracı politikası gerektirdiği
    için (**OD32/OD44** ile birlikte) kural motoruyla gelir.
    """

    PAY = "pay", "Ücret"
    TIME_OFF = "time_off", "İzin"


class OvertimeRule(TenantScopedModel):
    """Kiracı tarafından tanımlanan fazla mesai periyot/katsayı kuralı (P5b)."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="overtime_rules",
        verbose_name="kiracı",
    )
    code = models.CharField("kod", max_length=40)
    name = models.CharField("ad", max_length=120)
    min_minutes = models.PositiveIntegerField("asgari dakika", default=0)
    max_minutes = models.PositiveIntegerField(
        "azami dakika", null=True, blank=True,
        help_text="Boş ise üst sınır yoktur; aralık [min, max) kabul edilir.",
    )
    multiplier = models.DecimalField("katsayı", max_digits=6, decimal_places=3)
    compensation_type = models.CharField(
        "karşılık türü", max_length=20, choices=CompensationType.choices
    )
    effective_from = models.DateField("başlangıç tarihi")
    effective_until = models.DateField("bitiş tarihi", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_overtime_rules",
        verbose_name="oluşturan",
    )

    class Meta:
        db_table = "overtime_rules"
        verbose_name = "fazla mesai kuralı"
        verbose_name_plural = "fazla mesai kuralları"
        constraints = [
            models.UniqueConstraint(
                fields=("tenant", "code", "effective_from"),
                condition=Q(deleted_at__isnull=True),
                name="overtime_rules_code_start_uniq",
            ),
            models.CheckConstraint(
                condition=Q(max_minutes__isnull=True) | Q(max_minutes__gt=F("min_minutes")),
                name="overtime_rules_range_valid",
            ),
            models.CheckConstraint(
                condition=Q(multiplier__gt=0),
                name="overtime_rules_multiplier_positive",
            ),
            models.CheckConstraint(
                condition=Q(compensation_type__in=[c.value for c in CompensationType]),
                name="overtime_rules_compensation_valid",
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True)
                | Q(effective_until__gte=F("effective_from")),
                name="overtime_rules_effective_range_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "effective_from"), name="overtime_rules_effective_idx"
            ),
        ]
        ordering = ["effective_from", "min_minutes"]

    def __str__(self) -> str:
        return f"{self.code}:{self.min_minutes}-{self.max_minutes}/{self.compensation_type}"


class OvertimeRequest(TenantScopedModel):
    """
    Fazla mesai bildirimi/talebi (çalışan beyanı → yönetici kararı).

    * `total_minutes` **sunucu tarafında** hesaplanır (`services/requests.py`).
    * Onay tek yerde tutulur: `status` (OD40: devam kaydı hesaplar, talep onaylar).
    * Onay adımı `hr.approval_steps`'tedir (`subject_kind='overtime_request'`) —
    genel onay altyapısı yeniden kullanılır, ikinci bir onay tablosu açılmaz.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="overtime_requests",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="overtime_requests",
        verbose_name="çalışan",
    )
    work_date = models.DateField("tarih")
    start_time = models.TimeField("başlangıç saati")
    end_time = models.TimeField("bitiş saati")
    total_minutes = models.PositiveIntegerField(
        "toplam süre (dakika)",
        help_text="Sunucuda `end_time - start_time` ile hesaplanır; istemciden alınmaz.",
    )
    description = models.TextField(
        "açıklama / neden",
        help_text="Fazla mesainin gerekçesi (denetim izi; asgari uzunluk DB guard'ı).",
    )
    overtime_rule = models.ForeignKey(
        "overtime.OvertimeRule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="overtime_requests",
        verbose_name="fazla mesai kuralı",
    )
    status = models.CharField(
        "durum",
        max_length=20,
        choices=OvertimeStatus.choices,
        default=OvertimeStatus.PENDING,
    )
    compensation_type = models.CharField(
        "karşılık türü",
        max_length=20,
        choices=CompensationType.choices,
        blank=True,
        default="",
        help_text=(
            "Boş dize = ayarlanmamış (P5b/P5a kural motoru gelene kadar dolmaz; "
            "DJ001 gereği `null=True` kullanılmaz)."
        ),
    )
    compensation_type_snapshot = models.CharField(
        "karşılık türü snapshot", max_length=20, choices=CompensationType.choices,
        blank=True, default="",
    )
    multiplier_snapshot = models.DecimalField(
        "katsayı snapshot", max_digits=6, decimal_places=3, null=True, blank=True,
    )
    decided_at = models.DateTimeField("karar zamanı", null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_overtime_requests",
        verbose_name="karar veren",
    )
    canceled_at = models.DateTimeField("iptal zamanı", null=True, blank=True)
    cancel_reason = models.TextField("iptal gerekçesi", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_overtime_requests",
        verbose_name="kaydı açan",
    )

    class Meta:
        db_table = "overtime_requests"
        verbose_name = "fazla mesai talebi"
        verbose_name_plural = "fazla mesai talepleri"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="overtime_requests_time_order",
            ),
            models.CheckConstraint(
                condition=Q(total_minutes__gt=0),
                name="overtime_requests_minutes_positive",
            ),
            models.CheckConstraint(
                condition=Q(total_minutes__lte=OVERTIME_MAX_MINUTES),
                name="overtime_requests_minutes_max",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[c.value for c in OvertimeStatus]),
                name="overtime_requests_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(compensation_type="")
                | Q(compensation_type__in=[c.value for c in CompensationType]),
                name="overtime_requests_compensation_valid",
            ),
            # İptal, gerekçesiz olamaz (denetim izi); durumla birlikte tutarlıdır.
            models.CheckConstraint(
                condition=~Q(status=OvertimeStatus.CANCELED) | ~Q(cancel_reason=""),
                name="overtime_requests_cancel_reason",
            ),
            # Aynı çalışanın aynı gün + aynı aralığı açık/onaylı kayıtlarda iki kez
            # bildirilemez. Reddedilen/iptal edilen kayıt yeniden açılabilir.
            models.UniqueConstraint(
                fields=("employee", "work_date", "start_time", "end_time"),
                condition=Q(deleted_at__isnull=True)
                & Q(status__in=[OvertimeStatus.PENDING, OvertimeStatus.APPROVED]),
                name="overtime_requests_slot_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tenant", "status", "work_date"),
                name="overtime_requests_status_idx",
            ),
            models.Index(
                fields=("tenant", "employee", "work_date"),
                name="overtime_requests_emp_idx",
            ),
        ]
        ordering = ["-work_date", "-start_time"]

    def __str__(self) -> str:
        return f"{self.employee_id}:{self.work_date} {self.start_time}-{self.end_time}"
