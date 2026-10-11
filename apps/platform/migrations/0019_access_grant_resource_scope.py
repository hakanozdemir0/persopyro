"""Bind platform access grants to the employee records requested."""

from django.db import migrations


FORWARD_SQL = """
ALTER TABLE platform.access_requests
    ADD COLUMN resource_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT access_requests_resource_ids_array
        CHECK (jsonb_typeof(resource_ids) = 'array');
ALTER TABLE platform.access_grants
    ADD COLUMN resource_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT access_grants_resource_ids_array
        CHECK (jsonb_typeof(resource_ids) = 'array');
"""

REVERSE_SQL = """
ALTER TABLE platform.access_grants
    DROP CONSTRAINT access_grants_resource_ids_array,
    DROP COLUMN resource_ids;
ALTER TABLE platform.access_requests
    DROP CONSTRAINT access_requests_resource_ids_array,
    DROP COLUMN resource_ids;
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0018_superadmin_role_assign")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
