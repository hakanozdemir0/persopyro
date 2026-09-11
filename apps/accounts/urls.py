"""Kimlik doğrulama uç noktaları (önek: /api/v1/auth/)."""

from django.urls import path

from apps.accounts.views import (
    SessionLoginView,
    SessionLogoutView,
    TokenLoginView,
    TokenLogoutView,
    TokenRevokeAllView,
)

urlpatterns = [
    path("login", SessionLoginView.as_view(), name="auth-login"),
    path("logout", SessionLogoutView.as_view(), name="auth-logout"),
    path("token", TokenLoginView.as_view(), name="auth-token"),
    path("token/logout", TokenLogoutView.as_view(), name="auth-token-logout"),
    path("token/revoke-all", TokenRevokeAllView.as_view(), name="auth-token-revoke-all"),
]
