from django.db import migrations


PERMISSION_CODE = "employee.photo.upload"


def add_photo_permission(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO hr.permissions
               (id, created_at, updated_at, code, name, module, description)
               VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, %s)
               ON CONFLICT (code) DO NOTHING""",
            [PERMISSION_CODE, "Çalışan fotoğrafı yükleme/değiştirme", "employee", ""],
        )
        cursor.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        tenants = [row[0] for row in cursor.fetchall()]
        for tenant_id in tenants:
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cursor.execute(
                """INSERT INTO hr.role_permissions
                   (id, created_at, updated_at, tenant_id, role_id, permission_id)
                   SELECT gen_random_uuid(), now(), now(), %s, r.id, p.id
                   FROM hr.roles r CROSS JOIN hr.permissions p
                   WHERE r.code IN ('tenant_admin', 'hr_manager')
                     AND r.deleted_at IS NULL AND p.code = %s
                   ON CONFLICT (role_id, permission_id) DO NOTHING""",
                [str(tenant_id), PERMISSION_CODE],
            )
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


def remove_photo_permission(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM hr.role_permissions rp USING hr.permissions p "
            "WHERE rp.permission_id = p.id AND p.code = %s",
            [PERMISSION_CODE],
        )
        cursor.execute("DELETE FROM hr.permissions WHERE code = %s", [PERMISSION_CODE])


class Migration(migrations.Migration):
    dependencies = [("rbac", "0023_seed_shift_manager_role")]
    operations = [migrations.RunPython(add_photo_permission, remove_photo_permission)]
