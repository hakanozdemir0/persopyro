"""
Test arayüzü (`/test-ui/`) testleri için ortak yardımcılar.

Giriş **mevcut oturum ucu** üzerinden yapılır ve yazma istekleri gerçek CSRF başlığı
taşır (varsayılan test istemcisi CSRF'i atlar; tarayıcı atlamaz). CSRF'i açık istemci
`tests/conftest.py` içindeki `csrf_client` fixture'ıdır (yeni test modüllerinde
doğrudan istek parametresi olarak kullanılır; fixture import edilmez — F811).
"""

from __future__ import annotations


def csrf(client) -> str:  # noqa: ANN001
    """Giriş sayfası CSRF çerezini üretir (tarayıcıdaki akışın aynısı)."""
    client.get("/test-ui/login/")
    return client.cookies["csrftoken"].value


def giris(client, tenant, email: str, parola: str):  # noqa: ANN001, ANN202
    """Mevcut oturum ucuyla giriş yapar (paralel kimlik yolu yoktur)."""
    return client.post(
        "/api/v1/auth/login",
        data={"tenant": tenant.slug, "email": email, "password": parola},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf(client),
    )


def post(client, yol: str, govde: dict | None = None):  # noqa: ANN001, ANN202
    """CSRF başlıklı JSON POST."""
    return client.post(
        yol,
        data=govde or {},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf(client),
    )


def get(client, yol: str):  # noqa: ANN001, ANN202
    """Arayüz okuma ucu."""
    return client.get(yol)
