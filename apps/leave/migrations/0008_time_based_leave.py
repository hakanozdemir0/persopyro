from django.db import migrations, models
from django.db.models import Q


def seed_time_off(apps, schema_editor):
    LeaveType = apps.get_model("leave", "LeaveType")
    Tenant = apps.get_model("tenancy", "Tenant")
    for tenant in Tenant.objects.all().iterator():
        LeaveType.objects.get_or_create(
            tenant_id=tenant.pk, code="time_off", deleted_at__isnull=True,
            defaults={
                "name": "Serbest Zaman", "category": "other", "is_paid": True,
                "uses_balance": True, "requires_approval": True,
                "requires_document": False, "is_system": True, "status": "active",
                "is_time_based": True, "workday_minutes": 480,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("leave", "0007_overtime_time_off_ledger_link")]
    operations = [
        migrations.AddField(
            model_name="leavetype", name="is_time_based",
            field=models.BooleanField(default=False, verbose_name="saat tabanlı"),
        ),
        migrations.AddField(
            model_name="leavetype", name="workday_minutes",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="1 gün dakika", help_text="Saat tabanlı izinlerde bir tam günün karşılığı."),
        ),
        migrations.AddConstraint(
            model_name="leavetype",
            constraint=models.CheckConstraint(
                condition=Q(is_time_based=False) | Q(workday_minutes__gt=0),
                name="leave_types_time_based_workday_positive",
            ),
        ),
        migrations.RunPython(seed_time_off, migrations.RunPython.noop),
    ]
