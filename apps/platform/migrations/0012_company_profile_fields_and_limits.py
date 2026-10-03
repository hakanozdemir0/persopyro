"""Company profile configuration and tenant limits in platform registry."""

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE platform.tenant_registry
    ADD COLUMN valid_from date,
    ADD COLUMN max_active_employees integer NOT NULL DEFAULT 0,
    ADD COLUMN max_employee_records integer NOT NULL DEFAULT 0,
    ADD COLUMN profile_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT platform_registry_employee_limits_nonneg
        CHECK (max_active_employees >= 0 AND max_employee_records >= 0),
    ADD CONSTRAINT platform_registry_profile_fields_array
        CHECK (jsonb_typeof(profile_fields) = 'array');

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
        created_at, updated_at, deleted_at, valid_until, valid_from,
        max_active_employees, max_employee_records
    ) VALUES (
        NEW.id, NEW.slug, NEW.domain, NEW.status, NEW.max_users, NEW.max_storage_mb,
        NEW.created_at, now(), NEW.deleted_at, NEW.valid_until, NEW.valid_from,
        NEW.max_active_employees, NEW.max_employee_records
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
        valid_from = EXCLUDED.valid_from,
        max_active_employees = EXCLUDED.max_active_employees,
        max_employee_records = EXCLUDED.max_employee_records,
        updated_at = now();
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
CREATE TRIGGER trg_tenants_sync_registry
    AFTER INSERT OR UPDATE OF slug, domain, status, max_users, max_storage_mb,
        deleted_at, valid_until, valid_from, max_active_employees,
        max_employee_records OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.sync_tenant_registry();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS trg_tenants_sync_registry ON hr.tenants;
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
        slug = EXCLUDED.slug, domain = EXCLUDED.domain, status = EXCLUDED.status,
        max_users = EXCLUDED.max_users, max_storage_mb = EXCLUDED.max_storage_mb,
        created_at = EXCLUDED.created_at, deleted_at = EXCLUDED.deleted_at,
        valid_until = EXCLUDED.valid_until, updated_at = now();
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_tenants_sync_registry
    AFTER INSERT OR UPDATE OF slug, domain, status, max_users, max_storage_mb,
        deleted_at, valid_until OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION platform.sync_tenant_registry();
ALTER TABLE platform.tenant_registry
    DROP CONSTRAINT platform_registry_employee_limits_nonneg,
    DROP CONSTRAINT platform_registry_profile_fields_array,
    DROP COLUMN valid_from,
    DROP COLUMN max_active_employees,
    DROP COLUMN max_employee_records,
    DROP COLUMN profile_fields;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0011_access_requests_and_grants"),
        ("tenancy", "0011_tenant_company_limits"),
    ]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
