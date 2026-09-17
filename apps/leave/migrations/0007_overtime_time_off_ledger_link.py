"""P5a — link TIME_OFF ledger entries to their overtime request."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0006_m14_leave_decision_snapshot"),
        ("overtime", "0006_overtime_rule_snapshot"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="leavebalanceentry",
            name="leave_balance_entries_kind_valid",
        ),
        migrations.AddField(
            model_name="leavebalanceentry",
            name="overtime_request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="time_off_balance_entries",
                to="overtime.overtimerequest",
                verbose_name="fazla mesai talebi",
            ),
        ),
        migrations.AlterField(
            model_name="leavebalanceentry",
            name="entry_kind",
            field=models.CharField(
                choices=[
                    ("accrual", "Hakediş"),
                    ("carryover", "Devreden"),
                    ("adjustment", "Manuel düzeltme"),
                    ("reservation", "Rezervasyon (onay bekliyor)"),
                    ("reservation_release", "Rezervasyon iptali"),
                    ("usage", "Kullanım (onaylandı)"),
                    ("refund", "İade (iptal)"),
                    ("time_off_credit", "Fazla mesai serbest zaman kredisi"),
                ],
                max_length=20,
                verbose_name="olay türü",
            ),
        ),
        migrations.AddConstraint(
            model_name="leavebalanceentry",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    entry_kind__in=[
                        "accrual",
                        "carryover",
                        "adjustment",
                        "reservation",
                        "reservation_release",
                        "usage",
                        "refund",
                        "time_off_credit",
                    ]
                ),
                name="leave_balance_entries_kind_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="leavebalanceentry",
            constraint=models.UniqueConstraint(
                condition=models.Q(overtime_request__isnull=False),
                fields=("overtime_request", "entry_kind"),
                name="leave_balance_entries_overtime_uniq",
            ),
        ),
    ]
