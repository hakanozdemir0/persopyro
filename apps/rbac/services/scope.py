"""
Organizasyon kapsamı çözümlemesi (OD5 kapalı ✅ · M10–M13 uygulandı).

Bu modül **kapsamın tek çözümleme noktasıdır** (OD24 §8: organizasyon değişiminde
önbellek geçersiz kılınır; v1'de önbellek yoktur, her çözümleme veritabanından
yapılır — bkz. `invalidate_org_scope_cache`).

Model (docs/AUTHORIZATION.md §4.4 + OD5):

| `scope_type`   | Etkin kapsam                                            |
|----------------|---------------------------------------------------------|
| `TENANT`       | kiracının tamamı                                         |
| `UNIT`         | atanan birim + **tüm alt ağaç** (OD24 recursive CTE)     |
| `UNIT_DIRECT`  | yalnızca atanan birim                                    |
| `ASSIGNED`     | yalnızca açıkça atanmış kayıtlar (nesne düzeyi; onay modülü) |
| `SELF`         | yalnızca kullanıcının kendi kaydı                        |

Fail-closed kurallar (ihlal = kapsam **üretilmez**):
  * eksik/silinmiş birim, beklenmedik döngü, kiracı uyuşmazlığı → `None`
  * **silinmiş birimin kendisi ve alt ağacı** kapsam dışıdır (kapsam sessizce
    genişlemez; restore sonrası atamanın **açık** yeniden etkinleştirilmesi gerekir)
  * silinmiş / süresi geçmiş atama, silinmiş rol, pasif kullanıcı → katkı yok

**Ağaç yetki mirası üretmez**: bu modül yalnızca *hangi kayıtlar* sorusunu yanıtlar;
izin kodu daima RBAC'ten gelir (izin ∧ kapsam).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from django.db import connection

from apps.rbac.models import ORG_SCOPED_ROLE_SCOPE_TYPES, RoleScopeType

#: OD24: en fazla organizasyon derinliği (tetikleyici + CTE sınırı).
MAX_DEPTH = 20

ORG_SCOPED_SCOPE_TYPES = frozenset(ORG_SCOPED_ROLE_SCOPE_TYPES)


def org_scope_required(role_scope_type: str) -> bool:
    """Bu kapsam tipi için atama düzeyinde birim kapsamı **zorunlu** mu (OD5)."""
    return role_scope_type in ORG_SCOPED_SCOPE_TYPES


# --- Birim ağacı çözümlemesi (recursive CTE) ---------------------------------


def resolve_department_scope(
    department_id: uuid.UUID | str | None,
    *,
    include_descendants: bool = True,
) -> frozenset[uuid.UUID] | None:
    """
    Bir birimin kapsamını çözer: kendisi (+ alt ağaç).

    Dönen değer:
      * `None` → **çözülemedi** (birim yok, silinmiş, kiracı dışı) → fail-closed.
      * boş olmayan küme → kapsamdaki birim kimlikleri (kendisi dahil).

    Kiracı bağlamı **zorunludur**: sorgu RLS altında çalışır, bu yüzden başka
    kiracının birimini "bulmak" mümkün değildir → `None` (fail-closed).
    Silinmiş birim (ve alt ağacı) kapsama **alınmaz**.
    """
    if department_id is None:
        return None

    if include_descendants:
        sql = """
            WITH RECURSIVE alt_agac AS (
                SELECT d.id, 1 AS derinlik
                  FROM hr.departments d
                 WHERE d.id = %s AND d.deleted_at IS NULL
                UNION ALL
                SELECT c.id, a.derinlik + 1
                  FROM hr.departments c
                  JOIN alt_agac a ON c.parent_id = a.id
                 WHERE a.derinlik < %s AND c.deleted_at IS NULL
            )
            SELECT id FROM alt_agac
        """
        params = [str(department_id), MAX_DEPTH]
    else:
        sql = "SELECT d.id FROM hr.departments d WHERE d.id = %s AND d.deleted_at IS NULL"
        params = [str(department_id)]

    with connection.cursor() as cur:
        cur.execute(sql, params)
        kimlikler = {satir[0] for satir in cur.fetchall()}

    return frozenset(kimlikler) or None


# --- Atama düzeyi kapsamı ----------------------------------------------------


def assignment_org_scope(assignment) -> frozenset[uuid.UUID] | None:  # noqa: ANN001
    """
    Bir atamanın etkin organizasyon kapsamı (yalnız `UNIT`/`UNIT_DIRECT` için).

    `None` → bu atama organizasyon kapsamlı **değil** (TENANT/ASSIGNED/SELF) veya
    kapsam **çözülemedi** (fail-closed). Ayrım için `org_scope_required()` kullanın.
    """
    kapsam_tipi = assignment.role.scope_type
    if kapsam_tipi == RoleScopeType.UNIT:
        return resolve_department_scope(assignment.scope_org_unit_id, include_descendants=True)
    if kapsam_tipi == RoleScopeType.UNIT_DIRECT:
        return resolve_department_scope(assignment.scope_org_unit_id, include_descendants=False)
    return None


def resolve_assignment_org_scope(assignment) -> frozenset[uuid.UUID] | None:  # noqa: ANN001
    """`assignment_org_scope` ile aynı (geriye dönük uyumlu ad)."""
    return assignment_org_scope(assignment)


def assignment_grants_permissions(assignment) -> bool:  # noqa: ANN001
    """
    Atama izin üretebilir mi? (OD5 fail-closed)

    * `UNIT` / `UNIT_DIRECT`: **kapsam çözülebilirse** (M10 sonrası: gerçek alt ağaç),
    * `TENANT` / `ASSIGNED` / `SELF`: `True` (mevcut davranış; nesne düzeyi
      kısıtlar ayrı katmanın işidir).

    Bu fonksiyon **izin üretmez**; yalnızca "izin kodu sayılabilir mi" sorusunu
    yanıtlar. Nihai karar daima `izin ∧ kapsam`dır (OD5 §5.1).
    """
    if not org_scope_required(assignment.role.scope_type):
        return True
    return assignment_org_scope(assignment) is not None


def scope_type_of(role) -> str:  # noqa: ANN001
    """Rolün kapsam tipi (yoksa güvenli varsayılan: kiracı geneli)."""
    return getattr(role, "scope_type", RoleScopeType.TENANT)


# --- Kullanıcı düzeyi etkin kapsam -------------------------------------------


@dataclass(frozen=True)
class OrgScope:
    """
    Kullanıcının **etkin** organizasyon kapsamı (etkin atamaların birleşimi).

    Birleşim **tek kiracı içindedir**; kiracılar arası birleşim yoktur (her sorgu
    zaten kiracı bağlamında ve RLS altında çalışır).
    """

    tenant_wide: bool = False
    unit_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    self_only: bool = False
    assigned_only: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.tenant_wide or self.unit_ids or self.self_only or self.assigned_only)


def user_org_scope(user) -> OrgScope:  # noqa: ANN001
    """
    Kullanıcının etkin kapsamı: **etkin** atamaların birleşimi.

    Katkı sağlamayanlar: pasif/silinmiş kullanıcı, silinmiş atama, süresi geçmiş
    atama (`expires_at <= now()`), silinmiş rol ve **çözülemeyen** org kapsamı.
    """
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return OrgScope()

    # Döngüsel içe aktarmadan kaçınmak için gecikmeli içe aktarma.
    from apps.rbac.services.permissions import active_role_assignments

    tenant_wide = False
    self_only = False
    assigned_only = False
    birimler: set[uuid.UUID] = set()

    for atama in active_role_assignments(user):
        kapsam_tipi = atama.role.scope_type
        if kapsam_tipi == RoleScopeType.TENANT:
            tenant_wide = True
        elif kapsam_tipi == RoleScopeType.SELF:
            self_only = True
        elif kapsam_tipi == RoleScopeType.ASSIGNED:
            assigned_only = True
        elif kapsam_tipi in ORG_SCOPED_SCOPE_TYPES:
            cozulen = assignment_org_scope(atama)
            if cozulen:
                birimler.update(cozulen)
            # Çözülemeyen kapsam: katkı YOK (fail-closed) — sessizce genişlemez.

    return OrgScope(
        tenant_wide=tenant_wide,
        unit_ids=frozenset(birimler),
        self_only=self_only,
        assigned_only=assigned_only,
    )


def object_in_org_scope(  # noqa: PLR0913
    user,  # noqa: ANN001
    *,
    department_id: uuid.UUID | str | None = None,
    owner_user_id: uuid.UUID | str | None = None,
    explicitly_assigned: bool = False,
) -> bool:
    """
    Nesne, kullanıcının etkin organizasyon kapsamında mı? (**izin üretmez**)

    Bu fonksiyon yalnızca *kapsam* eksenini yanıtlar; çağıran taraf izin kodunu da
    doğrulamalıdır (`izin ∧ kapsam`, OD5 §5.1). Kapsam boşsa **reddet**.
    """
    kapsam = user_org_scope(user)
    if kapsam.is_empty:
        return False
    if kapsam.tenant_wide:
        return True
    if explicitly_assigned and kapsam.assigned_only:
        return True
    if owner_user_id is not None and kapsam.self_only:
        if str(owner_user_id) == str(getattr(user, "pk", None)):
            return True
    if department_id is not None and kapsam.unit_ids:
        if uuid.UUID(str(department_id)) in kapsam.unit_ids:
            return True
    return False


# --- Önbellek sınırı (OD24 §8 / K17) ----------------------------------------


def invalidate_org_scope_cache(tenant_id: uuid.UUID | str | None = None) -> None:
    """
    Kapsam önbelleğini geçersiz kılma **sınırı**.

    v1'de kapsam sonucu önbelleğe **alınmaz** (sahte önbellek kurulmaz): her
    çözümleme veritabanından yapılır, bu yüzden geçersiz kılınacak bir şey yoktur.
    Organizasyonu değiştiren servisler (`move_department`, `set_primary_manager`)
    **bu fonksiyonu çağırır**; ileride eklenecek önbellek (K17) yalnızca burayı
    doldurarak devreye girer — çağıranlar değişmez.
    """
    return None
