"""SUPERADMIN'a şirket ve şirket yöneticisi oluşturma izni verir."""

from django.db import migrations


PERMISSION_CODE = "platform.tenant.create"
PERMISSION_NAME = "Platform: şirket ve şirket yöneticisi oluşturma (SUPERADMIN)"


def create_permission(apps, schema_editor):  # noqa: ANN001, ARG001
    Permission = apps.get_model("rbac", "Permission")
    permission, _ = Permission.objects.get_or_create(
        code=PERMISSION_CODE,
        defaults={"name": PERMISSION_NAME, "module": "platform", "description": ""},
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT id, deleted_at FROM platform.roles WHERE code = 'superadmin'")
        role = cursor.fetchone()
        if role is None or role[1] is not None:
            raise RuntimeError("Etkin platform superadmin rolü bulunamadı.")
        cursor.execute(
            "SELECT deleted_at FROM platform.role_permissions "
            "WHERE role_id = %s AND permission_id = %s",
            [role[0], permission.pk],
        )
        binding = cursor.fetchone()
        if binding is not None:
            if binding[0] is not None:
                raise RuntimeError("Şirket oluşturma izni soft-delete edilmiş; otomatik canlandırılmaz.")
            return
        cursor.execute(
            "INSERT INTO platform.role_permissions (role_id, permission_id) VALUES (%s, %s)",
            [role[0], permission.pk],
        )


def remove_permission(apps, schema_editor):  # noqa: ANN001, ARG001
    Permission = apps.get_model("rbac", "Permission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if permission is None:
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM platform.role_permissions WHERE permission_id = %s", [permission.pk]
        )
    permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0008_tenant_registry_valid_until"),
        ("rbac", "0004_od3_platform_permissions"),
    ]
    operations = [migrations.RunPython(create_permission, remove_permission)]
