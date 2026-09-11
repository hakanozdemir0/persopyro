"""
Kiracı izolasyonu testleri (Faz 2).

Bu dosya projenin en kritik güvence testleridir: bir kiracının verisi başka bir
kiracıya hiçbir yoldan görünmemelidir (docs/DEVELOPMENT_RULES.md).

Testler gerçek PostgreSQL üzerinde çalışır; RLS davranışı SQLite ile taklit
edilemez. Katmanlar:
  1. Yapısal: her kiracıya bağlı tabloda RLS + FORCE etkin mi?
  2. Davranışsal: bağlam yokken 0 satır; başka kiracının satırı görünmez/yazılamaz.
  3. Yetki: uygulama rolü denetim kaydını değiştiremez/silemez.
"""

import uuid

import pytest
from django.apps import apps
from django.db import DatabaseError, connection, transaction

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.rbac.models import Role, UserRole
from apps.tenancy.context import (
    TenantContextMissingError,
    require_tenant_id,
    tenant_context,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

# RLS beklenmeyen tablolar: yalnızca GLOBAL tanım tablosu (kiracı verisi içermez).
# NOT: `role_permissions` burada DEĞİLDİR — ilk tasarımda tanım tablosu sayılmıştı,
# bir test bunun çapraz kiracı açığı oluşturduğunu gösterdi ve tablo tenant_id
# kazanıp RLS'e alındı (bkz. apps/rbac/migrations/0002_rolepermission_tenant.py).
TANIM_TABLOLARI = {"permissions"}


def _rls_durumu(tablo: str) -> tuple[bool, bool]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'hr' AND c.relname = %s",
            [tablo],
        )
        row = cur.fetchone()
    assert row is not None, f"hr.{tablo} tablosu bulunamadı"
    return row


def _kiracıya_bagli_tablolar() -> set[str]:
    """tenant alanı olan yerel modellerin tabloları (+ tenants tablosu)."""
    tablolar: set[str] = set()
    for model in apps.get_models():
        if model._meta.app_label not in {"tenancy", "accounts", "rbac", "audit"}:
            continue
        tablo = model._meta.db_table
        if model is Tenant or any(f.name == "tenant" for f in model._meta.get_fields()):
            tablolar.add(tablo)
    return tablolar


# --- 1. Yapısal güvence -----------------------------------------------------


def test_tum_tenant_tablolarinda_rls_ve_force_etkin() -> None:
    """
    Yeni bir kiracıya bağlı tablo RLS'siz eklenirse bu test kırmızıya döner.

    Kural: kiracı kolonu taşıyan her tablo RLS + FORCE taşımalıdır
    (FORCE olmadan tablo sahibi politikaları atlar).
    """
    tablolar = _kiracıya_bagli_tablolar()
    assert "tenants" in tablolar and "users" in tablolar, "beklenen modeller bulunamadı"
    eksik = []
    for tablo in sorted(tablolar):
        rls, force = _rls_durumu(tablo)
        if not (rls and force):
            eksik.append(tablo)
    assert not eksik, f"RLS/FORCE eksik tablolar: {eksik}"


def test_her_tenant_tablosunda_tenant_isolation_politikasi_var() -> None:
    tablolar = _kiracıya_bagli_tablolar()
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_policies "
            "WHERE schemaname = 'hr' AND policyname = 'tenant_isolation'"
        )
        politikalı = {row[0] for row in cur.fetchall()}
    eksik = sorted(tablolar - politikalı)
    assert not eksik, f"tenant_isolation politikası eksik tablolar: {eksik}"


def test_tanim_tablolari_rls_disi_tutuldu() -> None:
    """Kiracı verisi içermeyen tanım tabloları bilinçli olarak RLS dışıdır."""
    for tablo in TANIM_TABLOLARI:
        rls, _ = _rls_durumu(tablo)
        assert rls is False, f"{tablo} tanım tablosudur, RLS beklenmiyor"


# --- 2. Davranışsal güvence -------------------------------------------------


def test_baglam_yokken_hicbir_satir_gorunmez(tenant_a, tenant_b, user_a) -> None:  # noqa: ANN001, ARG001
    """Fail-closed: kiracı bağlamı yoksa RLS hiçbir satır döndürmez (hata değil)."""
    assert Tenant.objects.count() == 0
    assert User.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_uygulama_kati_kisi_baglam_yokken_acik_hata_verir() -> None:
    """Uygulama katmanı sessiz kalmaz: bağlam yoksa açık hata yükseltir."""
    with pytest.raises(TenantContextMissingError):
        require_tenant_id()


def test_kiracı_yalnizca_kendini_gorur(tenant_a, tenant_b) -> None:
    with tenant_context(tenant_a.id):
        assert list(Tenant.objects.values_list("name", flat=True)) == ["Kiracı A"]
    with tenant_context(tenant_b.id):
        assert list(Tenant.objects.values_list("name", flat=True)) == ["Kiracı B"]


def test_baska_kiracinin_kullanicisi_gorunmez(tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001, ARG001
    with tenant_context(tenant_a.id):
        assert list(User.objects.values_list("email", flat=True)) == ["a@ornek.test"]
    with tenant_context(tenant_b.id):
        assert list(User.objects.values_list("email", flat=True)) == ["b@ornek.test"]


def test_ayni_eposta_farkli_kiracilarda_olabilir(tenant_a, tenant_b, user_a) -> None:  # noqa: ANN001
    """E-posta kiracı başına tekildir; global tekil değildir."""
    with tenant_context(tenant_b.id):
        ikinci = User.objects.create_user(
            email="a@ornek.test",  # aynı e-posta, farklı kiracı
            password="Baska-Parola-789",
            tenant=tenant_b,
            first_name="Aynı",
            last_name="Eposta",
        )
    assert ikinci.pk != user_a.pk


def test_baska_kiracinin_kullanicisi_yazilamaz(tenant_a, tenant_b) -> None:  # noqa: ANN001
    """A kiracısı bağlamında B kiracısına kullanıcı eklenemez (RLS WITH CHECK)."""
    with tenant_context(tenant_a.id):
        # savepoint: beklenen hata dış işlemi bozmasın (Django transaction semantiği)
        with pytest.raises(DatabaseError), transaction.atomic():
            User.objects.create_user(
                email="sizma@ornek.test",
                password="Parola-123456",
                tenant=tenant_b,
                first_name="Sızma",
                last_name="Denemesi",
            )
    with tenant_context(tenant_b.id):
        assert not User.objects.filter(email="sizma@ornek.test").exists()


def test_baska_kiracinin_satiri_guncellenemez_silinemez(tenant_a, tenant_b, user_b) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        assert User.objects.filter(pk=user_b.pk).update(first_name="Ele Geçirildi") == 0
        assert User.objects.filter(pk=user_b.pk).delete()[0] == 0
    with tenant_context(tenant_b.id):
        kullanici = User.objects.get(pk=user_b.pk)
        assert kullanici.first_name == "Burak"


def test_rol_ve_istisnalar_kiracıya_ozel(tenant_a, tenant_b) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        rol_a = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        assert Role.objects.count() == 1
    with tenant_context(tenant_b.id):
        assert Role.objects.count() == 0
    with tenant_context(tenant_a.id):
        assert Role.objects.get(pk=rol_a.pk).code == "hr_manager"


def test_capraz_kiracı_referansi_engellenir(tenant_a, tenant_b, user_a, user_b) -> None:  # noqa: ANN001
    """
    RLS yalnızca satırın kendi tenant_id'sini denetler; yabancı anahtar kontrolü
    ondan etkilenmez. Bu yüzden guard tetikleyicisi gerekir: A bağlamında
    tenant_id=A olan bir satır, B kiracısının nesnesine bağlanamaz.
    """
    with tenant_context(tenant_a.id):
        rol_a = Role.objects.create(tenant=tenant_a, name="Yönetici", code="manager")
        UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol_a)

        with pytest.raises(DatabaseError), transaction.atomic():
            UserRole.objects.create(tenant=tenant_a, user=user_b, role=rol_a)


def test_role_permission_capraz_kiracı_engellenir(tenant_a, tenant_b) -> None:  # noqa: ANN001
    """Başka kiracının rolüne izin kaydı eklenemez (guard tetikleyicisi)."""
    from apps.rbac.models import Permission, RolePermission

    izin = Permission.objects.create(code="employee.read", name="Çalışan görme")
    with tenant_context(tenant_b.id):
        rol_b = Role.objects.create(tenant=tenant_b, name="İK B", code="hr_manager")

    with tenant_context(tenant_a.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            RolePermission.objects.create(tenant=tenant_a, role=rol_b, permission=izin)


def test_kendi_kiracisindaki_referans_serbest(tenant_a, user_a) -> None:  # noqa: ANN001
    """Guard yalnızca çapraz kiracıyı engeller; normal kullanım bozulmaz."""
    with tenant_context(tenant_a.id):
        rol = Role.objects.create(tenant=tenant_a, name="İK", code="hr_manager")
        atama = UserRole.objects.create(tenant=tenant_a, user=user_a, role=rol)
        assert atama.pk is not None


def test_denetim_kaydi_kiracıya_bagli_ve_kiracilar_arasi_gorunmez(  # noqa: ANN001
    tenant_a, tenant_b, user_a
) -> None:
    with tenant_context(tenant_a.id):
        AuditLog.objects.create(
            tenant=tenant_a, user=user_a, action="create", table_name="users",
            record_id=user_a.pk, changes={},
        )
        assert AuditLog.objects.count() == 1
    with tenant_context(tenant_b.id):
        assert AuditLog.objects.count() == 0


def test_ic_ice_baglam_dis_katmani_bozmaz(tenant_a, tenant_b) -> None:  # noqa: ANN001
    """İç içe tenant_context kullanımı dış bağlamı sızdırmaz/bozmaz."""
    with tenant_context(tenant_a.id):
        assert Tenant.objects.count() == 1
        with tenant_context(tenant_b.id):
            assert list(Tenant.objects.values_list("name", flat=True)) == ["Kiracı B"]
        assert list(Tenant.objects.values_list("name", flat=True)) == ["Kiracı A"]


def test_baglam_bosaltilinca_erisim_kapanir(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        assert Tenant.objects.count() == 1
    with tenant_context(None):
        assert Tenant.objects.count() == 0


# --- 3. Yetki (denetim kaydı değiştirilemezliği) ---------------------------


def test_uygulama_rolu_denetim_kaydini_degistiremez_silemez(tenant_a) -> None:  # noqa: ANN001, ARG001
    """
    Denetim kaydı yalnızca eklenir. Bu, veritabanı yetkisiyle dayatılır ve
    burada uygulama rolünün gerçek yetkileri sorgulanarak doğrulanır.
    """
    from django.conf import settings
    app_role = settings.APP_DB_ROLE
    with connection.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, 'hr.audit_logs', 'SELECT'), "
            "has_table_privilege(%s, 'hr.audit_logs', 'INSERT'), "
            "has_table_privilege(%s, 'hr.audit_logs', 'UPDATE'), "
            "has_table_privilege(%s, 'hr.audit_logs', 'DELETE')",
            [app_role, app_role, app_role, app_role],
        )
        sec, ekle, guncelle, sil = cur.fetchone()
    assert sec is True and ekle is True, "denetim kaydı okunabilir ve eklenebilir olmalı"
    assert guncelle is False, "uygulama rolü denetim kaydını GÜNCELLEYEMEMELİ"
    assert sil is False, "uygulama rolü denetim kaydını SİLEMEMELİ"


def test_uuid_uretiminde_db_bagimsiz_davranis(tenant_a) -> None:  # noqa: ANN001
    """Kayıt kimlikleri uygulama tarafında üretilir (uuid4)."""
    with tenant_context(tenant_a.id):
        kullanici = User.objects.create_user(
            email="uuid@ornek.test", password="Parola-123456", tenant=tenant_a,
            first_name="U", last_name="Test",
        )
    assert isinstance(kullanici.pk, uuid.UUID)
