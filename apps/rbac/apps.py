from django.apps import AppConfig


class RbacConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rbac"
    verbose_name = "Yetkilendirme (RBAC)"

    def ready(self) -> None:
        # P1-B performans: RBAC yazımlarında request-scope izin önbelleğini
        # geçersiz kılan save/delete sarmalayıcılarını bağla.
        from apps.rbac.services import _cache_invalidation  # noqa: F401, PLC0415
