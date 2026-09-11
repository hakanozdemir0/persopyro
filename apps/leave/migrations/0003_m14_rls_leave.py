# M14 — izin tablolarında **RLS + FORCE RLS** (mevcut `tenant_isolation` standardı).
#
# Yedi yeni kiracı tablosu korunur: `leave_types`, `leave_policies`,
# `leave_policy_rules`, `leave_balances`, `leave_balance_entries`, `leave_requests`,
# `approval_steps`. Politika gövdesi mevcut tablolarla birebir aynıdır
# (`tenant_id = hr.current_tenant_id()`); `BYPASSRLS` verilmez, `persopyro_platform`
# bu tablolarda **hiçbir** yetki almaz (ayrı güvenlik sınırı).

from django.db import migrations

LEAVE_TABLES = (
    "leave_types",
    "leave_policies",
    "leave_policy_rules",
    "leave_balances",
    "leave_balance_entries",
    "leave_requests",
    "approval_steps",
)


def _rls_sql() -> str:
    parcalar = []
    for tablo in LEAVE_TABLES:
        parcalar.append(f"ALTER TABLE hr.{tablo} ENABLE ROW LEVEL SECURITY;")
        parcalar.append(f"ALTER TABLE hr.{tablo} FORCE ROW LEVEL SECURITY;")
        parcalar.append(
            f"DROP POLICY IF EXISTS tenant_isolation ON hr.{tablo};"
            f"CREATE POLICY tenant_isolation ON hr.{tablo} "
            "FOR ALL USING (tenant_id = hr.current_tenant_id()) "
            "WITH CHECK (tenant_id = hr.current_tenant_id());"
        )
    return "\n".join(parcalar)


def _rls_geri() -> str:
    parcalar = []
    for tablo in LEAVE_TABLES:
        parcalar.append(f"DROP POLICY IF EXISTS tenant_isolation ON hr.{tablo};")
        parcalar.append(f"ALTER TABLE hr.{tablo} NO FORCE ROW LEVEL SECURITY;")
        parcalar.append(f"ALTER TABLE hr.{tablo} DISABLE ROW LEVEL SECURITY;")
    return "\n".join(parcalar)


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0002_m14_leave_guards"),
    ]

    operations = [
        migrations.RunSQL(sql=_rls_sql(), reverse_sql=_rls_geri()),
    ]
