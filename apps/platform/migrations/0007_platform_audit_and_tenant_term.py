"""Şirket geçerlilik tarihi ve platform denetim defteri."""

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE platform.tenant_registry
    ADD COLUMN valid_until date;

CREATE TABLE platform.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid,
    tenant_id uuid,
    action varchar(80) NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    ip_address varchar(45) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_audit_events_details_object
        CHECK (jsonb_typeof(details) = 'object')
);
CREATE INDEX platform_audit_events_created_idx
    ON platform.audit_events (created_at DESC);
CREATE INDEX platform_audit_events_tenant_created_idx
    ON platform.audit_events (tenant_id, created_at DESC);

CREATE OR REPLACE FUNCTION platform.audit_events_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'platform.audit_events is append-only';
END
$$;
CREATE TRIGGER trg_platform_audit_events_immutable
    BEFORE UPDATE OR DELETE ON platform.audit_events
    FOR EACH ROW EXECUTE FUNCTION platform.audit_events_immutable();

REVOKE ALL ON platform.audit_events FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.audit_events FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT ON platform.audit_events TO persopyro_platform;
    END IF;
END
$$;
"""

REVERSE_SQL = """
DROP TABLE IF EXISTS platform.audit_events;
DROP FUNCTION IF EXISTS platform.audit_events_immutable();
ALTER TABLE platform.tenant_registry DROP COLUMN IF EXISTS valid_until;
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0006_superadmin_pii_read")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
