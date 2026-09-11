"""
F7 regresyon testleri — soft-delete edilmiş RBAC kayıtları yetki vermemeli.

Kural (docs/AUTHORIZATION.md OD21, docs/SECURITY.md F7/T20): yetki çözümlemesinde
kullanılan her sorgu `deleted_at IS NULL` uygular. Silinmiş rol, rol-izin bağı, rol
ataması veya istisna **yetki sonucuna katkı sağlamaz**; silinmiş `deny` istisnası da
aktif karar sayılmaz. Ayrıca silinmiş varsayılan rol sessizce canlandırılmaz.
"""

import pytest
from django.utils import timezone

from apps.rbac.models import (
    Permission,
    PermissionEffect,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from apps.rbac.permission_catalog import ALL_PERMISSION_CODES
from apps.rbac.services import effective_permission_codes
from apps.rbac.services.roles import SoftDeletedDefaultRoleError, create_default_roles
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db


@pytest.fixture
def izinler(db):  # noqa: ANN001, ANN201
    """Testin kullandığı izin kodları (katalogda mevcut olanlar)."""
    sonuc = {}
    for kod, ad in (
        ("employee.read", "Çalışan görme"),
        ("leave.request.approve", "İzin onaylama"),
        ("role.manage", "Rol yönetimi"),
    ):
        sonuc[kod], _ = Permission.objects.get_or_create(code=kod, defaults={"name": ad})
    return sonuc


def _rol_ve_atama(tenant, user, izin_kodu_model, *, kod="hr_manager"):  # noqa: ANN001, ANN202
    rol = Role.objects.create(tenant=tenant, name="İK", code=kod)
    RolePermission.objects.create(tenant=tenant, role=rol, permission=izin_kodu_model)
    UserRole.objects.create(tenant=tenant, user=user, role=rol)
    return rol


# --- 1. Silinmiş rol yetki vermez ------------------------------------------


def test_silinmis_rol_yetki_vermez(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol = _rol_ve_atama(tenant_a, user_a, izinler["employee.read"])
        assert effective_permission_codes(user_a) == {"employee.read"}

        # Rol soft-delete edilir; atama ve izin bağı hâlâ "sağlam" görünür.
        Role.objects.filter(pk=rol.pk).update(deleted_at=timezone.now())

        assert effective_permission_codes(user_a) == set()


# --- 2. Silinmiş rol ataması (UserRole) yetki vermez -----------------------


def test_silinmis_user_role_yetki_vermez(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["employee.read"]
        )
        atama = UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert effective_permission_codes(user_a) == {"employee.read"}

        UserRole.objects.filter(pk=atama.pk).update(deleted_at=timezone.now())

        assert effective_permission_codes(user_a) == set()


# --- 3. Kaldırılmış (fiziksel silinmiş) rol-izin bağı yetki vermez ---------


def test_kaldirilmis_role_permission_yetki_vermez(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    """
    DÜZELTME (ölçüm): `hr.role_permissions` bir **bağ tablosudur** ve `deleted_at`
    kolonu **yoktur** (kolonlar: `id, created_at, updated_at, permission_id, role_id,
    tenant_id`). Dolayısıyla burada "soft-delete edilmiş bağ" durumu yoktur; kayıt
    fiziksel silinir ve silinen kayıt zaten yetki veremez. Önceki F7 metni bu tabloda
    bir `deleted_at` filtresinin eksik olduğunu varsayıyordu — bu yanlıştı.
    """
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        bag = RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["employee.read"]
        )
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert effective_permission_codes(user_a) == {"employee.read"}

        bag.delete()

        assert effective_permission_codes(user_a) == set()
        assert RolePermission.objects.filter(role=rol).count() == 0


def test_kaldirilmis_rol_izin_bagi_yeniden_kurulabilir(tenant_a) -> None:  # noqa: ANN001
    """Bağ tablosunda soft-delete olmadığı için eksik bağ yeniden kurulabilir (istenen davranış)."""
    with tenant_context(tenant_a.id):
        eksikler = [
            kod for kod in ALL_PERMISSION_CODES if not Permission.objects.filter(code=kod).exists()
        ]
        if eksikler:
            Permission.objects.bulk_create(
                [Permission(code=kod, name=kod) for kod in eksikler], ignore_conflicts=True
            )

        roller = create_default_roles(tenant_a)
        rol = roller["employee"]
        bag = RolePermission.objects.filter(role=rol).first()
        silinen_kod = bag.permission.code
        bag.delete()

        create_default_roles(tenant_a)  # hata yok, bağ yeniden kurulur

        assert RolePermission.objects.filter(role=rol, permission__code=silinen_kod).count() == 1


# --- 4. Silinmiş ALLOW istisnası izin eklemez ------------------------------


def test_silinmis_allow_override_izin_eklemez(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        istisna = UserPermissionOverride.objects.create(
            tenant=tenant_a,
            user=user_a,
            permission=izinler["role.manage"],
            effect=PermissionEffect.ALLOW,
            reason="Geçici görevlendirme",
        )
        assert effective_permission_codes(user_a) == {"role.manage"}

        UserPermissionOverride.objects.filter(pk=istisna.pk).update(deleted_at=timezone.now())

        assert effective_permission_codes(user_a) == set()


# --- 5. Aktif DENY hâlâ kazanır; silinmiş DENY karar sayılmaz --------------


def test_aktif_deny_allow_uzerinde_kazanir(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        _rol_ve_atama(tenant_a, user_a, izinler["employee.read"])
        UserPermissionOverride.objects.create(
            tenant=tenant_a,
            user=user_a,
            permission=izinler["employee.read"],
            effect=PermissionEffect.DENY,
            reason="Ayrılış süreci",
        )
        assert effective_permission_codes(user_a) == set()


def test_silinmis_deny_aktif_karar_sayilmaz(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    """Silinmiş `deny` yetkiyi engellemeye devam etmemeli (silinmiş kayıt karar vermez)."""
    with tenant_context(tenant_a.id):
        _rol_ve_atama(tenant_a, user_a, izinler["employee.read"])
        istisna = UserPermissionOverride.objects.create(
            tenant=tenant_a,
            user=user_a,
            permission=izinler["employee.read"],
            effect=PermissionEffect.DENY,
            reason="Ayrılış süreci",
        )
        assert effective_permission_codes(user_a) == set()

        UserPermissionOverride.objects.filter(pk=istisna.pk).update(deleted_at=timezone.now())

        assert effective_permission_codes(user_a) == {"employee.read"}


# --- 6. Silinmiş varsayılan rol sessizce canlandırılmaz --------------------


def test_silinmis_varsayilan_rol_otomatik_restore_edilmez(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        # Varsayılan roller şablonda tüm katalog kodlarını kullanır.
        eksikler = [
            kod for kod in ALL_PERMISSION_CODES if not Permission.objects.filter(code=kod).exists()
        ]
        if eksikler:
            Permission.objects.bulk_create(
                [Permission(code=kod, name=kod) for kod in eksikler], ignore_conflicts=True
            )

        roller = create_default_roles(tenant_a)
        rol = roller["employee"]
        assert rol.deleted_at is None

        Role.objects.filter(pk=rol.pk).update(deleted_at=timezone.now())

        with pytest.raises(SoftDeletedDefaultRoleError):
            create_default_roles(tenant_a)

        rol.refresh_from_db()
        assert rol.deleted_at is not None  # otomatik restore YOK
        assert Role.objects.filter(tenant=tenant_a, code="employee").count() == 1  # kopya YOK
