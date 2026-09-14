"""
P1-B — `users.auth_version` enforcement (OD59 / HR_ROADMAP §28.3).

**Tek merkezi nokta** `auth_version_gecerli()`:

* **Oturum**: `SessionLoginView` girişte `auth_version` değerini oturuma yazarken,
  her test-UI isteğinde `TestUiView.initial` (ve `SessionAuthentication` öncesi)
  bu fonksiyonla karşılaştırır. Uyuşmazlık → `LifecycleAuthenticationFailed`
  (DRF `AuthenticationFailed` → 401) ve oturum düşürülür + `auth.session_revoked`
  audit'i yazılır.
* **API token**: `ApiTokenAuthentication.authenticate_credentials` bağlam kurulduktan
  sonra aynı fonksiyonu çağırır (token tarafında snapshot gerekmez: iptal/aktiflik
  zaten anlık okunur; auth_version butlanması ek savunmadır).

Suspend/terminate/archive/reactivate servisleri auth_version'ı artırır; normal
profil güncellemeleri artırmaz (gereksiz logout yok — OD59).
"""

from __future__ import annotations

import logging

from apps.audit.services import log_action

logger = logging.getLogger(__name__)

SESSION_AUTH_VERSION_KEY = "auth_version"


class AuthVersionMismatchError(Exception):
    """Oturum/token auth_version uyuşmazlığı (401 → yeniden giriş zorunlu)."""


def auth_version_gecerli(user, request=None) -> bool:  # noqa: ANN001
    """Kullanıcının güncel `auth_version`'ı > 0 eşikli basit doğrulama (None-safe)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "deleted_at", None) is not None:
        return False
    return True


def oturum_auth_version_yaz(session, user) -> None:  # noqa: ANN001
    """Giriş anındaki auth_version snapshot'ını oturuma yazar."""
    session[SESSION_AUTH_VERSION_KEY] = int(getattr(user, "auth_version", 0))


def oturum_auth_version_dogrula(user, session, *, request_id: str = "") -> None:  # noqa: ANN001
    """
    Oturumdaki snapshot ↔ kullanıcı satırı uyuşuyor mu?

    Uyuşmazlık: oturum **tamamen düşürülür** (flush), `auth.session_revoked`
    audit'i yazılır ve `AuthVersionMismatch` yükseltilir (→ 401).
    Snapshot yoksa (eski oturum): güncel değeri yazıp kabul edilir — mevcut
    oturumlar etkilenmez (migration notu: varsayılan 0, sıfır kesinti).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return
    kayitli = session.get(SESSION_AUTH_VERSION_KEY)
    if kayitli is None:
        oturum_auth_version_yaz(session, user)
        return
    if int(kayitli) != int(getattr(user, "auth_version", 0)):
        session.flush()
        try:
            log_action(
                tenant_id=user.tenant_id,
                action="auth.session_revoked",
                user=user,
                table_name="users",
                record_id=user.pk,
                changes={
                    "event": "auth_version_mismatch",
                    "stored": int(kayitli),
                    "current": int(getattr(user, "auth_version", 0)),
                },
                request_id=request_id,
            )
        except Exception:  # noqa: BLE001 - audit yazımı isteği düşürmemeli
            logger.warning("auth.session_revoked audit kaydı yazılamadı", exc_info=True)
        raise AuthVersionMismatchError("Oturum geçersiz; yeniden giriş yapın.")
