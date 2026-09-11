# M11 (OD24 kapalı) — organizasyon ağacı **veritabanı garantileri**.
#
# Zorlanan kurallar:
#   1. Birim **kendi ebeveyni olamaz** → `CHECK (parent_id IS DISTINCT FROM id)`.
#   2. **Döngü kurulamaz** (A→B→C→A) → tetikleyici ata zincirini yürür ve
#      zincirde kendi kaydını bulursa **hata verir** (fail-closed).
#   3. **Maksimum derinlik 20** → aynı yürüyüş 20 adımla sınırlıdır.
#   4. **Silinmiş (soft-delete) birimin altına yeni aktif birim bağlanamaz.**
#   5. Üst birim **görünmüyorsa** (başka kiracı / yok) yazım reddedilir; kiracı
#      eşitliği ayrıca `hr.ensure_same_tenant_ref()` guard'ıyla doğrulanır
#      (`0007_org_same_tenant_guards`).
#
# NEDEN VERİTABANINDA: döngü, kapsam çözümlemesini bozarak **yetki sınırlarını
# kaydırabilir** (OD24 güvenlik etkisi). Uygulama katmanındaki kontrol ikinci
# savunmadır (daha anlaşılır hata mesajı için).
#
# Tetikleyici **SECURITY INVOKER**'dır: hedef satırı RLS görünürlüğü üzerinden
# okur; satır görünmüyorsa `NOT FOUND` → hata (fail-closed). Sabit `search_path`
# ile tam nitelemeli SQL kullanılır.

from django.db import migrations, models

TREE_GUARD = """
CREATE OR REPLACE FUNCTION hr.departments_tree_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $fn$
DECLARE
    ata        uuid := NEW.parent_id;
    silinmis   timestamptz;
    derinlik   integer := 0;
BEGIN
    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- (a) Üst birim görünür ve kiracı içinde mi? (RLS: görünmüyorsa NOT FOUND)
    SELECT d.deleted_at INTO silinmis FROM hr.departments d WHERE d.id = NEW.parent_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Ust birim bulunamadi (yok veya kiraci disi): %', NEW.parent_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- (b) Silinmiş birimin altına yeni AKTİF birim bağlanamaz (OD21 fail-closed).
    IF silinmis IS NOT NULL AND NEW.deleted_at IS NULL THEN
        RAISE EXCEPTION
            'Silinmis birim altina yeni aktif birim baglanamaz (OD21): %', NEW.parent_id
            USING ERRCODE = 'check_violation';
    END IF;

    -- (c) Döngü ve derinlik: ata zinciri yürünür (en fazla 20 adım).
    WHILE ata IS NOT NULL LOOP
        IF ata = NEW.id THEN
            RAISE EXCEPTION
                'Organizasyon dongusu engellendi: % kendi atasidir', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        derinlik := derinlik + 1;
        IF derinlik >= 20 THEN
            RAISE EXCEPTION
                'Maksimum organizasyon derinligi (20) asildi (OD24)'
                USING ERRCODE = 'check_violation';
        END IF;
        SELECT d.parent_id INTO ata FROM hr.departments d WHERE d.id = ata;
    END LOOP;

    RETURN NEW;
END
$fn$;

COMMENT ON FUNCTION hr.departments_tree_guard() IS
    'OD24: organizasyon agacinda dongu, derinlik (20) ve silinmis ata ihlalini reddeder.';

DROP TRIGGER IF EXISTS trg_departments_tree_guard ON hr.departments;
CREATE TRIGGER trg_departments_tree_guard
    BEFORE INSERT OR UPDATE ON hr.departments
    FOR EACH ROW EXECUTE FUNCTION hr.departments_tree_guard();
"""

TREE_GUARD_REVERSE = """
DROP TRIGGER IF EXISTS trg_departments_tree_guard ON hr.departments;
DROP FUNCTION IF EXISTS hr.departments_tree_guard();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0001_od24_departments_positions"),
    ]

    operations = [
        # 1) Kendi ebeveyni olamaz (CHECK).
        migrations.AddConstraint(
            model_name="department",
            constraint=models.CheckConstraint(
                condition=models.Q(("parent_id", models.F("id")), _negated=True),
                name="departments_parent_not_self",
            ),
        ),
        # 2-4) Döngü / derinlik / silinmiş ata garantileri (tetikleyici).
        migrations.RunSQL(TREE_GUARD, TREE_GUARD_REVERSE),
    ]
