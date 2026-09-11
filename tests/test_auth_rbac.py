"""
Kimlik doğrulama ve yetkilendirme testleri (Faz 2).

Kapsam:
  * RBAC çözümlemesi: (rollerin izinleri) + (allow istisnaları) - (deny istisnaları)
  * Süresi geçmiş rol dikkate alınmaz
  * Giriş: başarılı, hatalı parola, kilitlenme, yanlış kiracı, pasif kullanıcı
  * Denetim kaydı: başarılı/başarısız girişler kayıtlanır
"""

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.accounts.backends import TenantScopedModelBackend
from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.rbac.models import (
    Permission,
    PermissionEffect,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from apps.rbac.services import effective_permission_codes, user_has_permission
from apps.tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

backend = TenantScopedModelBackend()


@pytest.fixture
def izinler(db):  # noqa: ANN001, ANN201
    """
    Test izinleri.

    NOT: Bu kodlar üretimde `rbac.0003_seed_permissions` veri migration'ı ile
    gelir; testler migration'ı varsayar ve `get_or_create` kullanır.
    """
    tanimlar = [
        ("employee.read", "Çalışan görme"),
        ("leave.request.approve", "İzin onaylama"),
        ("role.manage", "Rol yönetimi"),
    ]
    sonuc = {}
    for kod, ad in tanimlar:
        sonuc[kod], _ = Permission.objects.get_or_create(code=kod, defaults={"name": ad})
    return sonuc


# --- RBAC çözümlemesi -------------------------------------------------------


def test_rol_izinleri_etkin_izinlere_katilir(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["employee.read"]
        )
        RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["leave.request.approve"]
        )
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)

        etkin = effective_permission_codes(user_a)
    assert etkin == {"employee.read", "leave.request.approve"}


def test_izin_verilmeyen_kod_reddedilir_varsayilan_reddet(tenant_a, user_a, izinler) -> None:  # noqa: ANN001, ARG001
    with tenant_context(tenant_a.id):
        assert user_has_permission(user_a, "role.manage") is False


def test_allow_istisnasi_izin_ekler(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        UserPermissionOverride.objects.create(
            tenant=tenant_a, user=user_a, permission=izinler["role.manage"],
            effect=PermissionEffect.ALLOW, reason="Geçici görevlendirme",
        )
        assert "role.manage" in effective_permission_codes(user_a)


def test_deny_istisnasi_rol_iznini_ezer(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["employee.read"]
        )
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        UserPermissionOverride.objects.create(
            tenant=tenant_a, user=user_a, permission=izinler["employee.read"],
            effect=PermissionEffect.DENY, reason="Ayrılış süreci",
        )
        assert "employee.read" not in effective_permission_codes(user_a)


def test_suresi_gecmis_rol_dikkate_alinmaz(tenant_a, user_a, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="Vekil", code="acting")
        RolePermission.objects.create(
            tenant=tenant_a, role=rol, permission=izinler["employee.read"]
        )
        UserRole.objects.create(
            tenant=tenant_a, user=user_a, role=rol,
            expires_at=timezone.now() - timedelta(days=1),
        )
        assert effective_permission_codes(user_a) == set()


def test_baska_kiracinin_rolu_bu_kullaniciya_izin_vermez(
    tenant_a, tenant_b, user_a, izinler  # noqa: ANN001
) -> None:
    """B kiracısında tanımlı rol, A kullanıcısına izin kazandırmamalı."""
    with tenant_context(tenant_b.id):
        rol_b = Role.objects.create(tenant=tenant_b, name="İK B", code="hr_manager")
        RolePermission.objects.create(
            tenant=tenant_b, role=rol_b, permission=izinler["employee.read"]
        )
    with tenant_context(tenant_a.id):
        assert effective_permission_codes(user_a) == set()


def test_baska_kiracinin_user_role_kaydi_gorunmez(tenant_a, tenant_b, user_b, izinler) -> None:  # noqa: ANN001
    with tenant_context(tenant_b.id):
        rol_b = Role.objects.create(tenant=tenant_b, name="İK B", code="hr_manager")
        RolePermission.objects.create(
            tenant=tenant_b, role=rol_b, permission=izinler["employee.read"]
        )
        UserRole.objects.create(tenant=tenant_b, user=user_b, role=rol_b)
    with tenant_context(tenant_a.id):
        # A bağlamında B'nin rol ataması görünmez; dolayısıyla izin de yoktur.
        assert UserRole.objects.count() == 0


# --- Giriş (authentication) -------------------------------------------------


def test_basarili_giris(tenant_a, user_a) -> None:  # noqa: ANN001
    kullanici = backend.authenticate(
        None, username="a@ornek.test", password="Guclu-Parola-123", tenant=tenant_a
    )
    assert kullanici is not None and kullanici.pk == user_a.pk
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="login").count() == 1


def test_e_posta_buyuk_harf_ile_de_giris_yapilabilir(tenant_a, user_a) -> None:  # noqa: ANN001, ARG001
    assert backend.authenticate(
        None, username="A@ORNEK.TEST", password="Guclu-Parola-123", tenant=tenant_a
    ) is not None


def test_hatali_parola_giris_vermez_ve_sayaci_artirir(tenant_a, user_a) -> None:  # noqa: ANN001
    assert backend.authenticate(
        None, username="a@ornek.test", password="yanlis-parola", tenant=tenant_a
    ) is None
    with tenant_context(tenant_a.id):
        user_a.refresh_from_db()
    assert user_a.failed_login_count == 1
    with tenant_context(tenant_a.id):
        assert AuditLog.objects.filter(action="login_failed").count() == 1


def test_art_arda_hatali_denemede_hesap_kilitlenir(tenant_a, user_a) -> None:  # noqa: ANN001
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS):
        backend.authenticate(None, username="a@ornek.test", password="yanlis", tenant=tenant_a)
    with tenant_context(tenant_a.id):
        user_a.refresh_from_db()
    assert user_a.is_locked is True
    # Kilitliyken DOĞRU parola da kabul edilmez.
    assert backend.authenticate(
        None, username="a@ornek.test", password="Guclu-Parola-123", tenant=tenant_a
    ) is None


def test_basarili_giris_sayaci_ve_kilidi_sifirlar(tenant_a, user_a) -> None:  # noqa: ANN001
    backend.authenticate(None, username="a@ornek.test", password="yanlis", tenant=tenant_a)
    assert backend.authenticate(
        None, username="a@ornek.test", password="Guclu-Parola-123", tenant=tenant_a
    ) is not None
    with tenant_context(tenant_a.id):
        user_a.refresh_from_db()
    assert user_a.failed_login_count == 0 and user_a.lockout_until is None
    assert user_a.last_login_at is not None


def test_yanlis_kiracida_giris_yapilamaz(tenant_a, tenant_b, user_a) -> None:  # noqa: ANN001, ARG001
    """A kullanıcısı, B kiracısı bağlamında giriş yapamaz (izolasyon)."""
    assert backend.authenticate(
        None, username="a@ornek.test", password="Guclu-Parola-123", tenant=tenant_b
    ) is None


def test_kiracisiz_giris_denemesi_reddedilir(user_a) -> None:  # noqa: ANN001, ARG001
    """Kiracı belirlenemiyorsa kimlik doğrulama yapılmaz (fail-closed)."""
    assert backend.authenticate(None, username="a@ornek.test", password="Guclu-Parola-123") is None


def test_pasif_kullanici_giris_yapamaz(tenant_a, user_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        user_a.status = "inactive"
        user_a.save(update_fields=["status", "updated_at"])
    assert backend.authenticate(
        None, username="a@ornek.test", password="Guclu-Parola-123", tenant=tenant_a
    ) is None


def test_olmayan_kullanici_istisna_uretmez_ve_kayitlanir(tenant_a) -> None:  # noqa: ANN001
    assert backend.authenticate(
        None, username="yok@ornek.test", password="herhangi-bir-parola", tenant=tenant_a
    ) is None
    with tenant_context(tenant_a.id):
        kayit = AuditLog.objects.get(action="login_failed")
    assert kayit.changes.get("reason") == "kullanici_yok"
    assert kayit.user_id is None


def test_parola_hash_argon2_ile_saklaniyor(tenant_a, user_a) -> None:  # noqa: ANN001, ARG001
    assert user_a.password.startswith("argon2$")


def test_denetim_kaydinda_parola_degeri_tutulmaz(tenant_a, user_a) -> None:  # noqa: ANN001
    backend.authenticate(None, username="a@ornek.test", password="yanlis-parola", tenant=tenant_a)
    with tenant_context(tenant_a.id):
        kayit = AuditLog.objects.get(action="login_failed")
    assert "yanlis-parola" not in str(kayit.changes)


def test_kullanici_olustururken_gevsek_parola_kurali_uygulanmaz_model_katmani(tenant_a) -> None:  # noqa: ANN001
    """
    Model katmanı parola doğrulayıcılarını çalıştırmaz; doğrulama form/API
    katmanının işidir (Django standardı). Burada yalnızca hash'lendiği doğrulanır.
    """
    with tenant_context(tenant_a.id):
        kullanici = User.objects.create_user(
            email="test@ornek.test", password="kisa", tenant=tenant_a,
            first_name="Test", last_name="Kullanıcı",
        )
    assert kullanici.password != "kisa"
