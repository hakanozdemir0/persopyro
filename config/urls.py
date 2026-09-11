"""
Kök URL yapılandırması.

Sürümleme yol üzerinden yapılır: `/api/v1/...` (docs/API.md bölüm 1).

Django Admin yolu **YOKTUR** (OD19 — kapalı): `/admin/` yayınlanmaz ve
`django.contrib.admin` uygulama listesinde tutulmaz. Admin'in yetki kapısı
(`is_active and is_staff`) kendi RBAC katmanımızla hizalandırılamaz; paralel bir
yetkilendirme yolu bırakmamak için route tamamen kaldırılmıştır. Bu yüzden
`GET /admin/` beklenen davranışı **404**'tür. Ayrıntı: docs/SECURITY.md bölüm 3.
"""

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
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/health", health, name="health"),
    path("api/ready", readiness, name="readiness"),
]

# Manuel test arayüzü (`/test-ui/`) — YALNIZCA geliştirme/test.
#
# NEDEN KOŞULSUZ KAYIT + ÇALIŞMA ANINDA 404: gating çalışma anında yapılır
# (`TEST_UI_ENABLED`, bkz. `apps/testui/views.py` ve `apps/testui/api.py`), çünkü
# (a) davranış testlerde ölçülebilir kalır, (b) URL yapılandırması import anında
# dondurulmuş bir karara bağlı olmaz. Üretimde `TEST_UI_ENABLED` varsayılan olarak
# kapalıdır (DEBUG'a bağlı): tüm `/test-ui/*` yolları **404** döner ve hiçbir veri
# açılmaz. Güvenlik kararları bu arayüzde verilmez; servis katmanı + RLS geçerlidir.
urlpatterns += [path("test-ui/", include("apps.testui.urls"))]
