from django.db import migrations


PERMISSION_CODES = (
    "org.department.read",
    "employee.read",
    "shift.read",
    "shift.assign",
    "shift.schedule.manage",
)


def seed_shift_manager_role(apps, schema_editor):  # noqa: ANN001
    Role = apps.get_model("rbac", "Role")
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        tenant_ids = [row[0] for row in cursor.fetchall()]

    permissions = {
        item.code: item for item in Permission.objects.filter(code__in=PERMISSION_CODES)
    }
    missing = set(PERMISSION_CODES) - set(permissions)
    if missing:
        raise RuntimeError("Vardiya Sorumlusu izinleri eksik: " + ", ".join(sorted(missing)))

    for tenant_id in tenant_ids:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
        existing = Role.objects.filter(tenant_id=tenant_id, code="shift_manager").first()
        if existing is not None:
            continue
        role = Role.objects.create(
            tenant_id=tenant_id,
            code="shift_manager",
            name="Vardiya Sorumlusu",
            description="Atandığı departman ve alt departmanlarının vardiya planını yönetir.",
            role_type="default",
            scope_type="UNIT",
            is_assignable=True,
        )
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    tenant_id=tenant_id,
                    role_id=role.pk,
                    permission_id=permissions[code].pk,
                )
                for code in PERMISSION_CODES
            ],
            ignore_conflicts=True,
        )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("rbac", "0022_role_scope_turkish_labels")]
    operations = [migrations.RunPython(seed_shift_manager_role, migrations.RunPython.noop)]
