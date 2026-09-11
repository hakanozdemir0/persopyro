"""
M1–M4 (OD3/OD20/OD22) regresyon testleri — platform katmanı.

Kapsam:
  * `platform` şeması ve temel tabloları doğru şemada (kiracı tablolarıyla karışmaz)
  * platform kullanıcısı `tenant_id` **taşımaz** ve `hr.users` FK'si **değildir**
  * platform rol/izin ilişkileri **tenant RBAC'ten ayrıdır**
  * platform token: ham sır saklanmaz, boş kapsam reddedilir, süre/iptal guardları
  * registry: yalnızca izin verilen minimum alanlar; aynı işlemde senkron;
    başarısızlıkta **fail-closed** rollback; ters yön yazımı yok
  * yetki allowlist: `persopyro_app` platform'da hiç yetkili değil; platform rolü
    yalnızca allowlist'teki nesnelerde (rol yoksa test atlanır — bkz. rapor)

Testler gerçek PostgreSQL üzerinde çalışır; RLS/şema davranışı SQLite ile
taklit edilemez.
"""

import hashlib
import re
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.platform import constants as platform_constants
from apps.platform import services as platform_services
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PLATFORM_TABLOLARI = {
    "users",
    "roles",
    "role_permissions",
    "user_roles",
    "tokens",
    "tenant_registry",
}
# Kiracıya bağlı OLMAYAN platform tabloları (tenant_id kolonu taşımamalı).
TENANT_ID_YASAK = {"users", "roles", "role_permissions", "user_roles", "tokens"}
CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def _platform_rolu_var() -> bool:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = %s",
            [settings.PLATFORM_DB_ROLE],
        )
        return cur.fetchone()[0] == 1


def _kolonlar(sema: str, tablo: str) -> set[str]:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            [sema, tablo],
        )
        return {satir[0] for satir in cur.fetchall()}


def _token_uret(**kwargs):  # noqa: ANN003
    """Test yardımcısı: `token_kind` zorunlu olduğu için varsayılanı burada verir."""
    kwargs.setdefault("token_kind", platform_constants.PLATFORM_TOKEN_KIND_INTERACTIVE)
    return platform_services.issue_platform_token(**kwargs)


def _platform_kullanici_ekle(email: str = "ops@persopyro.test") -> tuple[uuid.UUID, str]:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.users (email, full_name, password)"
            " VALUES (%s, %s, %s) RETURNING id, public_id",
            [email, "Platform Operatörü", "argon2$placeholder"],
        )
        kayit = cur.fetchone()
    return kayit[0], kayit[1]


# --- 1-2) şema ve tablolar --------------------------------------------------


def test_platform_semasi_var() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'platform'")
        assert cur.fetchone()[0] == 1


def test_platform_tablolari_platform_semasinda() -> None:
    """Tablolar `platform` şemasındadır; `hr` şemasına sızmamıştır."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'platform' AND c.relkind = 'r'"
        )
        platformda = {satir[0] for satir in cur.fetchall()}
    assert PLATFORM_TABLOLARI <= platformda, (
        f"eksik platform tablosu: {PLATFORM_TABLOLARI - platformda}"
    )

    with connection.cursor() as cur:
        # Aynı adlı kiracı tabloları `hr` şemasında bulunur (tasarla örtüşür);
        # önemli olan platform nesnelerinin AYRI şema olması ve `public`e
        # sızmamasıdır.
        cur.execute(
            "SELECT count(*) FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = ANY(%s)",
            [sorted(PLATFORM_TABLOLARI)],
        )
        assert cur.fetchone()[0] == 0, "platform tablosu public şemasına sızmış"

        for tablo in PLATFORM_TABLOLARI:
            cur.execute(
                "SELECT to_regclass('platform.' || %s) IS NOT NULL,"
                " to_regclass('platform.' || %s) IS DISTINCT FROM to_regclass('hr.' || %s)",
                [tablo, tablo, tablo],
            )
            platformda, ayri = cur.fetchone()
            assert platformda and ayri, f"platform.{tablo} ayrı bir nesne değil"


# --- 3-4) platform kullanıcısı --------------------------------------------


def test_platform_kullanicisi_tenant_id_tasimaz() -> None:
    """OD3: platform kullanıcısı kiracıya bağlı değildir."""
    for tablo in TENANT_ID_YASAK:
        assert "tenant_id" not in _kolonlar("platform", tablo), (
            f"platform.{tablo} tenant_id taşımamalı (OD3)"
        )
    # `tenant_registry.tenant_id` bir **referanstır** (OD20), sahiplik kolonu değil.
    assert "tenant_id" in _kolonlar("platform", "tenant_registry")


def test_platform_kullanicisi_hr_users_fk_degil() -> None:
    user_id, public_id = _platform_kullanici_ekle()
    assert user_id is not None
    # public_id: `plt-<8×Crockford>` (OD27/OD28) ve `plt` rezerve listededir.
    assert re.fullmatch(r"plt-[0-9A-HJKMNP-TV-Z]{8}", public_id), public_id

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.confrelid::regclass::text
              FROM pg_constraint c
             WHERE c.conrelid = 'platform.users'::regclass AND c.contype = 'f'
            """
        )
        hedefler = {satir[0] for satir in cur.fetchall()}
    assert hedefler <= {"platform.users"}, f"beklenmeyen FK: {hedefler}"


def test_platform_kullanicisinda_django_yetki_alanlari_yok() -> None:
    """OD19/OD3: `is_staff`/`is_superuser`/`groups` platform yetkisi DEĞİLDİR."""
    kolonlar = _kolonlar("platform", "users")
    assert "is_staff" not in kolonlar
    assert "is_superuser" not in kolonlar
    assert not any("group" in k for k in kolonlar)


# --- 5) roller ve izin ilişkileri -----------------------------------------


def test_platform_rolleri_kapali_kume() -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT code FROM platform.roles ORDER BY code")
        kodlar = [satir[0] for satir in cur.fetchall()]
    assert kodlar == ["platform_admin", "superadmin"]  # idempotent seed

    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO platform.roles (code, name) VALUES ('bilinmeyen_rol', 'X')"
            )


def test_platform_izin_iliskileri_tenant_rbac_den_ayri() -> None:
    """Platform FK'leri kiracı RBAC tablolarına **bağlanmaz**."""
    beklenen = {
        "role_permissions": {"platform.roles", "hr.permissions"},
        "user_roles": {"platform.users", "platform.roles"},
        "tokens": {"platform.users"},
    }
    with connection.cursor() as cur:
        for tablo, hedefler in beklenen.items():
            cur.execute(
                """
                SELECT n.nspname || '.' || hedef.relname AS hedef
                  FROM pg_constraint c
                  JOIN pg_class hedef ON hedef.oid = c.confrelid
                  JOIN pg_namespace n ON n.oid = hedef.relnamespace
                 WHERE c.conrelid = ('platform.' || %s)::regclass AND c.contype = 'f'
                """,
                [tablo],
            )
            gercek = {satir[0] for satir in cur.fetchall()}
            assert gercek <= hedefler, f"platform.{tablo} beklenmeyen FK: {gercek - hedefler}"
            assert not any(hedef.startswith("hr.roles") for hedef in gercek)
            assert not any(hedef.startswith("hr.users") for hedef in gercek)


# --- 6-8) platform token ----------------------------------------------------


def test_platform_token_ham_sir_saklamaz() -> None:
    user_id, _ = _platform_kullanici_ekle("token@persopyro.test")
    token_id, duz_metin = _token_uret(
        platform_user_id=user_id,
        purpose="od22 regresyon testi icin token",
        scope=["platform.tenants.read"],
        token_kind="interactive",
        ttl_hours=1,
    )

    with connection.cursor() as cur:
        cur.execute(
            "SELECT token_hash, expires_at, created_at FROM platform.tokens WHERE id = %s",
            [str(token_id)],
        )
        token_hash, expires_at, created_at = cur.fetchone()

    assert token_hash == hashlib.sha256(duz_metin.encode()).hexdigest()
    assert duz_metin not in token_hash
    assert len(token_hash) == 64
    # Süre zorunlu ve TTL sınırı içinde (OD22/6).
    assert created_at < expires_at <= created_at + timedelta(hours=24)

    # Aynı düz metinle arama yalnızca özet üzerinden çalışır.
    satir = platform_services.platform_token_by_plaintext(duz_metin)
    assert satir is not None and platform_services.token_is_usable(satir) is True
    assert platform_services.platform_token_by_plaintext("uydurma-token") is None


def test_platform_token_bos_kapsam_reddedilir() -> None:
    user_id, _ = _platform_kullanici_ekle("bos-scope@persopyro.test")
    with pytest.raises(platform_services.PlatformTokenRuleError):
        _token_uret(
            platform_user_id=user_id, purpose="bos kapsam denemesi", scope=[]
        )

    # DB kısıtı da reddeder (servis atlatılsa bile).
    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO platform.tokens (platform_user_id, purpose, scope, token_hash)"
                " VALUES (%s, %s, %s, %s)",
                [str(user_id), "bos kapsam ham sql denemesi", [], "b" * 64],
            )


def test_platform_token_kurallari_ve_guardlar() -> None:
    """TTL tavanı, joker yasağı, süre bitimi ve iptal davranışı."""
    user_id, _ = _platform_kullanici_ekle("kurallar@persopyro.test")

    with pytest.raises(platform_services.PlatformTokenRuleError):
        _token_uret(
            platform_user_id=user_id, purpose="uzun ttl denemesi", scope=["x.read"], ttl_hours=25
        )
    with pytest.raises(platform_services.PlatformTokenRuleError):
        _token_uret(
            platform_user_id=user_id, purpose="joker kapsam denemesi", scope=["*"]
        )
    with pytest.raises(platform_services.PlatformTokenRuleError):
        _token_uret(
            platform_user_id=user_id, purpose="kisa", scope=["x.read"]
        )

    # Süresi geçmiş token: kullanılamaz (DB kısıtı TTL tavanına izin verdiği için
    # doğrudan geçmiş tarihli satır yazılır).
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.tokens (platform_user_id, purpose, scope, token_hash,"
            " created_at, expires_at) VALUES (%s, %s, %s, %s, now() - interval '2 hours',"
            " now() - interval '1 hour') RETURNING id",
            [str(user_id), "suresi gecmis token testi", ["p.read"], "c" * 64],
        )
        gecmis_id = cur.fetchone()[0]
    with connection.cursor() as cur:
        cur.execute(
            "SELECT id, platform_user_id, token_kind, purpose, scope, token_hash, created_at,"
            " expires_at, revoked_at, revoked_reason, last_used_at"
            " FROM platform.tokens WHERE id = %s",
            [str(gecmis_id)],
        )
        gecmis_satir = cur.fetchone()
    assert platform_services.token_is_usable(gecmis_satir) is False

    # İptal: işaretlenir, tek yönlüdür, kayıt silinmez.
    token_id, _ = _token_uret(
        platform_user_id=user_id, purpose="iptal regresyon testi", scope=["p.read"]
    )
    assert platform_services.revoke_platform_token(token_id, "admin_revoke") is True
    assert platform_services.revoke_platform_token(token_id, "admin_revoke") is False

    with connection.cursor() as cur:
        cur.execute(
            "SELECT revoked_at, revoked_reason FROM platform.tokens WHERE id = %s",
            [str(token_id)],
        )
        revoked_at, reason = cur.fetchone()
    assert revoked_at is not None and reason == "admin_revoke"

    with pytest.raises(Exception, match="tek yonludur"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE platform.tokens SET revoked_at = NULL, revoked_reason = NULL"
                    " WHERE id = %s",
                    [str(token_id)],
                )

    # Değişmezlik: kapsam sonradan değiştirilemez (OD22/OD26 simetrisi).
    with pytest.raises(Exception, match="degistirilemez"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE platform.tokens SET scope = ARRAY['other.read'] WHERE id = %s",
                    [str(token_id)],
                )


def test_service_token_superadmin_izin_kumesiyle_uretilemez() -> None:
    """OD22/6: yetki yükseltme yasağı — DB tetikleyicisiyle dayatılır."""
    from apps.rbac.models import Permission

    user_id, _ = _platform_kullanici_ekle("service@persopyro.test")
    izin = Permission.objects.filter(code="employee.read").first()
    assert izin is not None, "izin kataloğu boş; rbac 0003_seed_permissions uygulanmalı"

    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO platform.role_permissions (role_id, permission_id)"
            " SELECT id, %s FROM platform.roles WHERE code = 'superadmin'",
            [str(izin.pk)],
        )

    with pytest.raises(Exception, match="superadmin"):
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO platform.tokens (platform_user_id, token_kind, purpose,"
                    " scope, token_hash) VALUES (%s, 'service', %s, %s, %s)",
                    [
                        str(user_id),
                        "superadmin kapsamli service token denemesi",
                        ["employee.read"],
                        "d" * 64,
                    ],
                )


# --- 9-15) registry --------------------------------------------------------


def test_registry_minimum_alanlari_icerir() -> None:
    """OD20: registry ikinci tam kiraci kopyasi DEĞİLDİR; PII taşımaz."""
    kolonlar = _kolonlar("platform", "tenant_registry")
    beklenen = {
        "tenant_id",
        "slug",
        "domain",
        "status",
        "max_users",
        "max_storage_mb",
        "plan_code",
        "billing_ref",
        "internal_note",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    assert kolonlar == beklenen, f"beklenmeyen fazla/eksik kolon: {kolonlar ^ beklenen}"
    for yasak in ("name", "legal_name", "tax_number", "email", "phone", "address", "settings"):
        assert yasak not in kolonlar


def test_tenant_olusturunca_registry_ayni_islemde_olusur(tenant_a) -> None:  # noqa: ANN001
    """Yansı aynı işlem içinde yazılır (commit beklemeden görünür)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT slug, status FROM platform.tenant_registry WHERE tenant_id = %s",
            [str(tenant_a.id)],
        )
        kayit = cur.fetchone()
    assert kayit is not None, "registry satırı aynı işlemde oluşmalı (OD20)"
    assert kayit[0] == tenant_a.slug


def test_registry_guncellemesi_tenant_degisimini_yansitir(tenant_a) -> None:  # noqa: ANN001
    with tenant_context(tenant_a.id):
        Tenant.objects.filter(pk=tenant_a.id).update(status="suspended", max_users=42)
    with connection.cursor() as cur:
        cur.execute(
            "SELECT status, max_users FROM platform.tenant_registry WHERE tenant_id = %s",
            [str(tenant_a.id)],
        )
        assert cur.fetchone() == ("suspended", 42)


def test_registry_platform_alanlari_yansi_ile_ezilmez(tenant_a) -> None:  # noqa: ANN001
    """`plan_code`/`billing_ref`/`internal_note` tek sahibi platformdur."""
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE platform.tenant_registry SET plan_code = 'pro', billing_ref = 'INV-1',"
            " internal_note = 'ticari not' WHERE tenant_id = %s",
            [str(tenant_a.id)],
        )

    with tenant_context(tenant_a.id):
        tenant_a.name = "Yeni Ad"
        tenant_a.save(update_fields=["name", "updated_at"])

    with connection.cursor() as cur:
        cur.execute(
            "SELECT plan_code, billing_ref, internal_note FROM platform.tenant_registry"
            " WHERE tenant_id = %s",
            [str(tenant_a.id)],
        )
        assert cur.fetchone() == ("pro", "INV-1", "ticari not")


def test_registry_yazimi_basarisizsa_tenant_degisikligi_geri_alinir(tenant_a, tenant_b) -> None:  # noqa: ANN001
    """Fail-closed (OD20): yansı yazılamazsa kaynak yazımı da geri alınır."""
    # Önce bilinçli bir tutarsızlık kurulur: başka kiracının satırı, yeni kiracının
    # slug'ını "işgal ediyor" → yeni kiracı INSERT'inde yansı benzersizliği kırılır.
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE platform.tenant_registry SET slug = 'cakisan-slug' WHERE tenant_id = %s",
            [str(tenant_b.id)],
        )

    yeni_id = uuid.uuid4()
    with tenant_context(yeni_id):
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenant.objects.create(id=yeni_id, name="Çakışan", slug="cakisan-slug")

    # Kiracı satırı da oluşmamış olmalı (rollback), registry'de yeni satır yok.
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM platform.tenant_registry WHERE tenant_id = %s", [str(yeni_id)]
        )
        assert cur.fetchone()[0] == 0
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM hr.tenant_directory WHERE tenant_id = %s", [str(yeni_id)])
        assert cur.fetchone()[0] == 0


def test_registry_soft_delete_yansitilir_ve_satir_kalir(tenant_a) -> None:  # noqa: ANN001
    """Soft-delete'te dizin satırı **çıkar** (giriş), registry satırı **kalır** (filtre)."""
    with tenant_context(tenant_a.id):
        Tenant.objects.filter(pk=tenant_a.id).update(deleted_at=timezone.now())

    client_okuma = _raw_registry(tenant_a.id)
    assert client_okuma is not None and client_okuma[0] is not None  # deleted_at dolu

    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM hr.tenant_directory WHERE tenant_id = %s", [str(tenant_a.id)]
        )
        assert cur.fetchone()[0] == 0


def _raw_registry(tenant_id) -> tuple:  # noqa: ANN001
    with connection.cursor() as cur:
        cur.execute(
            "SELECT deleted_at FROM platform.tenant_registry WHERE tenant_id = %s",
            [str(tenant_id)],
        )
        return cur.fetchone()


def test_registryden_hr_tenants_a_ters_yazim_yok() -> None:
    """
    OD20: ters yön yoktur.

    (a) Platform taraflı fonksiyonlar `hr.tenants`'a yazmaz,
    (b) platform rolü `hr.tenants` üzerinde yazma yetkisine sahip değildir.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT p.proname, pg_get_functiondef(p.oid)
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'platform'
            """
        )
        for ad, tanim in cur.fetchall():
            govde = tanim.lower()
            assert "hr.tenants" not in govde or "insert into hr" not in govde, ad
            assert "update hr.tenants" not in govde, ad
            assert "delete from hr.tenants" not in govde, ad

    if not _platform_rolu_var():
        pytest.skip(f"{settings.PLATFORM_DB_ROLE} rolü yok — platform yetki ölçümü atlandı")
    with connection.cursor() as cur:
        for yetki in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'hr.tenants', %s)",
                [settings.PLATFORM_DB_ROLE, yetki],
            )
            assert cur.fetchone()[0] is False, f"platform rolü hr.tenants {yetki} yetkili"


# --- yetki allowlist (13-14) ve korunmuş daraltmalar (16-17) --------------


def test_uygulama_rolu_platform_semasinda_yetkisiz() -> None:
    """OD3/M1: kiracı runtime yolu platform verisine ULAŞAMAZ (şema dahil)."""
    with connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'platform', 'USAGE')", [settings.APP_DB_ROLE])
        assert cur.fetchone()[0] is False

        cur.execute(
            "SELECT c.relname,"
            " has_table_privilege(%s, c.oid, 'SELECT'),"
            " has_table_privilege(%s, c.oid, 'INSERT'),"
            " has_table_privilege(%s, c.oid, 'UPDATE'),"
            " has_table_privilege(%s, c.oid, 'DELETE')"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'platform' AND c.relkind = 'r'",
            [settings.APP_DB_ROLE] * 4,
        )
        for relname, *yetkiler in cur.fetchall():
            assert not any(yetkiler), f"persopyro_app platform.{relname} üzerinde yetkili"


def test_platform_rolu_allowlist_durumu() -> None:
    """
    Platform rolü: platform şemasında DML, `hr` şemasında YALNIZCA katalog okuma.

    Rol yoksa (süper kullanıcı adımı çalıştırılmadıysa) test atlanır ve raporlanır.
    """
    if not _platform_rolu_var():
        pytest.skip(
            f"{settings.PLATFORM_DB_ROLE} rolü yok — `sudo -u postgres psql -d <db> "
            "-f scripts/03_platform_roles.sql` çalıştırılmalı"
        )
    rol = settings.PLATFORM_DB_ROLE
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles"
            " WHERE rolname = %s",
            [rol],
        )
        assert cur.fetchone() == (False, False, False, False)

        cur.execute("SELECT has_schema_privilege(%s, 'platform', 'USAGE')", [rol])
        assert cur.fetchone()[0] is True

        for tablo in PLATFORM_TABLOLARI:
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, ('platform.' || %s)::regclass, %s)",
                    [rol, tablo, yetki],
                )
                assert cur.fetchone()[0] is True, f"platform rolü {tablo} {yetki} yetkisiz"

        # Kiracı tablolarına yetki VERİLMEZ (erişim-onay katmanı ayrı iş kalemi).
        for tablo in ("tenants", "users", "audit_logs", "api_tokens"):
            for yetki in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, ('hr.' || %s)::regclass, %s)",
                    [rol, tablo, yetki],
                )
                assert cur.fetchone()[0] is False, f"platform rolü hr.{tablo} {yetki} yetkili"

        cur.execute("SELECT has_schema_privilege(%s, 'platform', 'CREATE')", [rol])
        assert cur.fetchone()[0] is False


def test_uygulama_rolu_bypassrls_degil() -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s",
            [settings.APP_DB_ROLE],
        )
        assert cur.fetchone() == (False, False)


def test_hr_rls_politikalari_degismedi() -> None:
    """OD3/D.1: platform paketi kiracı RLS politikalarını değiştirmez."""
    kiracı_tabloları = (
        "tenants",
        "users",
        "roles",
        "user_roles",
        "user_permission_overrides",
        "audit_logs",
        "role_permissions",
    )
    with connection.cursor() as cur:
        for tablo in kiracı_tabloları:
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = 'hr' AND c.relname = %s",
                [tablo],
            )
            assert cur.fetchone() == (True, True), f"hr.{tablo} RLS/FORCE kaybı"

            cur.execute(
                "SELECT count(*), string_agg(qual, ' ') FROM pg_policies"
                " WHERE schemaname = 'hr' AND tablename = %s AND policyname = 'tenant_isolation'",
                [tablo],
            )
            adet, ifade = cur.fetchone()
            assert adet == 1, f"hr.{tablo} tenant_isolation politikası yok"
            assert "current_tenant_id" in (ifade or ""), f"hr.{tablo} politika ifadesi değişmiş"


def test_od19_ve_m30_daraltmalari_korunuyor() -> None:
    """M22b (auth teknik tablolar) ve M30 (api_tokens) daraltmaları bozulmadı."""
    rol = settings.APP_DB_ROLE
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
                    [rol, tablo, yetki],
                )
                assert cur.fetchone()[0] is False, f"{tablo} {yetki} daraltması kayboldu"

        cur.execute("SELECT has_table_privilege(%s, 'hr.api_tokens', 'DELETE')", [rol])
        assert cur.fetchone()[0] is False
        cur.execute("SELECT has_table_privilege(%s, 'hr.api_tokens', 'UPDATE')", [rol])
        assert cur.fetchone()[0] is False
        cur.execute(
            "SELECT has_column_privilege(%s, 'hr.api_tokens', 'revoked_at', 'UPDATE')", [rol]
        )
        assert cur.fetchone()[0] is True


def test_fresh_install_scripti_repo_icinde_ve_calistirilabilir() -> None:
    """18) Sıfırdan kurulum doğrulaması kalıcı bir script olarak repoda."""
    from pathlib import Path

    yol = Path(settings.BASE_DIR) / "scripts" / "verify_fresh_install.sh"
    assert yol.is_file(), "scripts/verify_fresh_install.sh bulunamadı"
    assert yol.stat().st_mode & 0o111, "script çalıştırılabilir değil"
    icerik = yol.read_text(encoding="utf-8")
    assert "DROP DATABASE" in icerik and "trap" in icerik
    assert "verify" in yol.name

