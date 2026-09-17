from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("organization", "0021_custom_employee_form_fields")]

    operations = [
        migrations.AddField(
            model_name="formfielddefinition",
            name="layout_width",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="form genişliği"),
        ),
        migrations.AddConstraint(
            model_name="formfielddefinition",
            constraint=models.CheckConstraint(
                condition=models.Q(layout_width__gte=1) & models.Q(layout_width__lte=5),
                name="ffd_layout_width_valid",
            ),
        ),
    ]
