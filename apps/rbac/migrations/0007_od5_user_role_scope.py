# M9 (OD5 kapalı ✅) — `hr.user_roles.scope_org_unit_id` (kısmi uygulama).
#
# KAPSAM NOTU (bilinçli, ölçülmüş): `docs/DATABASE.md` M9 satırı `M10
# (departments)` bağımlılığı taşır. **M10 tabloları bu turda oluşturulmadığı için**
# M9'un yalnızca organizasyon tablosuna bağlı OLMAYAN kısmı uygulanır:
#
#   ✅ Şimdi: `scope_org_unit_id` kolonu (nullable, **UUID** — M10 sonrası
#      `departments.id` FK'si olacak tip), benzersizliğin kapsamı içerecek şekilde
#      güncellenmesi (`NULLS NOT DISTINCT`), **guard tetikleyicisi**
#      (UNIT/UNIT_DIRECT ⇒ kapsam zorunlu; diğer tiplerde kapsam NULL olmalı).
#   ⏳ M10 sonrası: `REFERENCES hr.departments(id)` FK'si, "kapsam aynı kiracının
#      AKTİF birimini göstermeli" guard'ı (`hr.ensure_same_tenant_ref` deseni) ve
#      alt ağaç (subtree) çözümlemesi. Sahte FK/tablo üretilmedi.
#
# OD5 fail-closed davranışı bu migration'dan SONRA şu iki katmanda yürürlüktedir:
#   1. DB tetikleyicisi: kapsamsız UNIT ataması **hiç yazılamaz**.
#   2. Çözümleyici (`apps/rbac/services/scope.py`): kapsam **çözülemezse**
#      (M10 öncesi her zaman) UNIT/UNIT_DIRECT ataması **izin üretmez**.
#
# Mevcut kayıtlar: bugün hiçbir atama UNIT/UNIT_DIRECT tipli bir role ait değil
# (ölçüm: tüm atamalar `tenant_admin`), ancak M7 göçü sonrası doğabilecek kapsamsız
# `dept_manager` atamaları **silinmez** — yetki üretmezler ve açık bir işlemle
# kapsam atanması gerekir (sessiz kapsam uydurulmaz).
#
# Geri alma: tetikleyici/fonksiyon düşürülür, benzersizlik eski hâline döner,
# kolon silinir.

from django.db import migrations, models

GUARD_SQL = """
CREATE OR REPLACE FUNCTION hr.user_role_scope_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
DECLARE
    rol_kapsami text;
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
            'user_roles: % kapsamli rolde scope_org_unit_id NULL olmalidir (bu turda birim '
            'daraltmasi yalniz UNIT/UNIT_DIRECT tipli roller icin tanimlidir).', rol_kapsami;
    END IF;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_user_role_scope_guard ON hr.user_roles;
CREATE TRIGGER trg_user_role_scope_guard
    BEFORE INSERT OR UPDATE ON hr.user_roles
    FOR EACH ROW EXECUTE FUNCTION hr.user_role_scope_guard();
"""

GUARD_GERI_AL = """
DROP TRIGGER IF EXISTS trg_user_role_scope_guard ON hr.user_roles;
DROP FUNCTION IF EXISTS hr.user_role_scope_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0006_od5_role_scope_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="userrole",
            name="scope_org_unit_id",
            field=models.UUIDField(
                blank=True,
                help_text=(
                    "OD5: UNIT/UNIT_DIRECT tipli rollerde ZORUNLU (DB tetikleyicisi dayatır); "
                    "diğer tiplerde NULL olmalıdır. Değer, atanan birimin organizasyon "
                    "ağacındaki konumunu gösterir ve yetki kapsamı bu birim + alt ağacıdır. "
                    "FK (departments) M10 sonrası eklenecek; M10 öncesi kapsam "
                    "ÇÖZÜLEMEZ ve bu atamalar izin ÜRETMEZ (fail-closed)."
                ),
                null=True,
                verbose_name="organizasyon birimi kapsamı",
            ),
        ),
        # Benzersizlik artık kapsamı içerir: aynı rol farklı birimler için birden
        # fazla atanabilir (OD5 §5.3). `NULLS NOT DISTINCT` sayesinde kapsamsız
        # (TENANT/SELF) çift atama hâlâ engellenir.
        migrations.RemoveConstraint(model_name="userrole", name="user_roles_uniq"),
        migrations.AddConstraint(
            model_name="userrole",
            constraint=models.UniqueConstraint(
                fields=("user", "role", "scope_org_unit_id"),
                name="user_roles_user_role_scope_uniq",
                nulls_distinct=False,
            ),
        ),
        migrations.RunSQL(GUARD_SQL, GUARD_GERI_AL),
    ]
