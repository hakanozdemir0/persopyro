# M4 (OD20 kapalı) — `platform.tenant_registry` + tek yönlü yansı.
#
# `hr.tenants` **canonical / source-of-truth** kalır; registry onun ikinci tam
# kopyası DEĞİLDİR ve yalnızca platform operasyonları için gereken minimum
# alanları taşır (OD20):
#
#   * yansı (yalnız okunur kopya): `slug, domain, status, max_users,
#     max_storage_mb, created_at, deleted_at`
#   * yalnız platforma ait: `plan_code, billing_ref, internal_note`
#   * **kopyalanmaz:** `name, legal_name, tax_number, email, phone, address,
#     country, city, settings` (kiracı iş verisi/PII)
#
# Senkronizasyon **tek yönlüdür** (`hr.tenants` → registry), `SECURITY DEFINER`
# tetikleyiciyle **aynı işlem içinde** yapılır; yansı yazımı başarısız olursa
# kaynak yazımı da geri alınır (fail-closed, sapma yok). Ters yön yoktur:
# platform, kiracı alanlarını registry üzerinden değiştirmez.
#
# TETİKLEYİCİ GÜVENLİĞİ: `SECURITY DEFINER` + **sabit `search_path`**
# (`pg_catalog, pg_temp`) + tamamen şema-nitelemeli nesne adları → search_path
# ele geçirme (hijack) yüzeyi yoktur. Fonksiyon sahibi migration rolüdür
# (persopyro_owner) ve yalnızca registry tablosuna yazar.

from django.db import migrations

M4_SQL = """
CREATE TABLE platform.tenant_registry (
    tenant_id uuid PRIMARY KEY REFERENCES hr.tenants(id) ON DELETE CASCADE,
    slug varchar(60) NOT NULL,
    domain varchar(190),
    status varchar(20) NOT NULL,
    max_users integer NOT NULL,
    max_storage_mb numeric(12, 2) NOT NULL,
    plan_code varchar(40) NOT NULL DEFAULT '',
    billing_ref varchar(80) NOT NULL DEFAULT '',
    internal_note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    CONSTRAINT platform_tenant_registry_slug_key UNIQUE (slug),
    CONSTRAINT platform_tenant_registry_domain_key UNIQUE (domain),
    CONSTRAINT platform_tenant_registry_status_valid
        CHECK (status IN ('active', 'trial', 'suspended', 'canceled')),
    CONSTRAINT platform_tenant_registry_limits_nonneg
        CHECK (max_users >= 0 AND max_storage_mb >= 0)
);

COMMENT ON TABLE platform.tenant_registry IS
    'Kiraci referans indeksi (OD20): hr.tenants canonical; bu tablo minimum yansidir, '
    'ikinci mutable kiraci kaynagi DEGILDIR. Senkron tek yonlu ve fail-closed.';

-- Tek yönlü yansı tetikleyicisi (SECURITY DEFINER, sabit search_path) --------
CREATE OR REPLACE FUNCTION platform.sync_tenant_registry()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM platform.tenant_registry WHERE platform.tenant_registry.tenant_id = OLD.id;
        RETURN OLD;
    END IF;

    INSERT INTO platform.tenant_registry (
        tenant_id, slug, domain, status, max_users, max_storage_mb,
        created_at, updated_at, deleted_at
    )
    VALUES (
        NEW.id, NEW.slug, NEW.domain, NEW.status, NEW.max_users, NEW.max_storage_mb,
        NEW.created_at, now(), NEW.deleted_at
    )
    ON CONFLICT (tenant_id) DO UPDATE
        SET slug = EXCLUDED.slug,
            domain = EXCLUDED.domain,
            status = EXCLUDED.status,
            max_users = EXCLUDED.max_users,
            max_storage_mb = EXCLUDED.max_storage_mb,
            created_at = EXCLUDED.created_at,
            deleted_at = EXCLUDED.deleted_at,
            updated_at = now();
    -- DİKKAT: plan_code / billing_ref / internal_note BİLİNÇLİ olarak
    -- güncellenmez — tek sahibi platformdur (OD20).
    RETURN NEW;
END
$$;

COMMENT ON FUNCTION platform.sync_tenant_registry() IS
    'hr.tenants -> platform.tenant_registry tek yonlu yansi (OD20). Sabit search_path; '
    'yalnizca registry yazar; basarisizlik kaynak yazimi da geri alir.';

DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
CREATE TRIGGER trg_tenants_sync_registry
    AFTER INSERT OR UPDATE OF slug, domain, status, max_users, max_storage_mb, deleted_at
    OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.sync_tenant_registry();

-- Yetki turu: yalnızca platform rolü --------------------------------------
REVOKE ALL ON platform.tenant_registry FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.tenant_registry FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON platform.tenant_registry TO persopyro_platform;
    END IF;
END
$$;
"""

M4_REVERSE = """
DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
DROP FUNCTION IF EXISTS platform.sync_tenant_registry();
DROP TABLE IF EXISTS platform.tenant_registry;
"""


def registry_geri_doldur(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """
    Mevcut kiracıları registry'ye taşır (M4 backfill).

    `hr.tenants` FORCE RLS ile korunduğu için okuma **kiracı bağlamı içinde**
    yapılır; kiracı listesi RLS'siz `hr.tenant_directory`'den gelir.
    """
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        kiraci_idleri = [satir[0] for satir in cur.fetchall()]

        for tenant_id in kiraci_idleri:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute(
                """
                SELECT t.id, t.slug, t.domain, t.status, t.max_users,
                       t.max_storage_mb, t.created_at, t.deleted_at
                  FROM hr.tenants t
                 WHERE t.id = %s
                """,
                [str(tenant_id)],
            )
            satir = cur.fetchone()
            if satir is None:
                continue
            cur.execute(
                """
                INSERT INTO platform.tenant_registry (
                    tenant_id, slug, domain, status, max_users, max_storage_mb,
                    created_at, updated_at, deleted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                list(satir),
            )


class Migration(migrations.Migration):

    dependencies = [
        ("platform", "0002_od22_platform_tokens"),
        ("tenancy", "0007_tenant_directory"),
    ]

    operations = [
        migrations.RunSQL(sql=M4_SQL, reverse_sql=M4_REVERSE),
        migrations.RunPython(registry_geri_doldur, migrations.RunPython.noop),
    ]
