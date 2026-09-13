# P5a V1 — fazla mesai tablosu için **kiracılar arası referans guard'ları**.
#
# NEDEN: RLS yalnızca satırın kendi `tenant_id`'sini denetler; yabancı anahtar hedefi
# başka kiracıda olabilir. `hr.ensure_same_tenant_ref(sütun, hedef_tablo)` tetikleyicisi
# hedef satırın kiracısı ile satırın kiracısının aynı olmasını zorlar; hedef görünmüyorsa
# karşılaştırma **fail-closed** hata verir (mevcut desen: `organization/0017`,
# `leave/0004`).
#
# Kapsam:
#   * overtime_requests → employees (çalışan), users (kaydı açan, karar veren)
#   * `tenant_id` başka kiracıya işaret edemez (tenants FK'si + RLS politikası yeterlidir)

from django.db import migrations

GUARDS: list[tuple[str, str, str]] = [
    ("overtime_requests", "employee_id", "employees"),
    ("overtime_requests", "created_by_id", "users"),
    ("overtime_requests", "decided_by_id", "users"),
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
        ("overtime", "0003_p5a_overtime_rls"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_create_sql(*g) for g in GUARDS),
            reverse_sql="".join(_drop_sql(t, c) for t, c, _ in GUARDS),
        ),
    ]
