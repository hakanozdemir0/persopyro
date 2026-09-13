# P1-A — yeni kiracı tablolarında **RLS + FORCE RLS**.
#
# Desen mevcut standartla birebir aynıdır (`apps/organization/migrations/0006_rls_organization.py`):
#
#   * `ENABLE` → politikalar uygulanır
#   * `FORCE`  → **tablo sahibi rol bile** politikaları atlayamaz (kritik)
#   * politika: `tenant_isolation USING/WITH CHECK (tenant_id = hr.current_tenant_id())`
#
# Kiracı bağlamı (`app.tenant_id`) yoksa `hr.current_tenant_id()` NULL döner → hiçbir satır
# görünmez/eklenemez (fail-closed). `persopyro_platform` rolü bu tablolara **erişemez**
# (ayrı şema/rol; `scripts/02_grant_app_privileges.sql` ile de daraltılır).
#
# Kapsam: P1-A ile gelen 4 tablo (OD58/OD60/OD38). Sonraki paketler kendi RLS
# migration'larını ekler; kapsamı `tests/test_tenant_isolation.py` yapısal olarak denetler.

from django.db import migrations

RLS_TABLES: dict[str, str] = {
    "workplaces": "tenant_id",
    "employee_employment_terms": "tenant_id",
    "employee_assignments": "tenant_id",
    "form_field_definitions": "tenant_id",
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
        ("organization", "0015_p1a_backfill"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_enable_sql(t, c) for t, c in RLS_TABLES.items()),
            reverse_sql="".join(_disable_sql(t) for t in RLS_TABLES),
        ),
    ]
