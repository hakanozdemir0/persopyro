"""
API token servisleri (OD26 — kapalı karar).

Bu modül token'ın **tek üretim ve iptal yolu**dur:

  * `issue_api_token(...)` — doğrular ve üretir (kapsam, amaç, TTL tavanı)
  * `revoke_token(...)` / `revoke_tokens(...)` / `revoke_tenant_tokens(...)`
    — iptal (satır silinmez; denetim izi kalır)

Kurallar (docs/AUTHORIZATION.md OD26, docs/SECURITY.md §3.5):

  * `purpose` zorunlu, trim sonrası en az **10** karakter (boş/anlamsız reddedilir).
  * `scopes` boş olamaz; **joker (`*`) yasak**; her kod `hr.permissions`
    katalogunda bulunmalı; **`platform.*` ad alanı yasak**.
  * `scopes` **sahibinin izinlerinin ALT KÜMESİ** olmalı — token yetki üretemez.
  * TTL: `interactive` ≤ 30 gün (720 sa), `service` ≤ 90 gün (2160 sa);
    tavan aşımı **sessizce kırpılmaz**, açık doğrulama hatası verir.
  * Kapsam/amaç/tür/süre üretimden sonra **değişmez**; değişiklik = iptal + yeni token
    (veritabanı tetikleyicisi de bunu engeller).
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import ApiToken, TokenKind, TokenRevokeReason
from apps.audit.models import AuditAction
from apps.audit.services import log_action
from apps.rbac.models import Permission
from apps.rbac.services import effective_permission_codes
from apps.tenancy.context import tenant_context

WILDCARD = "*"
PLATFORM_PREFIX = "platform."
PURPOSE_MIN_LENGTH = 10


def validate_purpose(purpose: str) -> str:
    """Amaç/gerekçe: boş veya anlamsız olamaz (en az 10 karakter)."""
    temiz = (purpose or "").strip()
    if len(temiz) < PURPOSE_MIN_LENGTH:
        raise ValidationError(
            {
                "purpose": [
                    f"Amaç (purpose) zorunludur ve en az {PURPOSE_MIN_LENGTH} "
                    "karakter olmalıdır."
                ]
            }
        )
    return temiz


def normalize_scopes(scopes) -> list[str]:
    """
    Biçim kuralları: boş yok, joker yok, `platform.*` yok, tekrar yok.

    Katalog ve alt küme kontrolü `validate_scopes` içinde yapılır (veritabanı
    gerektirir).
    """
    if not scopes:
        raise ValidationError({"scopes": ["En az bir kapsam (scope) zorunludur."]})

    temiz: list[str] = []
    for ham in scopes:
        kod = str(ham or "").strip()
        if not kod:
            raise ValidationError({"scopes": ["Boş kapsam kabul edilmez."]})
        if WILDCARD in kod:
            raise ValidationError({"scopes": [f"Joker kapsam yasaktır: {kod}"]})
        if kod.lower().startswith(PLATFORM_PREFIX):
            raise ValidationError(
                {"scopes": [f"Platform kapsamı kiracı token'ında kullanılamaz: {kod}"]}
            )
        temiz.append(kod)

    if len(set(temiz)) != len(temiz):
        raise ValidationError({"scopes": ["Aynı kapsam birden fazla verilemez."]})
    return temiz


def validate_scopes(scopes, user) -> list[str]:
    """
    Kapsamları doğrular ve normalize edilmiş listeyi döner.

    İki kural: (1) her kod izin katalogunda olmalı, (2) kod **sahibinin mevcut
    izinlerinin alt kümesi** olmalı (token RBAC'i genişletemez).
    """
    temiz = normalize_scopes(scopes)

    katalog = set(
        Permission.objects.filter(code__in=temiz).values_list("code", flat=True)
    )
    bilinmeyen = sorted(set(temiz) - katalog)
    if bilinmeyen:
        raise ValidationError(
            {"scopes": [f"Bilinmeyen kapsam kodu: {', '.join(bilinmeyen)}"]}
        )

    sahip = effective_permission_codes(user)
    fazla = sorted(set(temiz) - sahip)
    if fazla:
        raise ValidationError(
            {
                "scopes": [
                    "Kapsam yalnızca sahibinin izinlerinin alt kümesi olabilir. "
                    f"Sahip olunmayan kapsam: {', '.join(fazla)}"
                ]
            }
        )
    return sorted(set(temiz))


def validate_ttl(token_kind: str, ttl_hours) -> int:  # noqa: ANN001
    """TTL tavanını dayatır; aşım sessizce kırpılmaz, hata verir."""
    from django.conf import settings

    tavan = settings.API_TOKEN_TTL_MAX_HOURS.get(token_kind)
    if tavan is None:
        raise ValidationError({"token_kind": [f"Bilinmeyen token türü: {token_kind}"]})

    if ttl_hours in (None, ""):
        return min(settings.API_TOKEN_TTL_HOURS, tavan)

    try:
        saat = int(ttl_hours)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"ttl_hours": ["Saat cinsinden tam sayı olmalıdır."]}) from exc

    if saat <= 0:
        raise ValidationError({"ttl_hours": ["Token ömrü pozitif olmalıdır."]})
    if saat > tavan:
        raise ValidationError(
            {"ttl_hours": [f"'{token_kind}' token için en fazla {tavan} saat (istenen: {saat})."]}
        )
    return saat


@transaction.atomic
def issue_api_token(  # noqa: PLR0913
    *,
    user,
    purpose: str,
    scopes,
    token_kind: str = TokenKind.INTERACTIVE,
    label: str = "",
    ttl_hours=None,  # noqa: ANN001
    request_id: str = "",
    ip_address: str | None = None,
) -> tuple[ApiToken, str]:
    """
    Token üretir (tek yol). Döner: (kayıt, düz_metin_token).

    Kapsam doğrulaması kiracı bağlamında yapılır (RBAC tabloları RLS'lidir).
    Üretim ve denetim kaydı aynı işlemde yazılır.
    """
    amac = validate_purpose(purpose)
    if token_kind not in TokenKind.values:
        raise ValidationError({"token_kind": [f"Bilinmeyen token türü: {token_kind}"]})
    saat = validate_ttl(token_kind, ttl_hours)

    with tenant_context(user.tenant_id):
        temiz_kapsam = validate_scopes(scopes, user)
        kayit, duz_metin = ApiToken.issue(
            user,
            purpose=amac,
            scopes=temiz_kapsam,
            token_kind=token_kind,
            label=label,
            ttl_hours=saat,
        )
        log_action(
            tenant_id=user.tenant_id,
            user=user,
            action=AuditAction.CREATE,
            table_name="api_tokens",
            record_id=kayit.pk,
            # Token'ın kendisi/özeti ASLA yazılmaz.
            changes={
                "event": "token_issue",
                "token_kind": token_kind,
                "scopes": temiz_kapsam,
                "purpose": amac,
                "ttl_hours": saat,
            },
            request_id=request_id,
            ip_address=ip_address,
        )
    return kayit, duz_metin


def revoke_token(
    token: ApiToken,
    reason: str,
    *,
    actor=None,  # noqa: ANN001
    request_id: str = "",
) -> bool:
    """
    Tek token'ı iptal eder (satır silinmez). Zaten iptal edilmişse `False`.

    Denetim kaydı yazılır; neden KAPALI kümeden olmalıdır.
    """
    if reason not in TokenRevokeReason.values:
        raise ValueError(f"Bilinmeyen iptal nedeni: {reason}")
    if token is None or not token.revoke(reason):
        return False

    log_action(
        tenant_id=token.tenant_id,
        user=actor or token.user,
        action=AuditAction.PERMISSION_REVOKE,
        table_name="api_tokens",
        record_id=token.pk,
        changes={"event": "token_revoke", "reason": reason},
        request_id=request_id,
    )
    return True


def revoke_tokens(
    *,
    reason: str,
    user=None,  # noqa: ANN001
    tenant_id=None,  # noqa: ANN001
    token_kind: str | None = None,
    request_id: str = "",
) -> int:
    """
    Toplu iptal (güvenlik olayı, sahip/kİracı pasifleşmesi, politika göçü).

    Kapsam verilmezse HİÇBİR ŞEY yapmaz (yanlışlıkla tüm tabloyu iptal etmemek
    için kapsam zorunludur). Döner: iptal edilen token sayısı.
    """
    if reason not in TokenRevokeReason.values:
        raise ValueError(f"Bilinmeyen iptal nedeni: {reason}")
    if user is None and tenant_id is None:
        raise ValueError("Toplu iptal için en az bir kapsam (user veya tenant_id) gerekir.")

    sorgu = ApiToken.objects.filter(revoked_at__isnull=True)
    if user is not None:
        sorgu = sorgu.filter(user=user)
    if tenant_id is not None:
        sorgu = sorgu.filter(tenant_id=tenant_id)
    if token_kind is not None:
        sorgu = sorgu.filter(token_kind=token_kind)

    simdi = timezone.now()
    return sorgu.update(revoked_at=simdi, revoked_reason=reason)


def revoke_tenant_tokens(
    tenant_id,
    *,
    reason: str = TokenRevokeReason.TENANT_DISABLED,
    request_id: str = "",
) -> int:
    """
    Kiracı askıya alındığında/iptal edildiğinde o kiracının TÜM aktif token'larını
    iptal eder (defense in depth).

    ÖNEMLİ: Güvenlik yalnızca bu işleme bağlı DEĞİLDİR. Bir token iptal edilmemiş
    olsa bile kiracı durumu istek sırasında kontrol edilir ve erişim reddedilir
    (`apps/tenancy/access.py`).
    """
    return revoke_tokens(
        reason=reason, tenant_id=tenant_id, request_id=request_id
    )


def revoke_tokens_for_inactive_user(user, *, request_id: str = "") -> int:
    """Sahibi pasif/silinmiş olduğunda token'larını iptal eder (idari temizlik)."""
    return revoke_tokens(
        reason=TokenRevokeReason.OWNER_DISABLED, user=user, request_id=request_id
    )
