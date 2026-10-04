"""
Denetim kaydı (audit log).

Kurallar (docs/SECURITY.md bölüm 6, docs/DEVELOPMENT_RULES.md):
  * Denetim kaydı **değiştirilemez**: uygulama rolüne yalnızca INSERT ve SELECT
    verilir; UPDATE/DELETE/TRUNCATE verilmez. Bu, veritabanı yetkisiyle dayatılır
    (`scripts/02_grant_app_privileges.sql`) ve testle doğrulanır.
  * Teknik loglardan ayrıdır: hata ayıklama değil, "kim ne zaman neyi değiştirdi".
  * Kişisel veri değeri saklanmaz; `changes` alanında yalnızca değişen alanların
    adları ve güvenli temsilleri tutulur (değer maskeleme servis katmanında).
  * `tenant_id` zorunludur: kiracısı bilinmeyen bir olay buraya yazılmaz
    (örneğin eşleşmeyen e-posta ile giriş denemesi teknik loga gider).
"""

from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel, UUIDModel


class AuditAction(models.TextChoices):
    CREATE = "create", "Oluşturma"
    UPDATE = "update", "Güncelleme"
    DELETE = "delete", "Silme"
    LOGIN = "login", "Giriş"
    LOGIN_FAILED = "login_failed", "Başarısız giriş"
    LOGOUT = "logout", "Çıkış"
    PERMISSION_GRANT = "permission_grant", "Yetki verildi"
    PERMISSION_REVOKE = "permission_revoke", "Yetki alındı"
    VIEW = "view", "Görüntüleme"
    DOWNLOAD = "download", "İndirme"
    EXPORT = "export", "Dışa aktarma"
    MFA_ENABLED = "mfa_enabled", "MFA açıldı"
    MFA_DISABLED = "mfa_disabled", "MFA kapatıldı"


class AuditLog(UUIDModel, TimeStampedModel):
    """
    Tek satır = tek önemli işlem. Bu tablo yalnızca eklenir; güncellenmez/silinmez.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="audit_logs",
        verbose_name="kiracı",
    )
    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_logs",
        verbose_name="kullanıcı",
    )
    action = models.CharField("işlem", max_length=30, choices=AuditAction.choices)
    table_name = models.CharField("tablo", max_length=80, blank=True)
    record_id = models.UUIDField("kayıt kimliği", null=True, blank=True)
    changes = models.JSONField("değişiklikler", default=dict, blank=True)
    request_id = models.CharField("istek kimliği", max_length=64, blank=True)
    ip_address = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        db_table = "audit_logs"
        verbose_name = "denetim kaydı"
        verbose_name_plural = "denetim kayıtları"
        indexes = [
            models.Index(fields=["tenant", "-created_at"], name="audit_tenant_created_idx"),
            models.Index(fields=["tenant", "table_name", "record_id"], name="audit_record_idx"),
            models.Index(fields=["tenant", "action"], name="audit_tenant_action_idx"),
        ]

    def __str__(self) -> str:
        created_at = timezone.localtime(self.created_at)
        return f"{self.action} {self.table_name} ({created_at:%Y-%m-%d %H:%M})"
