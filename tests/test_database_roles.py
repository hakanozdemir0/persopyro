"""
Veritabanı rol/ayrıcalık değişmezleri.

Bu testler, çok kiracılı izolasyonun dayandığı yapısal koşulları doğrular:
uygulama rolü yükseltilmiş yetki taşımamalı ve DDL yapamamalıdır. Aksi halde
RLS sessizce etkisiz hale gelir (docs/SECURITY.md bölüm 4).

ÖNEMLİ: Testlerin bağlandığı rol (owner) ile yetkileri sınanan rol (app) ayrıdır.
Testler kendi test veritabanını oluşturabilmek için sahip rolle bağlanır; bu
yüzden sınanan hedef rol daima settings.APP_DB_ROLE'dur — bağlantı rolü değil.

Davranışsal kiracı izolasyonu (gerçek tablolarla SELECT/INSERT sınırları) faz 2'de,
Django modelleri ve migration'ları eklendiğinde test edilecektir. RLS'in
veritabanı seviyesinde çalıştığının kanıtı: scripts/02_verify_isolation.sql
(iddialar A1-A9).
"""

import pytest
from django.conf import settings
from django.db import OperationalError, connection

pytestmark = pytest.mark.django_db


def _db_erisilebilir() -> None:
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except OperationalError as exc:  # pragma: no cover - ortam eksikse
        pytest.skip(f"PostgreSQL erişilebilir değil (kurulum: scripts/bootstrap.sh): {exc}")


def _rol_ozellikleri(rol_adi: str):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
            "FROM pg_roles WHERE rolname = %s",
            [rol_adi],
        )
        return cur.fetchone()


def test_uygulama_rolu_var() -> None:
    _db_erisilebilir()
    assert _rol_ozellikleri(settings.APP_DB_ROLE) is not None, (
        f"{settings.APP_DB_ROLE} rolü yok; scripts/bootstrap.sh çalıştırılmalı"
    )


def test_uygulama_rolu_superuser_degil_ve_rls_atlayamiyor() -> None:
    """Bu dört bayrak yanlışsa kiracı izolasyonu sessizce çöker."""
    _db_erisilebilir()
    row = _rol_ozellikleri(settings.APP_DB_ROLE)
    assert row is not None, f"{settings.APP_DB_ROLE} rolü bulunamadı"
    rolsuper, rolcreatedb, rolcreaterole, rolbypassrls = row
    assert rolsuper is False, "uygulama rolü superuser olmamalı"
    assert rolbypassrls is False, "uygulama rolü RLS'i atlayabilmemeli"
    assert rolcreatedb is False, "uygulama rolü veritabanı oluşturmamalı"
    assert rolcreaterole is False, "uygulama rolü rol oluşturmamalı"


def test_uygulama_rolu_hr_semasinda_ddl_yapamaz() -> None:
    """DDL yetkisi yalnızca migration rolünde olmalı."""
    _db_erisilebilir()
    with connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'hr', 'CREATE')", [settings.APP_DB_ROLE])
        assert cur.fetchone()[0] is False, "uygulama rolü hr şemasında CREATE yetkisine sahip"


def test_uygulama_rolu_hr_semasini_kullanabilir() -> None:
    """İzolasyon, uygulamanın şemayı hiç kullanamaması anlamına gelmez."""
    _db_erisilebilir()
    with connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'hr', 'USAGE')", [settings.APP_DB_ROLE])
        assert cur.fetchone()[0] is True


def test_uygulama_rolu_public_semasinda_ddl_yapamaz() -> None:
    _db_erisilebilir()
    with connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", [settings.APP_DB_ROLE])
        assert cur.fetchone()[0] is False


def test_baglanti_tenant_baglami_icin_hazir() -> None:
    """
    Kiracı bağlamı okunabilmeli ve ayarlanmadığında NULL/boş dönmelidir.

    Boş string kabul edilir: PostgreSQL, SET LOCAL ile tanışan özel parametreyi
    işlem sonunda '' değerine döndürür (spike bulgusu, scripts/02_verify_isolation.sql A8).
    """
    _db_erisilebilir()
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.tenant_id', TRUE)")
        assert cur.fetchone()[0] in (None, "")


def test_baglanti_sema_arama_yolu_hr_oncelikli() -> None:
    """Ayarlardaki bağlantı seçeneği gerçekten uygulanıyor mu (hr önce gelmeli)."""
    _db_erisilebilir()
    with connection.cursor() as cur:
        cur.execute("SHOW search_path")
        search_path = cur.fetchone()[0]
    assert search_path.replace('"', "").startswith("hr"), (
        f"beklenen arama yolu 'hr,public', gelen: {search_path}"
    )


def test_migration_rolu_uygulama_rolunden_ayri() -> None:
    """İki rol ayrı olmalı: sahibi rol RLS'i atlatabilir, uygulama rolü atlatamaz."""
    _db_erisilebilir()
    assert settings.APP_DB_ROLE != settings.OWNER_DB_ROLE, (
        "uygulama ve migration rolleri aynı olamaz"
    )
