"""
Platform token servisi (OD22 — kapalı karar).

BU MODÜL **platform** bağlamında çalışır (platform servisi / CLI / göç betiği).
Kiracı isteği yolunda çağrılmaz: `persopyro_app` rolünün `platform` şemasında
hiçbir yetkisi yoktur, dolayısıyla buradaki sorgular kiracı runtime'ından
**fail-closed** hata verir (istenen davranış — OD3).

Kurallar (docs/AUTHORIZATION.md OD22, docs/DATABASE.md B.2 `platform.tokens`):

  * Ham token **saklanmaz**; yalnızca SHA-256 özeti (benzersiz) yazılır.
  * `purpose` zorunlu ve anlamlı (trim sonrası ≥ 10 karakter).
  * `scope` zorunlu: boş liste kabul edilmez, joker (`*`) yasak, tekrar yok.
  * `token_kind`: `interactive` ≤ 24 saat, `service` ≤ 90 gün; **süresiz yok**.
  * İptal `revoked_at` + `revoked_reason` ile **işaretlenir** (satır silinmez).
  * `service` token, superadmin izin kümesinin tamamıyla üretilemez
    (veritabanı tetikleyicisi de bunu reddeder — OD22/6).
  * Token **tenant RLS sınırını aşmaz**: platform rolü NOBYPASSRLS'tir ve bu
    modül kiracı bağlamı kurmaz.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.platform.constants import (
    PLATFORM_TOKEN_DEFAULT_TTL_HOURS,
    PLATFORM_TOKEN_FORBIDDEN_SCOPE_MARKERS,
    PLATFORM_TOKEN_REVOKE_REASONS,
    PLATFORM_TOKEN_TTL_MAX_HOURS,
)

PURPOSE_MIN_LENGTH = 10
TOKEN_BYTE_ENTROPY = 32  # token_urlsafe(32) → 256 bit


class PlatformTokenRuleError(ValueError):
    """OD22 kural ihlali (boş kapsam, joker, TTL dışı, anlamsız gerekçe...)."""


# --- doğrulayıcılar ---------------------------------------------------------


def validate_purpose(purpose: str) -> str:
    """`purpose` zorunlu ve anlamlı olmalıdır (OD22/3, OD4 ile aynı ilke)."""
    temiz = (purpose or "").strip()
    if len(temiz) < PURPOSE_MIN_LENGTH:
        raise PlatformTokenRuleError(
            f"purpose zorunludur ve en az {PURPOSE_MIN_LENGTH} karakter olmalıdır."
        )
    return temiz


def validate_scope(scope) -> list[str]:  # noqa: ANN001
    """Kapsam: boş olamaz, joker olamaz, boş/tekrarlı kod olamaz (OD22/6)."""
    if not scope:
        raise PlatformTokenRuleError("scope zorunludur; boş kapsamla token üretilemez.")

    temiz: list[str] = []
    for ham in scope:
        kod = str(ham or "").strip()
        if not kod:
            raise PlatformTokenRuleError("scope içinde boş değer olamaz.")
        if any(marker in kod for marker in PLATFORM_TOKEN_FORBIDDEN_SCOPE_MARKERS):
            raise PlatformTokenRuleError(f"Joker kapsam yasaktır: {kod}")
        temiz.append(kod)

    if len(set(temiz)) != len(temiz):
        raise PlatformTokenRuleError("Aynı kapsam birden fazla verilemez.")
    return temiz


def validate_ttl(token_kind: str, ttl_hours=None) -> int:  # noqa: ANN001
    """TTL tavanı: `interactive` ≤ 24 saat, `service` ≤ 90 gün (OD22/6)."""
    tavan = PLATFORM_TOKEN_TTL_MAX_HOURS.get(token_kind)
    if tavan is None:
        raise PlatformTokenRuleError(f"Bilinmeyen token türü: {token_kind}")

    if ttl_hours in (None, ""):
        # Varsayılan: türün makul ömrü (asla tavanı aşmaz, süresiz DEĞİL).
        return min(PLATFORM_TOKEN_DEFAULT_TTL_HOURS.get(token_kind, tavan), tavan)

    try:
        saat = int(ttl_hours)
    except (TypeError, ValueError) as exc:
        raise PlatformTokenRuleError("ttl_hours tam sayı olmalıdır.") from exc

    if saat <= 0:
        raise PlatformTokenRuleError("Token ömrü pozitif olmalıdır.")
    if saat > tavan:
        raise PlatformTokenRuleError(
            f"'{token_kind}' token için en fazla {tavan} saat (istenen: {saat})."
        )
    return saat


def hash_token(plaintext: str) -> str:
    """SHA-256 özeti (ham token DB'ye asla yazılmaz)."""
    return hashlib.sha256(plaintext.encode()).hexdigest()


# --- veri erişimi (ham SQL; şema-nitelemeli) --------------------------------


@transaction.atomic
def issue_platform_token(  # noqa: PLR0913
    *,
    platform_user_id,  # noqa: ANN001
    purpose: str,
    scope,  # noqa: ANN001
    token_kind: str,
    label: str = "",
    ttl_hours: int | None = None,  # noqa: ANN001
    created_by_id=None,  # noqa: ANN001
) -> tuple[uuid.UUID, str]:
    """
    Platform token'ı üretir. Döner: (token_id, düz_metin_token).

    Düz metin yalnızca bu anda döner; DB'de yalnızca özeti saklanır.
    """
    amac = validate_purpose(purpose)
    kapsam = validate_scope(scope)
    saat = validate_ttl(token_kind, ttl_hours)

    duz_metin = secrets.token_urlsafe(TOKEN_BYTE_ENTROPY)
    su_bir = timezone.now()

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO platform.tokens (
                platform_user_id, token_kind, purpose, scope, token_hash, label,
                created_at, expires_at, created_by_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [
                str(platform_user_id),
                token_kind,
                amac,
                kapsam,
                hash_token(duz_metin),
                label,
                su_bir,
                su_bir + timedelta(hours=saat),
                str(created_by_id) if created_by_id else None,
            ],
        )
        token_id = cur.fetchone()[0]
    return token_id, duz_metin


def platform_token_by_plaintext(plaintext: str):  # noqa: ANN201
    """Düz metin token'ın satırını döner (yoksa `None`). Yalnızca özet karşılaştırılır."""
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, platform_user_id, token_kind, purpose, scope, token_hash,
                   created_at, expires_at, revoked_at, revoked_reason, last_used_at
              FROM platform.tokens
             WHERE token_hash = %s
            """,
            [hash_token(plaintext)],
        )
        return cur.fetchone()


def token_is_usable(satir) -> bool:  # noqa: ANN001
    """
    Token kullanılabilir mi: iptal edilmemiş VE süresi geçmemiş.

    `satir`, `platform_token_by_plaintext` çıktısıdır (indeks 7 = expires_at,
    8 = revoked_at).
    """
    if satir is None:
        return False
    expires_at, revoked_at = satir[7], satir[8]
    if revoked_at is not None:
        return False
    return expires_at > timezone.now()


def revoke_platform_token(token_id, reason: str) -> bool:  # noqa: ANN001
    """
    Token'ı iptal eder (satır SİLİNMEZ). Zaten iptal edilmişse `False`.

    İptal tek yönlüdür; veritabanı tetikleyicisi geri almayı reddeder.
    """
    if reason not in PLATFORM_TOKEN_REVOKE_REASONS:
        raise PlatformTokenRuleError(f"Bilinmeyen iptal nedeni: {reason}")
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE platform.tokens
               SET revoked_at = now(), revoked_reason = %s
             WHERE id = %s AND revoked_at IS NULL
            RETURNING id
            """,
            [reason, str(token_id)],
        )
        return cur.fetchone() is not None
