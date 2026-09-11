# M10–M13 — kiracılar arası referans (cross-tenant reference) guard'ları.
#
# NEDEN: RLS yalnızca "satırın kendi `tenant_id`'si benim bağlamımla aynı mı?"
# sorusunu yanıtlar; **yabancı anahtar kontrolü RLS'ten etkilenmez**. Bu yüzden her
# kiracı-içi FK için, hedef satırın kiracısıyla satırın kiracısının aynı olduğunu
# doğrulayan `hr.ensure_same_tenant_ref()` tetikleyicisi gerekir (mevcut desen:
# `apps/tenancy/migrations/0003_same_tenant_guard.py`).
#
# Tetikleyici **SECURITY INVOKER**'dır: hedef satırı RLS görünürlüğü üzerinden
# okur; hedef görünmüyorsa (başka kiracı) `ref_tenant` NULL olur ve karşılaştırma
# **hata verir** → fail-closed.
#
# Kapsanan referanslar:
#   * `departments.parent_id`            → `departments` (kendi üst birimi)
#   * `departments.manager_employee_id`  → `employees`
#   * `employees.user_id`                → `users`
#   * `employees.department_id`          → `departments`
#   * `employees.position_id`            → `positions`
#   * `employee_personal_data.employee_id` → `employees`
#   * `employee_manager.employee_id`     → `employees`
#   * `employee_manager.manager_employee_id` → `employees`
#   * `employee_manager.created_by_id`   → `users`
#   * `user_roles.scope_org_unit_id`     → `departments` (M9 tamamlama)
#
# DİKKAT: `apps/tenancy/migrations/0003_same_tenant_guard.py` zaten UYGULANMIŞTI;
# listesi burada genişletilir (uygulanmış migration değiştirilmez).

from django.db import migrations

GUARDS: list[tuple[str, str, str]] = [
    ("departments", "parent_id", "departments"),
    ("departments", "manager_employee_id", "employees"),
    ("employees", "user_id", "users"),
    ("employees", "department_id", "departments"),
    ("employees", "position_id", "positions"),
    ("employee_personal_data", "employee_id", "employees"),
    ("employee_manager", "employee_id", "employees"),
    ("employee_manager", "manager_employee_id", "employees"),
    ("employee_manager", "created_by_id", "users"),
    ("user_roles", "scope_org_unit_id", "departments"),
]


def _trigger_name(table: str, column: str) -> str:
    return f"trg_{table}_{column}_same_tenant"


def _create_sql(table: str, column: str, ref_table: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS {_trigger_name(table, column)} ON hr.{table};
CREATE TRIGGER {_trigger_name(table, column)}
    BEFORE INSERT OR UPDATE ON hr.{table}
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('{column}', '{ref_table}');
"""


def _drop_sql(table: str, column: str) -> str:
    return f"DROP TRIGGER IF EXISTS {_trigger_name(table, column)} ON hr.{table};"


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0006_rls_organization"),
        ("rbac", "0008_od5_scope_org_unit_fk"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_create_sql(*g) for g in GUARDS),
            reverse_sql="".join(_drop_sql(t, c) for t, c, _ in GUARDS),
        ),
    ]
