from django.db import migrations


def repair_demo_admin_assignment(apps, schema_editor):  # noqa: ANN001
    Tenant = apps.get_model("tenancy", "Tenant")
    User = apps.get_model("accounts", "User")
    Role = apps.get_model("rbac", "Role")
    UserRole = apps.get_model("rbac", "UserRole")

    tenant = Tenant.objects.filter(slug="demo", deleted_at__isnull=True).first()
    if tenant is None:
        return
    user = User.objects.filter(
        tenant_id=tenant.pk, email="demo@persopyro.test", deleted_at__isnull=True
    ).first()
    role = Role.objects.filter(
        tenant_id=tenant.pk, code="tenant_admin", deleted_at__isnull=True
    ).first()
    if user is None or role is None:
        return
    UserRole.objects.get_or_create(
        tenant_id=tenant.pk, user_id=user.pk, role_id=role.pk,
    )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0019_repair_manager_role_scopes")]
    operations = [migrations.RunPython(repair_demo_admin_assignment, migrations.RunPython.noop)]
