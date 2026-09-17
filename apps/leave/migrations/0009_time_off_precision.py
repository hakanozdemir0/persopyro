from django.db import migrations, models
from django.db.models import F
from django.db.models.expressions import CombinedExpression


def available_field():
    return models.GeneratedField(
        expression=CombinedExpression(
            CombinedExpression(
                CombinedExpression(
                    CombinedExpression(F("entitled_days"), "+", F("carried_forward_days")),
                    "+", F("adjustment_days"),
                ),
                "-", F("used_days"),
            ),
            "-", F("reserved_days"),
        ),
        output_field=models.DecimalField(max_digits=8, decimal_places=4),
        db_persist=True,
    )


class Migration(migrations.Migration):
    dependencies = [("leave", "0008_time_based_leave")]
    state_operations = [
        migrations.AlterField(model_name="leavebalance", name="entitled_days", field=models.DecimalField(decimal_places=4, default=0, max_digits=8, verbose_name="hakediş")),
        migrations.AlterField(model_name="leavebalance", name="carried_forward_days", field=models.DecimalField(decimal_places=4, default=0, max_digits=8, verbose_name="devreden")),
        migrations.AlterField(model_name="leavebalance", name="adjustment_days", field=models.DecimalField(decimal_places=4, default=0, max_digits=8, verbose_name="düzeltme")),
        migrations.AlterField(model_name="leavebalance", name="used_days", field=models.DecimalField(decimal_places=4, max_digits=8, default=0, verbose_name="kullanılan")),
        migrations.AlterField(model_name="leavebalance", name="reserved_days", field=models.DecimalField(decimal_places=4, max_digits=8, default=0, verbose_name="rezerve (onay bekleyen)")),
        migrations.AlterField(model_name="leavebalanceentry", name="delta_days", field=models.DecimalField(decimal_places=4, max_digits=8, verbose_name="değişim (gün)")),
        migrations.AlterField(model_name="leaverequest", name="days_requested", field=models.DecimalField(decimal_places=4, max_digits=8, verbose_name="istenen gün")),
        migrations.AlterField(model_name="leavebalance", name="available_days", field=available_field()),
    ]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(
                sql="""
                ALTER TABLE hr.leave_balances DROP COLUMN available_days;
                ALTER TABLE hr.leave_balances
                  ALTER COLUMN entitled_days TYPE numeric(8,4) USING entitled_days::numeric(8,4),
                  ALTER COLUMN carried_forward_days TYPE numeric(8,4) USING carried_forward_days::numeric(8,4),
                  ALTER COLUMN adjustment_days TYPE numeric(8,4) USING adjustment_days::numeric(8,4),
                  ALTER COLUMN used_days TYPE numeric(8,4) USING used_days::numeric(8,4),
                  ALTER COLUMN reserved_days TYPE numeric(8,4) USING reserved_days::numeric(8,4);
                ALTER TABLE hr.leave_balance_entries
                  ALTER COLUMN delta_days TYPE numeric(8,4) USING delta_days::numeric(8,4);
                ALTER TABLE hr.leave_requests
                  ALTER COLUMN days_requested TYPE numeric(8,4) USING days_requested::numeric(8,4);
                ALTER TABLE hr.leave_balances ADD COLUMN available_days numeric(8,4)
                  GENERATED ALWAYS AS (entitled_days + carried_forward_days + adjustment_days - used_days - reserved_days) STORED;
                """,
                reverse_sql=migrations.RunSQL.noop,
            )],
            state_operations=state_operations,
        )
    ]
