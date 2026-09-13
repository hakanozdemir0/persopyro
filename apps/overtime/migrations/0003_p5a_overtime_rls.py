# P5a V1 — fazla mesai tablosunda **RLS + FORCE RLS** (mevcut `tenant_isolation` standardı).
#
# Desen mevcut standartla birebir aynıdır (`leave/0003_m14_rls_leave`,
# `organization/0016_rls_p1a`):
#
#   * `ENABLE` → politikalar uygulanır
#   * `FORCE`  → **tablo sahibi rol bile** politikaları atlayamaz (kritik)
#   * politika: `USING/WITH CHECK (tenant_id = hr.current_tenant_id())`
#
# Kiracı bağlamı (`app.tenant_id`) yoksa `hr.current_tenant_id()` NULL döner → hiçbir
# satır görünmez/eklenemez (**fail-closed**). `persopyro_platform` rolü bu tabloya
# erişemez (allowlist; `scripts/02_grant_app_privileges.sql` + `scripts/03_platform_roles.sql`).

from django.db import migrations

RLS_TABLES: dict[str, str] = {"overtime_requests": "tenant_id"}


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
        ("overtime", "0002_p5a_overtime_guards"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_enable_sql(t, c) for t, c in RLS_TABLES.items()),
            reverse_sql="".join(_disable_sql(t) for t in RLS_TABLES),
        ),
    ]
