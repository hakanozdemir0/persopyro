# Kiracılar arası referans (cross-tenant reference) guard'ı — elle yazılmıştır.
#
# NEDEN GEREKLİ: RLS yalnızca "satırın tenant_id'si benim bağlamımla aynı mı?"
# sorusunu yanıtlar. Yabancı anahtar kontrolü RLS'ten etkilenmediği için şu
# açıklık ortaya çıkmıştı (bir test yakaladı):
#
#     A kiracısı bağlamında, tenant_id = A olan bir user_roles satırı, B
#     kiracısının kullanıcısını işaret edebiliyordu.
#
# Yani satır "A'nın" görünüyor ama başka kiracının nesnesine bağlanıyordu.
# Çözüm: her kiracıya bağlı yabancı anahtar için, hedef satırın kiracısı ile
# satırın kiracısının aynı olduğunu doğrulayan bir tetikleyici.
#
# Tasarım notu: tetikleyici SECURITY INVOKER'dır; hedef satırı RLS görünürlüğü
# üzerinden okur. Hedef satır görünmüyorsa (başka kiracı) `ref_tenant` NULL olur
# ve karşılaştırma hata verir → fail-closed davranış.
#
# Yeni bir kiracıya bağlı yabancı anahtar eklendiğinde bu listeye EKLENMELİDİR.

from django.db import migrations

CREATE_GUARD = """
CREATE OR REPLACE FUNCTION hr.ensure_same_tenant_ref() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    col        text := TG_ARGV[0];
    ref_table  text := TG_ARGV[1];
    val        uuid;
    ref_tenant uuid;
BEGIN
    EXECUTE format('SELECT ($1).%I', col) INTO val USING NEW;
    IF val IS NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE format('SELECT tenant_id FROM hr.%I WHERE id = $1', ref_table)
        INTO ref_tenant USING val;

    IF ref_tenant IS DISTINCT FROM NEW.tenant_id THEN
        RAISE EXCEPTION
            'Kiracilar arasi referans engellendi: %.% -> %.id = %',
            TG_TABLE_NAME, col, ref_table, val
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$fn$;

COMMENT ON FUNCTION hr.ensure_same_tenant_ref() IS
    'Kiracılar arası yabancı anahtar referansını engeller (RLS yalnızca satırın kendi tenant_id''sini denetler).';
"""

DROP_GUARD = """
DROP FUNCTION IF EXISTS hr.ensure_same_tenant_ref();
"""

# (tablo, sütun, hedef tablo) — kiracıya bağlı her yabancı anahtar burada olmalı.
GUARDS: list[tuple[str, str, str]] = [
    ("users", "created_by_id", "users"),
    ("role_permissions", "role_id", "roles"),
    ("user_roles", "user_id", "users"),
    ("user_roles", "role_id", "roles"),
    ("user_roles", "granted_by_id", "users"),
    ("user_permission_overrides", "user_id", "users"),
    ("user_permission_overrides", "granted_by_id", "users"),
    ("audit_logs", "user_id", "users"),
]


def _trigger_name(table: str, column: str) -> str:
    return f"trg_{table}_{column}_same_tenant"


def _create_sql(table: str, column: str, ref_table: str) -> str:
    return f"""
CREATE TRIGGER {_trigger_name(table, column)}
    BEFORE INSERT OR UPDATE ON hr.{table}
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_same_tenant_ref('{column}', '{ref_table}');
"""


def _drop_sql(table: str, column: str) -> str:
    return f"DROP TRIGGER IF EXISTS {_trigger_name(table, column)} ON hr.{table};"


def _guards() -> list[tuple[str, str, str]]:
    return list(GUARDS)


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0002_enable_rls"),
        ("rbac", "0002_rolepermission_tenant"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_GUARD + "".join(_create_sql(*g) for g in _guards()),
            reverse_sql="".join(_drop_sql(table, column) for table, column, _ in _guards())
            + DROP_GUARD,
        ),
    ]
