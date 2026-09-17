"""
Yetki çözümleme servisi.

Kural: **varsayılan reddet.** Açık izin yoksa işlem yapılmaz
(docs/DEVELOPMENT_RULES.md, docs/AUTHORIZATION.md).

Soft-delete (bulgu F7): yetki çözümlemesinde kullanılan **her sorgu** `deleted_at IS
NULL` koşulunu **açıkça** uygular — silinmiş bir kayıt authorization sonucuna katkı
sağlamaz. Bu filtre gizli bir manager davranışına bırakılmaz (davranış görünmez
olmamalıdır): koşul sorgunun içinde, denetlenebilir ve test edilebilir biçimde durur.

Silinmiş kayıtlar:
  * silinmiş `Role` yetki vermez,
  * silinmiş `UserRole` (atama) yetki vermez,
  * silinmiş `allow` istisnası izin eklemez,
  * silinmiş `deny` istisnası **aktif karar sayılmaz** (yalnızca etkin kayıtlar
    değerlendirilir); etkin `deny` yine `allow`'u ezer,
  * pasif/silinmiş kullanıcı için izin hesaplanmaz.

NOT (`RolePermission`): rol-izin bağı bir **bağ tablosudur** ve şemada `deleted_at`
kolonu **yoktur** (ölçüm: `hr.role_permissions` kolonları `id, created_at,
updated_at, permission_id, role_id, tenant_id`); kayıt fiziksel silinir. Silinen bağ
zaten hiçbir satır bırakmadığı için yetki veremez — bu yüzden burada filtrelenecek
bir `deleted_at` yoktur (OD21 bağ tabloları için soft-delete öngörmez).
"""

import contextvars

from django.db.models import Q
from django.utils import timezone

from apps.rbac.models import (
    PermissionEffect,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from apps.rbac.services.scope import assignment_grants_permissions
from apps.tenancy.access import tenant_access_allowed


def active_role_assignments(user) -> list:
    """
    Kullanıcının **geçerli** rol atamaları (silinmemiş, süresi geçmemiş).

    Kapsam çözümlemesi (`apps.rbac.services.scope`) ile paylaşılan tek sorgu:
    salt `deleted_at`/`expires_at` süzgeci uygular; rolün kapsam tipine göre karar
    **çağıran** tarafın işidir.
    """
    now = timezone.now()
    return list(
        UserRole.objects.filter(user=user, deleted_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("role")
    )


def _active_role_ids(user) -> list:
    """
    Kullanıcının **izin üretebilen** rol kimlikleri.

    Dışarıda bırakılanlar: silinmiş `UserRole` (atama), silinmiş `Role`, süresi
    geçmiş atama **ve** organizasyon kapsamı çözülemeyen `UNIT`/`UNIT_DIRECT`
    atamaları (OD5 fail-closed: birim yöneticisinin izinleri kapsam çözülmeden
    kiracı geneli sayılamaz).
    """
    return [
        atama.role_id
        for atama in active_role_assignments(user)
        if atama.role.deleted_at is None and assignment_grants_permissions(atama)
    ]


def _role_permission_codes(role_ids) -> set[str]:  # noqa: ANN001
    if not role_ids:
        return set()
    return set(
        # `RolePermission` bir bağ tablosudur ve `deleted_at` kolonu yoktur; kayıt
        # fiziksel silinir, dolayısıyla var olan satır yetki verir (silinmiş satır yok).
        RolePermission.objects.filter(role_id__in=role_ids).values_list(
            "permission__code", flat=True
        )
    )


def declared_permission_codes(user) -> set[str]:
    """
    Kullanıcının rollerinden gelen **tüm** izin kodları — **kapsam filtresi YOK**.

    Bu, "permission code ownership" görünümüdür (yönetim/teşhis ekranları için).
    **Yetkilendirme kaynağı DEĞİLDİR**: `UNIT`/`UNIT_DIRECT` bir rolün kodları burada
    görünse bile, organizasyon kapsamı çözülmeden o kodlarla işlem yapılamaz. Karar
    için daima `effective_permission_codes` / `authorized_permission_codes` kullanılır.
    """
    if user is None or not user.is_authenticated or not user.is_active:
        return set()
    rol_ids = [
        atama.role_id
        for atama in active_role_assignments(user)
        if atama.role.deleted_at is None
    ]
    return _role_permission_codes(rol_ids)


# --- Request-scope permission cache (P1-B performans; OD5 anlamları değişmez) ---
#
# Aynı HTTP request/işlem içinde aynı kullanıcı için `effective_permission_codes`
# sonucunu DB'ye tekrar tekrar göndermemek için **contextvars tabanlı** önbellek.
#
# * Global/process cache DEĞİLDİR: contextvars async/task-safe'tir; farklı user/
#   tenant arasında paylaşım sıfırdır (her task kendi bağlamındadır).
# * Tenant/izin source-of-truth değişmez: rol/override yazım yolları
#   `invalidate_permission_cache()` çağırır; istek dışı süreçlerde önbellek
#   doğal olarak boştur (request başına yaşar).
_PERMISSION_CACHE_VAR: contextvars.ContextVar[dict[int, frozenset[str]] | None] = (
    contextvars.ContextVar("permission_cache", default=None)
)

#: Yalnız HTTP istek akışında (middleware) etkin olur. Test/shell bağlamlarında
#: önbellek devre dışıdır → doğrudan `effective_permission_codes` çağrıları daima
#: DB'den okur (mevcut davranış değişmez; queryset.update() gibi model-sinyali
#: yakalanamayan yazımlar stale döndüremez).
_REQUEST_SCOPE_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "permission_cache_active", default=False
)


def invalidate_permission_cache() -> None:
    """Rol/override değişiminde (isteğe bağlı) önbelleği sıfırlar."""
    _PERMISSION_CACHE_VAR.set(None)


def effective_permission_codes(user) -> set[str]:
    """
    Kullanıcının **kullanılabilir** (etkin) izin kodları.

    (rollerin izinleri) + (allow istisnaları) - (deny istisnaları)

    Kapsam (OD5): `UNIT`/`UNIT_DIRECT` tipli atamalar kapsamı çözülemezse izin
    üretmez (`apps/rbac/services/scope.py`); `TENANT`/`SELF`/`ASSIGNED` tiplerinde
    mevcut davranış korunur.

    Pasif veya silinmiş kullanıcı için **boş küme** döner (fail-closed): kullanıcı
    kaydı da yetki çözümlemesinin girdisidir ve silinmiş kayıt katkı sağlamaz.

    Performans (P1-B ölçümü): aynı bağlamda aynı kullanıcı için sonuç
    contextvars tabanlı önbellekten döner (request-scope; user/tenant izolasyonu
    korunur; global cache yok).
    """
    if user is None or not user.is_authenticated or not user.is_active:
        return set()

    # İstek akışı dışında (test/shell/job) önbellek devre dışıdır: daima DB'den.
    if not _REQUEST_SCOPE_ACTIVE.get():
        role_codes = _role_permission_codes(_active_role_ids(user))
        overrides = UserPermissionOverride.objects.filter(
            user=user, deleted_at__isnull=True
        ).values_list("permission__code", "effect")
        allowed = {code for code, effect in overrides if effect == PermissionEffect.ALLOW}
        denied = {code for code, effect in overrides if effect == PermissionEffect.DENY}
        return (role_codes | allowed) - denied

    cache = _PERMISSION_CACHE_VAR.get()
    if cache is not None:
        cozulmus = cache.get(user.pk)
        if cozulmus is not None:
            return set(cozulmus)

    role_codes = _role_permission_codes(_active_role_ids(user))
    overrides = UserPermissionOverride.objects.filter(
        user=user, deleted_at__isnull=True
    ).values_list("permission__code", "effect")
    allowed = {code for code, effect in overrides if effect == PermissionEffect.ALLOW}
    denied = {code for code, effect in overrides if effect == PermissionEffect.DENY}
    sonuc = (role_codes | allowed) - denied
    if cache is None:
        cache = {}
        _PERMISSION_CACHE_VAR.set(cache)
    cache[user.pk] = frozenset(sonuc)
    return sonuc


def set_request_scope_permission_cache(active: bool) -> None:
    """Middleware: istek akışında önbelleği etkinleştirir/kapatır."""
    _REQUEST_SCOPE_ACTIVE.set(active)


def user_has_permission(user, code: str) -> bool:
    """Tek izin kontrolü. `deny` istisnası varsa false döner."""
    if user is None or not user.is_authenticated:
        return False
    return code in effective_permission_codes(user)


def user_has_any_permission(user) -> bool:
    """Kullanıcının en az bir izni var mı (yönetim paneli kapısı için)."""
    return bool(effective_permission_codes(user))


def authorized_permission_codes(request) -> set[str]:
    """
    İstek düzeyinde **TEK MERKEZİ** yetki kararı (F8 + F11).

        etkin izinler = kullanıcının izinleri ∩ token kapsamı

    * Token ile doğrulanmış istekte (`request.auth` bir API token'ı) kesişim
      uygulanır. Böylece token:
        - sahibinin sahip olmadığı bir yetkiyi **taşıyamaz**,
        - sonradan verilen izni **kazanmaz**,
        - kullanıcıdan izin geri alınırsa o izni **kullanamaz**.
    * Oturum (session) ile doğrulanmış istekte `request.auth` `None`'dır ve
      kullanıcının izinleri aynen geçerlidir (mevcut davranış korunur).
    * Fail-closed ek kontroller: kullanıcı aktif mi, kiracı erişilebilir mi
      (`active`/`trial`; aksi halde hiçbir izin yoktur).

    Silinmiş RBAC kayıtları `effective_permission_codes` içinde zaten elenir (F7).
    """
    kullanici = getattr(request, "user", None)
    if kullanici is None or not kullanici.is_authenticated or not kullanici.is_active:
        return set()

    if not tenant_access_allowed(getattr(kullanici, "tenant_id", None)):
        return set()

    # RBAC tabloları RLS ile korunur: yetki çözümlemesi kiracı bağlamında yapılır.
    # Bağlam istek hattında zaten kuruluysa iç içe kullanım zararsızdır (önceki
    # değer çıkışta geri yüklenir); token ile gelen isteklerde ise zorunludur.
    from apps.tenancy.context import tenant_context

    with tenant_context(getattr(kullanici, "tenant_id", None)):
        kodlar = effective_permission_codes(kullanici)

        kapsam = getattr(getattr(request, "auth", None), "scopes", None)
        if isinstance(kapsam, (list, tuple, set)):
            return kodlar & {str(kod).strip() for kod in kapsam}
        return kodlar
