from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("overtime", "0007_overtime_request_rule")]
    operations = [
        migrations.RemoveConstraint(
            model_name="overtimerequest", name="overtime_requests_slot_uniq"
        ),
        migrations.AddConstraint(
            model_name="overtimerequest",
            constraint=models.UniqueConstraint(
                fields=("employee", "work_date", "start_time", "end_time"),
                condition=Q(deleted_at__isnull=True)
                & Q(status__in=["pending", "approved"]),
                name="overtime_requests_slot_uniq",
            ),
        ),
    ]
