"""UI başlık ve alt başlıklarının rol bazlı görünürlük izinleri.

Mevcut işlem izinleri korunur. Bu migration yalnızca mevcut rol izinlerinden
geriye dönük uyumlu `ui.*` izinlerini türetir; özel rollerin iş izinleri
değiştirilmez.
"""

from django.db import migrations


UI_IZINLERI = (
    ("ui.panel.view", "Panel başlığını görme", "ui"),
    ("ui.departments.view", "Departmanlar başlığını görme", "ui"),
    ("ui.employees.view", "Çalışanlar başlığını görme", "ui"),
    ("ui.employees.list", "Çalışan Ara / Liste alt başlığını görme", "ui"),
    ("ui.employees.new", "Yeni Çalışan alt başlığını görme", "ui"),
    ("ui.employees.form", "Kişisel Bilgi Formu alt başlığını görme", "ui"),
    ("ui.employees.exit", "İşten Çıkış / Çıkış Süreci alt başlığını görme", "ui"),
    ("ui.employees.suspended", "Askıya Alınanlar alt başlığını görme", "ui"),
    ("ui.leave.view", "İzin Talebi başlığını görme", "ui"),
    ("ui.leave.request", "İzin talebi alt bölümünü görme", "ui"),
    ("ui.leave.policy", "İzin politikası alt bölümünü görme", "ui"),
    ("ui.leave.types", "İzin türleri alt bölümünü görme", "ui"),
    ("ui.leave.entitlements", "İzin hakları alt bölümünü görme", "ui"),
    ("ui.overtime.view", "Fazla Mesai başlığını görme", "ui"),
    ("ui.overtime.requests", "Fazla mesai bildirimleri alt bölümünü görme", "ui"),
    ("ui.overtime.rules", "Fazla mesai kuralları alt bölümünü görme", "ui"),
    ("ui.approvals.view", "Onaylar başlığını görme", "ui"),
    ("ui.roles.view", "Yetki Yönetimi başlığını görme", "ui"),
)

UI_GEREKLILERI = {
    "ui.panel.view": {"employee.read", "leave.request.read", "leave.request.create", "overtime.request.read", "role.manage"},
    "ui.departments.view": {"org.department.read"},
    "ui.employees.view": {"employee.read"},
    "ui.employees.list": {"employee.read"},
    "ui.employees.new": {"employee.create"},
    "ui.employees.form": {"employee.form.manage"},
    "ui.employees.exit": {"employee.terminate", "employee.restore"},
    "ui.employees.suspended": {"employee.suspend", "employee.restore"},
    "ui.leave.view": {"leave.request.read", "leave.request.create"},
    "ui.leave.request": {"leave.request.read", "leave.request.create"},
    "ui.leave.policy": {"leave.type.manage"},
    "ui.leave.types": {"leave.type.manage"},
    "ui.leave.entitlements": {"leave.balance.read", "leave.balance.manage"},
    "ui.overtime.view": {"overtime.request.read", "overtime.request.create"},
    "ui.overtime.requests": {"overtime.request.read", "overtime.request.create"},
    "ui.overtime.rules": {"overtime.rule.read", "overtime.rule.manage"},
    "ui.approvals.view": {"leave.request.approve", "overtime.request.approve"},
    "ui.roles.view": {"role.read", "role.manage"},
}


def ekle(apps, schema_editor):  # noqa: ANN001
    with schema_editor.connection.cursor() as cur:
        for kod, ad, modul in UI_IZINLERI:
            cur.execute(
                "INSERT INTO hr.permissions "
                "(id, created_at, updated_at, code, name, module, description) "
                "VALUES (gen_random_uuid(), now(), now(), %s, %s, %s, '') "
                "ON CONFLICT (code) DO NOTHING",
                [kod, ad, modul],
            )
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute("SELECT id FROM hr.roles WHERE deleted_at IS NULL")
            for role_id, in cur.fetchall():
                cur.execute(
                    "SELECT p.code FROM hr.role_permissions rp "
                    "JOIN hr.permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = %s",
                    [str(role_id)],
                )
                mevcut = {satir[0] for satir in cur.fetchall()}
                for ui_kodu, gerekliler in UI_GEREKLILERI.items():
                    if not mevcut.intersection(gerekliler):
                        continue
                    cur.execute(
                        "INSERT INTO hr.role_permissions "
                        "(id, created_at, updated_at, tenant_id, role_id, permission_id) "
                        "SELECT gen_random_uuid(), now(), now(), %s, %s, p.id "
                        "FROM hr.permissions p WHERE p.code = %s "
                        "ON CONFLICT (role_id, permission_id) DO NOTHING",
                        [str(tenant_id), str(role_id), ui_kodu],
                    )
        cur.execute("SELECT set_config('app.tenant_id', '', true)")


def geri_al(apps, schema_editor):  # noqa: ANN001
    kodlar = [kod for kod, _ad, _modul in UI_IZINLERI]
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        for tenant_id, in cur.fetchall():
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute(
                "DELETE FROM hr.role_permissions rp USING hr.permissions p "
                "WHERE rp.permission_id = p.id AND p.code = ANY(%s)",
                [kodlar],
            )
        cur.execute("SELECT set_config('app.tenant_id', '', true)")
        cur.execute("DELETE FROM hr.permissions WHERE code = ANY(%s)", [kodlar])


class Migration(migrations.Migration):
    dependencies = [("rbac", "0014_p5b_overtime_rule_permissions")]
    operations = [migrations.RunPython(ekle, geri_al)]
