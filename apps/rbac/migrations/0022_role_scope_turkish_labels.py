from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rbac", "0021_turkish_default_role_names")]

    operations = [
        migrations.AlterField(
            model_name="role",
            name="scope_type",
            field=models.CharField(
                choices=[
                    ("TENANT", "Şirket genelinde"),
                    ("UNIT", "Atandığı departman ve alt departmanları"),
                    ("UNIT_DIRECT", "Yalnızca atandığı departman"),
                    ("ASSIGNED", "Kendisine açık kayıtlar"),
                    ("SELF", "Yalnızca kendi hesabı ve çalışan kaydı"),
                ],
                default="TENANT",
                help_text=(
                    "OD5: rolün izinlerinin ulaşabileceği azami kapsam. İzin ÜRETMEZ; "
                    "izin ∧ kapsam birlikte değerlendirilir. UNIT/UNIT_DIRECT tiplerinde "
                    "atama (user_roles.scope_org_unit_id) zorunludur ve çözülemeyen "
                    "kapsam yetki vermez (fail-closed)."
                ),
                max_length=20,
                verbose_name="azami organizasyon kapsamı",
            ),
        ),
    ]
