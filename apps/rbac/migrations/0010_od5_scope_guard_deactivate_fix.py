# M9/OD5 düzeltmesi — kapsam guard'ı **devre dışı bırakmayı** engellememeli.
#
# ÖLÇÜLEN SORUN (M10–M13 turu, test): birim silme servisi (`soft_delete_department`)
# birim silindikten SONRA ona bağlı kapsam atamalarını devre dışı bırakır
# (`user_roles.deleted_at`). `0009_od5_scope_guard_department` sürümündeki guard,
# atama **silinirken** bile "silinmiş birim kapsam olarak atanamaz" diyerek bu
# güncellemeyi reddediyordu → birim silme işlemi tümden başarısız oluyordu
# (sessiz yetki genişlemesi değil, işlev bozukluğu).
#
# DÜZELTME: "birim aktif olmalı" kuralı yalnızca **AKTİF** (yani `deleted_at IS NULL`)
# atamalar için geçerlidir. Silinmiş bir atamanın korunması/etiketlenmesi serbesttir;
# zaten kapsam **üretmez** (fail-closed). Böylece:
#   * silinmiş birime **yeni aktif** atama → yine REDDEDİLİR,
#   * silinmiş birime işaret eden atamanın **devre dışı bırakılması** → serbest.
#
# Diğer kurallar (kapsam zorunluluğu, tür-NULL uyumu) değişmez.

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

    -- Birim doğrulaması yalnızca AKTİF atamalar için (devre dışı bırakma serbesttir).
    IF NEW.scope_org_unit_id IS NOT NULL AND NEW.deleted_at IS NULL THEN
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
    'OD5/M9: UNIT kapsam zorunlulugu + AKTIF atamalarda kapsamin AKTIF birim olmasi; '
    'devre disi birakma serbesttir.';
"""

# Geri alma: 0009 sürümü (devre dışı bırakmayı da engelleyen hâli).
GUARD_REVERSE = """
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

    IF NEW.scope_org_unit_id IS NOT NULL THEN
        SELECT d.deleted_at INTO birim_silinmis
          FROM hr.departments d WHERE d.id = NEW.scope_org_unit_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'user_roles: kapsam birimi bulunamadi: %', NEW.scope_org_unit_id;
        END IF;
        IF birim_silinmis IS NOT NULL THEN
            RAISE EXCEPTION 'user_roles: silinmis birim kapsam olarak atanamaz: %',
                NEW.scope_org_unit_id;
        END IF;
    END IF;

    RETURN NEW;
END
$fn$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0009_od5_scope_guard_department"),
        ("organization", "0001_od24_departments_positions"),
    ]

    operations = [
        migrations.RunSQL(GUARD_SQL, GUARD_REVERSE),
    ]
