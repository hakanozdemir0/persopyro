"""
Organizasyon ve çalışan temel veri modeli (M10–M13).

Kaynak kararlar (hepsi **kapalı**):
  * **OD24** — ağaç modeli: `departments.parent_id` **adjacency list** + özyinelemeli
    CTE; materialized path / nested set / closure tablosu **yok**.
  * **OD27** — v1'de **tek tüzel kişilik**: `company_id`/`legal_entity_id` **yok**,
    tablolar doğrudan `tenant_id`'ye bağlıdır.
  * **OD21** — soft-delete (`deleted_at`) + kısmi benzersizlikler.
  * **OD5** — `UNIT` kapsamı `departments` ağacından hesaplanır; **ağaç yetki
    mirası üretmez**, `employee_manager` / `manager_employee_id` **yetki üretmez**.

Sorumluluk ayrımı (docs/ORGANIZATION.md):

    departments ──┬── positions                (tenant düzeyinde tanımlar)
                  ├── employees                (İŞ kaydı — PII **içermez**)
                  │      ├── employee_personal_data (K2 açık / K3 şifreli, 1:1)
                  │      └── employee_manager       (raporlama ilişkisi — RBAC değil)
                  └── manager_employee               (birim sorumlusu — yetki üretmez)

Kiracı izolasyonu: her tabloda `tenant_id` + RLS + FORCE; kiracı-içi yabancı
anahtarlar `hr.ensure_same_tenant_ref()` tetikleyicileriyle korunur (RLS yalnızca
satırın kendi `tenant_id`'sini denetler).
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.common.fields import EncryptedCharField, EncryptedTextField
from apps.common.models import SoftDeleteModel, TenantScopedModel, TimeStampedModel
from apps.organization.employee_form_catalog import FIELD_CODES


def validate_iana_timezone(value: str) -> None:
    """`timezone` alanı **geçerli IANA** saat dilimi olmalı (P1-A/OD58)."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as hata:  # pragma: no cover - tip
        raise ValidationError(
            f"Geçersiz IANA saat dilimi: {value!r} (ör. 'Europe/Istanbul')."
        ) from hata


class RecordStatus(models.TextChoices):
    """
    Organizasyon kaydı yaşam döngüsü (OD21: `status` = yaşam döngüsü, `deleted_at` = silme).

    Değer kümesi mevcut proje standardından alınmıştır (`accounts.UserStatus`,
    referans tasarımın `hr.record_status` tipi): `active`/`inactive`/`archived`.
    Birim arşivlenmesi `archived` ile ifade edilir.
    """

    ACTIVE = "active", "Aktif"
    INACTIVE = "inactive", "Pasif"
    ARCHIVED = "archived", "Arşiv"


class EmployeeStatus(models.TextChoices):
    """Çalışan yaşam döngüsü — docs/ORGANIZATION.md §7 (kapalı küme)."""

    ACTIVE = "active", "Aktif"
    SUSPENDED = "suspended", "Askıda"
    TERMINATED = "terminated", "Ayrıldı"
    ARCHIVED = "archived", "Arşiv"


class ManagerRelationType(models.TextChoices):
    """Yönetici ilişkisi türü — OD24/§6 (kapalı küme)."""

    PRIMARY = "primary", "Birincil"
    SECONDARY = "secondary", "İkincil"
    DELEGATE = "delegate", "Yetki devri (delege)"


class WorkplaceType(models.TextChoices):
    """İşyeri türü (OD58 — kapalı küme)."""

    OFFICE = "office", "Ofis"
    BRANCH = "branch", "Şube"
    REMOTE = "remote", "Uzaktan"
    CLIENT_SITE = "client_site", "Müşteri sahası"


class EmploymentType(models.TextChoices):
    """İstihdam tipi (P1/OD60 — kapalı küme)."""

    FULL_TIME = "full_time", "Tam zamanlı"
    PART_TIME = "part_time", "Yarı zamanlı"
    CONTRACTOR = "contractor", "Sözleşmeli (dış kaynak)"
    INTERN = "intern", "Stajyer"
    TEMPORARY = "temporary", "Geçici"


class WorkingModel(models.TextChoices):
    """Çalışma modeli (P1/OD60 — kapalı küme)."""

    ONSITE = "onsite", "İşyerinde"
    HYBRID = "hybrid", "Hibrit"
    REMOTE = "remote", "Uzaktan"


class ContractType(models.TextChoices):
    """Sözleşme tipi (P1/OD60 — kapalı küme)."""

    PERMANENT = "permanent", "Belirsiz süreli"
    FIXED_TERM = "fixed_term", "Belirli süreli"
    PROJECT = "project", "Proje bazlı"
    INTERNSHIP = "internship", "Staj sözleşmesi"


class TerminationReason(models.TextChoices):
    """İşten ayrılış nedeni (P1/OD41 — kapalı küme)."""

    RESIGNATION = "resignation", "İstifa"
    EMPLOYER_TERMINATION = "employer_termination", "İşveren feshi"
    MUTUAL_AGREEMENT = "mutual_agreement", "Karşılıklı anlaşma"
    CONTRACT_END = "contract_end", "Sözleşme bitişi"
    RETIREMENT = "retirement", "Emeklilik"
    DEATH = "death", "Vefat"
    OTHER = "other", "Diğer"


class FormFieldLockLevel(models.TextChoices):
    """Form alanı kilit seviyesi (OD35/OD37/§28.4)."""

    SYSTEM_LOCKED = "A", "A — Sistem-kilitli"
    CONFIGURABLE = "B", "B — Yapılandırılabilir"
    CONDITIONAL = "C", "C — Koşullu"


class FormFieldDataClass(models.TextChoices):
    """Form alanı veri sınıfı (AUTHORIZATION §7)."""

    K0 = "K0", "K0 — Genel"
    K1 = "K1", "K1 — İç"
    K2 = "K2", "K2 — Kişisel"
    K3 = "K3", "K3 — Özel nitelikli"


class BloodType(models.TextChoices):
    """
    Kan grubu — **K3 (özel nitelikli: sağlık)** verisi; şifreli saklanır.

    Neden K3: KVKK m.6 kapsamında sağlık verisi özel nitelikli kişisel veridir;
    projede sağlık alanları (`health_notes`, `disability_status`) **şifreli**
    tutulur (docs/AUTHORIZATION.md §7). Kan grubu da aynı sınıfta ele alınır.

    Yazım: Türkiye kullanımı — **`0` = O** (ör. `0 Rh+`); Rh faktörü `+`/`-`.
    Değer kapalı kümedendir; doğrulama servis katmanında yapılır (şifreli kolonda
    veritabanı `CHECK` uygulanamaz).
    """

    A_POS = "A+", "A Rh+"
    A_NEG = "A-", "A Rh-"
    B_POS = "B+", "B Rh+"
    B_NEG = "B-", "B Rh-"
    AB_POS = "AB+", "AB Rh+"
    AB_NEG = "AB-", "AB Rh-"
    ZERO_POS = "0+", "0 Rh+"
    ZERO_NEG = "0-", "0 Rh-"


class Department(TenantScopedModel):
    """
    Organizasyon birimi (ağaç düğümü).

    `UNIT` kapsamının **tek** kaynağıdır: birim + alt ağacı. Ağaç kuralları
    (kendi atası olamama, döngü yasağı, derinlik ≤ 20, silinmiş birimin altına
    yeni aktif birim bağlanamaması) **veritabanında** zorlanır (M11).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="departments",
        verbose_name="kiracı",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="üst birim",
    )
    manager_employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="managed_departments",
        help_text=(
            "Birim sorumlusu (onay yönlendirmesi). **Yetki üretmez**: bu bilgi "
            "`employee_manager` ilişkisinden ve RBAC kapsamından ayrıdır (OD5 §5.5)."
        ),
        verbose_name="birim sorumlusu",
    )
    code = models.CharField("birim kodu", max_length=50)
    name = models.CharField("birim adı", max_length=190)
    status = models.CharField(
        "durum", max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE
    )

    class Meta:
        db_table = "departments"
        verbose_name = "organizasyon birimi"
        verbose_name_plural = "organizasyon birimleri"
        constraints = [
            # OD21: yalnızca AKTİF kayıt benzersizliği kısıtlar (silinen kod serbest).
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="departments_tenant_code_uniq",
            ),
            # M11/OD24: birim kendi ebeveyni olamaz (asıl döngü kontrolü tetikleyicide).
            models.CheckConstraint(
                condition=~models.Q(parent_id=models.F("id")),
                name="departments_parent_not_self",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[k for k, _ in RecordStatus.choices]),
                name="departments_status_valid",
            ),
        ]
        indexes = [
            # Ağaç sorgularının giriş noktası (OD24: recursive CTE bu indeksle başlar).
            models.Index(fields=["tenant", "parent"], name="departments_tenant_parent_idx"),
            models.Index(fields=["tenant", "status"], name="departments_tenant_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class Position(TenantScopedModel):
    """
    Unvan/kadro tanımı (tenant düzeyinde).

    Bilinçli olarak **birime bağlı değildir** (docs/DATABASE.md B.4: `UNIQUE
    (tenant_id, code)`, tek yabancı anahtar `tenant_id`): aynı unvan birden fazla
    birimde kullanılabilir. Çalışan–unvan bağı `employees.position_id` üzerindedir.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="positions",
        verbose_name="kiracı",
    )
    code = models.CharField("pozisyon kodu", max_length=50)
    name = models.CharField("pozisyon adı", max_length=190)
    level = models.CharField(
        "seviye",
        max_length=40,
        blank=True,
        help_text="Serbest etiket (ör. junior/senior/manager); sıralama/raporlama amaçlıdır.",
    )

    class Meta:
        db_table = "positions"
        verbose_name = "pozisyon"
        verbose_name_plural = "pozisyonlar"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="positions_tenant_code_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class Employee(TenantScopedModel):
    """
    Çalışanın **iş** kaydı — organizasyon/kimlik verisi; **PII içermez**.

    Ad, iletişim, kimlik ve sağlık verisi `EmployeePersonalData` içindedir (K2/K3).
    Böylece "bu birimde kim çalışıyor" (K0/K1) ile "bu kişinin TCKN'si ne" (K2/K3)
    **farklı izinlerle** yanıtlanır (OD5 §5.7; AUTHORIZATION bölüm 7).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="employees",
        verbose_name="kiracı",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_records",
        help_text="Giriş hesabı (OD2); çalışanın giriş hesabı olmak zorunda değildir.",
        verbose_name="kullanıcı",
    )
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.PROTECT,
        related_name="employees",
        verbose_name="birim",
    )
    position = models.ForeignKey(
        "organization.Position",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employees",
        verbose_name="pozisyon",
    )
    employee_no = models.CharField("personel numarası", max_length=50)
    hire_date = models.DateField("işe giriş tarihi")
    termination_date = models.DateField("çıkış tarihi", null=True, blank=True)
    status = models.CharField(
        "durum", max_length=20, choices=EmployeeStatus.choices, default=EmployeeStatus.ACTIVE
    )
    # --- P1 yaşam döngüsü alanları (OD41/OD6; kararlar AUTHORIZATION §16) -------
    termination_reason_code = models.CharField(
        "ayrılış nedeni",
        max_length=40,
        choices=TerminationReason.choices,
        blank=True,
        default="",
        help_text=(
            "Boş dize = ayarlanmamış. `status='terminated'` ise **zorunludur** (DB CHECK). "
            "DJ001 gereği `null=True` kullanılmaz."
        ),
    )
    termination_note = models.TextField(
        "ayrılış notu", blank=True, default="", help_text="Serbest metin; PII yazılmaz."
    )
    notice_end_date = models.DateField(
        "ihbar bitiş tarihi",
        null=True,
        blank=True,
        help_text=(
            "İhbar dönemi **durum değildir**: `status ∈ {active, suspended}` ve "
            "`termination_date > bugün` ise arayüzde 'ihbar döneminde' olarak türetilir (OD41)."
        ),
    )

    class Meta:
        db_table = "employees"
        verbose_name = "çalışan"
        verbose_name_plural = "çalışanlar"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "employee_no"],
                condition=models.Q(deleted_at__isnull=True),
                name="employees_tenant_no_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(termination_date__isnull=True)
                | models.Q(termination_date__gte=models.F("hire_date")),
                name="employees_termination_after_hire",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[k for k, _ in EmployeeStatus.choices]),
                name="employees_status_valid",
            ),
            # P1 (OD41): ihbar bitişi çıkış tarihinden önce olamaz.
            models.CheckConstraint(
                condition=models.Q(notice_end_date__isnull=True)
                | models.Q(termination_date__isnull=True)
                | models.Q(notice_end_date__gte=models.F("termination_date")),
                name="employees_notice_after_termination",
            ),
            # P1 (OD41): ayrılış nedeni boş ya da kapalı kümeden olmalı.
            models.CheckConstraint(
                condition=models.Q(termination_reason_code="")
                | models.Q(termination_reason_code__in=[k for k, _ in TerminationReason.choices]),
                name="employees_termination_reason_valid",
            ),
            # P1 (OD41): `terminated` ise tarih + neden zorunlu (fail-closed).
            models.CheckConstraint(
                condition=~models.Q(status=EmployeeStatus.TERMINATED)
                | (
                    models.Q(termination_date__isnull=False)
                    & ~models.Q(termination_reason_code="")
                ),
                name="employees_terminated_fields_required",
            ),
            # P1 (OD59): bir kullanıcıya en fazla BİR aktif/arşivlenmemiş çalışan kaydı.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(deleted_at__isnull=True)
                & ~models.Q(status=EmployeeStatus.ARCHIVED),
                name="employees_user_active_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "department"], name="employees_tenant_dept_idx"),
            models.Index(fields=["tenant", "user"], name="employees_tenant_user_idx"),
            models.Index(fields=["tenant", "status"], name="employees_tenant_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_no} ({self.department_id})"


class EmployeePersonalData(TimeStampedModel, SoftDeleteModel):
    """
    Kişisel veri (K2) ve özel nitelikli veri (K3) — `employees` ile **1:1**.

    Ayrı tablo olmasının nedeni yetkilendirmedir: maskeleme/erişim **tablo
    kapsamında** kontrol edilir; organizasyon kapsamı (UNIT) bu tabloya **tek
    başına erişim hakkı vermez** (OD5 §5.7). K3 alanları Fernet ile şifrelenir.

    Birincil anahtar `employee_id`'dir (1:1); ayrı `id` kolonu **yoktur**
    (docs/DATABASE.md B.4).
    """

    employee = models.OneToOneField(
        "organization.Employee",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="personal_data",
        verbose_name="çalışan",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="employee_personal_data",
        verbose_name="kiracı",
    )
    # --- K2 (kişisel veri) ---
    first_name = models.CharField("ad", max_length=100)
    last_name = models.CharField("soyad", max_length=100)
    birth_date = models.DateField("doğum tarihi", null=True, blank=True)
    national_id = models.CharField("kimlik numarası", max_length=30, blank=True)
    address = models.TextField("adres", blank=True)
    personal_email = models.EmailField("kişisel e-posta", max_length=190, blank=True)
    personal_phone = models.CharField("kişisel telefon", max_length=30, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    bank_name = models.CharField(
        "banka",
        max_length=100,
        blank=True,
        default="",
        help_text="Banka adı (IBAN ile birlikte banka bilgisi).",
    )
    # --- K2 devamı (M12 genişletme, 2026-09-11) ---
    emergency_contact_phone = models.CharField("yakın kişi telefonu", max_length=30, blank=True)
    emergency_contact_relation = models.CharField("yakınlık derecesi", max_length=60, blank=True)
    citizenship = models.CharField(
        "vatandaşlık",
        max_length=60,
        blank=True,
        default="",
        help_text="ISO ülke kodu veya ad; boş = bilinmiyor. 'TR' = Türkiye.",
    )
    work_permit_type = models.CharField(
        "oturma/çalışma izni",
        max_length=60,
        blank=True,
        help_text="Türk vatandaşı değilse izin türü (ör. 'çalışma izni', 'oturma izni').",
    )
    work_permit_valid_until = models.DateField("izin geçerlilik tarihi", null=True, blank=True)
    # --- K3 (özel nitelikli kişisel veri — ŞİFRELİ) ---
    blood_type = EncryptedCharField(
        "kan grubu (şifreli)",
        max_length=500,
        blank=True,
        null=True,
        choices=BloodType.choices,
        help_text=(
            "K3 (sağlık) verisi: Fernet ile şifreli saklanır; boş = bilinmiyor. "
            "Kapalı küme: A+/A-/B+/B-/AB+/AB-/0+/0- (Türkiye yazımı: 0 = O)."
        ),
    )
    health_notes = EncryptedTextField("sağlık notları (şifreli)", blank=True, null=True)
    disability_status = EncryptedCharField("engellilik durumu (şifreli)", blank=True, null=True)
    biometric_ref = EncryptedCharField("biyometrik referans (şifreli)", blank=True, null=True)

    class Meta:
        db_table = "employee_personal_data"
        verbose_name = "çalışan kişisel verisi"
        verbose_name_plural = "çalışan kişisel verileri"
        indexes = [models.Index(fields=["tenant"], name="emp_personal_tenant_idx")]

    def __str__(self) -> str:
        return f"kişisel veri ({self.employee_id})"


class EmployeeManager(TenantScopedModel):
    """
    Çalışan–yönetici **raporlama** ilişkisi (geçmişli, çoklu yönetici destekli).

    **RBAC DEĞİLDİR** (OD5 §5.5): bu satırlar **yetki üretmez**. Yetki `user_roles`
    (+ `scope_org_unit_id`) üzerinden gelir; ikisi onay akışında birlikte
    kullanılabilir ama kaynakları ayrıdır. `relation_type='primary'` için çalışan
    başına **tek aktif** ilişki veritabanında zorlanır.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="employee_manager_relations",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        related_name="manager_relations",
        verbose_name="çalışan",
    )
    manager_employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        related_name="subordinate_relations",
        verbose_name="yönetici çalışan",
    )
    relation_type = models.CharField(
        "ilişki türü",
        max_length=20,
        choices=ManagerRelationType.choices,
        default=ManagerRelationType.PRIMARY,
    )
    effective_from = models.DateField("başlangıç")
    effective_to = models.DateField("bitiş", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_manager_relations",
        verbose_name="kaydeden",
    )

    class Meta:
        db_table = "employee_manager"
        verbose_name = "yönetici ilişkisi"
        verbose_name_plural = "yönetici ilişkileri"
        constraints = [
            # OD24: çalışan kendi yöneticisi olamaz.
            models.CheckConstraint(
                condition=~models.Q(employee_id=models.F("manager_employee_id")),
                name="employee_manager_not_self",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="employee_manager_date_range_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(relation_type__in=[k for k, _ in ManagerRelationType.choices]),
                name="employee_manager_relation_type_valid",
            ),
            # OD24 §4: çalışanın **tek aktif birincil** yöneticisi olur.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(
                    relation_type="primary",
                    effective_to__isnull=True,
                    deleted_at__isnull=True,
                ),
                name="employee_manager_primary_uniq",
            ),
            # Aynı çift için aynı anda iki açık ilişki olamaz (tür fark etmez).
            models.UniqueConstraint(
                fields=["employee", "manager_employee"],
                condition=models.Q(effective_to__isnull=True, deleted_at__isnull=True),
                name="employee_manager_pair_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "manager_employee"], name="emp_manager_tenant_mgr_idx"
            ),
            models.Index(fields=["tenant", "employee"], name="emp_manager_tenant_emp_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} -> {self.manager_employee_id} [{self.relation_type}]"

class Workplace(TenantScopedModel):
    """
    Fiziksel/mali çalışma lokasyonu (P1 — OD58, kapalı).

    **Departman ile karıştırılmaz:** departman organizasyon eksenidir ("kim nereye
    bağlı"), işyeri fiziksel/mali eksendir (ülke, saat dilimi, resmî tatil, bordro).
    Bir işyeri birden çok departman/çalışan tarafından kullanılabilir; `department`
    ile zorunlu bir FK ilişkisi **kurulmaz**.

    `country_code` ve `timezone` zorunludur: ileride devam/vardiya (P4) ve ülke kural
    motoru (P7) bu alanlara bağlanır. `timezone` **IANA** olmalıdır (model doğrulaması).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="workplaces",
        verbose_name="kiracı",
    )
    code = models.CharField("işyeri kodu", max_length=50)
    name = models.CharField("işyeri adı", max_length=190)
    type = models.CharField(
        "tür",
        max_length=20,
        choices=WorkplaceType.choices,
        default=WorkplaceType.OFFICE,
    )
    country_code = models.CharField(
        "ülke kodu (ISO 3166-1 alpha-2)",
        max_length=2,
        help_text="Ör. TR, DE, US. Ülke kural motoru (P7) ve tatil takvimi (P4) bu alanı kullanır.",
    )
    city = models.CharField("şehir", max_length=100, blank=True, default="")
    address = models.TextField("adres", blank=True, default="")
    timezone = models.CharField(
        "saat dilimi (IANA)",
        max_length=64,
        validators=[validate_iana_timezone],
        help_text="Ör. Europe/Istanbul, Europe/Berlin, America/New_York.",
    )
    status = models.CharField(
        "durum",
        max_length=20,
        choices=RecordStatus.choices,
        default=RecordStatus.ACTIVE,
    )

    class Meta:
        db_table = "workplaces"
        verbose_name = "işyeri"
        verbose_name_plural = "işyerleri"
        constraints = [
            # OD21: soft-delete sonrası kod yeniden kullanılabilir.
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="workplaces_tenant_code_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(country_code__regex=r"^[A-Z]{2}$"),
                name="workplaces_country_code_format",
            ),
            models.CheckConstraint(
                condition=models.Q(type__in=[k for k, _ in WorkplaceType.choices]),
                name="workplaces_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[k for k, _ in RecordStatus.choices]),
                name="workplaces_status_valid",
            ),
            models.CheckConstraint(
                condition=~models.Q(timezone=""),
                name="workplaces_timezone_not_blank",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="workplaces_tenant_status_idx"),
            models.Index(fields=["tenant", "country_code"], name="workplaces_tenant_country_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"


class EmployeeEmploymentTerms(TenantScopedModel):
    """
    İstihdam koşulları — **etkili tarihli** tarihçe (P1 — OD60, kapalı).

    Sözleşme yenileme ve model/tip/işyeri değişikliği **üzerine yazılmaz**: yeni
    satır açılır, eskisi `effective_to` ile kapatılır (tek açık satır DB'de zorlanır).
    **Ücret/maaş alanı içermez** (P6 `hr.employee_compensation`).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="employee_employment_terms",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        related_name="employment_terms",
        verbose_name="çalışan",
    )
    effective_from = models.DateField("başlangıç")
    effective_to = models.DateField(
        "bitiş", null=True, blank=True, help_text="Boş = **güncel** koşul satırı."
    )
    employment_type = models.CharField(
        "istihdam tipi", max_length=20, choices=EmploymentType.choices
    )
    working_model = models.CharField(
        "çalışma modeli", max_length=20, choices=WorkingModel.choices
    )
    workplace = models.ForeignKey(
        "organization.Workplace",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employment_terms",
        verbose_name="işyeri",
        help_text="Soft-delete edilmiş işyeri yeni koşul satırında kullanılamaz (servis kuralı).",
    )
    contract_type = models.CharField(
        "sözleşme tipi", max_length=20, choices=ContractType.choices, blank=True, default=""
    )
    contract_start_date = models.DateField("sözleşme başlangıcı", null=True, blank=True)
    contract_end_date = models.DateField("sözleşme bitişi", null=True, blank=True)
    probation_end_date = models.DateField("deneme süresi bitişi", null=True, blank=True)
    reason = models.CharField("gerekçe", max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_employment_terms",
        verbose_name="kaydeden",
    )

    class Meta:
        db_table = "employee_employment_terms"
        verbose_name = "istihdam koşulu"
        verbose_name_plural = "istihdam koşulları"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="eet_date_range_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(employment_type__in=[k for k, _ in EmploymentType.choices]),
                name="eet_employment_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(working_model__in=[k for k, _ in WorkingModel.choices]),
                name="eet_working_model_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(contract_type="")
                | models.Q(contract_type__in=[k for k, _ in ContractType.choices]),
                name="eet_contract_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(contract_start_date__isnull=True)
                | models.Q(contract_end_date__isnull=True)
                | models.Q(contract_end_date__gte=models.F("contract_start_date")),
                name="eet_contract_range_valid",
            ),
            # OD60: çalışan başına **tek açık** koşul satırı.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(effective_to__isnull=True, deleted_at__isnull=True),
                name="eet_employee_open_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "employee", "effective_from"],
                name="eet_tenant_emp_from_idx",
            ),
            models.Index(fields=["tenant", "workplace"], name="eet_tenant_workplace_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} {self.effective_from}…{self.effective_to or 'açık'}"


class EmployeeAssignment(TenantScopedModel):
    """
    Organizasyon atama tarihçesi (P1 — OD60, kapalı).

    `move_employee` **eski satırı kapatır, yenisini açar**; `employees.department_id`
    ve `employees.position_id` "güncel değer" olarak **korunur** ve aynı servis
    yolundan yazılır (çift kaynak ayrışması test edilir).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="employee_assignments",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        "organization.Employee",
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="çalışan",
    )
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.PROTECT,
        related_name="employee_assignments",
        verbose_name="birim",
    )
    position = models.ForeignKey(
        "organization.Position",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employee_assignments",
        verbose_name="pozisyon",
    )
    effective_from = models.DateField("başlangıç")
    effective_to = models.DateField("bitiş", null=True, blank=True)
    reason = models.CharField("gerekçe", max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_employee_assignments",
        verbose_name="kaydeden",
    )

    class Meta:
        db_table = "employee_assignments"
        verbose_name = "çalışan ataması"
        verbose_name_plural = "çalışan atamaları"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="ea_date_range_valid",
            ),
            # OD60: çalışan başına **tek açık** atama.
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(effective_to__isnull=True, deleted_at__isnull=True),
                name="ea_employee_open_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "employee", "effective_from"],
                name="ea_tenant_emp_from_idx",
            ),
            models.Index(fields=["tenant", "department"], name="ea_tenant_dept_idx"),
            models.Index(fields=["tenant", "position"], name="ea_tenant_position_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.employee_id} @ {self.department_id} ({self.effective_from})"


class FormFieldDefinition(TenantScopedModel):
    """
    Onboarding formu alan yapılandırması (P1 — OD35/OD36/OD37/OD38, kapalı).

    **Tenant yalnız override yazar**; varsayılanlar koddaki katalogdan gelir
    (`apps/organization/employee_form_catalog.py`). Bilinmeyen `field_code`
    reddedilir (OD35: custom alan yok) — hem model doğrulaması hem DB `CHECK`.

    `lock_level` ve `data_class` **sunucu hesaplar** (katalogdan); tenant bu
    kolonları değiştiremez. DB `CHECK`'leri:
      * **A** seviyesi gizlenemez/opsiyonel yapılamaz,
      * **K3** alan zorunlu yapılamaz (OD37).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="form_field_definitions",
        verbose_name="kiracı",
    )
    form_code = models.CharField("form kodu", max_length=50, default="employee_onboarding")
    field_code = models.CharField("alan kodu", max_length=50)
    section_code = models.CharField("bölüm kodu", max_length=50)
    section_label = models.CharField("bölüm etiketi", max_length=100)
    sort_order = models.PositiveIntegerField("sıra", default=0)
    is_visible = models.BooleanField("görünür", default=True)
    is_required = models.BooleanField("zorunlu", default=False)
    label_override = models.CharField(
        "etiket (override)", max_length=190, blank=True, default=""
    )
    helper_text = models.CharField("yardım metni", max_length=300, blank=True, default="")
    lock_level = models.CharField(
        "kilit seviyesi",
        max_length=1,
        choices=FormFieldLockLevel.choices,
        help_text="Katalogdan gelir; tenant değiştiremez.",
    )
    data_class = models.CharField(
        "veri sınıfı",
        max_length=2,
        choices=FormFieldDataClass.choices,
        help_text="Katalogdan gelir; tenant değiştiremez (AUTHORIZATION §7).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_form_field_definitions",
        verbose_name="kaydeden",
    )

    class Meta:
        db_table = "form_field_definitions"
        verbose_name = "form alanı yapılandırması"
        verbose_name_plural = "form alanı yapılandırmaları"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "form_code", "field_code"],
                condition=models.Q(deleted_at__isnull=True),
                name="ffd_tenant_form_field_uniq",
            ),
            # OD35: bilinmeyen alan kodu DB seviyesinde de reddedilir (fail-closed).
            models.CheckConstraint(
                condition=models.Q(field_code__in=list(FIELD_CODES)),
                name="ffd_field_code_known",
            ),
            models.CheckConstraint(
                condition=models.Q(form_code="employee_onboarding"),
                name="ffd_form_code_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(lock_level__in=[k for k, _ in FormFieldLockLevel.choices]),
                name="ffd_lock_level_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(data_class__in=[k for k, _ in FormFieldDataClass.choices]),
                name="ffd_data_class_valid",
            ),
            # A seviyesi: gizlenemez + opsiyonel yapılamaz.
            models.CheckConstraint(
                condition=~models.Q(lock_level=FormFieldLockLevel.SYSTEM_LOCKED)
                | (models.Q(is_visible=True) & models.Q(is_required=True)),
                name="ffd_locked_fields_forced",
            ),
            # K3: zorunlu yapılamaz (OD37).
            models.CheckConstraint(
                condition=~models.Q(data_class=FormFieldDataClass.K3) | models.Q(is_required=False),
                name="ffd_k3_not_required",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "form_code", "sort_order"],
                name="ffd_tenant_form_sort_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.form_code}:{self.field_code}"
