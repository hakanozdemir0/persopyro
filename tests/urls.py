"""Testlerde kullanılan URL yapılandırması (üretim URL'leri + sondalar)."""

from django.urls import include, path

from tests.views import (
    IzinliSonda,
    IzinsizTanimsizSonda,
    KorumaliSonda,
    baglam_sondasi,
)

urlpatterns = [
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("_probe/baglam", baglam_sondasi),
    path("_probe/korumali", KorumaliSonda.as_view()),
    path("_probe/izinli", IzinliSonda.as_view()),
    path("_probe/izin-tanimsiz", IzinsizTanimsizSonda.as_view()),
]
