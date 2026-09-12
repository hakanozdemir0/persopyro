"""
Organizasyon ve çalışan servisleri (M10–M13).

Kapsam (bu turda bilinçli olarak **minimum**): bu tablolar için gerçekten gerekli
olan servis/guard katmanı. CRUD uç noktaları (API), izin/onay akışları, vardiya,
evrak, raporlama **kapsam dışıdır**.

Tüm servisler:
  * aktörün **aktif** olmasını ve gerekli **izin kodunu** taşımasını dayatır
    (`izin ∧ kapsam`, OD5 §5.1),
  * hedef nesnenin aktörün **organizasyon kapsamında** olmasını dayatır
    (`object_in_org_scope`; TENANT / UNIT / UNIT_DIRECT / SELF),
  * **denetim kaydı** yazar (yüksek etkili olaylar: birim oluşturma/taşıma/silme/
    geri alma, çalışanın birim değişimi, birincil yönetici değişimi),
  * denetim `changes` sözlüğüne **kişisel veri değeri yazmaz** (yalnızca kimlik ve
    alan adı düzeyinde bilgi).

Ağaç garantileri (döngü, derinlik, silinmiş ata) **veritabanında** zorlanır; servis
katmanı bunları anlaşılır bir hataya çevirir (`OrganizationTreeError`) ama tek
başına garanti sayılmaz (OD24: döngü yetki sınırını kaydırabildiği için DB şarttır).

**Kiracı bağlamı ZORUNLUDUR** (RLS): servisler çağrıldıkları bağlamda çalışır.
Bağlam yoksa izin çözümlemesi boş döner → işlem **reddedilir** (fail-closed); servis
kendi başına bağlam **değiştirmez** (yoksa yanlış kiracı bağlamı sessizce kurulabilirdi).

Silme/geri alma (OD5 §5.6 + OD21):
  * birim **boşaltılmadan** silinemez (aktif alt birim veya aktif çalışan varsa red),
  * birim silinince ona bağlı **kapsam atamaları da devre dışı bırakılır**
    (`user_roles.deleted_at`) → birim **restore edilse bile** atama **sessizce**
    yetki kazanmaz; yetki için **açık** yeni atama (denetimli) gerekir.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.organization.models import (
    Department,
    Employee,
    EmployeeManager,
    ManagerRelationType,
    Position,
    RecordStatus,
)
from apps.rbac.models import ORG_SCOPED_ROLE_SCOPE_TYPES, UserRole
from apps.rbac.services.permissions import user_has_permission
from apps.rbac.services.scope import (
    invalidate_org_scope_cache,
    object_in_org_scope,
    resolve_department_scope,
)


class OrganizationError(RuntimeError):
    """Organizasyon işlemi reddedildi (izin, kapsam veya iş kuralı)."""


class OrganizationAuthorizationError(OrganizationError):
    """
    İzin veya organizasyon kapsamı yetersiz (fail-closed).

    `OrganizationError`'un alt sınıfıdır: mevcut çağıranlar (`pytest.raises(OrganizationError)`)
    etkilenmez; HTTP katmanı bu türü **403** olarak eşler.
    """


class OrganizationTreeError(OrganizationError):
    """Ağaç garantisi ihlali (döngü, derinlik, silinmiş ata) — DB tarafından reddedildi."""


# --- Ortak yardımcılar -------------------------------------------------------


def _aktoru_dogrula(actor, izin_kodu: str) -> None:  # noqa: ANN001
    """Aktör aktif mi ve gerekli izne sahip mi? (fail-closed)"""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        raise OrganizationAuthorizationError("İşlem için aktif bir kullanıcı gerekir.")
    if not user_has_permission(actor, izin_kodu):
        raise OrganizationAuthorizationError(f"Bu işlem için '{izin_kodu}' izni gerekir.")


def _kapsam_dogrula(actor, *, department_id, islem: str) -> None:  # noqa: ANN001
    """Hedef birim aktörün organizasyon kapsamında mı? (izin ∧ kapsam)"""
    if not object_in_org_scope(actor, department_id=department_id):
        raise OrganizationAuthorizationError(
            f"{islem}: hedef birim aktörün organizasyon kapsamı dışında (OD5 fail-closed)."
        )


def _ayni_kiracili_mi(actor, nesne, islem: str) -> None:  # noqa: ANN001
    if str(getattr(nesne, "tenant_id", None)) != str(getattr(actor, "tenant_id", None)):
        raise OrganizationAuthorizationError(f"{islem}: nesne ve aktör aynı kiracıda olmalıdır.")


# --- Birim (department) servisleri ------------------------------------------


@transaction.atomic
def create_department(  # noqa: PLR0913
    *,
    actor,  # noqa: ANN001
    tenant,  # noqa: ANN001
    code: str,
    name: str,
    parent: Department | None = None,
    status: str = RecordStatus.ACTIVE,
) -> Department:
    """Birim oluşturur (denetimli). Ağaç kuralları DB'de doğrulanır."""
    _aktoru_dogrula(actor, "org.department.manage")
    if parent is not None:
        _ayni_kiracili_mi(actor, parent, "Birim oluşturma")

    try:
        departman = Department.objects.create(
            tenant=tenant, code=code, name=name, parent=parent, status=status
        )
    except DatabaseError as exc:
        raise OrganizationTreeError(
            "Birim oluşturulamadı (ağaç/kiracı garantisi): "
            f"{getattr(exc, 'args', [''])[0] if exc.args else exc}"
        ) from exc

    log_action(
        tenant_id=tenant.id,
        action="org.unit.create",
        user=actor,
        table_name="departments",
        record_id=departman.pk,
        changes={
            "code": code,
            "name": name,
            "parent_id": str(parent.pk) if parent is not None else None,
            "status": status,
        },
    )
    invalidate_org_scope_cache(tenant.id)
    return departman


@transaction.atomic
def update_department(  # noqa: ANN001, PLR0913
    *,
    actor,
    department: Department,
    code: str | None = None,
    name: str | None = None,
    status: str | None = None,
    reason: str = "",
) -> Department:
    """Birimin kod/ad/durum bilgisini günceller (denetimli; değişiklik yoksa kayıt yazılmaz)."""
    _aktoru_dogrula(actor, "org.department.manage")
    _ayni_kiracili_mi(actor, department, "Birim güncelleme")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Birim güncelleme")

    degisen: dict[str, dict] = {}
    if code is not None and code != department.code:
        degisen["code"] = {"before": department.code, "after": code}
        department.code = code
    if name is not None and name != department.name:
        degisen["name"] = {"before": department.name, "after": name}
        department.name = name
    if status is not None and status != department.status:
        degisen["status"] = {"before": department.status, "after": status}
        department.status = status

    if degisen:
        department.save(update_fields=[*degisen, "updated_at"])
        log_action(
            tenant_id=department.tenant_id,
            action="org.unit.update",
            user=actor,
            table_name="departments",
            record_id=department.pk,
            changes={"fields": degisen, "reason": reason[:200]},
        )
    return department


@transaction.atomic
def move_department(  # noqa: ANN001
    *,
    actor,
    department: Department,
    new_parent: Department | None,
    reason: str = "",
) -> Department:
    """
    Birimi başka bir üst birime taşır (OD5 §5.8).

    Taşıma **kapsamı yeniden hesaplatır** (alt ağaç değişir): bu yüzden yüksek etkili
    denetim olayı yazılır ve önbellek geçersiz kılınır. Döngü/derinlik ihlali
    **veritabanında** reddedilir.
    """
    _aktoru_dogrula(actor, "org.department.manage")
    _ayni_kiracili_mi(actor, department, "Birim taşıma")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Birim taşıma")
    if new_parent is not None:
        _ayni_kiracili_mi(actor, new_parent, "Birim taşıma")
        if new_parent.pk == department.pk:
            raise OrganizationError("Birim kendi üst birimi olamaz.")
        _kapsam_dogrula(actor, department_id=new_parent.pk, islem="Birim taşıma")

    eski_parent_id = department.parent_id
    department.parent = new_parent
    try:
        department.save(update_fields=["parent", "updated_at"])
    except DatabaseError as exc:
        raise OrganizationTreeError(
            "Birim taşınamadı (döngü/derinlik/silinmiş ata garantisi): "
            f"{getattr(exc, 'args', [''])[0] if exc.args else exc}"
        ) from exc

    log_action(
        tenant_id=department.tenant_id,
        action="org.unit.move",
        user=actor,
        table_name="departments",
        record_id=department.pk,
        changes={
            "old_parent_id": str(eski_parent_id) if eski_parent_id else None,
            "new_parent_id": str(new_parent.pk) if new_parent is not None else None,
            "reason": reason[:200],
        },
    )
    invalidate_org_scope_cache(department.tenant_id)
    return department


@transaction.atomic
def soft_delete_department(*, actor, department: Department, reason: str = "") -> Department:  # noqa: ANN001
    """
    Birimi siler (soft-delete) ve ona bağlı **kapsam atamalarını devre dışı bırakır**.

    Kurallar:
      * birim **boşaltılmadan** silinemez (aktif alt birim veya aktif çalışan varsa red),
      * silinen birim kapsam **üretmez**; bağlı atamaların `deleted_at` işaretlenir →
        birim restore edilse bile atama **sessizce** yetki kazanmaz (OD21/OD5 §5.6).
    """
    _aktoru_dogrula(actor, "org.department.manage")
    _ayni_kiracili_mi(actor, department, "Birim silme")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Birim silme")

    alt_birimler = Department.objects.filter(parent=department, deleted_at__isnull=True).count()
    if alt_birimler:
        raise OrganizationError(
            f"Birim silinemez: {alt_birimler} aktif alt birim var (önce onlar arşivlenmeli)."
        )

    kapsamdaki_birimler = [department.pk]
    # Alt ağaçtaki çalışanlar da denetlenir (boşaltılmadan arşivlenmez — OD24 §4).
    alt_agac = Department.objects.filter(parent=department).values_list("pk", flat=True)
    kapsamdaki_birimler.extend(alt_agac)
    calisanlar = Employee.objects.filter(
        department_id__in=kapsamdaki_birimler, deleted_at__isnull=True
    ).count()
    if calisanlar:
        raise OrganizationError(
            f"Birim silinemez: {calisanlar} aktif çalışan var (çalışanlar taşınmalı)."
        )

    department.deleted_at = timezone.now()
    department.save(update_fields=["deleted_at", "updated_at"])

    # Kapsam atamaları devre dışı (silinmez: denetim izi kalır; sessiz restore yok).
    devre_disi = list(
        UserRole.objects.filter(
            scope_org_unit=department,
            deleted_at__isnull=True,
            role__scope_type__in=list(ORG_SCOPED_ROLE_SCOPE_TYPES),
        )
    )
    for atama in devre_disi:
        atama.deleted_at = timezone.now()
        atama.save(update_fields=["deleted_at", "updated_at"])

    log_action(
        tenant_id=department.tenant_id,
        action="org.unit.delete",
        user=actor,
        table_name="departments",
        record_id=department.pk,
        changes={
            "code": department.code,
            "disabled_scope_assignments": [str(a.pk) for a in devre_disi],
            "reason": reason[:200],
        },
    )
    invalidate_org_scope_cache(department.tenant_id)
    return department


@transaction.atomic
def restore_department(*, actor, department: Department, reason: str = "") -> Department:  # noqa: ANN001
    """
    Silinmiş birimi geri alır (OD21: **açık** işlem + denetim).

    Kapsam atamaları **canlandırılmaz** (silme sırasında devre dışı bırakıldılar):
    yetki için açık yeni atama gerekir. Üst birim silinmişse geri alma DB'de reddedilir.
    """
    _aktoru_dogrula(actor, "org.department.manage")
    _ayni_kiracili_mi(actor, department, "Birim geri alma")
    if department.deleted_at is None:
        raise OrganizationError("Birim zaten aktif; geri alınacak kayıt yok.")

    department.deleted_at = None
    try:
        department.save(update_fields=["deleted_at", "updated_at"])
    except DatabaseError as exc:
        raise OrganizationTreeError(
            "Birim geri alınamadı (silinmiş ata garantisi): "
            f"{getattr(exc, 'args', [''])[0] if exc.args else exc}"
        ) from exc

    log_action(
        tenant_id=department.tenant_id,
        action="org.unit.restore",
        user=actor,
        table_name="departments",
        record_id=department.pk,
        changes={"code": department.code, "assignments_revived": 0, "reason": reason[:200]},
    )
    invalidate_org_scope_cache(department.tenant_id)
    return department


@transaction.atomic
def set_department_manager(*, actor, department: Department, employee: Employee | None, reason: str = "") -> Department:  # noqa: ANN001, E501
    """
    Birim sorumlusunu (`manager_employee_id`) atar/temizler.

    **Yetki üretmez** (OD5 §5.5): yalnızca onay yönlendirmesi bilgisidir.
    """
    _aktoru_dogrula(actor, "org.department.manage")
    _ayni_kiracili_mi(actor, department, "Birim sorumlusu atama")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Birim sorumlusu atama")
    if employee is not None:
        _ayni_kiracili_mi(actor, employee, "Birim sorumlusu atama")
        if employee.deleted_at is not None:
            raise OrganizationError("Silinmiş çalışan birim sorumlusu olamaz (OD21).")

    onceki = department.manager_employee_id
    department.manager_employee = employee
    department.save(update_fields=["manager_employee", "updated_at"])

    log_action(
        tenant_id=department.tenant_id,
        action="org.unit.manager_change",
        user=actor,
        table_name="departments",
        record_id=department.pk,
        changes={
            "old_manager_employee_id": str(onceki) if onceki else None,
            "new_manager_employee_id": str(employee.pk) if employee is not None else None,
            "reason": reason[:200],
        },
    )
    return department


# --- Pozisyon servisleri ----------------------------------------------------


@transaction.atomic
def create_position(*, actor, tenant, code: str, name: str, level: str = "") -> Position:  # noqa: ANN001
    """Pozisyon (unvan/kadro) tanımı oluşturur (denetimli)."""
    _aktoru_dogrula(actor, "org.position.manage")
    pozisyon = Position.objects.create(tenant=tenant, code=code, name=name, level=level)
    log_action(
        tenant_id=tenant.id,
        action="org.position.create",
        user=actor,
        table_name="positions",
        record_id=pozisyon.pk,
        changes={"code": code, "name": name, "level": level},
    )
    return pozisyon


# --- Çalışan servisleri -----------------------------------------------------

#: Çalışan numarası biçimi (sicil numarası): `EMP-000001`.
#: **`public_id` DEĞİLDİR** (OD2/OD25): kimlik değil, kiracı içi sicil numarasıdır;
#: tahmin edilebilir olması güvenlik açısından kritik değildir.
EMPLOYEE_NO_PREFIX = "EMP"
EMPLOYEE_NO_DIGITS = 6
#: Aynı numara veritabanında varsa kaç kez yeni sıra değeri denenecek (bounded).
_EMPLOYEE_NO_DENEME = 5


def _sequence_degeri() -> int:
    """`hr.employee_no_seq` üzerinden atomik sıra değeri üretir."""
    with connection.cursor() as cur:
        cur.execute("SELECT nextval('hr.employee_no_seq')")
        return int(cur.fetchone()[0])


def next_employee_no(tenant) -> str:  # noqa: ANN001
    """
    Sıradaki **çalışan numarasını** üretir (sunucu tarafı, concurrency-safe).

    Kaynak: PostgreSQL `sequence` (`hr.employee_no_seq`, migration 0013).
    `nextval` atomiktir: iki eşzamanlı istek **aynı değeri alamaz** ve kilit
    beklemesi oluşmaz. Numara istemciden **kabul edilmez**; frontend hesaplamaz.

    * Format: `EMP-000001` (prefix + 6 hane).
    * **Birimden bağımsızdır:** çalışan başka birime taşınsa da numara değişmez.
    * İşlem geri alınsa bile tüketilen sıra değeri geri gelmez (boşluk olabilir);
      bu bilinçlidir — numara **benzersizlik** garantisi için vardır, ardışıklık
      garantisi vermez.
    * Elle girilmiş bir numarayla çakışma olursa (nadir) sıradaki değer denenir;
      ısrarcı çakışmada işlem hata verir (fail-closed).
    """
    for _ in range(_EMPLOYEE_NO_DENEME):
        numara = f"{EMPLOYEE_NO_PREFIX}-{_sequence_degeri():0{EMPLOYEE_NO_DIGITS}d}"
        if not Employee.objects.filter(tenant=tenant, employee_no=numara).exists():
            return numara
    raise OrganizationError(
        "Çalışan numarası üretilemedi: benzersiz bir sicil numarası bulunamadı."
    )


@transaction.atomic
def create_employee(  # noqa: PLR0913
    *,
    actor,  # noqa: ANN001
    tenant,  # noqa: ANN001
    department: Department,
    hire_date: dt.date,
    employee_no: str | None = None,
    position: Position | None = None,
    user=None,  # noqa: ANN001
    status: str = "active",
) -> Employee:
    """
    Çalışan (iş) kaydı oluşturur — **PII yazmaz** (kişisel veri ayrı tabloda).

    `employee_no` **boş bırakılırsa sunucu üretir** (`next_employee_no`);
    istemci numara uydurmaz/hesaplamaz. İzin `employee.create` + hedef birim
    aktörün kapsamında olmalıdır.
    """
    _aktoru_dogrula(actor, "employee.create")
    _ayni_kiracili_mi(actor, department, "Çalışan oluşturma")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Çalışan oluşturma")
    if position is not None:
        _ayni_kiracili_mi(actor, position, "Çalışan oluşturma")
    if user is not None:
        _ayni_kiracili_mi(actor, user, "Çalışan oluşturma")

    numara = (employee_no or "").strip() or next_employee_no(tenant)
    calisan = Employee.objects.create(
        tenant=tenant,
        department=department,
        position=position,
        user=user,
        employee_no=numara,
        hire_date=hire_date,
        status=status,
    )
    log_action(
        tenant_id=tenant.id,
        action="employee.create",
        user=actor,
        table_name="employees",
        record_id=calisan.pk,
        changes={
            "employee_no": numara,
            "employee_no_auto": not (employee_no or "").strip(),
            "department_id": str(department.pk),
            "position_id": str(position.pk) if position is not None else None,
            "hire_date": hire_date.isoformat(),
            "status": status,
            "user_linked": user is not None,
        },
    )
    invalidate_org_scope_cache(tenant.id)
    return calisan


@transaction.atomic
def move_employee(*, actor, employee: Employee, department: Department, reason: str = "") -> Employee:  # noqa: ANN001, E501
    """Çalışanın birimini değiştirir (yüksek etkili denetim olayı)."""
    _aktoru_dogrula(actor, "employee.update")
    _ayni_kiracili_mi(actor, employee, "Çalışan birim değişimi")
    _ayni_kiracili_mi(actor, department, "Çalışan birim değişimi")
    _kapsam_dogrula(actor, department_id=employee.department_id, islem="Çalışan birim değişimi")
    _kapsam_dogrula(actor, department_id=department.pk, islem="Çalışan birim değişimi")

    eski_departman = employee.department_id
    employee.department = department
    employee.save(update_fields=["department", "updated_at"])

    log_action(
        tenant_id=employee.tenant_id,
        action="employee.department_change",
        user=actor,
        table_name="employees",
        record_id=employee.pk,
        changes={
            "old_department_id": str(eski_departman),
            "new_department_id": str(department.pk),
            "reason": reason[:200],
        },
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return employee


@transaction.atomic
def set_primary_manager(  # noqa: ANN001, PLR0913
    *,
    actor,
    employee: Employee,
    manager_employee: Employee,
    effective_from: dt.date | None = None,
    reason: str = "",
) -> EmployeeManager:
    """
    Çalışanın **birincil** yöneticisini atar (denetimli, tarihli).

    Eski açık birincil ilişki **kapatılır** (`effective_to`), yenisi açılır → geçmiş
    korunur (OD21). Bu ilişki **yetki üretmez** (OD5 §5.5); yalnızca raporlama/onay
    yönlendirmesidir. Aynı çalışan için ikinci bir **açık birincil** ilişki
    veritabanında reddedilir (kısmi benzersizlik).
    """
    _aktoru_dogrula(actor, "employee.update")
    _ayni_kiracili_mi(actor, employee, "Birincil yönetici atama")
    _ayni_kiracili_mi(actor, manager_employee, "Birincil yönetici atama")
    _kapsam_dogrula(actor, department_id=employee.department_id, islem="Birincil yönetici atama")
    if employee.pk == manager_employee.pk:
        raise OrganizationError("Çalışan kendi yöneticisi olamaz.")

    baslangic = effective_from or timezone.localdate()
    mevcut = (
        EmployeeManager.objects.filter(
            employee=employee,
            relation_type=ManagerRelationType.PRIMARY,
            effective_to__isnull=True,
            deleted_at__isnull=True,
        )
        .order_by("-effective_from")
        .first()
    )
    if mevcut is not None:
        if mevcut.effective_from >= baslangic:
            raise OrganizationError(
                "Yeni birincil ilişkinin başlangıcı mevcut ilişkiden sonra olmalıdır "
                f"(mevcut başlangıç: {mevcut.effective_from})."
            )
        mevcut.effective_to = baslangic - dt.timedelta(days=1)
        mevcut.save(update_fields=["effective_to", "updated_at"])

    try:
        iliski = EmployeeManager.objects.create(
            tenant_id=employee.tenant_id,
            employee=employee,
            manager_employee=manager_employee,
            relation_type=ManagerRelationType.PRIMARY,
            effective_from=baslangic,
            created_by=actor,
        )
    except DatabaseError as exc:
        raise OrganizationError(
            "Birincil yönetici ilişkisi kurulamadı (kısmi benzersizlik/kiracı garantisi): "
            f"{getattr(exc, 'args', [''])[0] if exc.args else exc}"
        ) from exc

    log_action(
        tenant_id=employee.tenant_id,
        action="employee.primary_manager_set",
        user=actor,
        table_name="employee_manager",
        record_id=iliski.pk,
        changes={
            "employee_id": str(employee.pk),
            "old_manager_employee_id": str(mevcut.manager_employee_id) if mevcut else None,
            "new_manager_employee_id": str(manager_employee.pk),
            "effective_from": baslangic.isoformat(),
            "reason": reason[:200],
        },
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return iliski


@transaction.atomic
def close_manager_relation(*, actor, relation: EmployeeManager, effective_to: dt.date, reason: str = "") -> EmployeeManager:  # noqa: ANN001, E501
    """Açık bir yönetici ilişkisini kapatır (silmez; geçmiş korunur)."""
    _aktoru_dogrula(actor, "employee.update")
    _ayni_kiracili_mi(actor, relation, "Yönetici ilişkisi kapatma")
    if effective_to < relation.effective_from:
        raise OrganizationError("Kapanış tarihi başlangıçtan önce olamaz.")

    relation.effective_to = effective_to
    relation.save(update_fields=["effective_to", "updated_at"])
    log_action(
        tenant_id=relation.tenant_id,
        action="employee.manager.close",
        user=actor,
        table_name="employee_manager",
        record_id=relation.pk,
        changes={
            "employee_id": str(relation.employee_id),
            "manager_employee_id": str(relation.manager_employee_id),
            "effective_to": effective_to.isoformat(),
            "reason": reason[:200],
        },
    )
    invalidate_org_scope_cache(relation.tenant_id)
    return relation


# --- Yetki yardımcıları (izin ∧ kapsam) -------------------------------------


def can_access_employee(user, employee: Employee, permission_code: str) -> bool:  # noqa: ANN001
    """
    **Merkezi** çalışan erişim kararı: `izin ∧ kapsam` (OD5 §5.1).

    * izin kodu yoksa → **DENY**
    * farklı kiracı → **DENY**
    * çalışan aktörün organizasyon kapsamı dışında (kardeş birim, kapsamsız UNIT
      ataması, çözülemeyen kapsam) → **DENY**

    Görünümler/servisler bu fonksiyonu kullanmalıdır; aynı kontrol kopyalanmaz.
    """
    if employee is None:
        return False
    if not user_has_permission(user, permission_code):
        return False
    if str(getattr(employee, "tenant_id", None)) != str(getattr(user, "tenant_id", None)):
        return False
    return object_in_org_scope(
        user, department_id=employee.department_id, owner_user_id=employee.user_id
    )


def can_access_employee_personal_data(  # noqa: ANN001
    user,
    employee: Employee,
    *,
    mfa_ok: bool = False,
    purpose_given: bool = False,
    permission_code: str = "employee.sensitive.read",
) -> bool:
    """
    K2/K3 (kişisel veri) erişim kararı — **organizasyon kapsamı tek başına yetki VERMEZ**.

    Gerekli olan **hepsi** birden:
      1. `employee.sensitive.read` izni (izin kodundan gelir, kapsamdan **değil**),
      2. koşullar: `MFA_OK` + `PURPOSE_GIVEN` (docs/AUTHORIZATION.md §7),
      3. çalışanın aktörün organizasyon kapsamında olması (ek sınır; asla izin üretmez).

    Bu turda tam maskeleme motoru yazılmadı; ancak **doğrudan erişim yolu açılmadı**:
    birim yöneticisi (UNIT kapsamı) bu fonksiyondan geçemez çünkü izin koduna sahip
    değildir (§6.2 DY satırı: `employee.sensitive.read` = —).
    """
    if employee is None or user is None:
        return False
    if not user_has_permission(user, permission_code):
        return False
    if not (mfa_ok and purpose_given):
        return False
    if str(getattr(employee, "tenant_id", None)) != str(getattr(user, "tenant_id", None)):
        return False
    return object_in_org_scope(user, department_id=employee.department_id)


def sub_tree_department_ids(department: Department) -> frozenset[uuid.UUID]:
    """
    Bir birimin alt ağacı (kendisi dahil) — **kapsam** ile aynı kaynak (OD24).

    Çözülemeyen ağaç için `OrganizationError` yükseltir (sessiz genişleme yok).
    """
    cozulen = resolve_department_scope(department.pk, include_descendants=True)
    if cozulen is None:
        raise OrganizationError("Birim alt ağacı çözülemedi (silinmiş veya kiracı dışı).")
    return cozulen
