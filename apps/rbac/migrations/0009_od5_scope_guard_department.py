# M9 tamamlama (OD5 kapalı) — kapsam guard'ı artık **birimi de doğrular**.
#
# `0007_od5_user_role_scope` içindeki `hr.user_role_scope_guard()` (M9 kısmi)
# yalnızca şunları dayatıyordu:
#   * `UNIT`/`UNIT_DIRECT` rol → `scope_org_unit_id` **zorunlu**,
#   * diğer tipler → kapsam **NULL** olmalı.
#
# M10 (`hr.departments`) geldiğine göre aynı fonksiyon **genişletilir**
# (`CREATE OR REPLACE`; uygulanmış migration değiştirilmez, yeni migration eklenir):
#   * kapsam **aynı kiracının AKTİF** birimini göstermeli (görünmüyorsa fail-closed),
#   * **soft-delete edilmiş birim** kapsam olarak atanamaz → silinen birim
#     "sessizce tenant-wide" hâle **gelemez** (OD5 §5.6),
#   * silinmiş bir birimi kapsayan **mevcut** atamalar yetki üretmez; sessiz
#     yeniden etkinleşme olmaması için birim restore edilse bile atamanın
#     **açık işlemle** yeniden etkinleştirilmesi gerekir (OD21) — çözümleyici
#     (`apps/rbac/services/scope.py`) yalnızca **aktif** birimden kapsam üretir.
#
# DEĞİŞMEYEN kural (M9): `scope_org_unit_id IS NULL` **asla** "tüm kiracı" değildir.

from django.db import migrations

GUARD_SQL = """
CREATE OR REPLACE FUNCTION hr.user_role_scope_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $fn$
DECLARE
    rol_kapsami text;
    birim_silinmis timestamptz;
BEGIN
    SELECT r.scope_type INTO rol_kapsami FROM hr.roles r WHERE r.id = NEW.role_id;

    IF rol_kapsami IS NULL THEN
        -- Rol görünmüyorsa (kiracı bağlamı yok/yanlış) yazım reddedilir.
        RAISE EXCEPTION 'user_roles: rol bulunamadi (%) — kapsam dogrulanamaz (fail-closed)',
            NEW.role_id;
    END IF;

    IF rol_kapsami IN ('UNIT', 'UNIT_DIRECT') AND NEW.scope_org_unit_id IS NULL THEN
        RAISE EXCEPTION
            'user_roles: % kapsamli rol icin scope_org_unit_id ZORUNLUDUR (OD5 fail-closed). '
            'NULL kapsam "tum kiraci" anlamina GELMEZ.', rol_kapsami;
    END IF;

    IF rol_kapsami NOT IN ('UNIT', 'UNIT_DIRECT') AND NEW.scope_org_unit_id IS NOT NULL THEN
        RAISE EXCEPTION
            'user_roles: % kapsamli rolde scope_org_unit_id NULL olmalidir (birim daraltmasi '
            'yalniz UNIT/UNIT_DIRECT tipli roller icin tanimlidir).', rol_kapsami;
    END IF;

    IF NEW.scope_org_unit_id IS NOT NULL THEN
        SELECT d.deleted_at INTO birim_silinmis
          FROM hr.departments d WHERE d.id = NEW.scope_org_unit_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'user_roles: kapsam birimi bulunamadi (yok veya kiraci disi): %',
                NEW.scope_org_unit_id;
        END IF;

        IF birim_silinmis IS NOT NULL THEN
            RAISE EXCEPTION
                'user_roles: silinmis birim (%) kapsam olarak atanamaz (OD21/OD5 fail-closed)',
                NEW.scope_org_unit_id;
        END IF;
    END IF;

    RETURN NEW;
END
$fn$;

COMMENT ON FUNCTION hr.user_role_scope_guard() IS
    'OD5/M9: UNIT kapsam zorunlulugu + kapsamin AKTIF, ayni kiracidaki birim olmasi.';
"""

# Geri alma: M9'daki (0007) sürüme döner — birim doğrulaması olmadan.
GUARD_REVERSE = """
CREATE OR REPLACE FUNCTION hr.user_role_scope_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $fn$
DECLARE
    rol_kapsami text;
BEGIN
    SELECT r.scope_type INTO rol_kapsami FROM hr.roles r WHERE r.id = NEW.role_id;

    IF rol_kapsami IS NULL THEN
        RAISE EXCEPTION 'user_roles: rol bulunamadi (%) — kapsam dogrulanamaz (fail-closed)',
            NEW.role_id;
    END IF;

    IF rol_kapsami IN ('UNIT', 'UNIT_DIRECT') AND NEW.scope_org_unit_id IS NULL THEN
        RAISE EXCEPTION
            'user_roles: % kapsamli rol icin scope_org_unit_id ZORUNLUDUR (OD5 fail-closed). '
            'NULL kapsam "tum kiraci" anlamina GELMEZ.', rol_kapsami;
    END IF;

    IF rol_kapsami NOT IN ('UNIT', 'UNIT_DIRECT') AND NEW.scope_org_unit_id IS NOT NULL THEN
        RAISE EXCEPTION
            'user_roles: % kapsamli rolde scope_org_unit_id NULL olmalidir.', rol_kapsami;
    END IF;

    RETURN NEW;
END
$fn$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0008_od5_scope_org_unit_fk"),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, GUARD_REVERSE),
    ]
