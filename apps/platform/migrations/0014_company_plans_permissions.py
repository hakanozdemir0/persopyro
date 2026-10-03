"""Grant the platform runtime role access to the plan catalog."""

from django.db import migrations

FORWARD_SQL = """
REVOKE ALL ON platform.company_plans FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON platform.company_plans TO persopyro_platform;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.company_plans FROM persopyro_app;
    END IF;
END
$$;
"""

REVERSE_SQL = "REVOKE ALL ON platform.company_plans FROM persopyro_platform;"


class Migration(migrations.Migration):
    dependencies = [("platform", "0013_company_plans")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
