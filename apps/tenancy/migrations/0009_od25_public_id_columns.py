# OD25/OD28 (kapalı) — `hr.tenants.public_id_prefix` (M5, kiracı tarafı).
#
# Sıra (veri kaybı ve körlemesine değişiklik yok):
#   1. kolon **nullable** eklenir,
#   2. mevcut kiracılar için değer üretilir (slug'tan; çakışmada rastgele sonek),
#   3. `NOT NULL` yapılır,
#   4. tam `UNIQUE` (ödünç verilen prefix **kalıcı rezerve** — kısmi değil),
#   5. format + rezerve ad `CHECK`'leri,
#   6. **immutability** tetikleyicisi (OD28: rebrand/slug değişimi prefix'i
#      değiştirmez; değiştirme uç noktası yoktur) ve yeni kiracılar için
#      `BEFORE INSERT` atama tetikleyicisi.
#
# `hr.tenants` FORCE RLS ile korunduğu için geri doldurma **kiracı bağlamı
# içinde** yapılır; kiracı listesi RLS'siz `hr.tenant_directory`'den okunur.

import django.db.models.deletion
from django.db import migrations, models


def prefix_geri_doldur(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Mevcut kiracılara `public_id_prefix` atar (deterministik + çakışmasız)."""
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        kiraci_idleri = [satir[0] for satir in cur.fetchall()]

        kullanilan: set[str] = set()
        for tenant_id in kiraci_idleri:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute(
                "SELECT slug, public_id_prefix FROM hr.tenants WHERE id = %s",
                [str(tenant_id)],
            )
            satir = cur.fetchone()
            if satir is None:  # kiracı silinmiş olabilir (dizin gecikmiş olabilir)
                continue
            slug, mevcut = satir
            if mevcut:
                kullanilan.add(mevcut)
                continue

            aday = None
            for deneme in range(6):
                if deneme == 0:
                    cur.execute("SELECT hr.generate_public_id_prefix(%s)", [slug])
                else:
                    # Çakışmada kısa rastgele sonek (OD25/2).
                    cur.execute(
                        "SELECT left(hr.generate_public_id_prefix(%s), 14)"
                        " || lower(hr.crockford_suffix(4))",
                        [slug],
                    )
                deneme_degeri = cur.fetchone()[0]
                if deneme_degeri not in kullanilan:
                    aday = deneme_degeri
                    break

            if aday is None:
                raise RuntimeError(
                    f"public_id_prefix üretilemedi (çakışma): kiracı {tenant_id} / slug {slug}"
                )

            cur.execute(
                "UPDATE hr.tenants SET public_id_prefix = %s WHERE id = %s",
                [aday, str(tenant_id)],
            )
            kullanilan.add(aday)


KISITLAR = """
-- Format + rezerve ad kuralları (OD25/OD28) --------------------------------
ALTER TABLE hr.tenants
    ADD CONSTRAINT tenants_public_id_prefix_format
        CHECK (public_id_prefix ~ '^[a-z0-9]{3,20}$');
ALTER TABLE hr.tenants
    ADD CONSTRAINT tenants_public_id_prefix_not_reserved
        CHECK (NOT hr.is_reserved_name(public_id_prefix));
ALTER TABLE hr.tenants
    ADD CONSTRAINT tenants_slug_not_reserved
        CHECK (NOT hr.is_reserved_name(slug));

-- Kalıcı rezerve: silinmiş kiracının prefix'i yeniden tahsis edilmez (OD25/4).
ALTER TABLE hr.tenants
    ADD CONSTRAINT tenants_public_id_prefix_key UNIQUE (public_id_prefix);

-- Yeni kiracıda prefix ataması (kolon NOT NULL; uygulama boş bırakabilir) ----
CREATE OR REPLACE FUNCTION hr.tenants_public_id_prefix_assign()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
BEGIN
    IF NEW.public_id_prefix IS NULL OR btrim(NEW.public_id_prefix) = '' THEN
        NEW.public_id_prefix := hr.generate_public_id_prefix(NEW.slug);
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tenants_public_id_prefix_assign ON hr.tenants;
CREATE TRIGGER trg_tenants_public_id_prefix_assign
    BEFORE INSERT ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION hr.tenants_public_id_prefix_assign();

-- Değişmezlik (OD28): prefix bir kez atandıktan sonra DEĞİŞMEZ ---------------
CREATE OR REPLACE FUNCTION hr.tenants_public_id_prefix_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
BEGIN
    IF OLD.public_id_prefix IS NOT NULL
       AND NEW.public_id_prefix IS DISTINCT FROM OLD.public_id_prefix THEN
        RAISE EXCEPTION
            'hr.tenants: public_id_prefix degistirilemez (OD25/OD28); yeni kiracı acin';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_tenants_public_id_prefix_guard ON hr.tenants;
CREATE TRIGGER trg_tenants_public_id_prefix_guard
    BEFORE UPDATE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION hr.tenants_public_id_prefix_guard();
"""

KISITLAR_REVERSE = """
DROP TRIGGER IF EXISTS trg_tenants_public_id_prefix_guard ON hr.tenants;
DROP FUNCTION IF EXISTS hr.tenants_public_id_prefix_guard();
DROP TRIGGER IF EXISTS trg_tenants_public_id_prefix_assign ON hr.tenants;
DROP FUNCTION IF EXISTS hr.tenants_public_id_prefix_assign();
ALTER TABLE hr.tenants DROP CONSTRAINT IF EXISTS tenants_public_id_prefix_key;
ALTER TABLE hr.tenants DROP CONSTRAINT IF EXISTS tenants_slug_not_reserved;
ALTER TABLE hr.tenants DROP CONSTRAINT IF EXISTS tenants_public_id_prefix_not_reserved;
ALTER TABLE hr.tenants DROP CONSTRAINT IF EXISTS tenants_public_id_prefix_format;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0008_od25_public_id_helpers"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="public_id_prefix",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "OD25/OD28: immutable; DB tetikleyicisi üretir ve değişimini "
                    "reddeder (yanlışlıkla ORM ile yazılan boş değer doldurulur)."
                ),
                max_length=20,
                null=True,
                verbose_name="public ID ön eki",
            ),
        ),
        migrations.RunPython(prefix_geri_doldur, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tenant",
            name="public_id_prefix",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "OD25/OD28: immutable; DB tetikleyicisi üretir ve değişimini "
                    "reddeder (yanlışlıkla ORM ile yazılan boş değer doldurulur)."
                ),
                max_length=20,
                verbose_name="public ID ön eki",
            ),
        ),
        migrations.RunSQL(sql=KISITLAR, reverse_sql=KISITLAR_REVERSE),
    ]
