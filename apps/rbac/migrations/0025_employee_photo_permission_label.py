from django.db import migrations


def update_label(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hr.permissions SET name = %s WHERE code = %s",
            ["Çalışan fotoğrafı ekleme, değiştirme ve kaldırma", "employee.photo.upload"],
        )


def restore_label(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE hr.permissions SET name = %s WHERE code = %s",
            ["Çalışan fotoğrafı yükleme/değiştirme", "employee.photo.upload"],
        )


class Migration(migrations.Migration):
    dependencies = [("rbac", "0024_employee_photo_upload_permission")]
    operations = [migrations.RunPython(update_label, restore_label)]
