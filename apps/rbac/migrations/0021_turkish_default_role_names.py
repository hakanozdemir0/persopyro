from django.db import migrations


ROLE_LABELS = {
    "tenant_admin": (
        "Sistem Yöneticisi",
        "Şirket genelinde tüm uygulama modüllerinde tam yetkilidir. Platform yönetim yetkisi vermez.",
    ),
    "tenant_officer": (
        "Şirket Operasyon Yetkilisi",
        "Şirket operasyonlarında tanımlı izinleri kullanır.",
    ),
    "hr_manager": (
        "İnsan Kaynakları Yöneticisi",
        "İnsan kaynakları işlemlerini şirket genelinde yönetir.",
    ),
    "dept_manager": (
        "Departman Sorumlusu",
        "Atandığı departmanda ve alt departmanlarında yetkilidir.",
    ),
    "employee": (
        "Çalışan",
        "Çalışan kendi hesabına ve kendisine açık işlemlere erişir.",
    ),
}


def update_default_role_labels(apps, schema_editor):  # noqa: ANN001
    Role = apps.get_model("rbac", "Role")
    for code, (name, description) in ROLE_LABELS.items():
        Role.objects.filter(code=code).update(name=name, description=description)


def restore_previous_labels(apps, schema_editor):  # noqa: ANN001
    Role = apps.get_model("rbac", "Role")
    previous = {
        "tenant_admin": "Kiracı Yöneticisi",
        "tenant_officer": "Şirket Yetkilisi",
        "hr_manager": "İK Yöneticisi",
        "dept_manager": "Departman Yöneticisi",
        "employee": "Çalışan",
    }
    for code, name in previous.items():
        Role.objects.filter(code=code).update(name=name, description="")


class Migration(migrations.Migration):
    dependencies = [("rbac", "0020_repair_demo_admin_assignment")]
    operations = [
        migrations.RunPython(update_default_role_labels, restore_previous_labels),
    ]
