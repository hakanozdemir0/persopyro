from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overtime", "0005_overtimerule"),
    ]

    operations = [
        migrations.AddField(
            model_name="overtimerequest",
            name="compensation_type_snapshot",
            field=models.CharField(
                blank=True,
                choices=[("pay", "Ücret"), ("time_off", "İzin")],
                default="",
                max_length=20,
                verbose_name="karşılık türü snapshot",
            ),
        ),
        migrations.AddField(
            model_name="overtimerequest",
            name="multiplier_snapshot",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=6,
                null=True,
                verbose_name="katsayı snapshot",
            ),
        ),
    ]
