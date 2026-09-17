"""
RBAC (rol tabanlı erişim kontrolü) veri modeli.

Yetki çözümlemesi (docs/AUTHORIZATION.md bölüm 2):

    etkin izinler = (rollerin izinleri) + (kullanıcı eklemeleri) - (kullanıcı çıkarmaları)

İzolasyon notları:
  * `Role.tenant_id` ZORUNLUDUR: bu sürümde sistem geneli (tenant_id NULL) rol
    yoktur. Eklendiğinde RLS politikası ve oluşturma yetkisi ayrıca ele alınmalıdır
    (aksi halde bir kiracı, sistem rolü üretmeye çalışabilir).
  * `UserRole.tenant_id` denormalize edilmiştir; kullanıcının kiracısıyla eşit
    olması uygulama katmanında doğrulanır ve RLS bu sütun üzerinden çalışır.
  * `Permission` tanım tablosudur (kiracıya bağlı değildir, RLS uygulanmaz):
    yalnızca izin kodlarını barındırır, kiracı verisi içermez.
  * `RolePermission` da tanım tablosudur; erişim yolu korunan `Role` tablosundan
    geçer (bkz. docs/DATABASE.md bölüm 3).
"""

from django.db import models

from apps.common.models import TenantScopedModel, TimeStampedModel, UUIDModel


class RoleType(models.TextChoices):
    SYSTEM = "system", "Sistem"
    DEFAULT = "default", "Varsayılan"
    CUSTOM = "custom", "Özel"


class RoleScopeType(models.TextChoices):
    """
    Rolün izinlerinin ulaşabileceği **azami organizasyon kapsamı** (OD5 kapalı).

    `RoleType` (rol nasıl oluştu) ile **karıştırılmaz**. Kiracı rollerinde geçerli
    değerler §4.4'teki kapalı kümeden gelir; **`SYS` burada YOKTUR** çünkü §4.4
    `SYS`i yalnızca platform rollerine verir (`hr.roles` kiracı tablosudur;
    platform rolleri `platform.roles`'ta yaşar — OD3).

    Kapsam **izin üretmez**, izni daraltır; asıl karar `izin ∧ kapsam`dır.
    """

    TENANT = "TENANT", "Kiracı geneli"
    UNIT = "UNIT", "Birim + alt ağaç"
    UNIT_DIRECT = "UNIT_DIRECT", "Yalnızca birim (alt ağaç hariç)"
    ASSIGNED = "ASSIGNED", "Yalnızca atanmış kayıtlar"
    SELF = "SELF", "Yalnızca kendi kaydı"


#: `hr.roles` için geçerli kapalı küme (DB `CHECK` ile hizalıdır).
TENANT_ROLE_SCOPE_TYPES: tuple[str, ...] = (
    RoleScopeType.TENANT,
    RoleScopeType.UNIT,
    RoleScopeType.UNIT_DIRECT,
    RoleScopeType.ASSIGNED,
    RoleScopeType.SELF,
)

#: Org birimi kapsamı zorunlu olan tipler (OD5 fail-closed).
ORG_SCOPED_ROLE_SCOPE_TYPES: tuple[str, ...] = (
    RoleScopeType.UNIT,
    RoleScopeType.UNIT_DIRECT,
)


class Role(TenantScopedModel):
    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.CASCADE, related_name="roles", verbose_name="kiracı"
    )
    name = models.CharField("rol adı", max_length=100)
    code = models.CharField("rol kodu", max_length=100)
    role_type = models.CharField(
        "rol tipi", max_length=20, choices=RoleType.choices, default=RoleType.CUSTOM
    )
    scope_type = models.CharField(
        "azami organizasyon kapsamı",
        max_length=20,
        choices=RoleScopeType.choices,
        default=RoleScopeType.TENANT,
        help_text=(
            "OD5: rolün izinlerinin ulaşabileceği azami kapsam. İzin ÜRETMEZ; "
            "izin ∧ kapsam birlikte değerlendirilir. UNIT/UNIT_DIRECT tiplerinde "
            "atama (user_roles.scope_org_unit_id) zorunludur ve çözülemeyen "
            "kapsam yetki vermez (fail-closed)."
        ),
    )
    description = models.TextField("açıklama", blank=True)
    hierarchy_level = models.PositiveSmallIntegerField(
        "yetki hiyerarşi seviyesi",
        null=True,
        blank=True,
        help_text="Kiracı içi sıralama: 1 en üst, 30 en alt. 99/100 platform rollerine aittir.",
    )
    is_assignable = models.BooleanField("atanabilir", default=True)

    class Meta:
        db_table = "roles"
        verbose_name = "rol"
        verbose_name_plural = "roller"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="roles_tenant_code_uniq"),
            models.CheckConstraint(
                condition=models.Q(scope_type__in=[k for k, _ in RoleScopeType.choices]),
                name="roles_scope_type_valid",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "role_type"], name="roles_tenant_type_idx")]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class Permission(UUIDModel, TimeStampedModel):
    """
    Yetki tanımı. Kod biçimi: `<kaynak>.<işlem>` (docs/AUTHORIZATION.md bölüm 4).
    Kiracıya bağlı değildir; kiracı verisi içermez.
    """

    code = models.CharField("izin kodu", max_length=120, unique=True)
    name = models.CharField("ad", max_length=150)
    module = models.CharField("modül", max_length=60, blank=True)
    description = models.TextField("açıklama", blank=True)

    class Meta:
        db_table = "permissions"
        verbose_name = "izin"
        verbose_name_plural = "izinler"
        indexes = [models.Index(fields=["module"], name="permissions_module_idx")]

    def __str__(self) -> str:
        return self.code


class RolePermission(UUIDModel, TimeStampedModel):
    """
    Rol–izin eşleşmesi.

    `tenant_id` denormalize edilmiştir ve ZORUNLUDUR: bu sayede tablo da RLS ile
    korunur ve başka kiracının rolüne izin kaydı eklenmesi engellenir (guard
    tetikleyicisi bu sütunla karşılaştırır). Servis katmanı, kayıt eklerken
    `tenant` alanını rolün kiracısıyla aynı vermelidir.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="role_permissions",
        verbose_name="kiracı",
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )

    class Meta:
        db_table = "role_permissions"
        verbose_name = "rol izni"
        verbose_name_plural = "rol izinleri"
        constraints = [
            models.UniqueConstraint(
                fields=["role", "permission"], name="role_permissions_uniq"
            ),
        ]
        indexes = [models.Index(fields=["tenant", "role"], name="role_perms_tenant_idx")]


class UserRole(TenantScopedModel):
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="user_roles",
        verbose_name="kiracı",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="user_roles",
        verbose_name="kullanıcı",
    )
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="user_roles", verbose_name="rol"
    )
    granted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_roles",
    )
    granted_at = models.DateTimeField("verilme zamanı", auto_now_add=True)
    expires_at = models.DateTimeField("bitiş", null=True, blank=True)
    scope_org_unit = models.ForeignKey(
        "organization.Department",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="user_role_scopes",
        help_text=(
            "OD5: UNIT/UNIT_DIRECT tipli rollerde ZORUNLU (DB tetikleyicisi dayatır); "
            "diğer tiplerde NULL olmalıdır. Yetki kapsamı bu birim + alt ağacıdır "
            "(OD24: yalnızca AKTİF birim; silinmiş birim kapsam üretmez). "
            "M9 tamamlandı: gerçek FK (`hr.departments.id`), ON DELETE RESTRICT."
        ),
        verbose_name="organizasyon birimi kapsamı",
    )

    class Meta:
        db_table = "user_roles"
        verbose_name = "kullanıcı rolü"
        verbose_name_plural = "kullanıcı rolleri"
        constraints = [
            # OD5 §5.3: aynı kullanıcı aynı rolü farklı birimler için birden fazla
            # kez taşıyabilir (kapsam birleşimi). `nulls_distinct=False`: kapsamsız
            # (TENANT/SELF) atamalar NULL'ları "aynı" sayar → çift atama yine engellenir.
            models.UniqueConstraint(
                fields=["user", "role", "scope_org_unit"],
                name="user_roles_user_role_scope_uniq",
                nulls_distinct=False,
            ),
        ]
        indexes = [models.Index(fields=["tenant", "user"], name="user_roles_tenant_user_idx")]

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.role_id}"


class PermissionEffect(models.TextChoices):
    ALLOW = "allow", "İzin ver"
    DENY = "deny", "Engelle"


class UserPermissionOverride(TenantScopedModel):
    """
    Kullanıcı bazlı istisna. Yalnızca gerekçesiyle verilir ve denetim kaydına yazılır
    (docs/AUTHORIZATION.md bölüm 4). `deny` her zaman `allow`'u ezer.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="permission_overrides",
        verbose_name="kiracı",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="permission_overrides",
        verbose_name="kullanıcı",
    )
    permission = models.ForeignKey(
        Permission,
        on_delete=models.CASCADE,
        related_name="user_overrides",
        verbose_name="izin",
    )
    effect = models.CharField("etki", max_length=10, choices=PermissionEffect.choices)
    reason = models.TextField("gerekçe")
    granted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_overrides",
    )

    class Meta:
        db_table = "user_permission_overrides"
        verbose_name = "kullanıcı izin istisnası"
        verbose_name_plural = "kullanıcı izin istisnaları"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "permission"], name="user_permission_overrides_uniq"
            ),
            models.CheckConstraint(
                condition=models.Q(effect__in=["allow", "deny"]),
                name="user_permission_overrides_effect_valid",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "user"], name="upo_tenant_user_idx")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.effect} {self.permission_id}"
