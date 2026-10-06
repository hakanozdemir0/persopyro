"""Manuel test arayüzü — sayfa görünümleri (yalnızca geliştirme/test)."""

from __future__ import annotations

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie


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


@never_cache
@ensure_csrf_cookie
def profile_page(request):  # noqa: ANN001, ANN201
    """Kişisel profil ve parola işlemleri sayfası."""
    _kapali_mi()
    if not request.user.is_authenticated:
        return redirect("test-ui-login")
    return render(request, "testui/profile.html", {"kullanici_email": request.user.email})


@never_cache
@ensure_csrf_cookie
def password_reset_page(request):  # noqa: ANN001, ANN201
    """Email bağlantısından parola yenileme sayfası."""
    _kapali_mi()
    return render(request, "testui/password_reset.html")
