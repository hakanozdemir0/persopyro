from django.db import models

from apps.common.models import UUIDModel


class TenantStatus(models.TextChoices):
    ACTIVE = "active", "Aktif"
    TRIAL = "trial", "Deneme"
    SUSPENDED = "suspended", "Askıda"
    CANCELED = "canceled", "İptal"


class Tenant(UUIDModel):
    """
    Kiracı (müşteri şirket).

    Tüm iş tabloları `tenant_id` ile buraya bağlanır ve RLS ile korunur.
    Bu tablonun kendi RLS politikası `id = current_tenant_id()` biçimindedir.
    """

    name = models.CharField("şirket adı", max_length=200)
    slug = models.SlugField(
        "kısa ad (slug)",
        max_length=60,
        unique=True,
        help_text="Kiracıyı tanımlayan kısa ad; giriş ve alt alan adı için kullanılır.",
    )
    domain = models.CharField(
        "özel alan adı",
        max_length=190,
        unique=True,
        null=True,
        blank=True,
        help_text="Varsa bu alan adı doğrudan kiracıya çözümlenir (ör. ik.acme.com).",
    )
    legal_name = models.CharField("tüzel unvan", max_length=250, blank=True)
    tax_number = models.CharField("vergi numarası", max_length=50, blank=True)
    email = models.EmailField("e-posta", max_length=190, blank=True)
    phone = models.CharField("telefon", max_length=30, blank=True)
    country = models.CharField("ülke", max_length=100, blank=True)
    city = models.CharField("şehir", max_length=100, blank=True)
    address = models.TextField("adres", blank=True)

    status = models.CharField(
        "durum",
        max_length=20,
        choices=TenantStatus.choices,
        default=TenantStatus.TRIAL,
    )
    max_users = models.PositiveIntegerField("kullanıcı limiti", default=10)
    max_storage_mb = models.DecimalField(
        "depolama limiti (MB)", max_digits=12, decimal_places=2, default=500
    )
    settings = models.JSONField("ayarlar", default=dict, blank=True)

    created_at = models.DateTimeField("oluşturma", auto_now_add=True)
    updated_at = models.DateTimeField("güncelleme", auto_now=True)
    deleted_at = models.DateTimeField("silinme", null=True, blank=True)

    class Meta:
        db_table = "tenants"
        verbose_name = "kiracı"
        verbose_name_plural = "kiracılar"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_users__gte=0), name="tenants_max_users_nonneg"
            ),
            models.CheckConstraint(
                condition=models.Q(max_storage_mb__gte=0),
                name="tenants_max_storage_mb_nonneg",
            ),
        ]
        indexes = [models.Index(fields=["status"], name="tenants_status_idx")]

    def __str__(self) -> str:
        return self.name
