"""Kiracı kullanım sonunu platform şirket dizinine yansıtır."""

from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION platform.sync_tenant_registry()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM platform.tenant_registry WHERE tenant_id = OLD.id;
        RETURN OLD;
    END IF;

    INSERT INTO platform.tenant_registry (
        tenant_id, slug, domain, status, max_users, max_storage_mb,
        created_at, updated_at, deleted_at, valid_until
    ) VALUES (
        NEW.id, NEW.slug, NEW.domain, NEW.status, NEW.max_users, NEW.max_storage_mb,
        NEW.created_at, now(), NEW.deleted_at, NEW.valid_until
    )
    ON CONFLICT (tenant_id) DO UPDATE SET
        slug = EXCLUDED.slug,
        domain = EXCLUDED.domain,
        status = EXCLUDED.status,
        max_users = EXCLUDED.max_users,
        max_storage_mb = EXCLUDED.max_storage_mb,
        created_at = EXCLUDED.created_at,
        deleted_at = EXCLUDED.deleted_at,
        valid_until = EXCLUDED.valid_until,
        updated_at = now();
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
CREATE TRIGGER trg_tenants_sync_registry
    AFTER INSERT OR UPDATE OF slug, domain, status, max_users, max_storage_mb,
        deleted_at, valid_until OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.sync_tenant_registry();
"""

REVERSE_SQL = """
CREATE OR REPLACE FUNCTION platform.sync_tenant_registry()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM platform.tenant_registry WHERE tenant_id = OLD.id;
        RETURN OLD;
    END IF;

    INSERT INTO platform.tenant_registry (
        tenant_id, slug, domain, status, max_users, max_storage_mb,
        created_at, updated_at, deleted_at
    ) VALUES (
        NEW.id, NEW.slug, NEW.domain, NEW.status, NEW.max_users, NEW.max_storage_mb,
        NEW.created_at, now(), NEW.deleted_at
    )
    ON CONFLICT (tenant_id) DO UPDATE SET
        slug = EXCLUDED.slug,
        domain = EXCLUDED.domain,
        status = EXCLUDED.status,
        max_users = EXCLUDED.max_users,
        max_storage_mb = EXCLUDED.max_storage_mb,
        created_at = EXCLUDED.created_at,
        deleted_at = EXCLUDED.deleted_at,
        updated_at = now();
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
CREATE TRIGGER trg_tenants_sync_registry
    AFTER INSERT OR UPDATE OF slug, domain, status, max_users, max_storage_mb,
        deleted_at OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.sync_tenant_registry();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0007_platform_audit_and_tenant_term"),
        ("tenancy", "0010_tenant_valid_until"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
