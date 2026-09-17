"""P5b — fazla mesai kuralı yönetim izinleri."""

from django.db import migrations


YENI_IZINLER: tuple[tuple[str, str, str], ...] = (
    ("overtime.rule.read", "Fazla mesai kurallarını görme", "overtime"),
    ("overtime.rule.manage", "Fazla mesai kuralı yönetimi", "overtime"),
)

# Kural tanımı tenant yöneticisi veya tenant kapsamındaki HR rolüyle sınırlıdır.
ROL_IZINLERI: dict[str, tuple[str, ...]] = {
    "tenant_admin": ("overtime.rule.read", "overtime.rule.manage"),
    "hr_manager": ("overtime.rule.read", "overtime.rule.manage"),
}


def izinleri_ekle(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    with schema_editor.connection.cursor() as cur:
        for kod, ad, modul in YENI_IZINLER:
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
            for rol_kodu, kodlar in ROL_IZINLERI.items():
                cur.execute(
                    "SELECT id FROM hr.roles WHERE code = %s AND deleted_at IS NULL "
                    "AND role_type = 'default'",
                    [rol_kodu],
                )
                rol = cur.fetchone()
                if rol is None:
                    continue
                for kod in kodlar:
                    cur.execute(
                        "INSERT INTO hr.role_permissions "
                        "(id, created_at, updated_at, tenant_id, role_id, permission_id) "
                        "SELECT gen_random_uuid(), now(), now(), %s, %s, p.id "
                        "FROM hr.permissions p WHERE p.code = %s "
                        "ON CONFLICT (role_id, permission_id) DO NOTHING",
                        [str(tenant_id), str(rol[0]), kod],
                    )
        cur.execute("SELECT set_config('app.tenant_id', '', true)")


def izinleri_sil(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    kodlar = [kod for kod, _ad, _modul in YENI_IZINLER]
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
    dependencies = [("rbac", "0013_p5a_overtime_permissions")]
    operations = [migrations.RunPython(izinleri_ekle, izinleri_sil)]
