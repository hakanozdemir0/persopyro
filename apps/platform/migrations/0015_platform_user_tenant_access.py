"""Restrict platform administrator accounts to explicitly assigned companies."""

from django.db import migrations


FORWARD_SQL = """
CREATE TABLE platform.user_tenant_access (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL REFERENCES platform.tenant_registry(tenant_id) ON DELETE CASCADE,
    granted_by_id uuid REFERENCES platform.users(id) ON DELETE SET NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_user_tenant_access_uniq UNIQUE (platform_user_id, tenant_id)
);
CREATE INDEX platform_user_tenant_access_tenant_idx
    ON platform.user_tenant_access (tenant_id, platform_user_id);
REVOKE ALL ON platform.user_tenant_access FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.user_tenant_access FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON platform.user_tenant_access TO persopyro_platform;
    END IF;
END
$$;
"""

REVERSE_SQL = "DROP TABLE IF EXISTS platform.user_tenant_access;"


class Migration(migrations.Migration):
    dependencies = [("platform", "0014_company_plans_permissions")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
