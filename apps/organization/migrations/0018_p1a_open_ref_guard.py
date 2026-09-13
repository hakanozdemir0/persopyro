# P1-A — "soft-delete edilmiş hedefe yeni AÇIK kayıt" guard'ı (elle yazılmıştır).
#
# KAYNAK KARARLAR (kapalı): OD58 (işyeri), OD60 (geçmiş tabloları),
# `docs/HR_ROADMAP.md` §28.1 ve `docs/ORGANIZATION.md` §7.1.
#
# KURAL: Arşivlenmiş/silinmiş bir **birim, pozisyon veya işyeri** yeni bir **açık**
# (`effective_to IS NULL`) atama/koşul satırında kullanılamaz. Geçmiş (kapalı) satırlar
# etkilenmez: bir birim sonradan kapatılırsa eski atama kayıtları geçerli kalır.
#
# Uygulama biçimi: `hr.ensure_same_tenant_ref()` deseninin kardeşi olan
# `hr.ensure_open_ref_not_deleted(col, ref_table, open_col)` tetikleyicisi.
#   * `open_col` (ör. `effective_to`) NULL **değilse** → satır kapalıdır, kural uygulanmaz.
#   * Hedef satır görünmüyorsa (başka kiracı) `deleted_at` NULL döner ve karar vermez;
#     kiracılar arası durumu `hr.ensure_same_tenant_ref()` zaten reddeder (fail-closed).
#
# Not: DB seviyesinde zorlanır çünkü servis katmanı (P1-B) henüz yoktur ve kural
# "yeni açık kayıt" semantiği taşıdığı için yalnız uygulama katmanına bırakılamaz.

from django.db import migrations

CREATE_FN = """
CREATE OR REPLACE FUNCTION hr.ensure_open_ref_not_deleted() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    col        text := TG_ARGV[0];
    ref_table  text := TG_ARGV[1];
    open_col   text := TG_ARGV[2];
    val        uuid;
    acik       text;   -- açıklık sütunu (ör. `effective_to` DATE) metne çevrilir
    ref_deleted timestamptz;
BEGIN
    -- Satır kapalıysa (open_col doluysa) geçmiş kaydıdır: kural uygulanmaz.
    EXECUTE format('SELECT ($1).%I', open_col) INTO acik USING NEW;
    IF acik IS NOT NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE format('SELECT ($1).%I', col) INTO val USING NEW;
    IF val IS NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE format('SELECT deleted_at FROM hr.%I WHERE id = $1', ref_table)
        INTO ref_deleted USING val;

    IF ref_deleted IS NOT NULL THEN
        RAISE EXCEPTION
            'Silinmis/arsivlenmis kayit yeni acik satirda kullanilamaz: %.% -> %.id = %',
            TG_TABLE_NAME, col, ref_table, val
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END
$fn$;

COMMENT ON FUNCTION hr.ensure_open_ref_not_deleted() IS
    'Açık (current) kayıtlarda soft-delete edilmiş birim/pozisyon/işyeri kullanımını engeller.';
"""

DROP_FN = "DROP FUNCTION IF EXISTS hr.ensure_open_ref_not_deleted();"

# (tablo, sütun, hedef tablo, açıklık sütunu)
GUARDS: list[tuple[str, str, str, str]] = [
    ("employee_assignments", "department_id", "departments", "effective_to"),
    ("employee_assignments", "position_id", "positions", "effective_to"),
    ("employee_employment_terms", "workplace_id", "workplaces", "effective_to"),
]


def _trigger_name(table: str, column: str) -> str:
    return f"trg_{table}_{column}_open_ref_not_deleted"


def _create_sql(table: str, column: str, ref_table: str, open_col: str) -> str:
    return f"""
DROP TRIGGER IF EXISTS {_trigger_name(table, column)} ON hr.{table};
CREATE TRIGGER {_trigger_name(table, column)}
    BEFORE INSERT OR UPDATE ON hr.{table}
    FOR EACH ROW EXECUTE FUNCTION hr.ensure_open_ref_not_deleted('{column}', '{ref_table}', '{open_col}');
"""


def _drop_sql(table: str, column: str) -> str:
    return f"DROP TRIGGER IF EXISTS {_trigger_name(table, column)} ON hr.{table};"


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0017_p1a_same_tenant_guards"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_FN + "".join(_create_sql(*g) for g in GUARDS),
            reverse_sql="".join(_drop_sql(t, c) for t, c, _, _ in GUARDS) + DROP_FN,
        ),
    ]
