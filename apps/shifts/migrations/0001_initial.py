import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("organization", "0019_p1a_reason_not_null"),
        ("tenancy", "0009_od25_public_id_columns"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Shift",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("code", models.CharField(max_length=50, verbose_name="kod")),
                ("name", models.CharField(max_length=120, verbose_name="ad")),
                ("start_time", models.TimeField(verbose_name="başlangıç")),
                ("end_time", models.TimeField(verbose_name="bitiş")),
                ("break_minutes", models.PositiveIntegerField(default=0, verbose_name="mola (dakika)")),
                ("crosses_midnight", models.BooleanField(default=False, verbose_name="gece yarısını aşar")),
                ("work_model", models.CharField(choices=[("office", "Ofis"), ("hybrid", "Hibrit"), ("remote", "Uzaktan"), ("flex", "Esnek")], default="office", max_length=20, verbose_name="çalışma modeli")),
                ("description", models.TextField(blank=True, verbose_name="açıklama")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shifts", to="tenancy.tenant", verbose_name="kiracı")),
            ],
            options={"db_table": "shifts", "ordering": ["name", "start_time"], "verbose_name": "vardiya", "verbose_name_plural": "vardiyalar"},
        ),
        migrations.CreateModel(
            name="ShiftAssignment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("work_date", models.DateField(verbose_name="çalışma günü")),
                ("assignment_source", models.CharField(default="daily", max_length=20, verbose_name="atama kaynağı")),
                ("note", models.TextField(blank=True, verbose_name="not")),
                ("assigned_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_shifts", to=settings.AUTH_USER_MODEL, verbose_name="atanan")),
                ("employee", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shift_assignments", to="organization.employee", verbose_name="çalışan")),
                ("shift", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="shifts.shift", verbose_name="vardiya")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shift_assignments", to="tenancy.tenant", verbose_name="kiracı")),
            ],
            options={"db_table": "shift_assignments", "ordering": ["work_date", "employee_id"], "verbose_name": "vardiya ataması", "verbose_name_plural": "vardiya atamaları"},
        ),
        migrations.AddIndex(model_name="shift", index=models.Index(fields=["tenant", "name"], name="shifts_tenant_name_idx")),
        migrations.AddIndex(model_name="shiftassignment", index=models.Index(fields=["tenant", "employee", "work_date"], name="shift_assignments_emp_day_idx")),
        migrations.AddIndex(model_name="shiftassignment", index=models.Index(fields=["tenant", "work_date"], name="shift_assignments_day_idx")),
        migrations.AddConstraint(model_name="shift", constraint=models.UniqueConstraint(condition=Q(deleted_at__isnull=True), fields=("tenant", "code"), name="shifts_tenant_code_active_uniq")),
        migrations.AddConstraint(model_name="shift", constraint=models.CheckConstraint(condition=Q(break_minutes__gte=0), name="shifts_break_minutes_nonnegative")),
        migrations.AddConstraint(model_name="shiftassignment", constraint=models.UniqueConstraint(condition=Q(deleted_at__isnull=True), fields=("employee", "work_date"), name="shift_assignments_employee_day_active_uniq")),
    ]
