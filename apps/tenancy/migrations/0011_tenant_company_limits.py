# Şirket hizmet aralığı ve çalışan kayıt limitleri.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0010_tenant_valid_until")]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="valid_from",
            field=models.DateField(blank=True, null=True, verbose_name="kullanım başlangıcı"),
        ),
        migrations.AddField(
            model_name="tenant",
            name="max_active_employees",
            field=models.PositiveIntegerField(default=0, verbose_name="aktif çalışan limiti"),
        ),
        migrations.AddField(
            model_name="tenant",
            name="max_employee_records",
            field=models.PositiveIntegerField(default=0, verbose_name="çalışan kayıt limiti"),
        ),
        migrations.AddConstraint(
            model_name="tenant",
            constraint=models.CheckConstraint(
                condition=models.Q(max_active_employees__gte=0),
                name="tenants_max_active_emp_nonneg",
            ),
        ),
        migrations.AddConstraint(
            model_name="tenant",
            constraint=models.CheckConstraint(
                condition=models.Q(max_employee_records__gte=0),
                name="tenants_max_emp_records_nonneg",
            ),
        ),
    ]
