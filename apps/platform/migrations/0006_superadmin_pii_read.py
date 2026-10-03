"""SUPERADMIN'a platform üzerinden kişisel veri okuma izni ekler.

M31'de kullanılan canlı rol sabitleri tarihsel migration girdisidir; bu izin
ayrı migration ile eklenir, böylece eski migration'ların yeniden oynatılması
sonucu değişmez. platform_admin bu izni almaz.
"""

from django.db import migrations

PERMISSION_CODE = "platform.tenant.pii.read"
PERMISSION_NAME = "Platform: kiracı kişisel verilerini görme (SUPERADMIN)"


def pii_iznini_superadmina_ver(apps, schema_editor):  # noqa: ANN001, ARG001
    Permission = apps.get_model("rbac", "Permission")
    permission, _ = Permission.objects.get_or_create(
        code=PERMISSION_CODE,
        defaults={"name": PERMISSION_NAME, "module": "platform", "description": ""},
    )

    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "SELECT id, deleted_at FROM platform.roles WHERE code = 'superadmin'"
        )
        role = cur.fetchone()
        if role is None or role[1] is not None:
            raise RuntimeError("Etkin platform superadmin rolü bulunamadı.")
        role_id = role[0]
        cur.execute(
            "SELECT deleted_at FROM platform.role_permissions "
            "WHERE role_id = %s AND permission_id = %s",
            [role_id, permission.pk],
        )
        binding = cur.fetchone()
        if binding is not None:
            if binding[0] is not None:
                raise RuntimeError(
                    "SUPERADMIN kişisel veri izni bağı soft-delete edilmiş; otomatik geri alınmaz."
                )
            return
        cur.execute(
            "INSERT INTO platform.role_permissions (role_id, permission_id) VALUES (%s, %s)",
            [role_id, permission.pk],
        )


def pii_iznini_kaldir(apps, schema_editor):  # noqa: ANN001, ARG001
    Permission = apps.get_model("rbac", "Permission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if permission is None:
        return
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            "DELETE FROM platform.role_permissions WHERE permission_id = %s",
            [permission.pk],
        )
    permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0005_od3_platform_rolleri_ve_izinleri"),
        ("rbac", "0004_od3_platform_permissions"),
    ]

    operations = [migrations.RunPython(pii_iznini_superadmina_ver, pii_iznini_kaldir)]
