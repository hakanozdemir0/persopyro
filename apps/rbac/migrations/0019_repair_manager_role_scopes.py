from django.db import migrations


def repair_manager_scopes(apps, schema_editor):  # noqa: ANN001
    Role = apps.get_model("rbac", "Role")
    Role.objects.filter(
        code__in=("tenant_admin", "tenant_officer", "hr_manager"),
        deleted_at__isnull=True,
    ).exclude(scope_type="TENANT").update(scope_type="TENANT")
    Role.objects.filter(
        code="dept_manager", deleted_at__isnull=True
    ).exclude(scope_type="UNIT").update(scope_type="UNIT")


class Migration(migrations.Migration):
    dependencies = [("rbac", "0018_repair_shift_manager_permissions")]
    operations = [migrations.RunPython(repair_manager_scopes, migrations.RunPython.noop)]
