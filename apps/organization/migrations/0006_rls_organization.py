# M10–M13 — organizasyon/çalışan tablolarında **RLS + FORCE RLS**.
#
# Django'nun model/migration katmanı Row Level Security'yi ifade edemez; bu yüzden
# ham SQL ile uygulanır (mevcut desen: `apps/tenancy/migrations/0002_enable_rls.py`).
#
#   * `ENABLE` → politikalar uygulanır
#   * `FORCE`  → **tablo sahibi rol bile** politikaları atlayamaz (kritik)
#   * Politika adı ve biçimi mevcut standartla birebir aynıdır:
#     `tenant_isolation USING/WITH CHECK (tenant_id = hr.current_tenant_id())`
#
# Kiracı bağlamı yoksa `hr.current_tenant_id()` NULL döner → hiçbir satır
# görünmez/eklenemez (fail-closed).
#
# Yeni kiracı tablosu eklendiğinde bu liste AYRI bir migration ile genişletilir;
# kapsamı `tests/test_tenant_isolation.py` yapısal olarak denetler.

from django.db import migrations

RLS_TABLES: dict[str, str] = {
    "departments": "tenant_id",
    "positions": "tenant_id",
    "employees": "tenant_id",
    "employee_personal_data": "tenant_id",
    "employee_manager": "tenant_id",
}


def _enable_sql(table: str, column: str) -> str:
    return f"""
ALTER TABLE hr.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
CREATE POLICY tenant_isolation ON hr.{table}
    USING ({column} = hr.current_tenant_id())
    WITH CHECK ({column} = hr.current_tenant_id());
"""


def _disable_sql(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
ALTER TABLE hr.{table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0005_od24_department_manager_employee"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_enable_sql(t, c) for t, c in RLS_TABLES.items()),
            reverse_sql="".join(_disable_sql(t) for t in RLS_TABLES),
        ),
    ]
