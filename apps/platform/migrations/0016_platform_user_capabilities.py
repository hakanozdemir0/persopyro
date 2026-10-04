"""Per-account visibility choices for platform administrators."""

from django.db import migrations


FORWARD_SQL = """
CREATE TABLE platform.user_capabilities (
    platform_user_id uuid PRIMARY KEY REFERENCES platform.users(id) ON DELETE CASCADE,
    can_view_admins boolean NOT NULL DEFAULT false,
    can_view_audit boolean NOT NULL DEFAULT false,
    granted_by_id uuid REFERENCES platform.users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO platform.user_capabilities (platform_user_id, can_view_admins, can_view_audit)
SELECT u.id, false, bool_or(r.code = 'platform_admin')
  FROM platform.users u
  JOIN platform.user_roles ur ON ur.platform_user_id=u.id AND ur.deleted_at IS NULL
  JOIN platform.roles r ON r.id=ur.role_id AND r.deleted_at IS NULL
 WHERE u.deleted_at IS NULL
 GROUP BY u.id
ON CONFLICT (platform_user_id) DO NOTHING;
REVOKE ALL ON platform.user_capabilities FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.user_capabilities FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON platform.user_capabilities TO persopyro_platform;
    END IF;
END
$$;
"""

REVERSE_SQL = "DROP TABLE IF EXISTS platform.user_capabilities;"


class Migration(migrations.Migration):
    dependencies = [("platform", "0015_platform_user_tenant_access")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
