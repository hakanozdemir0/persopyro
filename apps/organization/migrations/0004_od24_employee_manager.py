# M13 (OD24/OD21 kapalı) — `hr.employee_manager` (raporlama ilişkisi).
#
# **RBAC DEĞİLDİR** (OD5 §5.5): bu satırlar **yetki üretmez**. Yetki `user_roles`
# (+ `scope_org_unit_id`) üzerinden gelir; `employee_manager` yalnızca kişi bazlı
# raporlama/onay yönlendirmesidir. `departments.manager_employee_id` ise "birim
# sorumlusu"dur — üçü ayrı anlam taşır ve birbirini üretmez.
#
# Veritabanı garantileri:
#   * `CHECK (employee_id <> manager_employee_id)` → çalışan kendi yöneticisi olamaz.
#   * `CHECK (effective_to IS NULL OR effective_to >= effective_from)` → tarih aralığı.
#   * **Tek aktif birincil yönetici:** kısmi UNIQUE `(employee_id) WHERE
#     relation_type='primary' AND effective_to IS NULL AND deleted_at IS NULL`.
#   * Aynı çift için aynı anda iki açık ilişki olamaz (tür fark etmez) — kısmi UNIQUE
#     `(employee_id, manager_employee_id) WHERE effective_to IS NULL AND deleted_at IS NULL`.
#   * Kiracı eşitliği: `employee_id`, `manager_employee_id`, `created_by_id`
#     (`0007_org_same_tenant_guards`) + **silinmiş çalışanla yeni aktif ilişki
#     kurulamaz** (`0008_employee_manager_active_guard`).
#
# `secondary`/`delegate` ilişkiler açıktır (gelecekteki onay/delege akışları) ama
# **izin üretmez**.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0003_od24_employees"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeManager",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "relation_type",
                    models.CharField(
                        choices=[
                            ("primary", "Birincil"),
                            ("secondary", "İkincil"),
                            ("delegate", "Yetki devri (delege)"),
                        ],
                        default="primary",
                        max_length=20,
                        verbose_name="ilişki türü",
                    ),
                ),
                ("effective_from", models.DateField(verbose_name="başlangıç")),
                ("effective_to", models.DateField(blank=True, null=True, verbose_name="bitiş")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_manager_relations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="kaydeden",
                    ),
                ),
                (
                    "employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="manager_relations",
                        to="organization.employee",
                        verbose_name="çalışan",
                    ),
                ),
                (
                    "manager_employee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="subordinate_relations",
                        to="organization.employee",
                        verbose_name="yönetici çalışan",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employee_manager_relations",
                        to="tenancy.tenant",
                        verbose_name="kiracı",
                    ),
                ),
            ],
            options={
                "verbose_name": "yönetici ilişkisi",
                "verbose_name_plural": "yönetici ilişkileri",
                "db_table": "employee_manager",
            },
        ),
        migrations.AddIndex(
            model_name="employeemanager",
            index=models.Index(
                fields=["tenant", "manager_employee"], name="emp_manager_tenant_mgr_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="employeemanager",
            index=models.Index(fields=["tenant", "employee"], name="emp_manager_tenant_emp_idx"),
        ),
        migrations.AddConstraint(
            model_name="employeemanager",
            constraint=models.CheckConstraint(
                condition=models.Q(("employee_id", models.F("manager_employee_id")), _negated=True),
                name="employee_manager_not_self",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeemanager",
            constraint=models.CheckConstraint(
                condition=models.Q(("effective_to__isnull", True))
                | models.Q(("effective_to__gte", models.F("effective_from"))),
                name="employee_manager_date_range_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeemanager",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("effective_to__isnull", True),
                    ("relation_type", "primary"),
                ),
                fields=("employee",),
                name="employee_manager_primary_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeemanager",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("effective_to__isnull", True)),
                fields=("employee", "manager_employee"),
                name="employee_manager_pair_uniq",
            ),
        ),
    ]
