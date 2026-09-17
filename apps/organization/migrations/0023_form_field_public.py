from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("organization", "0022_form_field_layout_width")]

    operations = [
        migrations.AddField(
            model_name="formfielddefinition",
            name="is_public",
            field=models.BooleanField(
                default=False,
                help_text="İşaretlenirse departmanlar dizininde çalışanlara gösterilebilir.",
                verbose_name="departmanlarda açık bilgi",
            ),
        ),
    ]
