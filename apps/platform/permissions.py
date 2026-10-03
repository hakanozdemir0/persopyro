"""
Platform authorization servisi (M8) — **tek merkezi çözümleyici** (OD3 kapalı).

Bu modül platform bağlamının yetki kararını verir. Kiracı RBAC kod yolundan
**tamamen ayrıdır** (bilinçli):

  * Django'nun yetki sistemi (`auth_permission`, `auth_group`, `is_staff`,
    `is_superuser`) **kullanılmaz** (OD19 kapalı; `/admin/` yok).
  * Kiracı RBAC servisleri (`apps.rbac.services.*`) ve kiracı tabloları
    (`hr.roles`, `hr.user_roles`, `hr.user_permission_overrides`) **kullanılmaz**;
    platform kullanıcıları kiracı verisi üzerinde yetki **kazanmaz** (OD1/OD3:
    kiracı verisi yalnızca erişim talebi/onayı + denetim kaydıyla açılır).
  * **İzin devralma (inheritance) YOKTUR** (AUTHORIZATION.md §4.3): hiyerarşi
    yalnızca yönetim ilişkisidir; çözümleyici yalnızca **açık** rol-izin bağlarını
    okur.
  * **Fail-closed:** karar verilemiyorsa (kullanıcı pasif, atama süresi geçmiş, rol
    silinmiş, kod katalogda yok) sonuç **izin yok**tur.

Etkin izin kümesi (tek sorgu, `ETKIN_IZIN_SQL`):

    platform.users (aktif, silinmemiş)
      × platform.user_roles (aktif, silinmemiş, süresi geçmemiş)
      × platform.roles (aktif, silinmemiş)
      × platform.role_permissions (aktif, silinmemiş)
      × hr.permissions (yalnızca `platform.*` kodları)
"""

from __future__ import annotations

import uuid

from django.db import connections, transaction
from django.utils import timezone

from apps.platform.constants import (
    PLATFORM_PERMISSION_PREFIX,
    PLATFORM_ROLE_ASSIGN_PERMISSION,
    PLATFORM_ROLE_CODES,
    PLATFORM_ROLE_RANK,
)
from apps.platform.services import platform_token_by_plaintext, token_is_usable


class PlatformAuthorizationError(PermissionError):
    """Platform yetki kuralı ihlali (fail-closed; çağıran işlemi durdurur)."""


# --- tek çözümleyici sorgusu -------------------------------------------------

# NOT: `platform.*` süzgeci bilinçlidir — ortak katalogda (`hr.permissions`) kiracı
# alan adları da vardır; platform rolü yanlışlıkla kiracı izni bağlasa bile bu
# çözümleyici o kodu **dikkate almaz** (platform yetkisi platform ad alanındadır).
ETKIN_IZIN_SQL = """
SELECT DISTINCT p.code
  FROM platform.users u
  JOIN platform.user_roles ur ON ur.platform_user_id = u.id
  JOIN platform.roles r ON r.id = ur.role_id
  JOIN platform.role_permissions rp ON rp.role_id = r.id
  JOIN hr.permissions p ON p.id = rp.permission_id
 WHERE u.id = %s
   AND u.deleted_at IS NULL
   AND u.status = 'active'
   AND u.is_active
   AND ur.deleted_at IS NULL
   AND (ur.expires_at IS NULL OR ur.expires_at > now())
   AND r.deleted_at IS NULL
   AND rp.deleted_at IS NULL
   AND p.code LIKE %s
"""

AKTIF_ROL_SQL = """
SELECT r.code
  FROM platform.user_roles ur
  JOIN platform.roles r ON r.id = ur.role_id
 WHERE ur.platform_user_id = %s
   AND ur.deleted_at IS NULL
   AND (ur.expires_at IS NULL OR ur.expires_at > now())
   AND r.deleted_at IS NULL
 ORDER BY r.code
"""

ROL_IZIN_SQL = """
SELECT DISTINCT p.code
  FROM platform.roles r
  JOIN platform.role_permissions rp ON rp.role_id = r.id
  JOIN hr.permissions p ON p.id = rp.permission_id
 WHERE r.code = %s AND r.deleted_at IS NULL AND rp.deleted_at IS NULL
   AND p.code LIKE %s
 ORDER BY p.code
"""


def _kodlar(cur, sql: str, params: list) -> frozenset[str]:
    cur.execute(sql, params)
    return frozenset(satir[0] for satir in cur.fetchall())


# --- kullanıcı durumu --------------------------------------------------------


def platform_user_row(platform_user_id) -> tuple | None:  # noqa: ANN001
    """`(id, public_id, email, status, is_active, deleted_at)` veya `None`."""
    with connections["platform"].cursor() as cur:
        cur.execute(
            "SELECT id, public_id, email, status, is_active, deleted_at"
            " FROM platform.users WHERE id = %s",
            [str(platform_user_id)],
        )
        return cur.fetchone()


def platform_user_is_active(platform_user_id) -> bool:  # noqa: ANN001
    """Aktif mi: kayıt var, `status='active'`, `is_active`, silinmemiş."""
    satir = platform_user_row(platform_user_id)
    if satir is None:
        return False
    return satir[5] is None and satir[3] == "active" and bool(satir[4])


def platform_user_can_authenticate(platform_user_id) -> bool:  # noqa: ANN001
    """
    Kimlik doğrulama kapısı (pasif/silinmiş platform kullanıcısı giriş yapamaz).

    Bu turda platform **login uç noktası yoktur**; kapı servis katmanında dayatılır
    (uç nokta eklendiğinde tek çağrı noktası burasıdır).
    """
    return platform_user_is_active(platform_user_id)


# --- izin çözümleme ----------------------------------------------------------


def effective_permissions(platform_user_id) -> frozenset[str]:  # noqa: ANN001
    """Etkin platform izinleri (aktif kullanıcı değilse **boş küme**)."""
    if not platform_user_is_active(platform_user_id):
        return frozenset()
    with connections["platform"].cursor() as cur:
        return _kodlar(
            cur,
            ETKIN_IZIN_SQL,
            [str(platform_user_id), f"{PLATFORM_PERMISSION_PREFIX}%"],
        )


def active_roles(platform_user_id) -> list[str]:  # noqa: ANN001
    """Kullanıcının **etkin** platform rolleri (kapalı kümeden)."""
    with connections["platform"].cursor() as cur:
        cur.execute(AKTIF_ROL_SQL, [str(platform_user_id)])
        return [satir[0] for satir in cur.fetchall()]


def role_permissions(role_code: str) -> frozenset[str]:
    """Rolün **tanımdan** gelen izin kümesi (DB; devralma yok)."""
    if role_code not in PLATFORM_ROLE_CODES:
        raise PlatformAuthorizationError(f"Bilinmeyen platform rolü: {role_code}")
    with connections["platform"].cursor() as cur:
        return _kodlar(cur, ROL_IZIN_SQL, [role_code, f"{PLATFORM_PERMISSION_PREFIX}%"])


def has_permission(platform_user_id, code: str) -> bool:  # noqa: ANN001
    return code in effective_permissions(platform_user_id)


def require_permission(platform_user_id, code: str) -> None:  # noqa: ANN001
    """İzin yoksa `PlatformAuthorizationError` (fail-closed)."""
    if not has_permission(platform_user_id, code):
        raise PlatformAuthorizationError(
            f"Platform izni yok: {code} (kullanıcı: {platform_user_id})"
        )


def authorize(platform_user_id, code: str) -> bool:  # noqa: ANN001
    """Uç nokta/servis katmanı için tek satırlık kapı (bool döner)."""
    return has_permission(platform_user_id, code)


# --- rol atama (self-elevation koruması) ------------------------------------


def _aktor_engeli(actor_id, role_code: str) -> str | None:  # noqa: ANN001
    """
    Aktör **kendi durumu** nedeniyle bu rolü atayabilir mi? Engel varsa gerekçe.

    Kurallar (docs/AUTHORIZATION.md §4.3 + §6.2A):
      * rol kapalı kümeden olmalı,
      * aktör **aktif** olmalı,
      * aktör `platform.role.assign` yetkisine sahip olmalı (yalnız SUPERADMIN),
      * aktör, atayacağı rolün **tüm** izinlerine sahip olmalı (sahip olmadığı
        yetkiyi dağıtamaz) → `platform_admin` kendini/kimseyi superadmin yapamaz,
      * yönetim hiyerarşisi: aktörün en yüksek rolü, atanacak rolden aşağı olamaz.
    """
    if role_code not in PLATFORM_ROLE_CODES:
        return f"Bilinmeyen platform rolü: {role_code}"

    if not platform_user_is_active(actor_id):
        return "İşlemi yapan platform kullanıcısı aktif değil."

    aktor_izinleri = effective_permissions(actor_id)
    if PLATFORM_ROLE_ASSIGN_PERMISSION not in aktor_izinleri:
        return f"Bu işlem için '{PLATFORM_ROLE_ASSIGN_PERMISSION}' izni gerekir."

    eksik = role_permissions(role_code) - aktor_izinleri
    if eksik:
        return (
            "Aktör, atayacağı rolün tüm izinlerine sahip değil (sahip olmadığı yetki "
            "dağıtılamaz): " + ", ".join(sorted(eksik))
        )

    aktor_rolleri = active_roles(actor_id)
    if not aktor_rolleri:
        return "Aktörün etkin platform rolü yok."
    if max(PLATFORM_ROLE_RANK[rol] for rol in aktor_rolleri) < PLATFORM_ROLE_RANK[role_code]:
        return "Yönetim hiyerarşisi: üst rol olmadan bu rol atanamaz."

    return None


def _atama_engeli(actor_id, target_user_id, role_code) -> str | None:  # noqa: ANN001
    """Aktör + hedef birlikte değerlendirilir (self-elevation dahil)."""
    if str(actor_id) == str(target_user_id):
        return "Kullanıcı kendi rolünü atayamaz/değiştiremez (self-elevation yasağı)."

    engel = _aktor_engeli(actor_id, role_code)
    if engel is not None:
        return engel

    if not platform_user_is_active(target_user_id):
        return "Hedef platform kullanıcısı aktif değil."

    return None


def can_assign_role(actor_id, role_code: str) -> tuple[bool, str]:  # noqa: ANN001
    """Aktör bu rolü **dağıtabilir mi**: `(izin, gerekçe)` (hedeften bağımsız ön kontrol)."""
    engel = _aktor_engeli(actor_id, role_code)
    return (False, engel) if engel else (True, "Atanabilir.")


@transaction.atomic(using="platform")
def assign_role(  # noqa: ANN001
    *,
    actor_id,
    target_user_id,
    role_code: str,
    expires_at=None,
) -> uuid.UUID:
    """
    Hedef platform kullanıcısına rol atar; atamayı `granted_by_id` ile kayıtlar.

    Kendi rolünü atama, sahip olmadığı izni dağıtma ve çift atama **reddedilir**.
    Soft-delete edilmiş atama satırı bulunursa otomatik canlandırma yapılmaz
    (açık geri alma işlemi gerekir — OD21/K25).
    """
    engel = _atama_engeli(actor_id, target_user_id, role_code)
    if engel is not None:
        raise PlatformAuthorizationError(engel)

    if expires_at is not None and expires_at <= timezone.now():
        raise PlatformAuthorizationError("expires_at gelecekte olmalıdır.")

    with connections["platform"].cursor() as cur:
        cur.execute("SELECT id FROM platform.roles WHERE code = %s", [role_code])
        rol_id = cur.fetchone()[0]

        cur.execute(
            "SELECT id, deleted_at FROM platform.user_roles"
            " WHERE platform_user_id = %s AND role_id = %s",
            [str(target_user_id), rol_id],
        )
        mevcut = cur.fetchone()
        if mevcut is not None:
            if mevcut[1] is None:
                raise PlatformAuthorizationError(
                    f"Kullanıcı bu role zaten sahip: {role_code}"
                )
            raise PlatformAuthorizationError(
                f"Platform rol ataması soft-delete edilmiş: {role_code}; otomatik geri "
                "alınmaz (açık geri alma işlemi gerekir)."
            )

        cur.execute(
            "INSERT INTO platform.user_roles"
            " (platform_user_id, role_id, granted_by_id, granted_at, expires_at)"
            " VALUES (%s, %s, %s, now(), %s) RETURNING id",
            [str(target_user_id), rol_id, str(actor_id), expires_at],
        )
        return cur.fetchone()[0]


@transaction.atomic(using="platform")
def revoke_role(*, actor_id, target_user_id, role_code: str) -> bool:  # noqa: ANN001
    """
    Rol atamasını **soft-delete** ile kaldırır (satır kalır → denetim izi).

    Kendi rolünü kaldırma, yetkisiz kaldırma ve hiyerarşi ihlali reddedilir.
    """
    if role_code not in PLATFORM_ROLE_CODES:
        raise PlatformAuthorizationError(f"Bilinmeyen platform rolü: {role_code}")
    if str(actor_id) == str(target_user_id):
        raise PlatformAuthorizationError(
            "Kullanıcı kendi rol atamasını değiştiremez (self-elevation yasağı)."
        )
    if not platform_user_is_active(actor_id):
        raise PlatformAuthorizationError("İşlemi yapan platform kullanıcısı aktif değil.")
    if PLATFORM_ROLE_ASSIGN_PERMISSION not in effective_permissions(actor_id):
        raise PlatformAuthorizationError(
            f"Bu işlem için '{PLATFORM_ROLE_ASSIGN_PERMISSION}' izni gerekir."
        )
    eksik = role_permissions(role_code) - effective_permissions(actor_id)
    if eksik:
        raise PlatformAuthorizationError(
            "Aktör, kaldıracağı rolün tüm izinlerine sahip değil: " + ", ".join(sorted(eksik))
        )

    with connections["platform"].cursor() as cur:
        cur.execute(
            """
            UPDATE platform.user_roles ur
               SET deleted_at = now()
              FROM platform.roles r
             WHERE ur.role_id = r.id AND r.code = %s
               AND ur.platform_user_id = %s AND ur.deleted_at IS NULL
            RETURNING ur.id
            """,
            [role_code, str(target_user_id)],
        )
        return cur.fetchone() is not None


# --- M9: bootstrap (aktörsüz sistem operasyonu) ------------------------------


@transaction.atomic(using="platform")
def bootstrap_assign_role(*, target_user_id, role_code: str, reason: str) -> uuid.UUID:
    """
    **Yalnızca M9 bootstrap** için: aktörsüz rol ataması (`granted_by_id = NULL`).

    Neden ayrı fonksiyon: ilk SUPERADMIN'i atayacak bir aktör henüz yoktur. Bu yol
    **yalnız** `create_platform_superadmin` yönetim komutundan çağrılır ve gerekçe
    zorunludur; kullanıcı tarafından çağrılabilen bir API/uç nokta DEĞİLDİR.
    Çift atama ve soft-delete edilmiş atama reddedilir.
    """
    if role_code not in PLATFORM_ROLE_CODES:
        raise PlatformAuthorizationError(f"Bilinmeyen platform rolü: {role_code}")
    if len((reason or "").strip()) < 10:
        raise PlatformAuthorizationError("Bootstrap gerekçesi zorunludur (≥10 karakter).")
    if not platform_user_is_active(target_user_id):
        raise PlatformAuthorizationError("Hedef platform kullanıcısı aktif değil.")

    with connections["platform"].cursor() as cur:
        cur.execute("SELECT id FROM platform.roles WHERE code = %s", [role_code])
        satir = cur.fetchone()
        if satir is None:
            raise PlatformAuthorizationError(f"Platform rolü tanımlı değil: {role_code}")
        rol_id = satir[0]

        cur.execute(
            "SELECT id, deleted_at FROM platform.user_roles"
            " WHERE platform_user_id = %s AND role_id = %s",
            [str(target_user_id), rol_id],
        )
        mevcut = cur.fetchone()
        if mevcut is not None:
            if mevcut[1] is None:
                raise PlatformAuthorizationError(f"Kullanıcı bu role zaten sahip: {role_code}")
            raise PlatformAuthorizationError(
                f"Platform rol ataması soft-delete edilmiş: {role_code}; otomatik geri "
                "alınmaz (açık geri alma işlemi gerekir)."
            )

        cur.execute(
            "INSERT INTO platform.user_roles"
            " (platform_user_id, role_id, granted_by_id, granted_at)"
            " VALUES (%s, %s, NULL, now()) RETURNING id",
            [str(target_user_id), rol_id],
        )
        return cur.fetchone()[0]


# --- token + kullanıcı durumu (tek kapı) -------------------------------------


def authenticate_platform_token(plaintext: str) -> dict | None:
    """
    Token ile platform oturumu açılabilir mi? Açılırsa bağlamı döner, yoksa `None`.

    Fail-closed: token yok/iptal/süresi geçmiş **veya sahibi pasif/silinmiş** ise
    sonuç `None`'dur. Pasif platform kullanıcısının token'ı **çalışmaz**
    (kullanıcı durumu token'dan bağımsız olarak yetkiyi keser).
    """
    satir = platform_token_by_plaintext(plaintext)
    if satir is None or not token_is_usable(satir):
        return None

    kullanici_id = satir[1]
    if not platform_user_can_authenticate(kullanici_id):
        return None

    return {
        "platform_user_id": kullanici_id,
        "token_id": satir[0],
        "roles": tuple(active_roles(kullanici_id)),
        "permissions": effective_permissions(kullanici_id),
    }
