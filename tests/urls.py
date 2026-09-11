"""Testlerde kullanılan URL yapılandırması (üretim URL'leri + sondalar).

Sağlık uçları ÜRETİM görünümleridir (`config.urls.health/readiness`); burada
birebir aynı görünümler bağlanır ki HTTP sözleşmesi (200/200) gerçek istek
üzerinden sınanabilsin.
"""

from django.urls import include, path

from config.urls import health, readiness
from tests.views import (
    IzinliIzinsizSonda,
    IzinliSonda,
    IzinsizTanimsizSonda,
    KorumaliSonda,
    baglam_sondasi,
)

urlpatterns = [
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/health", health, name="health"),
    path("api/ready", readiness, name="readiness"),
    path("_probe/baglam", baglam_sondasi),
    path("_probe/korumali", KorumaliSonda.as_view()),
    path("_probe/izinli", IzinliSonda.as_view()),
    path("_probe/izinli-leave", IzinliIzinsizSonda.as_view()),
    path("_probe/izin-tanimsiz", IzinsizTanimsizSonda.as_view()),
]
