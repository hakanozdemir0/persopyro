# P1-A — yeni tablolar için **kiracılar arası referans guard'ları**.
#
# NEDEN: RLS yalnızca satırın kendi `tenant_id`'sini denetler; yabancı anahtar hedefi
# başka kiracıda olabilir (mevcut desen: `apps/organization/migrations/0007_org_same_tenant_guards.py`).
# `hr.ensure_same_tenant_ref(sütun, hedef_tablo)` tetikleyicisi hedef satırın kiracısı ile
# satırın kiracısının aynı olmasını zorlar; hedef görünmüyorsa `ref_tenant` NULL olur ve
# karşılaştırma **fail-closed** hata verir.
#
# Kapsam (P1-A tabloları):
#   * employee_employment_terms → employee, workplace, created_by
#   * employee_assignments      → employee, department, position, created_by
#   * form_field_definitions    → created_by
#   * workplaces                → (yalnız `tenant_id`'ye bağlıdır; ek guard gerekmez)

from django.db import migrations

GUARDS: list[tuple[str, str, str]] = [
    ("employee_employment_terms", "employee_id", "employees"),
    ("employee_employment_terms", "workplace_id", "workplaces"),
    ("employee_employment_terms", "created_by_id", "users"),
    ("employee_assignments", "employee_id", "employees"),
    ("employee_assignments", "department_id", "departments"),
    ("employee_assignments", "position_id", "positions"),
    ("employee_assignments", "created_by_id", "users"),
    ("form_field_definitions", "created_by_id", "users"),
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
        ("organization", "0016_rls_p1a"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_create_sql(*g) for g in GUARDS),
            reverse_sql="".join(_drop_sql(t, c) for t, c, _ in GUARDS),
        ),
    ]
