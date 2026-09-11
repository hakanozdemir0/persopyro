# M14 — izin tablolarında **kiracılar arası referans guard'ları**.
#
# RLS "satırın kendi `tenant_id`'si bağlamımla aynı mı?" sorusunu yanıtlar; yabancı
# anahtar kontrolü RLS'i **atlar**. Bu yüzden her kiracılı FK için mevcut
# `hr.ensure_same_tenant_ref(sütun, hedef_tablo)` tetikleyicisi bağlanır.
#
# Kapsam (M14):
#   * `leave_policies.leave_type_id` → `leave_types`        (kendi tablosu)
#   * `leave_policy_rules.policy_id` → `leave_policies`
#   * `leave_balances.employee_id` → `employees`, `.leave_type_id` → `leave_types`
#   * `leave_balance_entries`: `balance_id` → `leave_balances`, `employee_id` →
#     `employees`, `leave_type_id` → `leave_types`, `leave_request_id` →
#     `leave_requests`, `actor_id` → `users`
#   * `leave_requests.employee_id` → `employees`, `.leave_type_id` → `leave_types`,
#     `.decided_by_id` → `users`
#   * `approval_steps.approver_user_id` → `users`, `.delegated_from_id` → `users`
#
# `approval_steps.subject_id` **FK değildir** (polimorfik hedef) → guard bağlanmaz;
# hedefin kiracıya ait olduğu servis katmanında doğrulanır.

from django.db import migrations

GUARDLAR: tuple[tuple[str, str, str], ...] = (
    ("leave_policies", "leave_type_id", "leave_types"),
    ("leave_policy_rules", "policy_id", "leave_policies"),
    ("leave_balances", "employee_id", "employees"),
    ("leave_balances", "leave_type_id", "leave_types"),
    ("leave_balance_entries", "balance_id", "leave_balances"),
    ("leave_balance_entries", "employee_id", "employees"),
    ("leave_balance_entries", "leave_type_id", "leave_types"),
    ("leave_balance_entries", "leave_request_id", "leave_requests"),
    ("leave_balance_entries", "actor_id", "users"),
    ("leave_requests", "employee_id", "employees"),
    ("leave_requests", "leave_type_id", "leave_types"),
    ("leave_requests", "decided_by_id", "users"),
    ("approval_steps", "approver_user_id", "users"),
    ("approval_steps", "delegated_from_id", "users"),
)


def _guard_sql() -> str:
    parcalar = []
    for tablo, sutun, hedef in GUARDLAR:
        tetik = f"trg_{tablo}_{sutun}_same_tenant"
        parcalar.append(f"DROP TRIGGER IF EXISTS {tetik} ON hr.{tablo};")
        parcalar.append(
            f"CREATE TRIGGER {tetik} BEFORE INSERT OR UPDATE ON hr.{tablo} "
            f"FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('{sutun}', '{hedef}');"
        )
    return "\n".join(parcalar)


def _guard_geri() -> str:
    parcalar = []
    for tablo, sutun, _hedef in GUARDLAR:
        parcalar.append(f"DROP TRIGGER IF EXISTS trg_{tablo}_{sutun}_same_tenant ON hr.{tablo};")
    return "\n".join(parcalar)


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0003_m14_rls_leave"),
        ("tenancy", "0003_same_tenant_guard"),
    ]

    operations = [
        migrations.RunSQL(sql=_guard_sql(), reverse_sql=_guard_geri()),
    ]
