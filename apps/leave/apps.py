"""İzin (leave) uygulaması yapılandırması."""

from django.apps import AppConfig


class LeaveConfig(AppConfig):
    """
    İzin yönetimi (M14): türler, hakediş politikası, bakiyeler, talep ve onay akışı.

    Admin kaydı **yapılmaz** (OD19: `/admin/` kaldırıldı; yetki yalnız Persopyro
    RBAC katmanından gelir).
    """

    default_auto_field = "django.db.models.UUIDField"
    name = "apps.leave"
    label = "leave"
    verbose_name = "İzin yönetimi"
