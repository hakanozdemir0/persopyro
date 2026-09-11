"""Manuel test arayüzü — sayfa görünümleri (yalnızca geliştirme/test)."""

from __future__ import annotations

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie


def _demo_hesabi(metin: str) -> dict:
    """
    `"eposta@ornek.test (rol)"` biçimini `{"email": ..., "rol": ...}` yapar.

    Arayüzde e-posta rol metninden ayrı gösterilir; kullanıcı tüm satırı kopyalayıp
    girişte hata almaz.
    """
    eposta, _, kalan = str(metin).partition("(")
    return {"email": eposta.strip(), "rol": kalan.rstrip(")").strip()}


def _kapali_mi() -> None:
    """
    Arayüz kapalıysa **404** döner (yol yok sayılır).

    Üretimde `TEST_UI_ENABLED` varsayılan olarak kapalıdır; bu kontrol URL
    yapılandırmasına ek olarak ikinci bir kapıdır (savunma derinliği).
    """
    if not getattr(settings, "TEST_UI_ENABLED", False):
        raise Http404("test_ui_disabled")


@never_cache
@ensure_csrf_cookie
def login_page(request):  # noqa: ANN001, ANN201
    """Giriş sayfası: mevcut oturum ucu (`/api/v1/auth/login`) kullanılır."""
    _kapali_mi()
    if request.user.is_authenticated:
        return redirect("test-ui-app")
    return render(
        request,
        "testui/login.html",
        {
            "demo_users": [_demo_hesabi(k) for k in getattr(settings, "TEST_UI_DEMO_USERS", [])],
            "tanitim": "Bu arayüz yalnızca elle (manuel) test içindir.",
        },
    )


@never_cache
@ensure_csrf_cookie
def app_page(request):  # noqa: ANN001, ANN201
    """Test paneli kabuğu (sekmeler JS ile doldurulur)."""
    _kapali_mi()
    if not request.user.is_authenticated:
        return redirect("test-ui-login")
    return render(
        request,
        "testui/app.html",
        {
            "csrf_token": request.META.get("CSRF_COOKIE", ""),
            "kullanici_email": request.user.email,
        },
    )
