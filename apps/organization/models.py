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

from django.conf import settings
from django.db import models

from apps.common.fields import EncryptedCharField, EncryptedTextField
from apps.common.models import SoftDeleteModel, TenantScopedModel, TimeStampedModel


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
