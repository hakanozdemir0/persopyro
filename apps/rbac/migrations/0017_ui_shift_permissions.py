from django.db import migrations


UI_PERMISSIONS = (
    ("ui.shift.view", "Vardiya başlığını görme"),
    ("ui.shift.definitions", "Vardiya tanımları alt bölümünü görme"),
    ("ui.shift.assignments", "Vardiya planı alt bölümünü görme"),
)


def forwards(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cur:
        for code, name in UI_PERMISSIONS:
            cur.execute(
                "INSERT INTO hr.permissions (id, created_at, updated_at, code, name, module, description) "
                "VALUES (gen_random_uuid(), now(), now(), %s, %s, 'ui', '') ON CONFLICT (code) DO NOTHING",
                [code, name],
            )
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute("SELECT id FROM hr.roles WHERE deleted_at IS NULL")
            for role_id, in cur.fetchall():
                cur.execute(
                    "SELECT p.code FROM hr.role_permissions rp JOIN hr.permissions p ON p.id=rp.permission_id WHERE rp.role_id=%s",
                    [str(role_id)],
                )
                mevcut = {row[0] for row in cur.fetchall()}
                gerekliler = {
                    "ui.shift.view": {"shift.read", "shift.assign", "shift.schedule.manage"},
                    "ui.shift.definitions": {"shift.schedule.manage"},
                    "ui.shift.assignments": {"shift.read", "shift.assign"},
                }
                for ui_code, needed in gerekliler.items():
                    if not mevcut.intersection(needed):
                        continue
                    cur.execute(
                        "INSERT INTO hr.role_permissions (id, created_at, updated_at, tenant_id, role_id, permission_id) "
                        "SELECT gen_random_uuid(), now(), now(), %s, %s, id FROM hr.permissions WHERE code=%s "
                        "ON CONFLICT (role_id, permission_id) DO NOTHING",
                        [str(tenant_id), str(role_id), ui_code],
                    )
        cur.execute("SELECT set_config('app.tenant_id', '', true)")


def backwards(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cur:
        cur.execute("DELETE FROM hr.role_permissions WHERE permission_id IN (SELECT id FROM hr.permissions WHERE code LIKE 'ui.shift.%')")
        cur.execute("DELETE FROM hr.permissions WHERE code LIKE 'ui.shift.%'")


class Migration(migrations.Migration):
    dependencies = [("rbac", "0016_role_hierarchy_level")]
    operations = [migrations.RunPython(forwards, backwards)]
