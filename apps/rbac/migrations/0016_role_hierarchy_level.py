from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("rbac", "0015_ui_baslik_izinleri")]

    operations = [
        migrations.AddField(
            model_name="role",
            name="hierarchy_level",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Kiracı içi sıralama: 1 en üst, 30 en alt. 99/100 platform rollerine aittir.",
                null=True,
                verbose_name="yetki hiyerarşi seviyesi",
            ),
        ),
    ]
