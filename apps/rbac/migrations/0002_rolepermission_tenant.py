# RolePermission tablosuna zorunlu `tenant_id` eklenmesi (elle yazılmıştır).
#
# NEDEN: Bu tablo ilk tasarımda "tanım tablosu" sayılıp RLS dışı bırakılmıştı.
# Bir test, bunun bir güvenlik açığı oluşturduğunu gösterdi: başka bir kiracının
# rolüne izin kaydı eklenebiliyordu (RLS yok, çapraz kiracı referansı
# engellenmiyordu). Çözüm: kiracı sütunu eklemek; böylece tablo da RLS ve guard
# tetikleyicisiyle korunur. Kural sadeleşti: kiracıya bağlı HER tablo `tenant_id`
# taşır.
#
# Mevcut satırlar için: alan önce NULL eklenip, doldurulduktan sonra NOT NULL'a
# çevrilecek şekilde üç adımda yazılmıştır (üretimde veri kaybı olmadan çalışır).

import django.db.models.deletion
from django.db import migrations, models


def _fill_from_role(apps, schema_editor):
    """Mevcut satırlarda tenant_id'yi rolün kiracısından doldurur."""
    RolePermission = apps.get_model("rbac", "RolePermission")
    through = RolePermission.objects.filter(tenant__isnull=True).select_related("role")
    for kayit in through:
        kayit.tenant_id = kayit.role.tenant_id
        kayit.save(update_fields=["tenant"])


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0001_initial"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolepermission",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="role_permissions",
                to="tenancy.tenant",
                verbose_name="kiracı",
            ),
        ),
        migrations.RunPython(_fill_from_role, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="rolepermission",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="role_permissions",
                to="tenancy.tenant",
                verbose_name="kiracı",
            ),
        ),
        migrations.AddIndex(
            model_name="rolepermission",
            index=models.Index(fields=["tenant", "role"], name="role_perms_tenant_idx"),
        ),
    ]
