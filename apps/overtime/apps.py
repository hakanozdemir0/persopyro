"""Fazla mesai (overtime) uygulaması yapılandırması."""

from django.apps import AppConfig


class OvertimeConfig(AppConfig):
    """
    Fazla mesai — P5a V1: **talep + onay temeli** (kural/multiplier/ücret YOK).

    Yol haritası kararları (docs/HR_ROADMAP.md §11 + P5a):

      * Modül **kendi uygulamasında** yaşar (`apps/overtime`; §"Modül → uygulama"
        tablosu overtime → `apps/overtime`), çünkü attendance/leave/payroll sınırları
        ayrı kalmalıdır. `apps.leave` içine gömülmez.
      * Bu dilimde yalnız **talep + onay** akışı kurulur: `hr.overtime_requests`.
        Katsayı dilimleri (`hr.overtime_rules` / `hr.overtime_entries`), ücret
        karşılığı (P5b) ve izin karşılığı (TIME_OFF) **ileriki fazdır** ve bu turda
        üretilmez; şema bu alanlara kapalı değildir (roadmap uyumu).
      * Admin kaydı **yapılmaz** (OD19: `/admin/` kaldırıldı; yetki yalnız Persopyro
        RBAC katmanından gelir).
    """

    default_auto_field = "django.db.models.UUIDField"
    name = "apps.overtime"
    label = "overtime"
    verbose_name = "Fazla mesai"
