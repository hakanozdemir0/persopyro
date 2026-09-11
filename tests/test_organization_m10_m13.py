"""
M10–M13 — organizasyon ve çalışan temel veri modeli testleri.

Kapsanan iş kalemleri (docs/DATABASE.md):
  * **M10** — `hr.departments` + `hr.positions` (RLS/FORCE, OD21/OD27/OD24)
  * **M11** — organizasyon ağacı DB garantileri (self-parent, döngü, derinlik 20,
    silinmiş ata, kiracı sınırı, taşıma)
  * **M12** — `hr.employees` (PII içermez) + `hr.employee_personal_data` (K2/K3)
  * **M13** — `hr.employee_manager` (RBAC değil; tek aktif birincil yönetici)
  * **M9 tamamlama** — `scope_org_unit_id` gerçek FK + kapsam guard'ı
  * **Kapsam çözümleyici** — UNIT/UNIT_DIRECT/TENANT/SELF + çoklu atama birleşimi
  * **İzin ∧ kapsam** — `can_access_employee` / `can_access_employee_personal_data`
"""

import datetime as dt
import uuid

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.organization.models import (
    Department,
    Employee,
    EmployeeManager,
    EmployeePersonalData,
    ManagerRelationType,
    Position,
    RecordStatus,
)
from apps.organization.services import (
    OrganizationError,
    OrganizationTreeError,
    can_access_employee,
    can_access_employee_personal_data,
    create_department,
    create_employee,
    create_position,
    move_department,
    move_employee,
    restore_department,
    set_department_manager,
    set_primary_manager,
    soft_delete_department,
    sub_tree_department_ids,
)
from apps.rbac.models import Role, UserRole
from apps.rbac.services import (
    create_default_roles,
    object_in_org_scope,
    resolve_department_scope,
    user_has_permission,
    user_org_scope,
)
from apps.tenancy.context import tenant_context

BUGUN = dt.date(2026, 1, 1)


# --- Yardımcılar -------------------------------------------------------------


def _birim(tenant, kod, ad=None, parent=None, **ek):  # noqa: ANN001
    """Birim oluşturur (ORM; yapısal testler için — servis guard'ları ayrı test edilir)."""
    with tenant_context(tenant.id):
        return Department.objects.create(
            tenant=tenant, code=kod, name=ad or kod.title(), parent=parent, **ek
        )


def _pozisyon(tenant, kod="PZ-1", ad="Uzman"):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Position.objects.create(tenant=tenant, code=kod, name=ad, level="mid")


def _calisan(tenant, birim, no="C-001", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return Employee.objects.create(
            tenant=tenant,
            department=birim,
            employee_no=no,
            hire_date=BUGUN,
            **ek,
        )


def _kisi_verisi(tenant, calisan, **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return EmployeePersonalData.objects.create(
            tenant=tenant,
            employee=calisan,
            first_name=ek.pop("first_name", "Test"),
            last_name=ek.pop("last_name", "Kisi"),
            **ek,
        )


def _kullanici(tenant, email="ikinci@ornek.test", **ek):  # noqa: ANN001
    with tenant_context(tenant.id):
        return User.objects.create_user(
            email=email, password="Guclu-Parola-789", tenant=tenant, first_name="Test", **ek
        )


def _tenant_admin(tenant, user):  # noqa: ANN001
    """TENANT kapsamlı yönetici rolü atar (org/çalışan yönetimi izinleri vardır)."""
    with tenant_context(tenant.id):
        roller = create_default_roles(tenant)
        UserRole.objects.create(tenant=tenant, user=user, role=roller["tenant_admin"])
        return roller["tenant_admin"]


def _dept_manager(tenant, user, birim):  # noqa: ANN001
    """`dept_manager` rolünü verilen birime kapsamlı atar (UNIT)."""
    with tenant_context(tenant.id):
        roller = create_default_roles(tenant)
        return UserRole.objects.create(
            tenant=tenant, user=user, role=roller["dept_manager"], scope_org_unit=birim
        )


@pytest.fixture
def finans(tenant_a):  # noqa: ANN001, ANN201
    """Ağaç: GM → (Finans → (Muhasebe, Bütçe), İK)."""
    gm = _birim(tenant_a, "GM", "Genel Müdürlük")
    finans_b = _birim(tenant_a, "FIN", "Finans", parent=gm)
    muhasebe = _birim(tenant_a, "MUH", "Muhasebe", parent=finans_b)
    butce = _birim(tenant_a, "BUT", "Bütçe", parent=finans_b)
    ik = _birim(tenant_a, "IK", "İnsan Kaynakları", parent=gm)
    return {"gm": gm, "finans": finans_b, "muhasebe": muhasebe, "butce": butce, "ik": ik}


# --- M10: departments / positions -------------------------------------------


def test_m10_department_ayni_kiraci_icinde_olusturulur(tenant_a) -> None:  # noqa: ANN001
    gm = _birim(tenant_a, "GM", "Genel Müdürlük")
    with tenant_context(tenant_a.id):
        assert Department.objects.count() == 1
        assert gm.tenant_id == tenant_a.id
        assert gm.status == RecordStatus.ACTIVE
        assert gm.deleted_at is None
        assert gm.created_at is not None and gm.updated_at is not None


def test_m10_department_kodu_aktif_kayitlarda_benzersiz(tenant_a) -> None:  # noqa: ANN001
    _birim(tenant_a, "GM")
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.create(tenant=tenant_a, code="GM", name="Kopya")
    # OD21: silinen kayıt kodu serbest bırakır, yeniden kullanılabilir.
    with tenant_context(tenant_a.id):
        Department.objects.filter(code="GM").update(deleted_at=timezone.now())
        Department.objects.create(tenant=tenant_a, code="GM", name="Yeni GM")
        assert Department.objects.filter(code="GM").count() == 2


def test_m10_position_tenanta_bagli_ve_kod_benzersiz(tenant_a) -> None:  # noqa: ANN001
    pozisyon = _pozisyon(tenant_a, "UZ-1", "Uzman")
    with tenant_context(tenant_a.id):
        assert pozisyon.tenant_id == tenant_a.id
        with pytest.raises(IntegrityError), transaction.atomic():
            Position.objects.create(tenant=tenant_a, code="UZ-1", name="Kopya")
    # Pozisyon birime bağlı DEĞİLDİR (docs/DATABASE.md B.4): birim kolonu yoktur.
    with tenant_context(tenant_a.id):
        assert not hasattr(pozisyon, "department_id")


def test_m10_cross_tenant_parent_reddedilir(tenant_a, tenant_b) -> None:  # noqa: ANN001
    yabanci = _birim(tenant_b, "B-GM")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        Department.objects.create(tenant=tenant_a, code="A-1", name="A Birim", parent=yabanci)


def test_m10_birim_durumu_kapali_kume_db_de_dayatilir(tenant_a) -> None:  # noqa: ANN001
    """`departments.status` kapalı kümesi DB `CHECK` ile korunur."""
    _birim(tenant_a, "GM")
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.filter(code="GM").update(status="bilinmeyen")
    with tenant_context(tenant_a.id):
        Department.objects.filter(code="GM").update(status=RecordStatus.ARCHIVED)
        assert Department.objects.get(code="GM").status == RecordStatus.ARCHIVED


# --- M11: ağaç garantileri --------------------------------------------------


def test_m11_self_parent_reddedilir(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        birim = Department.objects.create(tenant=tenant_a, code="GM", name="GM")
        with pytest.raises(IntegrityError), transaction.atomic():
            Department.objects.filter(pk=birim.pk).update(parent_id=birim.pk)


def test_m11_cycle_reddedilir(tenant_a) -> None:  # noqa: ANN001
    a = _birim(tenant_a, "A")
    b = _birim(tenant_a, "B", parent=a)
    c = _birim(tenant_a, "C", parent=b)
    # A→(B)→(C) iken A'nın ebeveynini C yapmak döngü kurar → DB reddeder.
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        Department.objects.filter(pk=a.pk).update(parent_id=c.pk)


def test_m11_derinlik_20_seviye_ile_sinirli(tenant_a) -> None:  # noqa: ANN001
    """20 seviye oluşturulabilir; 21. seviye reddedilir (OD24)."""
    ust = None
    for seviye in range(1, 21):
        ust = _birim(tenant_a, f"D{seviye:02d}", parent=ust)
    with tenant_context(tenant_a.id):
        assert Department.objects.count() == 20
        with pytest.raises(DatabaseError), transaction.atomic():
            Department.objects.create(tenant=tenant_a, code="D21", name="D21", parent=ust)


def test_m11_silinmis_ata_altina_yeni_aktif_birim_baglanamaz(tenant_a) -> None:  # noqa: ANN001
    gm = _birim(tenant_a, "GM")
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=gm.pk).update(deleted_at=timezone.now())
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        Department.objects.create(tenant=tenant_a, code="YENI", name="Yeni", parent=gm)


def test_m11_department_move_subtree_degistirir(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """
    Taşıma sonrası kapsam **yeni yapıya göre** hesaplanır (OD5 §5.8):
    Finans'ın altına İK taşınırsa Finans kapsamı büyür, GM kapsamı aynı kalır.
    """
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        onceki = resolve_department_scope(finans["finans"].pk)
    assert onceki == {finans["finans"].pk, finans["muhasebe"].pk, finans["butce"].pk}

    with tenant_context(tenant_a.id):
        move_department(
            actor=user_a,
            department=finans["ik"],
            new_parent=finans["finans"],
            reason="yeniden yapılanma",
        )
        sonraki = resolve_department_scope(finans["finans"].pk)
        gm_kapsam = resolve_department_scope(finans["gm"].pk)
    assert finans["ik"].pk in sonraki
    assert sonraki == onceki | {finans["ik"].pk}
    assert finans["ik"].pk in gm_kapsam  # GM alt ağacı hâlâ tümünü kapsar

    # Taşıma yüksek etkili denetim olayı yazar.
    with tenant_context(tenant_a.id):
        kayit = AuditLog.objects.filter(action="org.unit.move").first()
    assert kayit is not None
    assert kayit.changes["new_parent_id"] == str(finans["finans"].pk)


def test_m11_donanim_tetikleyicisi_ve_indexler_var(db) -> None:  # noqa: ANN001, ARG001
    """OD24 garantileri veritabanında: tetikleyici + `(tenant_id, parent_id)` indeksi."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid='hr.departments'::regclass "
            "AND tgname='trg_departments_tree_guard'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND tablename='departments' "
            "AND indexname='departments_tenant_parent_idx'"
        )
        assert cur.fetchone()[0] == 1
        # `path`/closure kolonu EKLENMEZ (OD24).
        cur.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' "
            "AND table_name='departments' AND column_name IN ('path', 'tree_path', 'lft', 'rgt')"
        )
        assert cur.fetchone()[0] == 0


def test_m11_service_cycle_denemesini_reddeder(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id), pytest.raises(OrganizationTreeError):
        move_department(
            actor=user_a, department=finans["gm"], new_parent=finans["muhasebe"], reason="döngü"
        )


# --- M12: employees + employee_personal_data ---------------------------------


def test_m12_calisan_birim_zorunlu_ve_ayni_kiracida(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    yabanci = _birim(tenant_b, "B-GM")
    _calisan(tenant_a, birim, no="C-1")

    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        Employee.objects.create(
            tenant=tenant_a, department=yabanci, employee_no="C-2", hire_date=BUGUN
        )


def test_m12_calisan_pozisyonu_ayni_kiracida(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    yabanci_pozisyon = _pozisyon(tenant_b, "B-PZ")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        Employee.objects.create(
            tenant=tenant_a,
            department=birim,
            position=yabanci_pozisyon,
            employee_no="C-3",
            hire_date=BUGUN,
        )


def test_m12_calisan_kaydi_pii_icermez(tenant_a) -> None:  # noqa: ANN001
    """`employees` yalnızca iş verisi taşır; ad/kimlik/iletişim ALANI YOKTUR."""
    alanlar = {f.name for f in Employee._meta.get_fields()}
    assert not (alanlar & {"first_name", "last_name", "national_id", "iban", "personal_email"})


def test_m12_kisisel_veri_bir_bir_iliskidir(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim)
    _kisi_verisi(tenant_a, calisan)
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeePersonalData.objects.create(
            tenant=tenant_a, employee=calisan, first_name="Kopya", last_name="Kayıt"
        )


def test_m12_kisisel_veri_cross_tenant_reddedilir(tenant_a, tenant_b) -> None:  # noqa: ANN001
    yabanci_birim = _birim(tenant_b, "B-GM")
    yabanci_calisan = _calisan(tenant_b, yabanci_birim, no="B-1")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        EmployeePersonalData.objects.create(
            tenant=tenant_a, employee=yabanci_calisan, first_name="X", last_name="Y"
        )


def test_m12_k3_alanlari_sifreli_saklanir(tenant_a) -> None:  # noqa: ANN001
    """K3 alanları veritabanında **düz metin** durmaz (Fernet)."""
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim)
    _kisi_verisi(tenant_a, calisan, health_notes="kronik rahatsızlık", disability_status="var")

    with tenant_context(tenant_a.id):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT health_notes, disability_status FROM hr.employee_personal_data "
                "WHERE employee_id = %s",
                [str(calisan.pk)],
            )
            ham_saglik, ham_engel = cur.fetchone()
    assert "kronik" not in ham_saglik
    assert ham_engel != "var"

    # Okuma şeffaftır (ORM çözer).
    with tenant_context(tenant_a.id):
        kayit = EmployeePersonalData.objects.get(pk=calisan.pk)
    assert kayit.health_notes == "kronik rahatsızlık"
    assert kayit.disability_status == "var"


# --- M13: employee_manager ---------------------------------------------------


def test_m13_calisan_kendi_yoneticisi_olamaz(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim)
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=calisan,
            effective_from=BUGUN,
        )


def test_m13_yonetici_cross_tenant_olamaz(tenant_a, tenant_b) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim, no="C-1")
    yabanci = _calisan(tenant_b, _birim(tenant_b, "B-GM"), no="B-1")
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=yabanci,
            effective_from=BUGUN,
        )


def test_m13_tek_aktif_birincil_yonetici(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim, no="C-1")
    yonetici_1 = _calisan(tenant_a, birim, no="C-2")
    yonetici_2 = _calisan(tenant_a, birim, no="C-3")

    with tenant_context(tenant_a.id):
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=yonetici_1,
            relation_type=ManagerRelationType.PRIMARY,
            effective_from=BUGUN,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            EmployeeManager.objects.create(
                tenant=tenant_a,
                employee=calisan,
                manager_employee=yonetici_2,
                relation_type=ManagerRelationType.PRIMARY,
                effective_from=BUGUN,
            )


def test_m13_ikincil_ve_delege_iliskileri_acik(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim, no="C-1")
    yonetici = _calisan(tenant_a, birim, no="C-2")
    diger = _calisan(tenant_a, birim, no="C-3")

    with tenant_context(tenant_a.id):
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=yonetici,
            relation_type=ManagerRelationType.PRIMARY,
            effective_from=BUGUN,
        )
        # İkincil ve delege ilişkiler birincil benzersizliği BOZMAZ.
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=diger,
            relation_type=ManagerRelationType.SECONDARY,
            effective_from=BUGUN,
        )
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=diger,
            relation_type=ManagerRelationType.DELEGATE,
            effective_from=BUGUN,
            effective_to=BUGUN + dt.timedelta(days=30),
        )
        assert EmployeeManager.objects.filter(employee=calisan).count() == 3
        assert (
            EmployeeManager.objects.filter(
                employee=calisan, relation_type=ManagerRelationType.PRIMARY
            ).count()
            == 1
        )


def test_m13_silinmis_calisan_ile_yeni_aktif_iliski_kurulamaz(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim, no="C-1")
    yonetici = _calisan(tenant_a, birim, no="C-2")
    with tenant_context(tenant_a.id):
        Employee.objects.filter(pk=yonetici.pk).update(deleted_at=timezone.now())
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=yonetici,
            effective_from=BUGUN,
        )


def test_m13_tarih_araligi_mantikli_olmali(tenant_a) -> None:  # noqa: ANN001
    birim = _birim(tenant_a, "GM")
    calisan = _calisan(tenant_a, birim, no="C-1")
    yonetici = _calisan(tenant_a, birim, no="C-2")
    with tenant_context(tenant_a.id), pytest.raises(IntegrityError), transaction.atomic():
        EmployeeManager.objects.create(
            tenant=tenant_a,
            employee=calisan,
            manager_employee=yonetici,
            effective_from=BUGUN,
            effective_to=BUGUN - dt.timedelta(days=1),
        )


# --- M9 tamamlama: gerçek FK + kapsam guard'ı --------------------------------


def test_m9_scope_org_unit_gercek_fk(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """
    M9 tamamlandı: `scope_org_unit_id` **gerçek FK**'dir (`hr.departments.id`).

    * Var olmayan birim: kapsam guard'ı **ve** FK reddeder (fail-closed).
    * Var olan birim: atama yapılabilir ve FK gerçekten kurulur.
    """
    _tenant_admin(tenant_a, user_a)
    hedef = _kullanici(tenant_a, "dept.manager@ornek.test")
    with tenant_context(tenant_a.id):
        rol = Role.objects.get(code="dept_manager")
        with pytest.raises(DatabaseError), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a, user=hedef, role=rol, scope_org_unit_id=uuid.uuid4()
            )
        atama = UserRole.objects.create(
            tenant=tenant_a, user=hedef, role=rol, scope_org_unit=finans["finans"]
        )
        assert atama.scope_org_unit_id == finans["finans"].pk
        assert atama.scope_org_unit.code == "FIN"

    # FK kısıtı katalogda gerçekten var (hedef tablo doğru).
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.user_roles'::regclass "
            "AND contype='f' AND confrelid='hr.departments'::regclass"
        )
        assert cur.fetchone()[0] == 1

    # Fiziksel silme engellenir (atama varken): FK "NO ACTION" + app rolünde DELETE yok.
    # FK `DEFERRABLE INITIALLY DEFERRED` olduğu için kontrolü hemen zorluyoruz.
    with tenant_context(tenant_a.id), pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cur.execute("DELETE FROM hr.departments WHERE id = %s", [str(finans["finans"].pk)])


def test_m9_silinmis_birim_kapsam_olarak_atanamaz(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    hedef = _kullanici(tenant_a, "dm2@ornek.test")
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=finans["butce"].pk).update(deleted_at=timezone.now())
        rol = Role.objects.get(code="dept_manager")
        with pytest.raises(DatabaseError), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a, user=hedef, role=rol, scope_org_unit=finans["butce"]
            )


def test_m9_unit_rol_kapsamsiz_atama_reddedilir(tenant_a, user_a) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    hedef = _kullanici(tenant_a, "dm3@ornek.test")
    with tenant_context(tenant_a.id):
        rol = Role.objects.get(code="dept_manager")
        with pytest.raises(DatabaseError, match="scope_org_unit_id"), transaction.atomic():
            UserRole.objects.create(tenant=tenant_a, user=hedef, role=rol)


# --- Kapsam çözümleyici -----------------------------------------------------


def test_scope_unit_birim_ve_alt_agac(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        kapsam = resolve_department_scope(finans["finans"].pk)
    assert kapsam == {finans["finans"].pk, finans["muhasebe"].pk, finans["butce"].pk}


def test_scope_unit_kardes_birmi_dondurmez(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        kapsam = resolve_department_scope(finans["finans"].pk)
    assert finans["ik"].pk not in kapsam
    assert finans["gm"].pk not in kapsam


def test_scope_unit_direct_yalniz_direkt_birim(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        kapsam = resolve_department_scope(finans["finans"].pk, include_descendants=False)
    assert kapsam == {finans["finans"].pk}


def test_scope_multi_assignment_birlesim(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    hedef = _kullanici(tenant_a, "cok.kapsam@ornek.test")
    with tenant_context(tenant_a.id):
        rol = Role.objects.get(code="dept_manager")
        UserRole.objects.create(
            tenant=tenant_a, user=hedef, role=rol, scope_org_unit=finans["finans"]
        )
        UserRole.objects.create(
            tenant=tenant_a, user=hedef, role=rol, scope_org_unit=finans["ik"]
        )
        kapsam = user_org_scope(hedef)
    assert kapsam.unit_ids == {
        finans["finans"].pk,
        finans["muhasebe"].pk,
        finans["butce"].pk,
        finans["ik"].pk,
    }
    assert kapsam.tenant_wide is False


def test_scope_silinmis_birim_kapsam_uretmez(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=finans["muhasebe"].pk).update(deleted_at=timezone.now())
        kapsam = resolve_department_scope(finans["finans"].pk)
    assert finans["muhasebe"].pk not in kapsam
    assert kapsam == {finans["finans"].pk, finans["butce"].pk}


def test_scope_silinmis_birim_kok_ise_kapsam_yok(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=finans["finans"].pk).update(deleted_at=timezone.now())
        assert resolve_department_scope(finans["finans"].pk) is None


def test_scope_expired_ve_silinmis_atama_kapsam_uretmez(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    hedef = _kullanici(tenant_a, "kapsam.yok@ornek.test")
    with tenant_context(tenant_a.id):
        rol = Role.objects.get(code="dept_manager")
        UserRole.objects.create(
            tenant=tenant_a,
            user=hedef,
            role=rol,
            scope_org_unit=finans["finans"],
            expires_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        assert user_org_scope(hedef).is_empty

        UserRole.objects.all().delete()
        UserRole.objects.create(
            tenant=tenant_a,
            user=hedef,
            role=rol,
            scope_org_unit=finans["finans"],
            deleted_at=timezone.now(),
        )
        assert user_org_scope(hedef).is_empty


def test_scope_baska_kiraci_birimini_cozemez(tenant_a, tenant_b) -> None:  # noqa: ANN001
    yabanci = _birim(tenant_b, "B-FIN")
    with tenant_context(tenant_a.id):
        assert resolve_department_scope(yabanci.pk) is None


def test_scope_tenant_role_kiraci_geneli(tenant_a, user_a) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        kapsam = user_org_scope(user_a)
    assert kapsam.tenant_wide is True
    assert kapsam.unit_ids == frozenset()


def test_scope_employee_rolu_self(tenant_a, finans) -> None:  # noqa: ANN001
    kullanici = _kullanici(tenant_a, "self@ornek.test")
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        UserRole.objects.create(tenant=tenant_a, user=kullanici, role=roller["employee"])
        kapsam = user_org_scope(kullanici)
    assert kapsam.self_only is True
    assert kapsam.tenant_wide is False


# --- İzin ∧ kapsam ----------------------------------------------------------


def test_yetki_subtree_calisanina_allow(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """dept_manager: doğru izin + alt ağaçtaki çalışan → ALLOW."""
    yonetici = _kullanici(tenant_a, "finans.muduru@ornek.test")
    _dept_manager(tenant_a, yonetici, finans["finans"])
    calisan = _calisan(tenant_a, finans["muhasebe"], no="MUH-1")

    with tenant_context(tenant_a.id):
        assert user_has_permission(yonetici, "employee.read") is True
        assert can_access_employee(yonetici, calisan, "employee.read") is True
        assert object_in_org_scope(yonetici, department_id=finans["butce"].pk) is True


def test_yetki_kardes_birim_calisanina_deny(tenant_a, finans) -> None:  # noqa: ANN001
    """Aynı izin + kardeş birimdeki çalışan → DENY."""
    yonetici = _kullanici(tenant_a, "finans.muduru2@ornek.test")
    _dept_manager(tenant_a, yonetici, finans["finans"])
    kardes_calisan = _calisan(tenant_a, finans["ik"], no="IK-1")

    with tenant_context(tenant_a.id):
        assert can_access_employee(yonetici, kardes_calisan, "employee.read") is False
        # Üst birim (GM) de kapsam dışıdır: UNIT kapsamı aşağı doğrudur, yukarı değil.
        assert object_in_org_scope(yonetici, department_id=finans["gm"].pk) is False
        # Çalışan CSV çıktısı çekmek gibi başka bir izin kodu da kapsamla sınırlıdır.
        assert can_access_employee(yonetici, kardes_calisan, "leave.request.approve") is False


def test_yetki_izin_yoksa_deny(tenant_a, finans) -> None:  # noqa: ANN001
    """Kapsam içinde ama izin yok → DENY."""
    yonetici = _kullanici(tenant_a, "finans.muduru3@ornek.test")
    _dept_manager(tenant_a, yonetici, finans["finans"])
    calisan = _calisan(tenant_a, finans["finans"], no="FIN-1")

    with tenant_context(tenant_a.id):
        assert user_has_permission(yonetici, "employee.delete") is False
        assert can_access_employee(yonetici, calisan, "employee.delete") is False
        # dept_manager'ın hedef seti (§6.2 DY) bu izni içermez.
        assert user_has_permission(yonetici, "employee.sensitive.read") is False


def test_yetki_tenant_rolu_kiraci_icindeki_calisana_allow(tenant_a, user_a, finans) -> None:  # noqa: ANN001, E501
    _tenant_admin(tenant_a, user_a)
    calisan = _calisan(tenant_a, finans["ik"], no="IK-2")
    with tenant_context(tenant_a.id):
        assert can_access_employee(user_a, calisan, "employee.read") is True


def test_yetki_cross_tenant_calisana_deny(tenant_a, tenant_b, user_a) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    yabanci_calisan = _calisan(tenant_b, _birim(tenant_b, "B-GM"), no="B-9")
    with tenant_context(tenant_a.id):
        assert can_access_employee(user_a, yabanci_calisan, "employee.read") is False


def test_yetki_kapsam_kisisel_veriyi_otomatik_acmaz(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """
    OD5 §5.7: organizasyon kapsamı K2/K3 erişimi **vermez**.

    * `dept_manager` kapsam içindeki çalışanı **yönetebilir** (employee.read ✓)
      ama kişisel veriyi okuyamaz (izin setinde `employee.sensitive.read` yok).
    * `tenant_admin` (kapsam ✓, izin ✓) bile koşullar (MFA + amaç) sağlanmadan okuyamaz.
    """
    yonetici = _kullanici(tenant_a, "finans.muduru4@ornek.test")
    _dept_manager(tenant_a, yonetici, finans["finans"])
    calisan = _calisan(tenant_a, finans["finans"], no="FIN-2")
    _kisi_verisi(tenant_a, calisan)

    with tenant_context(tenant_a.id):
        assert can_access_employee(yonetici, calisan, "employee.read") is True
        assert can_access_employee_personal_data(yonetici, calisan) is False

    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        assert user_has_permission(user_a, "employee.sensitive.read") is True
        # Koşullar yoksa yine DENY; koşullar sağlanınca ALLOW.
        assert can_access_employee_personal_data(user_a, calisan) is False
        assert (
            can_access_employee_personal_data(user_a, calisan, mfa_ok=True, purpose_given=True)
            is True
        )
        # `employee.read` izni kişisel veri yolunu AÇMAZ (izin kodları ayrıdır).
        assert can_access_employee(user_a, calisan, "employee.read") is True


def test_yetki_self_calisan_kendi_kaydi(tenant_a, finans) -> None:  # noqa: ANN001
    kullanici = _kullanici(tenant_a, "calisan.self@ornek.test")
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        UserRole.objects.create(tenant=tenant_a, user=kullanici, role=roller["employee"])
        kendi = Employee.objects.create(
            tenant=tenant_a,
            department=finans["finans"],
            employee_no="SELF-1",
            hire_date=BUGUN,
            user=kullanici,
        )
        digeri = Employee.objects.create(
            tenant=tenant_a,
            department=finans["finans"],
            employee_no="SELF-2",
            hire_date=BUGUN,
        )
        assert can_access_employee(kullanici, kendi, "employee.read") is True
        assert can_access_employee(kullanici, digeri, "employee.read") is False


# --- RLS / yetkiler ---------------------------------------------------------


def test_yeni_tenant_tablolarinda_rls_force(tenant_a) -> None:  # noqa: ANN001
    """Beş yeni tablo: RLS + FORCE + `tenant_isolation` politikası."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
            "(SELECT count(*) FROM pg_policies p WHERE p.schemaname='hr' "
            " AND p.tablename=c.relname AND p.policyname='tenant_isolation') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='hr' AND c.relname = ANY(%s)",
            [
                [
                    "departments",
                    "positions",
                    "employees",
                    "employee_personal_data",
                    "employee_manager",
                ]
            ],
        )
        satirlar = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    assert len(satirlar) == 5
    for tablo, (rls, force, politika) in satirlar.items():
        assert rls and force, f"hr.{tablo} RLS/FORCE eksik"
        assert politika == 1, f"hr.{tablo} tenant_isolation politikası yok"


def test_platform_rolu_yeni_hr_tablolarina_erisemez(db) -> None:  # noqa: ANN001, ARG001
    """`persopyro_platform` (ayrı güvenlik sınırı) bu tablolarda hiçbir yetkiye sahip olamaz."""
    from django.conf import settings

    with connection.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [settings.PLATFORM_DB_ROLE])
        if cur.fetchone() is None:
            pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok (süper kullanıcı oluşturur).")
        for tablo in (
            "departments",
            "positions",
            "employees",
            "employee_personal_data",
            "employee_manager",
        ):
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    [settings.PLATFORM_DB_ROLE, f"hr.{tablo}", yetki],
                )
                assert cur.fetchone()[0] is False, f"{settings.PLATFORM_DB_ROLE} hr.{tablo} {yetki}"


def test_app_rolu_soft_delete_ilkesi_korunur(db) -> None:  # noqa: ANN001, ARG001
    """
    Runtime rolü yeni tablolarda `DELETE` kullanamaz (OD21: silme = `deleted_at`).

    Bu daraltma, `ON DELETE CASCADE` zincirini de (employees → personal_data)
    imkânsız kılar: kişisel veri runtime yolunda **fiziksel olarak silinemez**.
    """
    from django.conf import settings

    with connection.cursor() as cur:
        for tablo in ("employees", "employee_personal_data", "departments", "positions"):
            cur.execute(
                "SELECT has_table_privilege(%s, %s, 'DELETE')",
                [settings.APP_DB_ROLE, f"hr.{tablo}"],
            )
            assert cur.fetchone()[0] is False, f"{settings.APP_DB_ROLE} hr.{tablo} DELETE almamalı"


# --- Servisler ve denetim ---------------------------------------------------


def test_servis_birim_silme_calisan_varsa_reddedilir(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """Alt birimi olmayan bir birim, içinde aktif çalışan varsa silinemez (OD24 §4)."""
    _tenant_admin(tenant_a, user_a)
    _calisan(tenant_a, finans["muhasebe"], no="MUH-9")
    with tenant_context(tenant_a.id), pytest.raises(OrganizationError, match="aktif çalışan"):
        soft_delete_department(actor=user_a, department=finans["muhasebe"], reason="test")


def test_servis_birim_silme_alt_birim_varsa_reddedilir(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id), pytest.raises(OrganizationError, match="alt birim"):
        soft_delete_department(actor=user_a, department=finans["finans"], reason="test")


def test_servis_birim_silme_kapsam_atamalarini_devre_disi_birakir(tenant_a, user_a, finans) -> None:  # noqa: ANN001, E501
    """
    OD5 §5.6/OD21: birim silinince kapsam ataması **devre dışı** bırakılır ve birim
    restore edilse bile atama **sessizce** yetki kazanmaz.
    """
    _tenant_admin(tenant_a, user_a)
    yonetici = _kullanici(tenant_a, "silinecek.birim@ornek.test")
    atama = _dept_manager(tenant_a, yonetici, finans["ik"])
    kapsam_icindeki_calisan = _calisan(tenant_a, finans["gm"], no="GM-50")

    with tenant_context(tenant_a.id):
        assert can_access_employee(yonetici, kapsam_icindeki_calisan, "employee.read") is False

    with tenant_context(tenant_a.id):
        soft_delete_department(actor=user_a, department=finans["ik"], reason="kapanan birim")
        atama.refresh_from_db()
    assert atama.deleted_at is not None, "kapsam ataması devre dışı bırakılmalı"
    with tenant_context(tenant_a.id):
        assert user_org_scope(yonetici).is_empty

    with tenant_context(tenant_a.id):
        restore_department(actor=user_a, department=finans["ik"], reason="geri açıldı")
        atama.refresh_from_db()
        kayit = AuditLog.objects.filter(action="org.unit.restore").first()
    assert atama.deleted_at is not None, "restore atamayı canlandırmamalı (sessiz yetki yok)"
    with tenant_context(tenant_a.id):
        assert user_org_scope(yonetici).is_empty
        assert can_access_employee(yonetici, kapsam_icindeki_calisan, "employee.read") is False
    assert kayit is not None and kayit.changes["assignments_revived"] == 0


def test_servis_islemleri_denetim_kaydi_yazar(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        yeni = create_department(
            actor=user_a, tenant=tenant_a, code="HUKUK", name="Hukuk", parent=finans["gm"]
        )
        pozisyon = create_position(actor=user_a, tenant=tenant_a, code="AV-K", name="Avukat")
        calisan = create_employee(
            actor=user_a,
            tenant=tenant_a,
            department=yeni,
            employee_no="HK-1",
            hire_date=BUGUN,
            position=pozisyon,
        )
        ikinci = Employee.objects.create(
            tenant=tenant_a, department=finans["gm"], employee_no="GM-1", hire_date=BUGUN
        )
        move_employee(actor=user_a, employee=calisan, department=finans["gm"], reason="rotasyon")
        set_department_manager(actor=user_a, department=yeni, employee=ikinci, reason="sorumlu")
        yonetici = Employee.objects.create(
            tenant=tenant_a, department=finans["gm"], employee_no="GM-2", hire_date=BUGUN
        )
        set_primary_manager(
            actor=user_a, employee=calisan, manager_employee=yonetici, reason="atama"
        )

        eylemler = set(AuditLog.objects.values_list("action", flat=True))
        assert {
            "org.unit.create",
            "org.position.create",
            "employee.create",
            "employee.department_change",
            "org.unit.manager_change",
            "employee.primary_manager_set",
        } <= eylemler

        # Denetim kayıtlarında ham kişisel veri DEĞERİ bulunmaz (yalnız kimlik/alan düzeyi).
        for kayit in AuditLog.objects.filter(action__startswith="employee."):
            assert "first_name" not in str(kayit.changes)


def test_servis_birincil_yonetici_degisimi_eski_iliskiyi_kapatir(tenant_a, user_a, finans) -> None:  # noqa: ANN001, E501
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        calisan = Employee.objects.create(
            tenant=tenant_a, department=finans["finans"], employee_no="FIN-20", hire_date=BUGUN
        )
        eski = Employee.objects.create(
            tenant=tenant_a, department=finans["finans"], employee_no="FIN-21", hire_date=BUGUN
        )
        yeni = Employee.objects.create(
            tenant=tenant_a, department=finans["finans"], employee_no="FIN-22", hire_date=BUGUN
        )

        set_primary_manager(
            actor=user_a,
            employee=calisan,
            manager_employee=eski,
            effective_from=BUGUN,
            reason="ilk",
        )
        set_primary_manager(
            actor=user_a,
            employee=calisan,
            manager_employee=yeni,
            effective_from=BUGUN + dt.timedelta(days=10),
            reason="değişim",
        )

        iliskiler = list(
            EmployeeManager.objects.filter(employee=calisan).order_by("effective_from")
        )
        assert len(iliskiler) == 2
        assert iliskiler[0].effective_to == BUGUN + dt.timedelta(days=9)
        assert iliskiler[1].effective_to is None
        assert iliskiler[1].manager_employee_id == yeni.pk
        # Aynı anda yalnızca BİR açık birincil ilişki vardır.
        assert (
            EmployeeManager.objects.filter(
                employee=calisan, effective_to__isnull=True, deleted_at__isnull=True
            ).count()
            == 1
        )


def test_servis_kardes_birim_yoneticisi_calisan_olusturamaz(tenant_a, finans) -> None:  # noqa: ANN001
    """`employee.create` izni olmayan dept_manager çalışan oluşturamaz (izin kapısı)."""
    yonetici = _kullanici(tenant_a, "kapsam.disi@ornek.test")
    _dept_manager(tenant_a, yonetici, finans["finans"])
    with tenant_context(tenant_a.id), pytest.raises(OrganizationError, match="employee.create"):
        create_employee(
            actor=yonetici,
            tenant=tenant_a,
            department=finans["finans"],
            employee_no="X-1",
            hire_date=BUGUN,
        )


def test_servis_kapsam_disi_birimde_calisan_tasinamaz(tenant_a, user_a, finans) -> None:  # noqa: ANN001
    """
    İzin ∧ kapsam: `tenant_officer` (TENANT kapsamlı, `employee.update` izinli) çalışanı
    taşıyabilir; ancak kapsamı **kardeş birimle sınırlı** bir aktör taşıyamaz.

    Burada kapsam dışılık, hedef birim aktörün kapsamında olmadığında **reddedilir**
    (fail-closed) — izin tek başına yetmez.
    """
    _tenant_admin(tenant_a, user_a)
    with tenant_context(tenant_a.id):
        sube_yoneticisi = User.objects.create_user(
            email="sube@ornek.test",
            password="Guclu-Parola-999",
            tenant=tenant_a,
            first_name="Şube",
        )
        roller = create_default_roles(tenant_a)
        UserRole.objects.create(
            tenant=tenant_a,
            user=sube_yoneticisi,
            role=roller["dept_manager"],
            scope_org_unit=finans["finans"],
        )
        # dept_manager'ın izin setinde employee.update vardır ama kapsamı dar.
        calisan = Employee.objects.create(
            tenant=tenant_a, department=finans["finans"], employee_no="FIN-30", hire_date=BUGUN
        )
        assert can_access_employee(sube_yoneticisi, calisan, "employee.update") is True
        # Kapsam DIŞINDAKİ birime taşıma denemesi reddedilir.
        with pytest.raises(OrganizationError, match="kapsam"):
            move_employee(
                actor=sube_yoneticisi,
                employee=calisan,
                department=finans["ik"],
                reason="kapsam dışı",
            )


def test_subtree_ids_cozulemezse_hata(tenant_a, finans) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        Department.objects.filter(pk=finans["finans"].pk).update(deleted_at=timezone.now())
    with pytest.raises(OrganizationError):
        sub_tree_department_ids(finans["finans"])


# --- OD27 -------------------------------------------------------------------


def test_od27_company_id_ve_legal_entity_yok() -> None:  # noqa: ANN001
    """v1'de tek tüzel kişilik: hiçbir organizasyon tablosunda şirket kolonu yoktur."""
    for model in (Department, Position, Employee, EmployeePersonalData, EmployeeManager):
        alanlar = {f.name for f in model._meta.get_fields()}
        assert "company" not in alanlar and "legal_entity" not in alanlar
        assert "company_id" not in alanlar and "legal_entity_id" not in alanlar
