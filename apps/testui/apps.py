"""Manuel test arayüzü uygulaması (yalnızca geliştirme/test)."""

from django.apps import AppConfig


class TestUiConfig(AppConfig):
    """
    Elle uçtan uca test için minimum web arayüzü (M15/test UI).

    * **Model içermez** → migration üretmez; mevcut şema ve migration geçmişi
      değişmez.
    * **Yalnızca geliştirme/test**: URL'ler `TEST_UI_ENABLED` kapalıyken
      kaydedilmez (üretimde yol yok → 404).
    * Güvenlik kararı **vermez**: tüm işlemler mevcut servis katmanına
      (`apps/organization/services.py`, `apps/leave/services/*`) delege edilir;
      orada `izin ∧ kapsam` ve RLS aynen geçerlidir.
    """

    default_auto_field = "django.db.models.UUIDField"
    name = "apps.testui"
    label = "testui"
    verbose_name = "Manuel test arayüzü"
