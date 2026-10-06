"""Vardiya tanımları ve gün bazlı çalışan atamaları."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import TenantScopedModel
from apps.organization.models import Employee


class WorkModel(models.TextChoices):
    OFFICE = "office", "Ofis"
    HYBRID = "hybrid", "Hibrit"
    REMOTE = "remote", "Uzaktan"
    FLEX = "flex", "Esnek"


class Shift(TenantScopedModel):
    """Tekrar tekrar kullanılabilen vardiya şablonu."""

    tenant = models.ForeignKey(
        "tenancy.Tenant", on_delete=models.CASCADE, related_name="shifts", verbose_name="kiracı"
    )
    code = models.CharField("kod", max_length=50)
    name = models.CharField("ad", max_length=120)
    start_time = models.TimeField("başlangıç")
    end_time = models.TimeField("bitiş")
    break_minutes = models.PositiveIntegerField("mola (dakika)", default=0)
    crosses_midnight = models.BooleanField("gece yarısını aşar", default=False)
    work_model = models.CharField(
        "çalışma modeli", max_length=20, choices=WorkModel.choices, default=WorkModel.OFFICE
    )
    description = models.TextField("açıklama", blank=True)

    class Meta:
        db_table = "shifts"
        verbose_name = "vardiya"
        verbose_name_plural = "vardiyalar"
        ordering = ["name", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                condition=Q(deleted_at__isnull=True),
                name="shifts_tenant_code_active_uniq",
            ),
            models.CheckConstraint(
                condition=Q(break_minutes__gte=0), name="shifts_break_minutes_nonnegative"
            ),
        ]
        indexes = [models.Index(fields=["tenant", "name"], name="shifts_tenant_name_idx")]


class ShiftAssignment(TenantScopedModel):
    """Bir çalışanın belirli bir gündeki vardiyası."""

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="shift_assignments",
        verbose_name="kiracı",
    )
    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="shift_assignments", verbose_name="çalışan"
    )
    shift = models.ForeignKey(
        Shift, on_delete=models.PROTECT, related_name="assignments", verbose_name="vardiya"
    )
    work_date = models.DateField("çalışma günü")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_shifts",
        verbose_name="atanan",
    )
    assignment_source = models.CharField("atama kaynağı", max_length=20, default="daily")
    note = models.TextField("not", blank=True)
    late_minutes = models.PositiveSmallIntegerField("geç kalınan dakika", default=0)
    late_reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reported_late_shift_assignments",
        verbose_name="geç kalışı bildiren",
    )
    late_reported_at = models.DateTimeField("geç kalış bildirim zamanı", null=True, blank=True)

    class Meta:
        db_table = "shift_assignments"
        verbose_name = "vardiya ataması"
        verbose_name_plural = "vardiya atamaları"
        ordering = ["work_date", "employee_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "work_date"],
                condition=Q(deleted_at__isnull=True),
                name="shift_assignments_employee_day_active_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "employee", "work_date"], name="shift_assignments_emp_day_idx"
            ),
            models.Index(fields=["tenant", "work_date"], name="shift_assignments_day_idx"),
        ]
