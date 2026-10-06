from django.db import migrations


PERMISSION = ("shift.report.read", "Vardiya raporlarını görme ve dışa aktarma", "shift")


def add_permission(apps, schema_editor):  # noqa: ANN001
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission, _ = Permission.objects.get_or_create(
        code=PERMISSION[0],
        defaults={"name": PERMISSION[1], "module": PERMISSION[2]},
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        tenant_ids = [row[0] for row in cursor.fetchall()]
    for tenant_id in tenant_ids:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        for role in Role.objects.filter(
            tenant_id=tenant_id,
            code__in=("tenant_admin", "hr_manager"),
            deleted_at__isnull=True,
        ):
            RolePermission.objects.get_or_create(
                tenant_id=tenant_id,
                role_id=role.pk,
                permission_id=permission.pk,
            )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


def remove_permission(apps, schema_editor):  # noqa: ANN001
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission = Permission.objects.filter(code=PERMISSION[0]).first()
    if permission is not None:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
            tenant_ids = [row[0] for row in cursor.fetchall()]
        for tenant_id in tenant_ids:
            with schema_editor.connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            RolePermission.objects.filter(tenant_id=tenant_id, permission_id=permission.pk).delete()
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', '', true)")
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0025_employee_photo_permission_label")]
    operations = [migrations.RunPython(add_permission, remove_permission)]
