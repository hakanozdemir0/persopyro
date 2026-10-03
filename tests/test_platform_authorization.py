"""
M6–M8 regresyon testleri — platform izin kataloğu, roller ve authorization servisi.

Kapsam (istenen test listesi):
  1. seed idempotent              2-3. rol izin setleri beklenen
  4. rol inheritance yok          5. platform_admin kendini superadmin yapamaz
  6. kullanıcı kendine yetki yükseltemez
  7-9. soft-deleted rol/atama/bağ yetki vermez
 10. pasif kullanıcı yetki alamaz
 11. Django `auth_permission` kullanılmaz  12. tenant RBAC'e düşülmez
 13. superadmin ≠ tenant kişisel verisi erişimi
 14. `persopyro_platform` NOBYPASSRLS      15. `hr.tenants` yazamaz
 16. platform rol DB allowlist'i          20. sıfırdan kurulum aynı sonucu üretir

Ek olarak M6 ile kapanan yetki boşluğu (`hr.permissions` salt okunur) ve önceki
güvenlik garantileri (OD19, M30, kiracı RLS) yeniden doğrulanır.
"""

import importlib
import re
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connections, transaction

from apps.platform import permissions as platform_yetki
from apps.platform.constants import (
    PLATFORM_ROLE_CODES,
    PLATFORM_ROLE_PERMISSIONS,
    PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS,
)
from apps.rbac.permission_catalog import (
    ALL_PERMISSION_CODES,
    DEFAULT_ROLE_TEMPLATES,
    PLATFORM_PERMISSION_CODES,
)

pytestmark = pytest.mark.django_db(databases=["default", "platform"])
connection = connections["platform"]

IZIN_SABLONU = re.compile(r"^[a-z][a-z0-9_.]*[a-z0-9]$")


def test_platform_db_baglantisi_ayri_rol_kullanir() -> None:
    platform_db = settings.DATABASES["platform"]
    default_db = settings.DATABASES["default"]
    assert platform_db["USER"] == settings.PLATFORM_DB_CONNECTION_USER
    assert platform_db["NAME"] == default_db["NAME"]
    assert platform_db["HOST"] == default_db["HOST"]
    assert platform_db["TEST"]["MIRROR"] == "default"
    assert "search_path=platform,hr,public" in platform_db["OPTIONS"]["options"]


def test_platform_rol_kurulum_betigi_veritabanina_baglanma_izni_verir() -> None:
    sql = (Path(settings.BASE_DIR) / "scripts" / "03_platform_roles.sql").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"GRANT\s+CONNECT\s+ON\s+DATABASE\s+persopyro\s+TO\s+[^;]*persopyro_platform",
        sql,
        re.IGNORECASE,
    )


# --- yardımcılar ------------------------------------------------------------


def _platform_user(
    email: str,
    *,
    status: str = "active",
    is_active: bool = True,
    deleted: bool = False,
) -> uuid.UUID:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO platform.users (email, full_name, password, status, is_active, deleted_at)
            VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END) RETURNING id
            """,
            [email, email.split("@")[0], "argon2$placeholder", status, is_active, deleted],
        )
        return cur.fetchone()[0]


def _rol_ata(  # noqa: PLR0913
    kullanici_id,  # noqa: ANN001
    rol_kodu: str,
    *,
    granted_by=None,  # noqa: ANN001
    expires_at=None,  # noqa: ANN001
    deleted_at=None,  # noqa: ANN001
) -> uuid.UUID:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO platform.user_roles
                (platform_user_id, role_id, granted_by_id, granted_at, expires_at, deleted_at)
            SELECT %s, r.id, %s, now(), %s, %s FROM platform.roles r WHERE r.code = %s
            RETURNING id
            """,
            [
                str(kullanici_id),
                str(granted_by) if granted_by else None,
                expires_at,
                deleted_at,
                rol_kodu,
            ],
        )
        return cur.fetchone()[0]


def _rol_soft_delete(rol_kodu: str) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE platform.roles SET deleted_at = now() WHERE code = %s", [rol_kodu]
        )


def _bag_soft_delete(rol_kodu: str, izin_kodu: str) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE platform.role_permissions rp SET deleted_at = now()
              FROM platform.roles r, hr.permissions p
             WHERE rp.role_id = r.id AND rp.permission_id = p.id
               AND r.code = %s AND p.code = %s
            """,
            [rol_kodu, izin_kodu],
        )


def _atama_soft_delete(kullanici_id, rol_kodu: str) -> None:  # noqa: ANN001
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE platform.user_roles ur SET deleted_at = now()
              FROM platform.roles r
             WHERE ur.role_id = r.id AND r.code = %s AND ur.platform_user_id = %s
            """,
            [rol_kodu, str(kullanici_id)],
        )


def _rolun_izinleri(rol_kodu: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT p.code FROM platform.role_permissions rp
              JOIN platform.roles r ON r.id = rp.role_id
              JOIN hr.permissions p ON p.id = rp.permission_id
             WHERE r.code = %s AND rp.deleted_at IS NULL
            """,
            [rol_kodu],
        )
        return {satir[0] for satir in cur.fetchall()}


def _platform_rolu_var() -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = %s", [settings.PLATFORM_DB_ROLE]
        )
        return cur.fetchone()[0] == 1


# --- 1) seed idempotent ------------------------------------------------------


def test_platform_izin_seed_idempotent() -> None:
    """Kod ekleme adımı iki kez çalışsa da çift kayıt üretmez."""
    migration = importlib.import_module(
        "apps.rbac.migrations.0004_od3_platform_permissions"
    )
    from django.apps import apps as django_apps

    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM hr.permissions WHERE code LIKE 'platform.%'")
        once = cur.fetchone()[0]

    migration.platform_izinlerini_ekle(django_apps, None)
    migration.platform_izinlerini_ekle(django_apps, None)

    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM hr.permissions WHERE code LIKE 'platform.%'")
        sonra = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_constraint WHERE conname = 'permissions_code_key'")
        kisit = cur.fetchone()[0]

    assert once == sonra == len(PLATFORM_PERMISSION_CODES)
    assert kisit == 1  # UNIQUE kısıtı hâlâ tek


def test_platform_rol_izin_seed_idempotent() -> None:
    """Rol-izin bağları idempotent: ikinci çalıştırma satır sayısını değiştirmez."""
    migration = importlib.import_module(
        "apps.platform.migrations.0005_od3_platform_rolleri_ve_izinleri"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM platform.role_permissions WHERE deleted_at IS NULL")
        once = cur.fetchone()[0]
        migration._rolleri_ve_baglari_kur(cur)
        migration._rolleri_ve_baglari_kur(cur)
        cur.execute("SELECT count(*) FROM platform.role_permissions WHERE deleted_at IS NULL")
        sonra = cur.fetchone()[0]

    beklenen = sum(len(izinler) for izinler in PLATFORM_ROLE_PERMISSIONS.values())
    assert once == sonra == beklenen + len(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS)


def test_platform_rolleri_kapali_kume_ve_seed() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT code FROM platform.roles ORDER BY code")
        kodlar = [satir[0] for satir in cur.fetchall()]
    assert kodlar == sorted(PLATFORM_ROLE_CODES)
    assert sum(len(i) for i in PLATFORM_ROLE_PERMISSIONS.values()) == 10  # M31: 6 + 4


# --- 2-3) rol izin setleri ---------------------------------------------------


def test_superadmin_izin_seti_beklenen() -> None:
    beklenen = set(PLATFORM_ROLE_PERMISSIONS["superadmin"])
    beklenen |= set(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS)
    assert _rolun_izinleri("superadmin") == beklenen
    assert beklenen <= set(PLATFORM_PERMISSION_CODES)
    assert "platform.tenant.pii.read" not in beklenen


def test_platform_roles_have_no_unapproved_tenant_pii_access() -> None:
    pii_izni = "platform.tenant.pii.read"
    assert pii_izni in PLATFORM_PERMISSION_CODES
    assert pii_izni not in _rolun_izinleri("superadmin")
    assert pii_izni not in PLATFORM_ROLE_PERMISSIONS["platform_admin"]


def test_platform_admin_izin_seti_beklenen() -> None:
    beklenen = set(PLATFORM_ROLE_PERMISSIONS["platform_admin"])
    assert _rolun_izinleri("platform_admin") == beklenen
    # Kritik ayrım: platform ayarları ve rol atama YOK (yükseltme yolu kapalı).
    assert "platform.role.assign" not in beklenen
    assert "platform.settings.manage" not in beklenen
    assert beklenen < (
        set(PLATFORM_ROLE_PERMISSIONS["superadmin"])
        | set(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS)
    )


# --- 4) inheritance yok ------------------------------------------------------


def test_rol_inheritance_yok() -> None:
    """
    Hiyerarşi izin devralma üretmez: platform_admin kullanıcısı superadmin
    izinlerini **almaz** ve DB'de devralma mekanizması (parent/trigger/rule) yoktur.
    """
    kullanici = _platform_user("inheritance@persopyro.test")
    _rol_ata(kullanici, "platform_admin")

    etkin = platform_yetki.effective_permissions(kullanici)
    assert etkin == set(PLATFORM_ROLE_PERMISSIONS["platform_admin"])
    assert "platform.role.assign" not in etkin
    assert "platform.settings.manage" not in etkin

    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'platform' AND table_name = 'roles'"
        )
        kolonlar = {satir[0] for satir in cur.fetchall()}
        assert not {"parent_id", "parent_role_id", "inherits"} & kolonlar

        cur.execute(
            "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'platform' AND NOT t.tgisinternal"
            " AND t.tgname NOT IN ('trg_platform_tokens_guard',"
            " 'trg_platform_audit_events_immutable')"
        )
        assert cur.fetchone()[0] == 0, "platform şemasında beklenmeyen tetikleyici"


# --- 5-6) self-elevation -----------------------------------------------------


def test_platform_admin_kendini_superadmin_yapamaz() -> None:
    admin = _platform_user("pa@persopyro.test")
    _rol_ata(admin, "platform_admin")

    izin_var, gerekce = platform_yetki.can_assign_role(admin, "superadmin")
    assert izin_var is False
    assert "role.assign" in gerekce

    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="self-elevation"):
        platform_yetki.assign_role(
            actor_id=admin, target_user_id=admin, role_code="superadmin"
        )

    # Başka bir kullanıcıya da dağıtamaz (sahip olmadığı yetki).
    hedef = _platform_user("hedef@persopyro.test")
    with pytest.raises(platform_yetki.PlatformAuthorizationError):
        platform_yetki.assign_role(
            actor_id=admin, target_user_id=hedef, role_code="superadmin"
        )

    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM platform.user_roles")
        assert cur.fetchone()[0] == 1  # yalnızca ilk atama; yeni atama yok


def test_kullanici_kendine_yetki_yukseltemez() -> None:
    """Superadmin bile kendi rolünü atayamaz/değiştiremez."""
    sa = _platform_user("sa@persopyro.test")
    _rol_ata(sa, "superadmin")

    assert platform_yetki.effective_permissions(sa) == set(
        PLATFORM_ROLE_PERMISSIONS["superadmin"]
    ) | set(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS)

    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="self-elevation"):
        platform_yetki.assign_role(actor_id=sa, target_user_id=sa, role_code="superadmin")

    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="self-elevation"):
        platform_yetki.revoke_role(actor_id=sa, target_user_id=sa, role_code="superadmin")


def test_superadmin_baska_kullaniciya_rol_atayabilir() -> None:
    """Meşru akış: superadmin, alt rolü atayabilir (üst rol, alt rolü atar)."""
    sa = _platform_user("sa2@persopyro.test")
    _rol_ata(sa, "superadmin")
    hedef = _platform_user("yeni-admin@persopyro.test")

    atama_id = platform_yetki.assign_role(
        actor_id=sa, target_user_id=hedef, role_code="platform_admin"
    )
    assert atama_id is not None
    assert platform_yetki.active_roles(hedef) == ["platform_admin"]

    with connection.cursor() as cur:
        cur.execute(
            "SELECT granted_by_id FROM platform.user_roles WHERE id = %s", [str(atama_id)]
        )
        assert cur.fetchone()[0] == sa  # aktör kayıtlandı (denetim izi)

    # Çift atama sessizce geçmez.
    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="zaten sahip"):
        platform_yetki.assign_role(actor_id=sa, target_user_id=hedef, role_code="platform_admin")


# --- 7-10) soft-delete ve pasiflik ------------------------------------------


def test_soft_deleted_platform_role_yetki_vermez() -> None:
    kullanici = _platform_user("soft-rol@persopyro.test")
    _rol_ata(kullanici, "platform_admin")
    assert platform_yetki.effective_permissions(kullanici)  # başlangıçta izinli

    _rol_soft_delete("platform_admin")

    assert platform_yetki.effective_permissions(kullanici) == frozenset()
    assert platform_yetki.platform_user_is_active(kullanici) is True  # kullanıcı etkilenmez


def test_soft_deleted_user_role_yetki_vermez() -> None:
    kullanici = _platform_user("soft-atama@persopyro.test")
    _rol_ata(kullanici, "superadmin")
    _atama_soft_delete(kullanici, "superadmin")

    assert platform_yetki.effective_permissions(kullanici) == frozenset()
    assert platform_yetki.active_roles(kullanici) == []


def test_soft_deleted_role_permission_yetki_vermez() -> None:
    kullanici = _platform_user("soft-bag@persopyro.test")
    _rol_ata(kullanici, "superadmin")
    _bag_soft_delete("superadmin", "platform.settings.manage")

    etkin = platform_yetki.effective_permissions(kullanici)
    assert "platform.settings.manage" not in etkin
    assert platform_yetki.has_permission(kullanici, "platform.settings.manage") is False
    assert platform_yetki.has_permission(kullanici, "platform.tenant.read") is True


def test_suresi_gecmis_rol_atamasi_yetki_vermez() -> None:
    kullanici = _platform_user("suresi-gecti@persopyro.test")
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO platform.user_roles
                (platform_user_id, role_id, granted_at, expires_at)
            SELECT %s, r.id, now() - interval '2 days', now() - interval '1 day'
              FROM platform.roles r WHERE r.code = 'superadmin'
            """,
            [str(kullanici)],
        )
    assert platform_yetki.effective_permissions(kullanici) == frozenset()


@pytest.mark.parametrize(
    ("durum", "aktif", "silinmis"),
    [
        ("inactive", True, False),
        ("archived", True, False),
        ("active", False, False),
        ("active", True, True),
    ],
)
def test_pasif_veya_silinmis_platform_kullanicisi_yetki_alamaz(
    durum: str, aktif: bool, silinmis: bool
) -> None:
    kullanici = _platform_user(
        f"pasif-{durum}-{aktif}-{silinmis}@persopyro.test".lower(),
        status=durum,
        is_active=aktif,
        deleted=silinmis,
    )
    _rol_ata(kullanici, "superadmin")

    assert platform_yetki.effective_permissions(kullanici) == frozenset()
    assert platform_yetki.platform_user_can_authenticate(kullanici) is False
    assert platform_yetki.has_permission(kullanici, "platform.tenant.read") is False
    with pytest.raises(platform_yetki.PlatformAuthorizationError, match="izni yok"):
        platform_yetki.require_permission(kullanici, "platform.tenant.read")


# --- 11-12) Django auth ve tenant RBAC'e düşmeme ----------------------------


def _kod_metinleri(modul) -> tuple[str, set[str]]:  # noqa: ANN001
    """
    Modülün **docstring ve yorumları hariç** metin sabitleri + import edilen modüller.

    Kaynak denetimi böylece dokümantasyon metnine değil, gerçek koda bakar
    (ör. "kiracı tabloları kullanılmaz" cümlesi yasaklı ad içerdiği için yanlış
    pozitif üretmesin).
    """
    import ast

    agac = ast.parse(Path(modul.__file__).read_text(encoding="utf-8"))
    docstringler = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            metin = ast.get_docstring(dugum, clean=False)
            if metin:
                docstringler.add(metin)

    sabitler = [
        dugum.value
        for dugum in ast.walk(agac)
        if isinstance(dugum, ast.Constant)
        and isinstance(dugum.value, str)
        and dugum.value not in docstringler
    ]
    importlar: set[str] = set()
    for dugum in ast.walk(agac):
        if isinstance(dugum, ast.Import):
            importlar.update(ad.name for ad in dugum.names)
        elif isinstance(dugum, ast.ImportFrom) and dugum.module:
            importlar.add(dugum.module)
    return "\n".join(sabitler).lower(), importlar


def test_platform_authorization_django_auth_kullanmaz() -> None:
    metinler, importlar = _kod_metinleri(platform_yetki)
    for yasak in ("auth_permission", "auth_group", "is_staff", "is_superuser"):
        assert yasak not in metinler, f"platform authorization Django yetki yoluna değiyor: {yasak}"
    assert not any("django.contrib.auth" in modul for modul in importlar), importlar

    # Çözümleyici sorguları yalnızca platform şeması + ortak katalog.
    for sorgu in (
        platform_yetki.ETKIN_IZIN_SQL,
        platform_yetki.AKTIF_ROL_SQL,
        platform_yetki.ROL_IZIN_SQL,
    ):
        assert "platform." in sorgu
        assert "auth_" not in sorgu
    assert "hr.permissions" in platform_yetki.ETKIN_IZIN_SQL
    assert "hr.permissions" in platform_yetki.ROL_IZIN_SQL


def test_platform_authorization_tenant_rbac_e_dusmez() -> None:
    """
    (a) Modül tenant RBAC servislerini/kiracı tablolarını kullanmaz,
    (b) ortak katalogda kiracı izni bağlansa bile platform yetkisi **üretmez**.
    """
    metinler, importlar = _kod_metinleri(platform_yetki)
    assert not any("rbac" in modul for modul in importlar), importlar
    for yasak in ("hr.roles", "hr.user_roles", "user_permission_overrides", "hr.users"):
        assert yasak not in metinler, f"kiracı RBAC tablosu kullanılmış: {yasak}"

    kullanici = _platform_user("tenant-dusmez@persopyro.test")
    _rol_ata(kullanici, "superadmin")
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO platform.role_permissions (role_id, permission_id)
            SELECT r.id, p.id FROM platform.roles r, hr.permissions p
             WHERE r.code = 'superadmin' AND p.code = 'employee.sensitive.read'
            """
        )

    etkin = platform_yetki.effective_permissions(kullanici)
    assert "employee.sensitive.read" not in etkin
    assert all(kod.startswith("platform.") for kod in etkin)


# --- 13) superadmin ≠ kişisel veri erişimi ----------------------------------


def test_superadmin_tenant_kisisel_verisine_dogrudan_erisim_vermez() -> None:
    """
    SUPERADMIN platform izinleri kiracı/PII kodlarını **içermez**; kiracı verisi
    yalnızca erişim talebi/onayı (AccessGrant) ile açılır → yetki seti bunu vermez.
    """
    superadmin = _platform_user("pii@persopyro.test")
    _rol_ata(superadmin, "superadmin")
    etkin = platform_yetki.effective_permissions(superadmin)

    assert etkin  # superadmin platform yetkilerine sahip
    for kod in etkin:
        assert kod.startswith("platform."), f"PII/kiracı izni sızmış: {kod}"
    for pii_kodu in (
        "employee.sensitive.read",
        "employee.read",
        "document.confidential.read",
        "audit.read",
        "tenant.read",
    ):
        assert pii_kodu not in etkin

    # DB rolü de kiracı tablolarında yetkisiz (PII'ye teknik yol yok).
    if not _platform_rolu_var():
        pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok")
    with connection.cursor() as cur:
        for tablo in ("hr.users", "hr.employees", "hr.audit_logs", "hr.api_tokens"):
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", [tablo])
            if not cur.fetchone()[0]:
                continue
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    [settings.PLATFORM_DB_ROLE, tablo, yetki],
                )
                assert cur.fetchone()[0] is False, f"platform rolü {tablo} {yetki} yetkili"


# --- 14-16) DB rolü ve allowlist --------------------------------------------


def test_platform_rolu_nobypassrls() -> None:
    if not _platform_rolu_var():
        pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok")
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole"
            " FROM pg_roles WHERE rolname = %s",
            [settings.PLATFORM_DB_ROLE],
        )
        assert cur.fetchone() == (False, False, False, False)


def test_platform_rolu_hr_tenants_yazamaz() -> None:
    if not _platform_rolu_var():
        pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok")
    with connection.cursor() as cur:
        for yetki in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'hr.tenants', %s)",
                [settings.PLATFORM_DB_ROLE, yetki],
            )
            assert cur.fetchone()[0] is False, f"platform rolü hr.tenants {yetki} yetkili"
        # Ters yön yok: registry üzerinden kiracı alanı değiştirilemez.
        cur.execute(
            "SELECT has_column_privilege(%s, 'hr.tenants', 'slug', 'UPDATE')",
            [settings.PLATFORM_DB_ROLE],
        )
        assert cur.fetchone()[0] is False


def test_platform_rolu_db_allowlist_beklenen() -> None:
    if not _platform_rolu_var():
        pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok")
    rol = settings.PLATFORM_DB_ROLE
    platform_tablolari = (
        "users",
        "roles",
        "role_permissions",
        "user_roles",
        "tokens",
        "tenant_registry",
        "company_plans",
    )
    with connection.cursor() as cur:
        for tablo in platform_tablolari:
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, ('platform.' || %s)::regclass, %s)",
                    [rol, tablo, yetki],
                )
                assert cur.fetchone()[0] is True, f"platform rolü {tablo} {yetki} yetkisiz"

        cur.execute("SELECT has_schema_privilege(%s, 'platform', 'CREATE')", [rol])
        assert cur.fetchone()[0] is False
        cur.execute("SELECT has_table_privilege(%s, 'hr.permissions', 'SELECT')", [rol])
        assert cur.fetchone()[0] is True
        # Django teknik tabloları ve kiracı token tablosu: erişilemez.
        for tablo in (
            "auth_permission",
            "auth_group",
            "auth_group_permissions",
            "django_admin_log",
            "api_tokens",
        ):
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", [f"hr.{tablo}"])
            if not cur.fetchone()[0]:
                continue
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, ('hr.' || %s)::regclass, %s)",
                    [rol, tablo, yetki],
                )
                assert cur.fetchone()[0] is False, f"platform rolü hr.{tablo} {yetki} yetkili"


# --- 20) sıfırdan kurulum aynı sonucu üretir ---------------------------------


def test_sifirdan_kurulum_ayni_sonucu_uretir() -> None:
    """
    Migration'lar sonrası durum: kapalı rol kümesi, beklenen bağlar, **seed edilmiş
    gerçek kullanıcı yok** (M9: kullanıcı migration içine seed edilmez).
    """
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM platform.users")
        assert cur.fetchone()[0] == 0, "migration gerçek platform kullanıcısı seed etmemeli"
        cur.execute(
            "SELECT r.code, count(*) FROM platform.role_permissions rp"
            " JOIN platform.roles r ON r.id = rp.role_id"
            " WHERE rp.deleted_at IS NULL GROUP BY r.code ORDER BY r.code"
        )
        sayimlar = dict(cur.fetchall())
    assert sayimlar == {
        "platform_admin": len(PLATFORM_ROLE_PERMISSIONS["platform_admin"]),
        "superadmin": len(PLATFORM_ROLE_PERMISSIONS["superadmin"])
        + len(PLATFORM_SUPERADMIN_EXTRA_PERMISSIONS),
    }

    # İzin kodları biçim kuralına uyar ve joker içermez.
    for kod in PLATFORM_PERMISSION_CODES:
        assert IZIN_SABLONU.match(kod), kod
        assert "*" not in kod

    # Kiracı şablon rollerine platform izni SIZMAZ (yetki yükseltme yolu kapalı).
    for sablon in DEFAULT_ROLE_TEMPLATES.values():
        assert not (set(sablon["permissions"]) & set(PLATFORM_PERMISSION_CODES))
    assert not (set(ALL_PERMISSION_CODES) & set(PLATFORM_PERMISSION_CODES))


# --- M6 boşluğu + önceki garantiler -----------------------------------------


def test_hr_permissions_uygulama_rolu_salt_okunur() -> None:
    """M6: global izin kataloğu kiracı runtime rolünde SALT OKUNUR olmalı."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, 'hr.permissions', 'SELECT')", [settings.APP_DB_ROLE]
        )
        assert cur.fetchone()[0] is True
        for yetki in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'hr.permissions', %s)",
                [settings.APP_DB_ROLE, yetki],
            )
            assert cur.fetchone()[0] is False, f"hr.permissions {yetki} hâlâ açık (M6)"


def test_onceki_guvenlik_garantileri_korunuyor() -> None:
    """OD19 (auth_*), M30 (api_tokens) ve kiracı RLS (7 tablo, FORCE) bozulmadı."""
    with connection.cursor() as cur:
        for tablo in (
            "auth_permission",
            "auth_group",
            "auth_group_permissions",
            "django_admin_log",
        ):
            cur.execute("SELECT to_regclass(%s) IS NOT NULL", [f"hr.{tablo}"])
            if not cur.fetchone()[0]:
                continue
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, ('hr.' || %s)::regclass, %s)",
                    [settings.APP_DB_ROLE, tablo, yetki],
                )
                assert cur.fetchone()[0] is False, f"{tablo} {yetki} daraltması kayboldu"

        cur.execute(
            "SELECT has_table_privilege(%s, 'hr.api_tokens', 'DELETE')", [settings.APP_DB_ROLE]
        )
        assert cur.fetchone()[0] is False
        cur.execute(
            "SELECT has_column_privilege(%s, 'hr.api_tokens', 'revoked_at', 'UPDATE')",
            [settings.APP_DB_ROLE],
        )
        assert cur.fetchone()[0] is True

        cur.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'hr' AND c.relrowsecurity AND c.relforcerowsecurity"
        )
        assert cur.fetchone()[0] >= 7, "kiracı RLS FORCE sayısı düştü"


def test_platform_yetki_hatasi_fail_closed() -> None:
    """Bilinmeyen izin/kod istendiğinde karar 'izin yok' olur (fail-closed)."""
    kullanici = _platform_user("fail-closed@persopyro.test")
    _rol_ata(kullanici, "superadmin")

    assert platform_yetki.has_permission(kullanici, "platform.yok.boyle.bir.izin") is False
    assert platform_yetki.authorize(kullanici, "platform.settings.manage") is True
    with pytest.raises(platform_yetki.PlatformAuthorizationError):
        platform_yetki.role_permissions("bilinmeyen_rol")

    # Var olmayan kullanıcı: hiçbir izin yok.
    assert platform_yetki.effective_permissions(uuid.uuid4()) == frozenset()
    assert platform_yetki.platform_user_can_authenticate(uuid.uuid4()) is False


def test_token_sahibi_pasifse_token_calismaz() -> None:
    """Pasif/silinmiş platform kullanıcısının token'ı kimlik doğrulama sağlamaz."""
    from apps.platform.services import issue_platform_token

    kullanici = _platform_user("token-pasif@persopyro.test")
    _rol_ata(kullanici, "superadmin")
    _, duz_metin = issue_platform_token(
        platform_user_id=kullanici,
        purpose="pasif sahip token testi",
        scope=["platform.tenant.read"],
        token_kind="interactive",
        ttl_hours=1,
    )

    baglam = platform_yetki.authenticate_platform_token(duz_metin)
    assert baglam is not None and baglam["platform_user_id"] == kullanici

    with connection.cursor() as cur:
        cur.execute(
            "UPDATE platform.users SET status = 'inactive' WHERE id = %s", [str(kullanici)]
        )
    assert platform_yetki.authenticate_platform_token(duz_metin) is None

    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE platform.users SET status = 'active', deleted_at = now()"
                " WHERE id = %s",
                [str(kullanici)],
            )
    assert platform_yetki.authenticate_platform_token(duz_metin) is None
