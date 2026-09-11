"""
Kullanıcı modeli.

Kritik kararlar (docs/ARCHITECTURE.md bölüm 7, docs/AUTHORIZATION.md):
  * E-posta ile giriş (USERNAME_FIELD = "email"); e-posta küçük harfe çevrilir.
  * `tenant_id` ZORUNLUDUR ve sonradan değiştirilemez: kiracı izolasyonunun
    çekirdeği budur. Platform yöneticileri de bir kiracıya bağlıdır; destek
    amaçlı erişimler denetim kaydına yazılır.
  * (tenant_id, email) ikilisi tekildir: aynı e-posta farklı kiracılarda olabilir.
  * Django'nun `PermissionsMixin`'i KULLANILMAZ: yetkilendirme bu projenin kendi
    RBAC tablolarından çözümlenir (apps/rbac). Django Admin uyumluluğu için
    gerekli `is_staff`, `has_perm`, `has_module_perms` burada tanımlıdır.
  * Parola hash'leme çerçeveye bırakılır (Argon2id birincil; bkz. settings).
"""


from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Aktif"
    INACTIVE = "inactive", "Pasif"
    ARCHIVED = "archived", "Arşiv"


class UserManager(BaseUserManager):
    """E-posta + kiracı zorunlu kullanıcı oluşturucu."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, tenant, **extra):
        if not email:
            raise ValueError("E-posta zorunludur.")
        if tenant is None:
            raise ValueError("Kiracı (tenant) zorunludur.")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, tenant=tenant, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, tenant=None, **extra):
        extra.setdefault("status", UserStatus.ACTIVE)
        return self._create_user(email, password, tenant, **extra)

    def create_superuser(self, email: str, password: str | None = None, tenant=None, **extra):
        extra.setdefault("status", UserStatus.ACTIVE)
        extra.setdefault("is_staff", True)
        return self._create_user(email, password, tenant, **extra)


class User(UUIDModel, AbstractBaseUser):
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,  # kiracı silinmeden kullanıcı silinmez
        related_name="users",
        verbose_name="kiracı",
    )
    email = models.EmailField("e-posta", max_length=190)
    first_name = models.CharField("ad", max_length=100)
    last_name = models.CharField("soyad", max_length=100)
    phone = models.CharField("telefon", max_length=30, blank=True)
    status = models.CharField(
        "durum", max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE
    )
    is_staff = models.BooleanField(
        "yönetim paneline erişebilir", default=False,
        help_text="Django yönetim paneline giriş izni (yetki değil, yalnızca kapı).",
    )

    # Güvenlik alanları (docs/SECURITY.md bölüm 3)
    email_verified_at = models.DateTimeField("e-posta doğrulama", null=True, blank=True)
    password_changed_at = models.DateTimeField("parola değişimi", null=True, blank=True)
    failed_login_count = models.PositiveIntegerField("başarısız giriş sayısı", default=0)
    lockout_until = models.DateTimeField("kilit bitişi", null=True, blank=True)
    multi_factor_enabled = models.BooleanField("çok faktörlü doğrulama", default=False)
    totp_secret = models.CharField(
        "TOTP anahtarı (şifreli)",
        max_length=255,
        blank=True,
        help_text="Bekleme (pending) TOTP anahtarı; onaylanınca oturuma alınır.",
    )

    # İzlenebilirlik
    created_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_users"
    )
    last_login_at = models.DateTimeField("son giriş", null=True, blank=True)
    created_at = models.DateTimeField("oluşturma", auto_now_add=True)
    updated_at = models.DateTimeField("güncelleme", auto_now=True)
    deleted_at = models.DateTimeField("silinme", null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        verbose_name = "kullanıcı"
        verbose_name_plural = "kullanıcılar"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "email"], name="users_tenant_email_uniq"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="users_tenant_status_idx"),
            models.Index(fields=["email"], name="users_email_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.tenant_id})"

    # --- Durum yardımcıları -------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Pasif/arşivlenmiş veya silinmiş kullanıcı giriş yapamaz."""
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def is_locked(self) -> bool:
        return self.lockout_until is not None and self.lockout_until > timezone.now()

    # --- Django Admin uyumluluğu -------------------------------------------

    def has_perm(self, perm: str, obj=None) -> bool:  # noqa: ARG002
        from apps.rbac.services import user_has_permission

        return user_has_permission(self, perm)

    def has_module_perms(self, app_label: str) -> bool:  # noqa: ARG002
        from apps.rbac.services import user_has_any_permission

        return user_has_any_permission(self)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.lower()
        if self.pk is None and self.password_changed_at is None:
            self.password_changed_at = timezone.now()
        super().save(*args, **kwargs)

    def set_password(self, raw_password: str | None) -> None:
        super().set_password(raw_password)
        if raw_password:
            self.password_changed_at = timezone.now()

    @property
    def id_str(self) -> str:
        return str(self.pk)
