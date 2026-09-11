"""
Organizasyon kapsamı köprüsü (OD5 — kapalı ✅; M9'un kısmi uygulaması).

`hr.departments` (**M10**) henüz oluşturulmadığı için **alt ağaç (subtree)
çözümlemesi bilinçli olarak YAZILMADI**: `resolve_assignment_org_scope()` bu turda
her zaman `None` döner. Bu, OD5'in fail-closed kuralını şimdiden yürürlüğe koyar:

    UNIT / UNIT_DIRECT tipli rol  +  NULL / çözülemeyen kapsam
    →  İZİN ÜRETMEZ

Bu kural olmadan bir `dept_manager` atamasının izinleri kiracı geneli (tenant-wide)
gibi görünürdü — bu, OD5 §5.1/§5.10'a göre **yetki yükseltme açığıdır**.

M10 sonrası yapılacak (bu dosya **tek çözümleme noktası** kalacak):
  * `scope_org_unit_id` → `WITH RECURSIVE` ile birim alt ağacı (OD24 modeli,
    derinlik ≤ 20, döngü DB tetikleyicisiyle engellenir),
  * "kapsam aynı kiracının **aktif** birimini göstermeli" kontrolü,
  * `hr.user_roles.scope_org_unit_id` → `hr.departments(id)` FK'si.
"""

from __future__ import annotations

import uuid

from apps.rbac.models import ORG_SCOPED_ROLE_SCOPE_TYPES, RoleScopeType

ORG_SCOPED_SCOPE_TYPES = frozenset(ORG_SCOPED_ROLE_SCOPE_TYPES)


def org_scope_required(role_scope_type: str) -> bool:
    """Bu kapsam tipi için atama düzeyinde birim kapsamı **zorunlu** mu (OD5)."""
    return role_scope_type in ORG_SCOPED_SCOPE_TYPES


def resolve_assignment_org_scope(assignment) -> frozenset[uuid.UUID] | None:  # noqa: ANN001
    """
    Atamanın etkin organizasyon kapsamını (birim kimlikleri) döner.

    Dönen değer:
      * `None` → kapsam **çözülemedi** (birim tablosu yok, kapsam NULL veya geçersiz).
      * boş olmayan küme → kapsam içindeki birimler.

    **M10 öncesi:** her zaman `None` (fail-closed). M10 sonrası: alt ağaç çözümlemesi
    burada yapılacak; çağıran taraf (`permissions.py`) **değişmeyecek**.
    """
    # M10 gelene kadar birim tablosu yok: sahte çözümleme YAPILMAZ.
    return None


def assignment_grants_permissions(assignment) -> bool:  # noqa: ANN001
    """
    Atama izin üretebilir mi? (OD5 fail-closed)

    * `UNIT` / `UNIT_DIRECT` tipli roller: **kapsam çözümlenebilirse** (M10 öncesi
      her zaman `False`),
    * `TENANT` / `ASSIGNED` / `SELF` tipli roller: `True` (mevcut davranış korunur;
      nesne düzeyi kapsam/koşul kontrolleri ayrı katmanın işidir).

    NOT: Bu fonksiyon **izin üretmez**; yalnızca "izin kodu sayılabilir mi" sorusunu
    yanıtlar. Nihai karar daima `izin ∧ kapsam`dır (OD5 §5.1).
    """
    if not org_scope_required(assignment.role.scope_type):
        return True
    return resolve_assignment_org_scope(assignment) is not None


def scope_type_of(role) -> str:  # noqa: ANN001
    """Rolün kapsam tipi (yoksa güvenli varsayılan: kiracı geneli)."""
    return getattr(role, "scope_type", RoleScopeType.TENANT)
