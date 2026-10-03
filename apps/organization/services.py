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
from apps.organization.employee_form_catalog import EMPLOYEE_FORM_FIELDS
from apps.organization.models import (
    Department,
    Employee,
    EmployeeAssignment,
    EmployeeEmploymentTerms,
    EmployeeManager,
    FormFieldDefinition,
    ManagerRelationType,
    Position,
    RecordStatus,
    Workplace,
)
from apps.rbac.models import ORG_SCOPED_ROLE_SCOPE_TYPES, UserRole
from apps.rbac.services.permissions import user_has_permission
from apps.rbac.services.scope import (
    invalidate_org_scope_cache,
    object_in_org_scope,
    resolve_department_scope,
)
from apps.tenancy.models import Tenant


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


@transaction.atomic
def update_position(*, actor, position: Position, code: str, name: str, level: str = "") -> Position:  # noqa: ANN001
    """Unvan kodu/adı/seviyesini aynı kiracıda denetimli günceller."""
    _aktoru_dogrula(actor, "org.position.manage")
    _ayni_kiracili_mi(actor, position, "Unvan güncelleme")
    code, name, level = code.strip(), name.strip(), level.strip()
    if not code or len(code) > 50 or not name or len(name) > 190 or len(level) > 40:
        raise OrganizationError("Unvan kodu (50), adı (190) ve seviye (40) geçerli olmalıdır.")
    changes = {}
    for field, value in (("code", code), ("name", name), ("level", level)):
        old = getattr(position, field)
        if old != value:
            changes[field] = {"before": old, "after": value}
            setattr(position, field, value)
    if changes:
        position.save(update_fields=[*changes, "updated_at"])
        log_action(
            tenant_id=position.tenant_id,
            action="org.position.update",
            user=actor,
            table_name="positions",
            record_id=position.pk,
            changes={"fields": changes},
        )
    return position


@transaction.atomic
def soft_delete_position(*, actor, position: Position) -> Position:  # noqa: ANN001
    """Unvanı arşivler; çalışanların geçmişteki unvan bağlantısı korunur."""
    _aktoru_dogrula(actor, "org.position.manage")
    _ayni_kiracili_mi(actor, position, "Unvan silme")
    if position.deleted_at is None:
        position.deleted_at = timezone.now()
        position.save(update_fields=["deleted_at", "updated_at"])
        log_action(
            tenant_id=position.tenant_id,
            action="org.position.delete",
            user=actor,
            table_name="positions",
            record_id=position.pk,
            changes={"code": position.code, "name": position.name},
        )
    return position


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


def validate_employee_capacity(tenant_id, *, adds_active: bool = False) -> Tenant:
    """Kiracının çalışan kotalarını kilit altında doğrular.

    Tenant satırı kilitlendiğinden aynı kiracı için eşzamanlı eklemeler kotayı
    birlikte aşamaz. Sıfır limit sınırsız anlamına gelir.
    """
    tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
    employees = Employee.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True)
    if tenant.max_employee_records and employees.count() >= tenant.max_employee_records:
        raise OrganizationError(
            f"Çalışan kayıt limitine ulaşıldı ({tenant.max_employee_records})."
        )
    if adds_active and tenant.max_active_employees:
        active_count = employees.filter(status__in=("active", "suspended")).count()
        if active_count >= tenant.max_active_employees:
            raise OrganizationError(
                f"Aktif çalışan limitine ulaşıldı ({tenant.max_active_employees})."
            )
    return tenant


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
    tenant = validate_employee_capacity(
        tenant.pk, adds_active=status in ("active", "suspended")
    )
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
    # Onboarding invariant (OD60 §28.6): yeni çalışanın **ilk açık ataması** aynı
    # transaction içinde açılır. Aksi halde ilk gerçek değişiklikte
    # `change_assignment` yalnız yeni satırı yazar (eski taraf boş kalır) →
    # Timeline old→new geçmişi kaybolur. `effective_from` = hire_date
    # (0015 backfill kuralıyla aynı başlangıç tarihi).
    try:
        EmployeeAssignment.objects.create(
            tenant_id=tenant.id,
            employee=calisan,
            department=department,
            position=position,
            effective_from=hire_date,
            reason="İşe alım: başlangıç ataması",
            created_by=actor,
        )
    except DatabaseError as exc:
        raise OrganizationError(
            f"Başlangıç ataması yazılamadı (kiracı/benzersizlik garantisi): {exc}"
        ) from exc
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

def ensure_default_form_fields(tenant) -> int:  # noqa: ANN001
    """
    Kiracı için onboarding formu **varsayılan alan yapılandırmasını** üretir (idempotent).

    P1-A/OD38: form yapısı koddaki katalogdan türetilir; bu fonksiyon her alan için bir
    **başlangıç satırı** yazar (görünür/zorunlu/sıra/bölüm/kilit seviyesi/veri sınıfı
    katalogdan gelir). Tenant sonradan bu satırları **override** eder; eksik satır varsa
    eklenir, **mevcut satırlar değiştirilmez** (tenant ayarı ezilmez).

    Nerede çağrılır: kiracı **provizyonu** sırasında (`create_default_roles` ile aynı akış).
    Bugün `scripts/_demo_data.py` çağırır; P1-B'de kiracı oluşturma servisine bağlanacaktır.

    Döner: bu çağrıda **oluşturulan** satır sayısı (0 = zaten kurulu).

    Kiracı bağlamı **zorunludur** (RLS): çağıran `tenant_context` içinde olmalıdır.
    """
    olusan = 0
    for alan in EMPLOYEE_FORM_FIELDS:
        _, yaratildi = FormFieldDefinition.objects.get_or_create(
            tenant=tenant,
            form_code="employee_onboarding",
            field_code=alan.code,
            deleted_at__isnull=True,
            defaults={
                "section_code": alan.section_code,
                "section_label": alan.section_label,
                "sort_order": alan.sort_order,
                "is_visible": alan.default_visible,
                "is_required": alan.default_required,
                "label_override": "",
                "helper_text": "",
                "lock_level": alan.lock_level,
                "data_class": alan.data_class,
            },
        )
        if yaratildi:
            olusan += 1
    return olusan


# ========================= P1-B — GEÇMİŞ SERVİSLERİ =========================
# Kaynak karar: OD60 (`docs/AUTHORIZATION.md` §16) + HR_ROADMAP §28.1/28.6.
# Model: eski açık satır KAPANIR (`effective_to = yeni başlangıç - 1 gün`), yeni
# satır açılır; üzerine YAZILMAZ. `employees.department_id/position_id` "güncel
# değer" snapshot'ı aynı transaction içinde senkron tutulur (tek servis yolu).


def _mevcut_atama_kilitle(employee: Employee) -> EmployeeAssignment | None:  # noqa: ANN001
    return (
        EmployeeAssignment.objects.select_for_update()
        .filter(employee=employee, effective_to__isnull=True, deleted_at__isnull=True)
        .order_by("-effective_from")
        .first()
    )


def _onboarding_baseline_duzelt(  # noqa: ANN001, PLR0913
    *,
    actor,
    employee: Employee,
    atama: EmployeeAssignment,
    yeni_birim,  # noqa: ANN001
    yeni_pozisyon,  # noqa: ANN001
    eski_birim_id,  # noqa: ANN001
    eski_pozisyon_id,  # noqa: ANN001
    gerekce: str,
) -> tuple[EmployeeAssignment, dict]:
    """
    İşe **başlamamış** çalışanın başlangıç atamasını **yerinde** düzeltir (OD60).

    Çalışan henüz işe başlamadıysa (bugün < `hire_date`) ve açık atama yalnız
    baseline satırıysa, bu iş geçmişte gerçekleşmiş bir görev değişikliği DEĞİL,
    onboarding bilgi düzeltmesidir: **ikinci atama satırı** ve "Görev yeri
    değişti" timeline olayı üretilmez (timeline yalnız atama satırlarından
    okunur). `effective_from` = `hire_date` korunur (onboarding invariant),
    `employees` snapshot'ı senkron edilir ve düzeltme denetim kaydına yazılır
    (`employee.assignment.change` + `onboarding_correction` işareti).
    """
    atama.department_id = yeni_birim
    atama.position_id = yeni_pozisyon
    atama.save(update_fields=["department", "position", "updated_at"])

    degisen: list[str] = []
    if employee.department_id != yeni_birim:
        employee.department_id = yeni_birim
        degisen.append("department")
    if employee.position_id != yeni_pozisyon:
        employee.position_id = yeni_pozisyon
        degisen.append("position")
    if degisen:
        employee.save(update_fields=[*degisen, "updated_at"])

    ortak = {
        "old_department_id": str(eski_birim_id) if eski_birim_id else None,
        "new_department_id": str(yeni_birim),
        "old_position_id": str(eski_pozisyon_id) if eski_pozisyon_id else None,
        "new_position_id": str(yeni_pozisyon) if yeni_pozisyon else None,
        "effective_from": atama.effective_from.isoformat(),
    }
    log_action(
        tenant_id=employee.tenant_id,
        action="employee.assignment.change",
        user=actor,
        table_name="employee_assignments",
        record_id=atama.pk,
        changes={
            "employee_id": str(employee.pk),
            "onboarding_correction": True,
            **ortak,
            "reason": gerekce[:200],
        },
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return atama, {"degisiklik": True, "onboarding_correction": True, **ortak}


@transaction.atomic
def change_assignment(
    *,
    actor,  # noqa: ANN001
    employee: Employee,  # noqa: ANN001
    department: Department | None = None,  # noqa: ANN001
    position: Position | None = None,  # noqa: ANN001
    effective_from: dt.date | None = None,
    reason: str = "",
) -> tuple[EmployeeAssignment, dict]:
    """
    Departman ve/veya pozisyon değişikliği — **atama geçmişi** yazılır (OD60).

    * Hedef birim/pozisyon aynı kiracıda ve soft-delete edilmemiş olmalıdır
      (DB guard'ı da dayatır); başlangıç tarihi en az bugün olmalıdır
      (geçmişe dönük atama P1'de desteklenmez) ve **işe giriş tarihinden önce
      olamaz** (`hire_date` invariant'ı).
    * Çalışan **henüz işe başlamadıysa** (bugün < hire_date) ve açık atama yalnız
      baseline satırıysa, istek onboarding bilgi düzeltmesi sayılır: ikinci satır
      ve timeline olayı üretilmez, baseline **yerinde** güncellenir
      (`_onboarding_baseline_duzelt`).
    * Eski açık atama kapanır (effective_to = yeni başlangıç − 1 gün);
      aynı gün geçiş için eski satır aynı gün kapanır (DATE modeli: iki satır
      aynı günü paylaşabilir, kapanış ≥ başlangıç CHECK'i bozulmaz).
    * `employees.department_id/position_id` snapshot'ı aynı işlemde senkron edilir.
    * Eski ve yeni değerler **aynıysa** no-op döner (audit yazılmaz).
    """
    _aktoru_dogrula(actor, "employee.update")
    _ayni_kiracili_mi(actor, employee, "Atama değişikliği")
    gerekce = (reason or "").strip()
    if not gerekce:
        raise OrganizationError("Atama değişikliği için gerekçe zorunludur.")
    baslangic = effective_from or timezone.localdate()
    if baslangic < timezone.localdate():
        raise OrganizationError(
            "Atama başlangıç tarihi geçmiş olamaz (geçmişe dönük yazım yok)."
        )

    hedef_birim = department
    hedef_pozisyon = position
    if hedef_birim is not None:
        _ayni_kiracili_mi(actor, hedef_birim, "Atama değişikliği")
        if hedef_birim.deleted_at is not None:
            raise OrganizationError("Silinmiş birim yeni atamada kullanılamaz.")
    if hedef_pozisyon is not None:
        _ayni_kiracili_mi(actor, hedef_pozisyon, "Atama değişikliği")
        if hedef_pozisyon.deleted_at is not None:
            raise OrganizationError("Silinmiş pozisyon yeni atamada kullanılamaz.")

    eski = _mevcut_atama_kilitle(employee)
    eski_birim_id = eski.department_id if eski else employee.department_id
    eski_pozisyon_id = eski.position_id if eski else employee.position_id

    yeni_birim = hedef_birim.pk if hedef_birim is not None else eski_birim_id
    yeni_pozisyon = hedef_pozisyon.pk if hedef_pozisyon is not None else eski_pozisyon_id
    if yeni_birim is None:
        raise OrganizationError("Çalışanın birimi boş olamaz; hedef birim verin.")

    # No-op koşulu: **açık satır varken** değerler değişmemişse (satır yoksa
    # backfill'siz/legacy çalışanda ilk satır mutlaka açılır — snapshot'la
    # eşit olsa bile; yoksa geçmiş sorgusu boş kalır, OD60 riski).
    if eski is not None and yeni_birim == eski_birim_id and yeni_pozisyon == eski_pozisyon_id:
        return eski, {"degisiklik": False}

    # Pre-hire onboarding düzeltmesi: çalışan henüz işe başlamadıysa (bugün <
    # hire_date) ve açık atama **yalnız baseline** satırıysa (kapalı geçmiş
    # satırı yok), kullanıcı başlangıç birim/pozisyon bilgisini düzeltiyordur.
    # Bu durumda ikinci satır açmak yerine baseline yerinde güncellenir
    # (timeline'da sahte "Görev yeri değişti" olayı doğmaz).
    if (
        eski is not None
        and eski.effective_from == employee.hire_date
        and employee.hire_date > timezone.localdate()
        and not EmployeeAssignment.objects.filter(
            employee=employee, deleted_at__isnull=True
        ).exclude(pk=eski.pk).exists()
        and (effective_from is None or effective_from == employee.hire_date)
    ):
        return _onboarding_baseline_duzelt(
            actor=actor,
            employee=employee,
            atama=eski,
            yeni_birim=yeni_birim,
            yeni_pozisyon=yeni_pozisyon,
            eski_birim_id=eski_birim_id,
            eski_pozisyon_id=eski_pozisyon_id,
            gerekce=gerekce,
        )

    # Onboarding invariant (OD60): atama başlangıcı işe giriş tarihinden ÖNCE
    # olamaz — baseline'ın `effective_from` değeri `hire_date`'tir; daha erken
    # bir tarih geçmişe dönük yazım olurdu.
    if baslangic < employee.hire_date:
        raise OrganizationError(
            "Atama başlangıcı işe giriş tarihinden önce olamaz "
            f"(işe giriş: {employee.hire_date})."
        )

    if eski is not None:
        if eski.effective_from > baslangic:
            raise OrganizationError(
                "Yeni atama başlangıcı, mevcut açık atamanın başlangıcından önce olamaz "
                f"(mevcut başlangıç: {eski.effective_from}). Aynı gün ikinci değişiklik "
                "için yeni satır sonraki bir günden başlamalıdır."
            )
        # DATE modeli: kapanış `yeni başlangıç` gününe kadar geçerli; aynı gün
        # iki satır üst üste binebilir (odak günü = yeni satır). Kapanış < başlangıç
        # olsaydı CHECK (ea_date_range_valid) kırılırdı — o yüzden `= baslangic`.
        eski.effective_to = baslangic
        eski.save(update_fields=["effective_to", "updated_at"])

    try:
        yeni = EmployeeAssignment.objects.create(
            tenant_id=employee.tenant_id,
            employee=employee,
            department_id=yeni_birim,
            position_id=yeni_pozisyon,
            effective_from=baslangic,
            reason=gerekce[:200],
            created_by=actor,
        )
    except DatabaseError as exc:
        raise OrganizationError(
            f"Atama geçmişi yazılamadı (kiracı/benzersizlik garantisi): {exc}"
        ) from exc

    # "Güncel değer" snapshot senkronu (tek servis yolu; OD60 risk notu).
    degisen: list[str] = []
    if employee.department_id != yeni_birim:
        employee.department_id = yeni_birim
        degisen.append("department")
    if employee.position_id != yeni_pozisyon:
        employee.position_id = yeni_pozisyon
        degisen.append("position")
    if degisen:
        employee.save(update_fields=[*degisen, "updated_at"])

    log_action(
        tenant_id=employee.tenant_id,
        action="employee.assignment.change",
        user=actor,
        table_name="employee_assignments",
        record_id=yeni.pk,
        changes={
            "employee_id": str(employee.pk),
            "old_department_id": str(eski_birim_id) if eski_birim_id else None,
            "new_department_id": str(yeni_birim),
            "old_position_id": str(eski_pozisyon_id) if eski_pozisyon_id else None,
            "new_position_id": str(yeni_pozisyon) if yeni_pozisyon else None,
            "effective_from": baslangic.isoformat(),
            "reason": gerekce[:200],
        },
    )
    invalidate_org_scope_cache(employee.tenant_id)
    return yeni, {
        "degisiklik": True,
        "old_department_id": str(eski_birim_id) if eski_birim_id else None,
        "new_department_id": str(yeni_birim),
        "old_position_id": str(eski_pozisyon_id) if eski_pozisyon_id else None,
        "new_position_id": str(yeni_pozisyon) if yeni_pozisyon else None,
    }


@transaction.atomic
def change_employment_terms(
    *,
    actor,  # noqa: ANN001
    employee: Employee,  # noqa: ANN001
    employment_type: str | None = None,
    working_model: str | None = None,
    workplace: Workplace | None = None,  # noqa: ANN001
    contract_type: str | None = None,
    contract_start_date: dt.date | None = None,
    contract_end_date: dt.date | None = None,
    probation_end_date: dt.date | None = None,
    effective_from: dt.date | None = None,
    reason: str = "",
) -> tuple[EmployeeEmploymentTerms, dict]:
    """
    İstihdam koşulu değişikliği — **yeni koşul satırı** açılır (OD60; ücret yok).

    Mevcut açık satır kapatılır; boş bırakılan alanlar mevcut değerden devralınır.
    `contract_end_date < contract_start_date` reddedilir (model CHECK'i de dayatır).
    """
    _aktoru_dogrula(actor, "employee.update")
    _ayni_kiracili_mi(actor, employee, "İstihdam koşulu değişikliği")
    gerekce = (reason or "").strip()
    if not gerekce:
        raise OrganizationError("İstihdam koşulu değişikliği için gerekçe zorunludur.")
    baslangic = effective_from or timezone.localdate()
    if baslangic < timezone.localdate():
        raise OrganizationError("Koşul başlangıç tarihi geçmiş olamaz.")
    if workplace is not None:
        _ayni_kiracili_mi(actor, workplace, "İstihdam koşulu değişikliği")
        if workplace.deleted_at is not None:
            raise OrganizationError("Silinmiş işyeri yeni koşul satırında kullanılamaz.")
    if (
        contract_start_date is not None
        and contract_end_date is not None
        and contract_end_date < contract_start_date
    ):
        raise OrganizationError("Sözleşme bitişi, başlangıcından önce olamaz.")

    mevcut = (
        EmployeeEmploymentTerms.objects.select_for_update()
        .filter(employee=employee, effective_to__isnull=True, deleted_at__isnull=True)
        .order_by("-effective_from")
        .first()
    )

    def _devral(eski_deger, yeni_deger):  # noqa: ANN001, ANN202
        return yeni_deger if yeni_deger is not None else eski_deger

    yeni_tip = _devral(mevcut.employment_type if mevcut else "", employment_type)
    yeni_model = _devral(mevcut.working_model if mevcut else "", working_model)
    yeni_sozlesme_tipi = _devral(mevcut.contract_type if mevcut else "", contract_type)
    yeni_sozlesme_bas = _devral(
        mevcut.contract_start_date if mevcut else None, contract_start_date
    )
    yeni_sozlesme_son = _devral(
        mevcut.contract_end_date if mevcut else None, contract_end_date
    )
    yeni_deneme = _devral(mevcut.probation_end_date if mevcut else None, probation_end_date)
    yeni_isyeri = workplace.pk if workplace is not None else (
        mevcut.workplace_id if mevcut else None
    )
    if not yeni_tip or not yeni_model:
        raise OrganizationError(
            "İstihdam tipi ve çalışma modeli zorunludur (ilk koşul satırında belirtilmelidir)."
        )

    degisiklik = mevcut is None or (
        mevcut.employment_type != yeni_tip
        or mevcut.working_model != yeni_model
        or mevcut.contract_type != yeni_sozlesme_tipi
        or mevcut.contract_start_date != yeni_sozlesme_bas
        or mevcut.contract_end_date != yeni_sozlesme_son
        or mevcut.probation_end_date != yeni_deneme
        or mevcut.workplace_id != yeni_isyeri
    )
    if not degisiklik:
        return mevcut, {"degisiklik": False}

    if mevcut is not None:
        mevcut.effective_to = baslangic
        mevcut.save(update_fields=["effective_to", "updated_at"])

    try:
        yeni = EmployeeEmploymentTerms.objects.create(
            tenant_id=employee.tenant_id,
            employee=employee,
            effective_from=baslangic,
            employment_type=yeni_tip,
            working_model=yeni_model,
            workplace_id=yeni_isyeri,
            contract_type=yeni_sozlesme_tipi or "",
            contract_start_date=yeni_sozlesme_bas,
            contract_end_date=yeni_sozlesme_son,
            probation_end_date=yeni_deneme,
            reason=gerekce[:200],
            created_by=actor,
        )
    except DatabaseError as exc:
        raise OrganizationError(
            f"İstihdam koşulu geçmişi yazılamadı: {exc}"
        ) from exc

    log_action(
        tenant_id=employee.tenant_id,
        action="employee.terms.change",
        user=actor,
        table_name="employee_employment_terms",
        record_id=yeni.pk,
        changes={
            "employee_id": str(employee.pk),
            "old_employment_type": mevcut.employment_type if mevcut else None,
            "new_employment_type": yeni_tip,
            "old_working_model": mevcut.working_model if mevcut else None,
            "new_working_model": yeni_model,
            "old_workplace_id": (
                str(mevcut.workplace_id) if mevcut and mevcut.workplace_id else None
            ),
            "new_workplace_id": str(yeni_isyeri) if yeni_isyeri else None,
            "effective_from": baslangic.isoformat(),
            "reason": gerekce[:200],
        },
    )
    return yeni, {"degisiklik": True}
