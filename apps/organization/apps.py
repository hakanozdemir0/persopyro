"""Organizasyon uygulaması yapılandırması."""

from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    """
    Organizasyon ve çalışan temel veri modeli (M10–M13).

    Admin kaydı **yoktur** (OD19: Django Admin kaldırılmıştır; yetki kaynağı
    Persopyro RBAC katmanıdır).
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organization"
    verbose_name = "Organizasyon"
