from django.db import migrations


def repair_shift_permissions(apps, schema_editor):  # noqa: ANN001
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission_ids = {
        p.code: p.pk
        for p in Permission.objects.filter(
            code__in=("shift.read", "shift.assign", "shift.schedule.manage")
        )
    }
    if len(permission_ids) != 3:
        return
    for role in Role.objects.filter(
        code__in=("tenant_admin", "tenant_officer", "hr_manager", "dept_manager"),
        deleted_at__isnull=True,
    ):
        # Varsayılan yönetici rollerinin kapsamı da sabitlenir. Eski demo
        # verilerinde rol izinleri doğru olsa bile kapsam tipi boş/yanlışsa
        # object_in_org_scope işlemi yine 403 döndürür.
        beklenen_kapsam = "UNIT" if role.code == "dept_manager" else "TENANT"
        if role.scope_type != beklenen_kapsam:
            role.scope_type = beklenen_kapsam
            role.save(update_fields=["scope_type", "updated_at"])
        for code, permission_id in permission_ids.items():
            RolePermission.objects.get_or_create(
                tenant_id=role.tenant_id,
                role_id=role.pk,
                permission_id=permission_id,
            )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0017_ui_shift_permissions")]
    operations = [migrations.RunPython(repair_shift_permissions, migrations.RunPython.noop)]
