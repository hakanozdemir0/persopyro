"""
Kök URL yapılandırması.

Sürümleme yol üzerinden yapılır: `/api/v1/...` (docs/API.md bölüm 1).
"""

from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    """Canlılık kontrolü (veritabanına dokunmaz)."""
    return JsonResponse({"status": "ok"})


def readiness(_request):
    """Hazır olma kontrolü: veritabanı erişilebilir mi?"""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception:  # noqa: BLE001 - sağlık kontrolü her hatayı bildirir
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/health", health, name="health"),
    path("api/ready", readiness, name="readiness"),
]
