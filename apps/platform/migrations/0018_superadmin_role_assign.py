"""Restore the superadmin's role assignment permission in existing databases."""

from django.db import migrations


def grant_role_assignment(apps, schema_editor):  # noqa: ANN001
    Permission = apps.get_model("rbac", "Permission")
    permission = Permission.objects.filter(code="platform.role.assign").first()
    if permission is None:
        raise RuntimeError("platform.role.assign permission is missing from the shared catalog.")

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, deleted_at FROM platform.roles WHERE code = 'superadmin'"
        )
        role = cursor.fetchone()
        if role is None or role[1] is not None:
            raise RuntimeError("An active platform superadmin role is required.")
        cursor.execute(
            "SELECT deleted_at FROM platform.role_permissions "
            "WHERE role_id = %s AND permission_id = %s",
            [role[0], permission.pk],
        )
        binding = cursor.fetchone()
        if binding is not None:
            if binding[0] is not None:
                raise RuntimeError(
                    "The superadmin role assignment permission is soft-deleted; "
                    "automatic restoration is refused."
                )
            return
        cursor.execute(
            "INSERT INTO platform.role_permissions (role_id, permission_id) "
            "VALUES (%s, %s)",
            [role[0], permission.pk],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0017_superadmin_capabilities"),
        ("rbac", "0004_od3_platform_permissions"),
    ]
    operations = [migrations.RunPython(grant_role_assignment, migrations.RunPython.noop)]
