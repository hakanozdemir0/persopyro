from django.db import migrations


TABLES = {"shifts": "tenant_id", "shift_assignments": "tenant_id"}


def _enable(table, column):
    return f"""
ALTER TABLE hr.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
CREATE POLICY tenant_isolation ON hr.{table}
    USING ({column} = hr.current_tenant_id())
    WITH CHECK ({column} = hr.current_tenant_id());
"""


def _disable(table):
    return f"""
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
ALTER TABLE hr.{table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} DISABLE ROW LEVEL SECURITY;
"""


def _same_tenant(table, column, ref_table):
    trigger = f"trg_{table}_{column}_same_tenant"
    return f"""
DROP TRIGGER IF EXISTS {trigger} ON hr.{table};
CREATE TRIGGER {trigger}
    BEFORE INSERT OR UPDATE ON hr.{table}
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('{column}', '{ref_table}');
"""


class Migration(migrations.Migration):
    dependencies = [("shifts", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="".join(_enable(t, c) for t, c in TABLES.items()),
            reverse_sql="".join(_disable(t) for t in TABLES),
        ),
        migrations.RunSQL(
            sql=_same_tenant("shift_assignments", "employee_id", "employees")
            + _same_tenant("shift_assignments", "shift_id", "shifts")
            + _same_tenant("shift_assignments", "assigned_by_id", "users"),
            reverse_sql="".join(
                f"DROP TRIGGER IF EXISTS trg_shift_assignments_{c}_same_tenant ON hr.shift_assignments;"
                for c in ("employee_id", "shift_id", "assigned_by_id")
            ),
        ),
    ]
