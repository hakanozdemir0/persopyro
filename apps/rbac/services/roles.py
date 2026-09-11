"""
Rol/izin üretim servisleri.

Kapsam: yeni bir kiracı oluşturulduğunda varsayılan rollerin ve izin
eşleşmelerinin üretilmesi. Nesne seviyesi kapsam (kendi kaydı / kendi ekibi)
organizasyon modülüyle birlikte eklenecektir.

Soft-delete (OD21 kararı, docs/AUTHORIZATION.md): silinmiş bir kayıt **sessizce**
yeniden etkinleştirilmez; geri alma açık `*.restore` izni + denetim kaydı gerektirir.
Bu yüzden burada `get_or_create` kullanılmaz: silinmiş varsayılan rol bulunursa
otomatik restore **edilmez**, kopya **üretilmez**, durum açık bir hatayla bildirilir.
"""

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.rbac.models import (
    ORG_SCOPED_ROLE_SCOPE_TYPES,
    Permission,
    Role,
    RolePermission,
    UserRole,
)
from apps.rbac.permission_catalog import DEFAULT_ROLE_TEMPLATES
from apps.rbac.services.permissions import effective_permission_codes


class MissingPermissionError(RuntimeError):
    """Şablonda tanımlı bir izin kodu veritabanında yoksa yükseltilir."""


class RoleAssignmentError(PermissionError):
    """
    Rol ataması reddedildi (self-elevation / kapsam / izin yetersizliği).

    OD5 §5.3 ve §4.3: kullanıcı kendi rolünü/scope'unu yükseltemez; sahip olmadığı
    bir yetki kümesini başkasına veremez; `UNIT`/`UNIT_DIRECT` rollerde kapsam
    zorunludur.
    """


class RoleAssignmentValidationError(RoleAssignmentError):
    """
    Rol ataması **girdisi** geçersiz (HTTP 400).

    Yetki/kapsam reddinden (`RoleAssignmentError` → 403) ayrılır: burada sorun
    aktörün yetkisi değil, isteğin kendisidir (atanamayan rol, eksik/fazla kapsam,
    silinmiş atamanın tekrarı, zaten kaldırılmış/etkin atama).
    """


class SoftDeletedDefaultRoleError(RuntimeError):
    """
    Varsayılan rol veya rol-izin bağı silinmiş durumda bulundu.

    Silinmiş kayıt yetki vermez (F7), dolayısıyla sessizce "var" sayılırsa kiracı
    **yetkisiz** kalır ama işlem başarılı görünür. Otomatik restore ise OD21'i ihlal
    eder. Bu yüzden işlem durdurulur ve çağıran açık bir geri alma kararı verir.
    """


@transaction.atomic
def create_default_roles(tenant) -> dict[str, Role]:
    """
    Kiracı için varsayılan rolleri üretir (idempotent).

    Var olan **etkin** rol atlanır; eksik izin eşleşmeleri tamamlanır. Şablonda
    tanımlı bir izin kodu veritabanında yoksa sessizce geçilmez, hata verilir: eksik
    izin "yetki verildi sanılan ama verilmeyen" bir güvenlik açığı yaratır.

    Silinmiş varsayılan rol/izin bağı varsa `SoftDeletedDefaultRoleError` yükseltilir;
    **hiçbir değişiklik yapılmaz** (doğrulama, değişiklikten önce çalışır).
    """
    izinler = {p.code: p for p in Permission.objects.all()}
    eksikler = sorted(
        {
            kod
            for sablon in DEFAULT_ROLE_TEMPLATES.values()
            for kod in sablon["permissions"]
            if kod not in izinler
        }
    )
    if eksikler:
        raise MissingPermissionError(
            "Şablonda tanımlı izin kodları veritabanında yok: " + ", ".join(eksikler)
        )

    # Ön kontrol: silinmiş varsayılan rol var mı?
    # (Silinmiş rol yetki vermez; sessizce "var" saymak veya otomatik canlandırmak
    #  yanlış olur — OD21: geri alma açık izin + denetim kaydı gerektirir.)
    # NOT: `RolePermission` bağ tablosunda `deleted_at` yoktur (fiziksel silinir), bu
    # yüzden burada yalnızca roller denetlenir.
    mevcut_roller = {
        rol.code: rol
        for rol in Role.objects.filter(tenant=tenant, code__in=list(DEFAULT_ROLE_TEMPLATES))
    }
    silinmis: list[str] = [
        f"role:{kod}" for kod, rol in mevcut_roller.items() if rol.deleted_at is not None
    ]
    if silinmis:
        raise SoftDeletedDefaultRoleError(
            "Silinmiş varsayılan rol bulundu; otomatik geri alınmaz (OD21). "
            "Açık `*.restore` işlemi gerekir: " + ", ".join(sorted(silinmis))
        )

    olusan: dict[str, Role] = {}
    for kod, sablon in DEFAULT_ROLE_TEMPLATES.items():
        rol = mevcut_roller.get(kod)
        if rol is None:
            rol = Role.objects.create(
                tenant=tenant,
                code=kod,
                name=sablon["name"],
                role_type=sablon["role_type"],
                scope_type=sablon["scope_type"],  # OD5/M8: azami organizasyon kapsamı
                is_assignable=True,
            )
        mevcut = set(
            RolePermission.objects.filter(role=rol).values_list("permission__code", flat=True)
        )
        yeni = [
            RolePermission(tenant=tenant, role=rol, permission=izinler[izin_kodu])
            for izin_kodu in sablon["permissions"]
            if izin_kodu not in mevcut
        ]
        if yeni:
            RolePermission.objects.bulk_create(yeni, ignore_conflicts=True)
        olusan[kod] = rol
    return olusan


# --- rol ataması (OD5 + self-elevation guard'ları) ---------------------------


def can_assign_role(actor, role) -> tuple[bool, str]:  # noqa: ANN001
    """
    `actor`, `role` rolünü atayabilir mi: `(izin, gerekçe)`.

    Kurallar (docs/AUTHORIZATION.md §4.3 + OD5 §5.3):
      * aktör kimliği doğrulanmış ve **aktif** olmalı,
      * aktör `role.manage` iznine sahip olmalı (§6.2/B: yalnızca şirket yöneticisi),
      * aktör, atayacağı rolün **tüm** izinlerine sahip olmalı — "sahip olmadığı
        yetkiyi veremez" (§4.3; ör. `tenant_officer` kendinde olmayan
        `document.confidential.read`ı dağıtamaz),
      * rol atanabilir olmalı (`is_assignable`) ve silinmemiş olmalı,
      * eşleşen kapsam tipi kuralı: `UNIT`/`UNIT_DIRECT` rollerde atama düzeyinde
        birim kapsamı **zorunlu**, diğer tiplerde kapsam **verilmemeli**.
    """
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        return False, "Aktör aktif bir kullanıcı değil."
    if role is None or role.deleted_at is not None:
        return False, "Rol bulunamadı veya silinmiş."
    if not role.is_assignable:
        return False, "Rol atanabilir olarak işaretlenmemiş."  # noqa: RUF003

    aktor_izinleri = effective_permission_codes(actor)
    if "role.manage" not in aktor_izinleri:
        return False, "Bu işlem için 'role.manage' izni gerekir."

    rol_izinleri = set(
        RolePermission.objects.filter(role=role).values_list("permission__code", flat=True)
    )
    eksik = rol_izinleri - aktor_izinleri
    if eksik:
        return False, (
            "Aktör, atayacağı rolün tüm izinlerine sahip değil (sahip olmadığı yetki "
            "dağıtılamaz): " + ", ".join(sorted(eksik))
        )
    return True, "Atanabilir."


@transaction.atomic
def assign_role_to_user(  # noqa: ANN001, PLR0913
    *,
    actor,
    target_user,
    role,
    scope_org_unit_id=None,
    expires_at=None,
    reason: str = "",
):
    """
    Rol ataması yapar; guard'ları (self-elevation + kapsam) dayatır ve **denetler**.

    * **Self-elevation:** aktör kendi rolünü atayamaz/değiştiremez; kendi kapsamını
      genişletemez (OD5 §5.3, §4.3).
    * **Kapsam:** `UNIT`/`UNIT_DIRECT` rollerde `scope_org_unit_id` **zorunludur**
      (NULL "tüm kiracı" anlamına gelemez); diğer tiplerde kapsam verilemez
      (M10 öncesi birim daraltması yalnız org-kapsamlı roller için tanımlıdır).
    * **Denetim:** her atama `audit_logs`'a yazılır (`role.assign`).

    NOT: M10 (`departments`) öncesi kapsam **çözülemez**; bu yüzden atanan
    `UNIT`/`UNIT_DIRECT` rol **izin üretmez** (fail-closed, `services/scope.py`).
    """
    if actor is None or target_user is None or role is None:
        raise RoleAssignmentValidationError("Aktör, hedef kullanıcı ve rol zorunludur.")
    if str(getattr(actor, "pk", None)) == str(getattr(target_user, "pk", None)):
        raise RoleAssignmentError(
            "Kullanıcı kendi rolünü atayamaz/değiştiremez (self-elevation yasağı)."
        )
    if str(role.tenant_id) != str(actor.tenant_id) or str(role.tenant_id) != str(
        target_user.tenant_id
    ):
        raise RoleAssignmentError("Rol ve kullanıcı aynı kiracıda olmalıdır.")

    izin_var, gerekce = can_assign_role(actor, role)
    if not izin_var:
        raise RoleAssignmentError(gerekce)

    if role.scope_type in ORG_SCOPED_ROLE_SCOPE_TYPES:
        if scope_org_unit_id is None:
            raise RoleAssignmentValidationError(
                f"'{role.code}' rolü {role.scope_type} kapsamlıdır: atama düzeyinde birim "
                "kapsamı (scope_org_unit_id) ZORUNLUDUR. NULL kapsam 'tüm kiracı' "
                "anlamına gelmez (OD5 fail-closed)."
            )
    elif scope_org_unit_id is not None:
        raise RoleAssignmentValidationError(
            f"'{role.code}' rolü {role.scope_type} kapsamlıdır: birim kapsamı yalnızca "
            "UNIT/UNIT_DIRECT tipli rollerde verilebilir."
        )

    # OD21: silinmiş bir atama **sessizce** canlandırılmaz. Aynı (kullanıcı, rol,
    # kapsam) için silinmiş satır varsa (benzersizlik kısıtı silinmişleri de kapsar)
    # INSERT çakışır; açık hata verilir ve `restore_role_assignment` çağrılır.
    silinmis_atama = UserRole.objects.filter(
        user=target_user, role=role, scope_org_unit_id=scope_org_unit_id, deleted_at__isnull=False
    ).first()
    if silinmis_atama is not None:
        raise RoleAssignmentValidationError(
            "Bu (kullanıcı, rol, kapsam) ataması daha önce kaldırılmış. OD21 gereği sessizce "
            "canlandırılmaz; açık geri alma gerekir (restore_role_assignment)."
        )

    atama = UserRole.objects.create(
        tenant_id=role.tenant_id,
        user=target_user,
        role=role,
        granted_by=actor,
        expires_at=expires_at,
        scope_org_unit_id=scope_org_unit_id,
    )
    log_action(
        tenant_id=role.tenant_id,
        action="role.assign",
        user=actor,
        table_name="user_roles",
        record_id=atama.pk,
        changes={
            "role_code": role.code,
            "scope_type": role.scope_type,
            "scope_org_unit_id": str(scope_org_unit_id) if scope_org_unit_id else None,
            "reason": reason[:200],
        },
    )
    return atama


# --- rol atamasını kaldırma / geri alma (OD21) --------------------------------


def _atama_guardlari(actor, assignment) -> None:  # noqa: ANN001
    """
    Kaldırma/geri alma ortak guard'ları: izin, self-elevation yasağı, kiracı eşitliği.

    Self-elevation kuralı atamada olduğu gibi burada da geçerlidir: kullanıcı **kendi**
    ataması üzerinde işlem yapamaz (aksi halde "kendi rolünü kaldır, sonra daha geniş
    rol ata" gibi dolaylı yükseltme yolu açılabilir).
    """
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise RoleAssignmentError("Aktör aktif bir kullanıcı değil.")
    if assignment is None or assignment.role is None or assignment.role.deleted_at is not None:
        raise RoleAssignmentValidationError("Rol ataması bulunamadı veya rolü silinmiş.")
    if str(getattr(actor, "pk", None)) == str(getattr(assignment, "user_id", None)):
        raise RoleAssignmentError(
            "Kullanıcı kendi rol atamasını değiştiremez (self-elevation yasağı)."
        )
    if str(assignment.tenant_id) != str(getattr(actor, "tenant_id", None)):
        raise RoleAssignmentError("Atama ve aktör aynı kiracıda olmalıdır.")
    if "role.manage" not in effective_permission_codes(actor):
        raise RoleAssignmentError("Bu işlem için 'role.manage' izni gerekir.")
    izin_var, gerekce = can_assign_role(actor, assignment.role)
    if not izin_var:
        raise RoleAssignmentError(gerekce)


@transaction.atomic
def revoke_role_assignment(*, actor, assignment, reason: str = ""):  # noqa: ANN001
    """
    Rol atamasını kaldırır (soft-delete, OD21) ve denetler (`role.revoke`).

    * Satır fiziksel olarak **silinmez**: atama geçmişi ve denetim izi korunur.
    * Kaldırılan atama izin üretmez (`active_role_assignments` silinmişleri saymaz).
    * Yalnızca `role.manage` sahibi ve atanabilir rollü aktörler çağırabilir; kullanıcı
      kendi atamasını kaldıramaz (self-elevation yasağı).
    """
    _atama_guardlari(actor, assignment)
    if assignment.deleted_at is not None:
        raise RoleAssignmentValidationError("Bu atama zaten kaldırılmış.")
    assignment.deleted_at = timezone.now()
    assignment.save(update_fields=["deleted_at", "updated_at"])
    log_action(
        tenant_id=assignment.tenant_id,
        action="role.revoke",
        user=actor,
        table_name="user_roles",
        record_id=assignment.pk,
        changes={
            "role_code": assignment.role.code,
            "target_user_id": str(assignment.user_id),
            "scope_org_unit_id": (
                str(assignment.scope_org_unit_id) if assignment.scope_org_unit_id else None
            ),
            "reason": reason[:200],
        },
    )
    return assignment


@transaction.atomic
def restore_role_assignment(*, actor, assignment, reason: str = ""):  # noqa: ANN001
    """
    Kaldırılmış (soft-delete) atamayı **açıkça** geri alır ve denetler (`role.restore`).

    OD21: geri alma sessizce olmaz; açık talep + denetim kaydı gerekir. Guard'lar
    atama ile aynıdır (`role.manage`, self-elevation yasağı, kiracı eşitliği).
    """
    _atama_guardlari(actor, assignment)
    if assignment.deleted_at is None:
        raise RoleAssignmentValidationError("Bu atama zaten etkin (geri alınacak bir şey yok).")
    assignment.deleted_at = None
    assignment.save(update_fields=["deleted_at", "updated_at"])
    log_action(
        tenant_id=assignment.tenant_id,
        action="role.restore",
        user=actor,
        table_name="user_roles",
        record_id=assignment.pk,
        changes={
            "role_code": assignment.role.code,
            "target_user_id": str(assignment.user_id),
            "scope_org_unit_id": (
                str(assignment.scope_org_unit_id) if assignment.scope_org_unit_id else None
            ),
            "reason": reason[:200],
        },
    )
    return assignment
