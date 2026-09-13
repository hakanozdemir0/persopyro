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


from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.models import UUIDModel


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Aktif"
    INACTIVE = "inactive", "Pasif"
    ARCHIVED = "archived", "Arşiv"


class TokenKind(models.TextChoices):
    """Token türü (OD26): yalnızca üst ömür sınırını ve denetim etiketini değiştirir."""

    INTERACTIVE = "interactive", "Etkileşimli (insan)"
    SERVICE = "service", "Servis/entegrasyon"


class TokenRevokeReason(models.TextChoices):
    """
    İptal nedeni — KAPALI küme (OD26; docs/AUTHORIZATION.md).

    Tabloda ayrıca `CHECK` kısıtı vardır; yeni bir neden eklenirse hem bu liste
    hem kısıt yeni bir migration ile güncellenir.
    """

    LOGOUT = "logout", "Çıkış"
    OWNER_DISABLED = "owner_disabled", "Sahibi pasif/silinmiş"
    TENANT_DISABLED = "tenant_disabled", "Kiracı askıda/iptal/silinmiş"
    ADMIN_REVOKE = "admin_revoke", "Yönetici iptali"
    SECURITY_INCIDENT = "security_incident", "Güvenlik olayı"
    POLICY_MIGRATION = "policy_migration", "Politika göçü (kapsamsız dönem)"


class ApiToken(UUIDModel):
    """
    API erişim token'ı (opak, hash'li, süreli, cihaz bazlı iptal edilebilir).

    NEDEN KENDİ MODELİMİZ (knox yerine): Token doğrulaması, kiracı bağlamı
    KURULMADAN yapılır — çünkü bağlamı token'ın kendisinden öğreniriz. knox,
    doğrulama sırasında kullanıcıyı `select_related` ile çeker; kullanıcı tablosu
    RLS ile korunduğu için o an satır görünmez ve token asla doğrulanamaz
    (ölçüldü: 401). Bu yüzden token satırı `tenant_id` taşır.

    Güvenlik özellikleri:
      * Düz metin token SAKLANMAZ; yalnızca SHA-256 özeti (yüksek entropili
        rastgele değer olduğu için hızlı özet yeterlidir).
      * Süre (`expires_at`) zorunludur; süresi geçen token reddedilir.
      * Cihaz bazlı iptal: çıkış yalnızca o token'ı siler.
      * Bu tablo RLS DIŞIDIR (giriş öncesi okunur), kiracı verisi içermez.
        Yapısal RLS testindeki istisna listesinde gerekçesiyle birlikte yer alır.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name="kiracı",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name="kullanıcı",
    )
    token_hash = models.CharField("token özeti (SHA-256)", max_length=64, unique=True)
    label = models.CharField("cihaz/etiket", max_length=80, blank=True)
    token_kind = models.CharField(
        "token türü", max_length=16, choices=TokenKind.choices, default=TokenKind.INTERACTIVE
    )
    purpose = models.TextField("amaç/gerekçe")
    scopes = ArrayField(
        models.CharField(max_length=120),
        verbose_name="izin kapsamı",
        default=list,
        help_text="Sahibinin izinlerinin ALT KÜMESİ; boş olamaz (iptal edilmiş satır hariç).",
    )
    expires_at = models.DateTimeField("bitiş")
    revoked_at = models.DateTimeField("iptal zamanı", null=True, blank=True)
    revoked_reason = models.CharField(  # noqa: DJ001 - NULL anlamlıdır: "iptal edilmedi"
        # NULL bilinçli: `CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))` ve
        # iptal nedeni kapalı kümedir; boş string geçerli bir "iptal edilmedi"
        # temsili olmaz (OD26).
        "iptal nedeni",
        max_length=32,
        choices=TokenRevokeReason.choices,
        null=True,
        blank=True,
    )
    last_used_at = models.DateTimeField("son kullanım", null=True, blank=True)
    created_at = models.DateTimeField("oluşturma", auto_now_add=True)

    class Meta:
        db_table = "api_tokens"
        verbose_name = "API token"
        verbose_name_plural = "API tokenları"
        indexes = [
            models.Index(fields=["tenant", "user"], name="api_tokens_tenant_user_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(token_kind__in=list(TokenKind.values)),
                name="api_tokens_kind_valid",
            ),
            # İptal izi ya tamamen boş ya tamamen dolu olur.
            models.CheckConstraint(
                condition=(
                    models.Q(revoked_at__isnull=True, revoked_reason__isnull=True)
                    | models.Q(revoked_at__isnull=False, revoked_reason__isnull=False)
                ),
                name="api_tokens_revoked_pair",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_reason__isnull=True)
                | models.Q(revoked_reason__in=list(TokenRevokeReason.values)),
                name="api_tokens_revoked_reason_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label or 'token'} ({self.user_id})"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_usable(self) -> bool:
        """Kullanılabilir mi: iptal edilmemiş VE süresi geçmemiş."""
        return not self.is_revoked and not self.is_expired

    def revoke(self, reason: str, *, save: bool = True) -> bool:
        """
        Token'ı iptal eder (satır SİLİNMEZ — denetim izi korunur).

        İptal **tek yönlüdür**: zaten iptal edilmiş bir token yeniden
        etkinleştirilemez (veritabanı tetikleyicisi de bunu engeller).
        """
        if self.revoked_at is not None:
            return False
        self.revoked_at = timezone.now()
        self.revoked_reason = reason
        if save:
            self.save(update_fields=["revoked_at", "revoked_reason"])
        return True

    @classmethod
    def issue(
        cls,
        user,
        *,
        purpose: str,
        scopes,
        token_kind: str = TokenKind.INTERACTIVE,
        label: str = "",
        ttl_hours: int | None = None,
    ) -> tuple["ApiToken", str]:
        """
        Yeni token üretir. Döner: (kayıt, düz_metin_token).

        Düz metin yalnızca bu anda gösterilir; bir daha elde edilemez.
        Doğrulama (kapsam katalogu, alt küme kuralı, amaç kalitesi) servis
        katmanında yapılır: `apps.accounts.services.issue_api_token`. Bu metot
        yalnızca TTL tavanını dayatır ve kaydı yazar.
        """
        import hashlib
        import secrets

        tavan = settings.API_TOKEN_TTL_MAX_HOURS.get(token_kind)
        if tavan is None:
            raise ValueError(f"Bilinmeyen token türü: {token_kind}")

        saat = settings.API_TOKEN_TTL_HOURS if ttl_hours is None else int(ttl_hours)
        if saat <= 0 or saat > tavan:
            raise ValueError(f"Token ömrü 1..{tavan} saat aralığında olmalıdır (istenen: {saat}).")

        duz_metin = secrets.token_urlsafe(32)
        kayit = cls.objects.create(
            tenant_id=user.tenant_id,
            user=user,
            token_hash=hashlib.sha256(duz_metin.encode()).hexdigest(),
            label=label,
            token_kind=token_kind,
            purpose=purpose,
            scopes=list(scopes),
            expires_at=timezone.now() + timedelta(hours=saat),
        )
        return kayit, duz_metin

    @staticmethod
    def hash_token(duz_metin: str) -> str:
        import hashlib

        return hashlib.sha256(duz_metin.encode()).hexdigest()


class DjangoAdminRemovedError(NotImplementedError, ValidationError):
    """
    OD19 (kapalı): Django Admin / superuser akışı ve `auth_*` tabanlı yetki yolu
    kaldırılmıştır; bu akış açık hata ile reddedilir (fail-closed).

    NEDEN İKİ ATALAR: `django.contrib.auth`'ın `createsuperuser` komutu etkin
    kalır (komut araması `INSTALLED_APPS` sırasına göre yapılır ve `django.contrib.
    auth` daha önce geldiği için bir override gölgede kalırdı — ölçüldü). Django'nun
    komutu `ValidationError`'ı yakalayıp temiz bir `CommandError` mesajına çevirir
    (`createsuperuser.py`); diğer istisnalar yığın iziyle düşer. Bu yüzden hata hem
    `NotImplementedError` (programatik çağrılar için: "desteklenmiyor") hem
    `ValidationError` (CLI için: anlaşılır mesaj) olacak şekilde tanımlandı.

    DİKKAT (`ölçüldü`): çoklu kalıtımda `ValidationError.__init__` kendiliğinden
    çalışmaz (MRO'da `NotImplementedError` önce gelir ve alanlar — `error_list`,
    `messages` — boş kalır; Django'nun komutu `e.messages`'ı okurken `AttributeError`
    verir). Bu yüzden iki başlatıcı da AÇIKÇA çağrılır.
    """

    def __init__(self, message: str) -> None:
        NotImplementedError.__init__(self, message)
        ValidationError.__init__(self, message)


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

    def create_superuser(
        self, email: str | None = None, password: str | None = None, tenant=None, **extra
    ):
        """
        Persopyro'da Django Admin **superuser** kavramı YOKTUR (OD19 — kapalı).

        Sessizce Django superuser semantiği üretmek yerine **açık hata** verilir:

          * `/admin/` yolu ve `django.contrib.admin` kaldırılmıştır,
          * `is_superuser` alanı hiç eklenmemiştir (`PermissionsMixin` kullanılmaz),
          * `is_staff` hiçbir Persopyro yetkisi **vermez** ve veritabanı kısıtıyla
            `False`'a zorlanır (`users_is_staff_disabled`).

        Kiracı yöneticisi oluşturmanın doğru yolu: normal kullanıcı üretmek ve
        `create_default_roles(<tenant>)` + `UserRole` ile rol atamak
        (çalışan örnek: `scripts/_demo_data.py`).
        """
        raise DjangoAdminRemovedError(
            "create_superuser desteklenmiyor (OD19 — kapalı): Persopyro'da Django Admin "
            "superuser kavramı yoktur ve is_staff hiçbir yetki vermez. Kiracı yöneticisi "
            "için User.objects.create_user(...) + create_default_roles(tenant) + UserRole "
            "kullanın."
        )


class User(UUIDModel, AbstractBaseUser):
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,  # kiracı silinmeden kullanıcı silinmez
        related_name="users",
        verbose_name="kiracı",
    )
    email = models.EmailField("e-posta", max_length=190)
    public_id = models.CharField(
        "public ID",
        max_length=32,
        blank=True,
        editable=False,
        help_text=(
            "OD25: <kiracı-prefix>-<8×Crockford>; DB tetikleyicisi üretir, "
            "sonradan değiştirilemez."
        ),
    )
    first_name = models.CharField("ad", max_length=100)
    last_name = models.CharField("soyad", max_length=100)
    phone = models.CharField("telefon", max_length=30, blank=True)
    status = models.CharField(
        "durum", max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE
    )
    auth_version = models.PositiveIntegerField(
        "kimlik doğrulama sürümü",
        default=0,
        help_text=(
            "P1 (OD59): çalışan askıya alındığında/çıkış verildiğinde artırılır. Oturum "
            "açılışında saklanan sürümle karşılaştırılır; uyuşmazsa oturum düşürülür "
            "(Django'nun `session_auth_hash` mekanizması yalnız parola değişiminde "
            "çalıştığı için açık bir sürüm alanı gerekir). Negatif olamaz."
        ),
    )
    is_staff = models.BooleanField(
        "yönetim paneline erişebilir (KULLANILMIYOR)",
        default=False,
        help_text=(
            "OD19 (kapalı): Django Admin kaldırılmıştır. Bu bayrak hiçbir Persopyro "
            "yetkisi VERMEZ ve üretimde True olamaz (uygulama guard'ı + DB kısıtı). "
            "Alan, Django uyumluluğu için şemada tutulur."
        ),
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
            # OD19 (kapalı): Django Admin yolu kaldırıldı; `is_staff` hiçbir
            # Persopyro yetkisi vermez ve üretimde True olamaz. Kural hem uygulama
            # katmanında (`User.save`) hem veritabanında (son savunma hattı)
            # dayatılır — ham SQL ile de bu bayrak açılamaz.
            models.CheckConstraint(
                condition=models.Q(is_staff=False), name="users_is_staff_disabled"
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
        # OD19 (kapalı): admin kaldırıldıktan sonra `is_staff` yalnızca yanlış
        # yapılandırma riski taşır. Sessizce kabul etmek yerine açık hata verilir
        # (fail-closed); aynı kural DB'de CHECK ile de dayatılır.
        if self.is_staff:
            raise ValueError(
                "is_staff=True geçersizdir (OD19 — kapalı): Django Admin kaldırıldı ve "
                "bu bayrak hiçbir Persopyro yetkisi vermez. Yetki yalnızca UserRole / "
                "RolePermission / UserPermissionOverride üzerinden verilir."
            )
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
