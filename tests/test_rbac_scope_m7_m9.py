"""
M7-M9 — tenant RBAC scope altyapısı testleri (OD5 kapalı ✅).

Kapsanan iş kalemleri (docs/DATABASE.md):
  * **M7** — `manager` → `dept_manager` yerinde göçü + `tenant_officer` hedef rolü
  * **M8** — `hr.roles.scope_type` (kapalı küme + dokümandaki backfill)
  * **M9** — `hr.user_roles.scope_org_unit_id` (kısmi: FK M10 sonrası) + guard
    tetikleyicisi + OD5 fail-closed çözümleyici köprüsü

M7/M8 veri göçleri testte **yeniden oynatılır** (`rolleri_goc_et`,
`kapsamlari_doldur`): veri göçünün davranışı yalnızca "fresh install sonrası" değil,
"eski veri nasıl dönüşür" sorusuyla da doğrulanmalıdır.
"""

import importlib
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from apps.organization.models import Department
from apps.rbac.models import (
    Permission,
    Role,
    RolePermission,
    RoleScopeType,
    RoleType,
    UserRole,
)
from apps.rbac.permission_catalog import (
    ALL_PERMISSION_CODES,
    DEFAULT_ROLE_TEMPLATES,
    DEPT_MANAGER_PERMISSIONS,
    LEGACY_ROLE_CODE_MAP,
    PLATFORM_PERMISSIONS,
    TENANT_OFFICER_PERMISSIONS,
)
from apps.rbac.services import (
    RoleAssignmentError,
    assign_role_to_user,
    can_assign_role,
    create_default_roles,
    declared_permission_codes,
    effective_permission_codes,
    user_has_permission,
)
from apps.rbac.services.scope import (
    ORG_SCOPED_SCOPE_TYPES,
    assignment_grants_permissions,
    org_scope_required,
    resolve_assignment_org_scope,
)
from apps.tenancy.context import tenant_context

m7 = importlib.import_module("apps.rbac.migrations.0005_od5_rol_katalogu_gocu")
m8 = importlib.import_module("apps.rbac.migrations.0006_od5_role_scope_type")

#: Veri göçleri yalnızca `schema_editor.connection` kullanır.
SCHEMA_EDITOR = SimpleNamespace(connection=connection)

#: Eski `manager` şablonunun izin seti (M7 öncesi hâl).
LEGACY_MANAGER_PERMS = (
    "org.department.read",
    "employee.read",
    "leave.request.read",
    "leave.request.approve",
    "shift.read",
    "attendance.read",
    "document.read",
)


def _legacy_manager_kur(tenant, permissions=LEGACY_MANAGER_PERMS):  # noqa: ANN001
    """M7 öncesi hâli taklit eder: `manager` rolü + eski izin seti."""
    with tenant_context(tenant.id):
        rol = Role.objects.create(
            tenant=tenant,
            code="manager",
            name="Yönetici",
            role_type=RoleType.DEFAULT,
            is_assignable=True,
        )
        for kod in permissions:
            RolePermission.objects.create(
                tenant=tenant, role=rol, permission=Permission.objects.get(code=kod)
            )
        return rol


def _ikinci_kullanici(tenant, email="ikinci@ornek.test"):  # noqa: ANN001
    """Aynı kiracıda ikinci bir aktif kullanıcı (hedef kullanıcı testleri için)."""
    from apps.accounts.models import User

    with tenant_context(tenant.id):
        return User.objects.create_user(
            email=email, password="Guclu-Parola-789", tenant=tenant, first_name="Cem"
        )


# --- M7: göç ----------------------------------------------------------------


def test_m7_manager_yerinde_dept_manager_olur_atamalar_korunur(tenant_a, user_a) -> None:
    """
    Güvenli göç: rol **yerinde** yeniden adlandırılır (yeni rol üretilmez), böylece
    atama bağları ve rol kimliği korunur; izin seti hedefe çekilir.
    """
    rol = _legacy_manager_kur(tenant_a)
    with tenant_context(tenant_a.id):
        atama = UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        rol_id, atama_id = rol.pk, atama.pk
        rol_sayisi = Role.objects.count()

    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        # Yeni rol üretilmedi: yalnızca `tenant_officer` eklendi (1 → 2).
        assert Role.objects.count() == rol_sayisi + 1
        assert set(Role.objects.values_list("code", flat=True)) == {
            "dept_manager",
            "tenant_officer",
        }
        assert not Role.objects.filter(code="manager").exists()
        # Duplicate dept_manager oluşmadı.
        assert Role.objects.filter(code="dept_manager").count() == 1
        yeni = Role.objects.get(pk=rol_id)  # kimlik korunur
        assert yeni.code == "dept_manager"
        assert yeni.name == m7.TARGET_ROLE_NAME
        assert yeni.role_type == RoleType.DEFAULT

        # Atama kaybolmadı ve aynı role işaret ediyor.
        assert UserRole.objects.filter(pk=atama_id, role_id=rol_id).exists()
        atama.refresh_from_db()
        assert atama.role_id == rol_id

        kodlar = set(
            RolePermission.objects.filter(role=yeni).values_list("permission__code", flat=True)
        )
        # M7 göçü **donmuş** seti yazar; sonradan eklenen kodlar ayrı migration'la
        # (M14 → rbac/0011) gelir. Bu yüzden karşılaştırma göçün kendi setiyle yapılır.
        assert kodlar == set(m7.DEPT_MANAGER_PERMISSIONS)


def test_m7_eski_fazla_izinler_kalmaz(tenant_a) -> None:
    """Hedef sette olmayan eski izinler göçte **çıkarılır** (sessiz kalıntı olmaz)."""
    fazladan = (*LEGACY_MANAGER_PERMS, "user.manage", "employee.sensitive.read")
    rol = _legacy_manager_kur(tenant_a, permissions=fazladan)

    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        kodlar = set(
            RolePermission.objects.filter(role_id=rol.pk).values_list(
                "permission__code", flat=True
            )
        )
        assert "user.manage" not in kodlar
        assert "employee.sensitive.read" not in kodlar
        # M7 göçü **donmuş** seti yazar; sonradan eklenen kodlar ayrı migration'la
        # (M14 → rbac/0011) gelir. Bu yüzden karşılaştırma göçün kendi setiyle yapılır.
        assert kodlar == set(m7.DEPT_MANAGER_PERMISSIONS)


def test_m7_tenant_officer_hedef_izin_setiyle_olusturulur(tenant_a) -> None:
    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        officer = Role.objects.get(code="tenant_officer")
        assert officer.role_type == RoleType.DEFAULT
        assert officer.is_assignable is True
        assert officer.scope_type == RoleScopeType.TENANT  # M8 eşlemesi
        kodlar = set(
            RolePermission.objects.filter(role=officer).values_list("permission__code", flat=True)
        )
        assert kodlar == set(m7.TENANT_OFFICER_PERMISSIONS)
        # ŞY ile aynı olmadığı (kısıtlı hedef set) açıkça doğrulanır.
        assert kodlar < set(ALL_PERMISSION_CODES)


def test_m7_idempotent_ikinci_calisma_degisiklik_uretmez(tenant_a) -> None:
    _legacy_manager_kur(tenant_a)
    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        onceki = {(r.code, r.pk) for r in Role.objects.all()}
        onceki_izin = {
            (rp.role_id, rp.permission_id) for rp in RolePermission.objects.all()
        }

    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        assert {(r.code, r.pk) for r in Role.objects.all()} == onceki
        assert {
            (rp.role_id, rp.permission_id) for rp in RolePermission.objects.all()
        } == onceki_izin


def test_m7_ayni_kiracida_hedef_rol_varsa_fail_closed(tenant_a) -> None:
    """`dept_manager` zaten varsa göç sessizce birleştirmez; açık hata verir."""
    _legacy_manager_kur(tenant_a)
    with tenant_context(tenant_a.id):
        hedef = Role.objects.create(
            tenant=tenant_a, code="dept_manager", name="Departman Yöneticisi (elle)"
        )

    with pytest.raises(RuntimeError, match="conflict"):
        m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        # Hiçbir değişiklik yapılmadı (fail-closed).
        assert Role.objects.filter(code="manager").exists()
        assert Role.objects.get(pk=hedef.pk).code == "dept_manager"


def test_m7_silinmis_manager_sessiz_restore_edilmez(tenant_a) -> None:
    rol = _legacy_manager_kur(tenant_a)
    with tenant_context(tenant_a.id):
        rol.deleted_at = timezone.now()
        rol.save(update_fields=["deleted_at"])

    m7.rolleri_goc_et(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        rol.refresh_from_db()
        assert rol.deleted_at is not None, "silinmiş rol canlandırılmamalı"
        assert rol.code == "manager", "silinmiş rolün adı değiştirilmemeli"
        assert not Role.objects.filter(code="dept_manager").exists()
        # tenant_officer göçü yine de tamamlanır (bağımsız iş).
        assert Role.objects.filter(code="tenant_officer").exists()


def test_m7_geri_alma_atama_varsa_reddeder(tenant_a, user_a) -> None:
    """Geri alma veri kaybı yaratacaksa (atama varsa) çalışmaz (fail-closed)."""
    _legacy_manager_kur(tenant_a)
    m7.rolleri_goc_et(None, SCHEMA_EDITOR)
    with tenant_context(tenant_a.id):
        officer = Role.objects.get(code="tenant_officer")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=officer)

    with pytest.raises(RuntimeError, match="Geri alma güvenli değil"):
        m7.rolleri_geri_al(None, SCHEMA_EDITOR)


def test_m7_katalog_ile_donmus_kopya_ayrismaz() -> None:
    """
    Katalog (canlı) ile migration'daki DONMUŞ kopya ayrışmamalı.

    Ayrışırsa: yeni kurulum (katalog) ile mevcut kurulum (migration) farklı rol
    setine sahip olur — sessiz yetki farkı.
    """
    # M14 (izin modülü) katalogu AUTHORIZATION §6.2 **E** bölümündeki iki kodla
    # genişletti; P1-A (OD61) ise üç kod ekledi. Fark, **yalnızca bu bilinçli
    # deltalardan** oluşmalı (sessiz ayrışma yasak). Deltalar sırasıyla
    # `rbac/0011_m14_leave_permissions` ve `rbac/0012_p1a_permissions_roles` ile
    # mevcut kurulumlara da uygulanır.
    m14_delta = {"leave.request.cancel", "leave.balance.read"}
    p1a_dept_manager_delta = {"employee.history.read"}
    p1a_tenant_officer_delta = {"employee.history.read", "org.workplace.read"}
    # P5a V1 (fazla mesai talep/onay temeli): dört kod (bkz. `rbac/0013_p5a_overtime_permissions`).
    p5a_delta = {
        "overtime.request.create",
        "overtime.request.read",
        "overtime.request.approve",
        "overtime.request.cancel",
    }

    assert (
        set(DEPT_MANAGER_PERMISSIONS) - set(m7.DEPT_MANAGER_PERMISSIONS)
        == m14_delta | p1a_dept_manager_delta | p5a_delta
    )
    assert set(m7.DEPT_MANAGER_PERMISSIONS) - set(DEPT_MANAGER_PERMISSIONS) == set()
    assert (
        set(TENANT_OFFICER_PERMISSIONS) - set(m7.TENANT_OFFICER_PERMISSIONS)
        == m14_delta | p1a_tenant_officer_delta | p5a_delta
    )
    assert set(m7.TENANT_OFFICER_PERMISSIONS) - set(TENANT_OFFICER_PERMISSIONS) == set()
    assert LEGACY_ROLE_CODE_MAP == {m7.LEGACY_ROLE_CODE: m7.TARGET_ROLE_CODE}
    assert DEFAULT_ROLE_TEMPLATES["dept_manager"]["permissions"] == list(
        DEPT_MANAGER_PERMISSIONS
    )
    assert DEFAULT_ROLE_TEMPLATES["tenant_officer"]["permissions"] == list(
        TENANT_OFFICER_PERMISSIONS
    )


# --- M8: scope_type ---------------------------------------------------------


def test_m8_varsayilan_roller_dogru_kapsam_tipiyle_olusur(tenant_a) -> None:
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)

    beklenen = {
        "tenant_admin": RoleScopeType.TENANT,
        "tenant_officer": RoleScopeType.TENANT,
        "hr_manager": RoleScopeType.TENANT,
        "dept_manager": RoleScopeType.UNIT,
        "shift_manager": RoleScopeType.UNIT,
        "employee": RoleScopeType.SELF,
    }
    assert {kod: rol.scope_type for kod, rol in roller.items()} == beklenen
    # Eski `manager` kodu varsayılan sette YOK; hedef kodlar kesin adlarıyla var.
    assert "manager" not in roller
    assert set(roller) == set(beklenen)


def test_m8_backfill_dokumandaki_eslemeyi_uygular(tenant_a) -> None:
    """M8 backfill'i: bilinen kodlar doküman eşlemesine, custom roller TENANT'a."""
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        # Yanlış başlangıç durumu (backfill'in düzelttiği doğrulanır).
        Role.objects.filter(pk__in=[r.pk for r in roller.values()]).update(
            scope_type=RoleScopeType.SELF
        )
        custom = Role.objects.create(
            tenant=tenant_a, code="ozel_rol", name="Özel Rol", role_type=RoleType.CUSTOM
        )
        Role.objects.filter(pk=custom.pk).update(scope_type=RoleScopeType.SELF)

    m8.kapsamlari_doldur(None, SCHEMA_EDITOR)

    with tenant_context(tenant_a.id):
        assert Role.objects.get(code="tenant_admin").scope_type == RoleScopeType.TENANT
        assert Role.objects.get(code="tenant_officer").scope_type == RoleScopeType.TENANT
        assert Role.objects.get(code="hr_manager").scope_type == RoleScopeType.TENANT
        assert Role.objects.get(code="dept_manager").scope_type == RoleScopeType.UNIT
        assert Role.objects.get(code="employee").scope_type == RoleScopeType.SELF
        # Bilinen kod dışı (custom) rol güvenli varsayılana çekilir: kiracı geneli.
        assert Role.objects.get(pk=custom.pk).scope_type == RoleScopeType.TENANT


def test_m8_gecersiz_scope_type_db_de_reddedilir(tenant_a) -> None:
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, code="g1", name="G1")
        with pytest.raises(IntegrityError), transaction.atomic():
            Role.objects.filter(pk=rol.pk).update(scope_type="SYS")
        with pytest.raises(IntegrityError), transaction.atomic():
            Role.objects.filter(pk=rol.pk).update(scope_type="GLOBAL")


def test_m8_platform_rolleri_hr_roles_icinde_degil(tenant_a) -> None:
    """`SYS` kapsamı yalnız platform rollerinindir; tenant rollerinde `platform.*` yok."""
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        tum_kodlar = set(
            RolePermission.objects.filter(role__in=list(roller.values())).values_list(
                "permission__code", flat=True
            )
        )
    platform_kodlari = {kod for kod, _, _ in PLATFORM_PERMISSIONS}
    assert tum_kodlar & platform_kodlari == set()
    assert not Role.objects.filter(scope_type="SYS").exists()
    # `SYS` (yalnız platform rolleri) tenant kapalı kümesinde YOKTUR.
    assert "SYS" not in set(RoleScopeType.values)


# --- M9: scope_org_unit_id + guard + fail-closed ---------------------------


def test_m9_birim_kapsamsiz_unit_atamasi_db_de_reddedilir(tenant_a, user_a) -> None:
    """OD5: `UNIT` tipli rolde kapsam ZORUNLU; NULL 'tüm kiracı' olamaz."""
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        assert roller["dept_manager"].scope_type == RoleScopeType.UNIT
        with pytest.raises(DatabaseError, match="scope_org_unit_id"), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a, user=user_a, role=roller["dept_manager"]
            )


def test_m9_org_kapsamsiz_rolde_birim_kapsami_reddedilir(tenant_a, user_a) -> None:
    """`TENANT` tipli role birim kapsamı verilemez (tanımsız/kullanılmayan veri olmaz)."""
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        birim = Department.objects.create(tenant=tenant_a, code="GM", name="Genel Müdürlük")
        with pytest.raises(DatabaseError, match="NULL olmalidir"), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a,
                user=user_a,
                role=roller["tenant_admin"],
                scope_org_unit=birim,
            )


def test_m9_ayni_rol_farkli_birimlere_atanabilir_ayni_birim_tekrarlanamaz(
    tenant_a, user_a
) -> None:
    """OD5 §5.3 çoklu kapsam: aynı rol farklı birimler için atanabilir; tekrar edemez."""
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["dept_manager"]
        birim_1 = Department.objects.create(tenant=tenant_a, code="B1", name="Birim 1")
        birim_2 = Department.objects.create(tenant=tenant_a, code="B2", name="Birim 2")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol, scope_org_unit=birim_1)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol, scope_org_unit=birim_2)
        assert UserRole.objects.filter(user=user_a, role=rol).count() == 2
        with pytest.raises(IntegrityError), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a, user=user_a, role=rol, scope_org_unit=birim_1
            )
        # Kapsamsız (TENANT/SELF) rollerde çift atama hâlâ engellenir.
        officer = Role.objects.get(code="tenant_officer")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=officer)
        with pytest.raises(IntegrityError), transaction.atomic():
            UserRole.objects.create(tenant=tenant_a, user=user_a, role=officer)


def test_m9_unit_rol_kapsam_cozulunce_izin_uretir(tenant_a, user_a) -> None:
    """
    M10 sonrası: kapsam **çözülebildiği** için `dept_manager` izinleri etkin olur.

    (M9 turunda kapsam çözülemediği için bu atamalar izin üretmiyordu; fail-closed
    köprü artık gerçek alt ağaç çözümlemesine geçti.)
    """
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["dept_manager"]
        birim = Department.objects.create(tenant=tenant_a, code="FIN", name="Finans")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol, scope_org_unit=birim)

        atama = UserRole.objects.get(user=user_a, role=rol)
        assert resolve_assignment_org_scope(atama) == {birim.pk}
        assert assignment_grants_permissions(atama) is True

        assert effective_permission_codes(user_a) == set(DEPT_MANAGER_PERMISSIONS)
        assert declared_permission_codes(user_a) == set(DEPT_MANAGER_PERMISSIONS)
        assert user_has_permission(user_a, "employee.read") is True
        assert user_has_permission(user_a, "leave.request.approve") is True


def test_m9_silinmis_birim_izin_uretmez(tenant_a, user_a) -> None:
    """Silinmiş birime kapsamlı atama **izin üretmez** (OD5 §5.6 fail-closed)."""
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["dept_manager"]
        birim = Department.objects.create(tenant=tenant_a, code="FIN", name="Finans")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol, scope_org_unit=birim)
        assert effective_permission_codes(user_a)

        Department.objects.filter(pk=birim.pk).update(deleted_at=timezone.now())
        assert effective_permission_codes(user_a) == set()
        assert user_has_permission(user_a, "employee.read") is False


def test_m9_tenant_rolu_birim_kapsami_olmadan_calisir(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["tenant_admin"]
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert effective_permission_codes(user_a) == set(ALL_PERMISSION_CODES)
        assert user_has_permission(user_a, "role.manage") is True


def test_m9_self_rolu_mevcut_davranisini_korur(tenant_a, user_a) -> None:
    """`SELF` kapsamlı `employee` rolü kapsam çözümü beklenmez (mevcut davranış)."""
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["employee"]
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert user_has_permission(user_a, "employee.read") is True
        assert user_has_permission(user_a, "employee.update") is False


def test_m9_org_kapsam_zorunlulugu_sabitleri() -> None:
    assert ORG_SCOPED_SCOPE_TYPES == frozenset({"UNIT", "UNIT_DIRECT"})
    assert org_scope_required(RoleScopeType.UNIT) is True
    assert org_scope_required(RoleScopeType.UNIT_DIRECT) is True
    assert org_scope_required(RoleScopeType.TENANT) is False
    assert org_scope_required(RoleScopeType.SELF) is False


# --- fail-closed: atama durumları -------------------------------------------


def test_silinmis_atama_izin_uretmez(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["tenant_admin"]
        atama = UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert effective_permission_codes(user_a)
        atama.deleted_at = timezone.now()
        atama.save(update_fields=["deleted_at"])
        assert effective_permission_codes(user_a) == set()


def test_suresi_gecmis_atama_izin_uretmez(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["tenant_admin"]
        UserRole.objects.create(
            tenant=tenant_a,
            user=user_a,
            role=rol,
            expires_at=timezone.now() - timezone.timedelta(seconds=1),
        )
        assert effective_permission_codes(user_a) == set()
        # Süresi gelmemiş atama yetki verir.
        UserRole.objects.all().delete()
        UserRole.objects.create(
            tenant=tenant_a,
            user=user_a,
            role=rol,
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        assert effective_permission_codes(user_a) == set(ALL_PERMISSION_CODES)


def test_silinmis_rol_izin_uretmez(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["tenant_admin"]
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        rol.deleted_at = timezone.now()
        rol.save(update_fields=["deleted_at"])
        assert effective_permission_codes(user_a) == set()


def test_pasif_kullanici_izin_uretmez(tenant_a, user_a) -> None:
    with tenant_context(tenant_a.id):
        rol = create_default_roles(tenant_a)["tenant_admin"]
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert effective_permission_codes(user_a)
        # `is_active` türetilmiştir (status/deleted_at): pasif kullanıcı yetki alamaz.
        user_a.status = "suspended"
        user_a.save(update_fields=["status"])
        assert effective_permission_codes(user_a) == set()
        assert declared_permission_codes(user_a) == set()

        user_a.status = "active"
        user_a.save(update_fields=["status"])
        assert effective_permission_codes(user_a) == set(ALL_PERMISSION_CODES)

        user_a.deleted_at = timezone.now()
        user_a.save(update_fields=["deleted_at"])
        assert effective_permission_codes(user_a) == set()


# --- self-elevation ----------------------------------------------------------


def test_self_elevation_kendi_rolunu_atayamaz(tenant_a, user_a) -> None:
    hedef = _ikinci_kullanici(tenant_a)
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=roller["tenant_admin"])
        with pytest.raises(RoleAssignmentError, match="kendi rolünü"):
            assign_role_to_user(
                actor=user_a, target_user=user_a, role=roller["tenant_officer"]
            )
        # Başkasına atama yapılabilir ve denetim kaydı yazılır.
        atama = assign_role_to_user(
            actor=user_a, target_user=hedef, role=roller["tenant_officer"], reason="test"
        )
        assert atama.pk is not None
        from apps.audit.models import AuditLog

        kayit = AuditLog.objects.filter(action="role.assign").first()
        assert kayit is not None
        assert kayit.changes["role_code"] == "tenant_officer"


def test_tan_olmadigi_yetkiyi_dagitamaz(tenant_a, user_a) -> None:
    """`tenant_officer` (kısıtlı set) şirket yöneticisinin rollerini dağıtamaz."""
    hedef = _ikinci_kullanici(tenant_a)
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=roller["tenant_officer"])
        izin_var, gerekce = can_assign_role(user_a, roller["tenant_admin"])
        assert izin_var is False
        assert "role.manage" in gerekce  # officer'da rol yönetimi yok
        with pytest.raises(RoleAssignmentError):
            assign_role_to_user(
                actor=user_a, target_user=hedef, role=roller["tenant_admin"]
            )


def test_dept_manager_atamasi_kapsam_zorunlu_kilar(tenant_a, user_a) -> None:
    hedef = _ikinci_kullanici(tenant_a)
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        birim = Department.objects.create(tenant=tenant_a, code="FIN", name="Finans")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=roller["tenant_admin"])
        with pytest.raises(RoleAssignmentError, match="ZORUNLUDUR"):
            assign_role_to_user(actor=user_a, target_user=hedef, role=roller["dept_manager"])
        atama = assign_role_to_user(
            actor=user_a,
            target_user=hedef,
            role=roller["dept_manager"],
            scope_org_unit_id=birim.pk,
        )
        assert atama.scope_org_unit_id == birim.pk
        # M10 sonrası: kapsam çözülür → hedef set etkin olur (kapsam ∧ izin).
        assert effective_permission_codes(hedef) == set(DEPT_MANAGER_PERMISSIONS)
        assert declared_permission_codes(hedef) == set(DEPT_MANAGER_PERMISSIONS)


# --- tenant izolasyonu -------------------------------------------------------


def test_scope_kapsami_tenant_izolasyonunu_bozmaz(tenant_a, tenant_b, user_a) -> None:
    """Başka kiracının rolü, bu kiracının kullanıcısına atanamaz (guard tetikleyicisi)."""
    with tenant_context(tenant_b.id):
        yabanci_rol = create_default_roles(tenant_b)["tenant_admin"]

    with tenant_context(tenant_a.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            UserRole.objects.create(
                tenant=tenant_a, user=user_a, role=yabanci_rol
            )


def test_tenant_rolleri_birbirini_gormez(tenant_a, tenant_b) -> None:
    with tenant_context(tenant_a.id):
        create_default_roles(tenant_a)
    with tenant_context(tenant_b.id):
        create_default_roles(tenant_b)
    with tenant_context(tenant_a.id):
        assert set(Role.objects.values_list("code", flat=True)) == set(DEFAULT_ROLE_TEMPLATES)
    with tenant_context(tenant_b.id):
        assert set(Role.objects.values_list("code", flat=True)) == set(DEFAULT_ROLE_TEMPLATES)


# --- fresh install -----------------------------------------------------------


def test_fresh_install_hedef_rol_seti_ve_kapsamlari(tenant_a) -> None:
    with tenant_context(tenant_a.id):
        roller = create_default_roles(tenant_a)
        assert set(roller) == {
            "tenant_admin",
            "tenant_officer",
            "hr_manager",
            "dept_manager",
            "shift_manager",
            "employee",
        }
        assert not Role.objects.filter(code="manager").exists()
        # tenant_admin hedef katalogun tamamına sahip (platform kodları hariç).
        admin_kodlar = set(
            RolePermission.objects.filter(role=roller["tenant_admin"]).values_list(
                "permission__code", flat=True
            )
        )
        assert admin_kodlar == set(ALL_PERMISSION_CODES)
