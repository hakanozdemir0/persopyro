"""Revoke unconditional platform-role access to tenant PII (OD1/OD4)."""

from django.db import migrations


REVOKE_UNCONDITIONAL_PII = """
UPDATE platform.role_permissions AS rp
   SET deleted_at = now()
  FROM platform.roles AS r, hr.permissions AS p
 WHERE rp.role_id = r.id
   AND rp.permission_id = p.id
   AND r.code = 'superadmin'
   AND p.code = 'platform.tenant.pii.read'
   AND rp.deleted_at IS NULL;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0009_superadmin_tenant_create"),
        ("rbac", "0004_od3_platform_permissions"),
    ]

    operations = [
        migrations.RunSQL(
            REVOKE_UNCONDITIONAL_PII,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
