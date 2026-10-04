"""Keep superadmin visibility unrestricted when per-admin controls are introduced."""

from django.db import migrations


FORWARD_SQL = """
UPDATE platform.user_capabilities c
   SET can_view_admins=true, can_view_audit=true, updated_at=now()
 WHERE EXISTS (
       SELECT 1 FROM platform.user_roles ur
       JOIN platform.roles r ON r.id=ur.role_id
       WHERE ur.platform_user_id=c.platform_user_id
         AND ur.deleted_at IS NULL
         AND (ur.expires_at IS NULL OR ur.expires_at > now())
         AND r.code='superadmin' AND r.deleted_at IS NULL
   );
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0016_platform_user_capabilities")]
    operations = [migrations.RunSQL(FORWARD_SQL, migrations.RunSQL.noop)]
