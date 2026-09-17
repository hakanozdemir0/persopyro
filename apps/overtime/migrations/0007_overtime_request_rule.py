"""P5b — fazla mesai talebinde seçilen kuralı sakla."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overtime", "0006_overtime_rule_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="overtimerequest",
            name="overtime_rule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="overtime_requests",
                to="overtime.overtimerule",
                verbose_name="fazla mesai kuralı",
            ),
        ),
    ]
