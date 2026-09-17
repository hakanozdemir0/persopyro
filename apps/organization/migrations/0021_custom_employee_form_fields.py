from django.db import migrations, models
from django.db.models import Q

from apps.organization.employee_form_catalog import FIELD_CODES


class Migration(migrations.Migration):
    dependencies = [("organization", "0020_employee_assignment_baseline_backfill")]

    operations = [
        migrations.AddField(
            model_name="employeepersonaldata",
            name="custom_fields",
            field=models.JSONField(blank=True, default=dict, verbose_name="özel form alanları"),
        ),
        migrations.AddField(
            model_name="formfielddefinition",
            name="field_type",
            field=models.CharField(default="text", max_length=20, verbose_name="alan tipi"),
        ),
        migrations.AddField(
            model_name="formfielddefinition",
            name="options",
            field=models.JSONField(blank=True, default=list, verbose_name="seçenekler"),
        ),
        migrations.RemoveConstraint(model_name="formfielddefinition", name="ffd_field_code_known"),
        migrations.AddConstraint(
            model_name="formfielddefinition",
            constraint=models.CheckConstraint(
                condition=Q(field_code__in=list(FIELD_CODES)) | Q(field_code__startswith="custom_"),
                name="ffd_field_code_known",
            ),
        ),
    ]
