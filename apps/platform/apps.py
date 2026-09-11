from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """
    Platform (ayrı güvenlik sınırı) uygulaması.

    Not: ORM modeli **yoktur**; `platform` şemasındaki nesneler migration içinde
    ham SQL ile kurulur (gerekçe: `__init__.py` ve docs/DATABASE.md B.1).
    """

    name = "apps.platform"
    label = "platform"
    verbose_name = "Platform (ayrı güvenlik sınırı)"

    def ready(self) -> None:
        # Sinyal/model kaydı yok; bilinçli olarak boş.
        return None
