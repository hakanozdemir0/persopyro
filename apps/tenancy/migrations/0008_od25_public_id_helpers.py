# OD25/OD28 (kapalı) — public ID yardımcı fonksiyonları (M5, yardımcı katman).
#
# NEDEN AYRI MIGRATION: M5 üç parçalıdır ve parçalar farklı uygulamalara aittir:
#   1. **yardımcı fonksiyonlar** (bu dosya) — `hr.*`, kiracı/kullanıcı fark etmez,
#   2. `hr.tenants.public_id_prefix` (tenancy 0009),
#   3. `hr.users.public_id` (accounts 0005).
# Platform kullanıcılarının `plt-...` public ID'si de aynı Crockford üreticisini
# kullandığı için platform migration'ı bu dosyaya bağlıdır (platform 0001).
#
# Kurallar (docs/DATABASE.md M5, docs/AUTHORIZATION.md OD25/OD28):
#   * `public_id_prefix` ∈ `[a-z0-9]{3,20}`, slug'tan türetilir, immutable,
#     **kalıcı rezerve** (tam UNIQUE), rezerve adlar kullanılamaz.
#   * `public_id` = `<prefix>-<8×Crockford Base32>`; Crockford alfabesinde
#     `I`, `L`, `O`, `U` **yoktur** (`0123456789ABCDEFGHJKMNPQRSTVWXYZ`).
#   * Rastgelelik `gen_random_uuid()` (çekirdek, kriptografik RNG) kaynağından
#     alınır; `pgcrypto`/`gen_random_bytes` **gerekmez** (256 % 32 == 0 → modulo
#     sapması yok).
#
# NOT (ölçülmüş sınır): çapraz kiracı tekillik **fonksiyon içinde** garanti
# edilemez; `hr.tenants`/`hr.users` FORCE RLS ile korunur ve fonksiyon yalnızca
# kiracı bağlamındaki satırları görür. Tekilliğin gerçek garantisi **UNIQUE**
# kısıtlarıdır (bkz. tenancy 0009 / accounts 0005); fonksiyon görünür satırlar
# için sınırlı bir yeniden deneme yapar.

from django.db import migrations

HELPERS = """
-- Crockford Base32 rastgele son ek (I, L, O, U hariç) ----------------------
CREATE OR REPLACE FUNCTION hr.crockford_suffix(boyut integer DEFAULT 8)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SET search_path = hr, pg_temp
AS $$
DECLARE
    alfabe constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    baytlar bytea;
    sonuc text := '';
    i integer;
BEGIN
    IF boyut IS NULL OR boyut < 1 OR boyut > 16 THEN
        RAISE EXCEPTION 'crockford_suffix: boyut 1..16 araliginda olmalidir';
    END IF;

    baytlar := uuid_send(gen_random_uuid());
    FOR i IN 0..boyut - 1 LOOP
        sonuc := sonuc || substr(alfabe, (get_byte(baytlar, i) % 32) + 1, 1);
    END LOOP;
    RETURN sonuc;
END
$$;

COMMENT ON FUNCTION hr.crockford_suffix(integer) IS
    'Crockford Base32 rastgele son ek (OD25). Kriptografik RNG: gen_random_uuid().';

-- Rezerve ad listesi (OD28) --------------------------------------------------
CREATE OR REPLACE FUNCTION hr.is_reserved_name(deger text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = hr, pg_temp
AS $$
    SELECT lower(btrim(coalesce(deger, ''))) IN (
        'www', 'api', 'app', 'admin', 'portal', 'mail',
        'support', 'hr', 'platform', 'plt', 'system'
    )
$$;

COMMENT ON FUNCTION hr.is_reserved_name(text) IS
    'Rezerve slug/prefix listesi (OD28): kiracı slug ve public ID on eki olarak kullanilamaz.';

-- slug -> public_id_prefix --------------------------------------------------
CREATE OR REPLACE FUNCTION hr.generate_public_id_prefix(slug text)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SET search_path = hr, pg_temp
AS $$
DECLARE
    temel text;
    sonuc text;
BEGIN
    temel := regexp_replace(lower(btrim(coalesce(slug, ''))), '[^a-z0-9]', '', 'g');
    IF length(temel) > 20 THEN
        temel := left(temel, 20);
    END IF;

    -- Çok kısa/boş slug: sabit olmayan ama güvenli bir taban üret.
    IF length(temel) < 3 THEN
        RETURN 'ktr' || lower(hr.crockford_suffix(5));
    END IF;

    sonuc := temel;
    IF hr.is_reserved_name(sonuc) THEN
        -- Rezerve ad (ör. 'admin') prefix olamaz: kısa rastgele sonek eklenir.
        sonuc := left(temel, 14) || lower(hr.crockford_suffix(4));
    END IF;
    RETURN sonuc;
END
$$;

COMMENT ON FUNCTION hr.generate_public_id_prefix(text) IS
    'Slug -> public_id_prefix (OD25): [a-z0-9]{3,20}, rezerve adlar icin rastgele sonek.';

-- tenant_id -> public_id ----------------------------------------------------
CREATE OR REPLACE FUNCTION hr.generate_public_id(tenant_id uuid)
RETURNS text
LANGUAGE plpgsql
VOLATILE
SET search_path = hr, pg_temp
AS $$
DECLARE
    onek text;
    sonuc text;
    deneme integer := 0;
BEGIN
    IF tenant_id IS NULL THEN
        -- Kiracı bilinmiyorsa üretim YAPILMAZ: çağıran taraf NOT NULL ihlaliyle
        -- fail-closed davranır (kapsamsız kimlik üretilmez).
        RETURN NULL;
    END IF;

    SELECT t.public_id_prefix INTO onek FROM hr.tenants t WHERE t.id = tenant_id;
    IF onek IS NULL THEN
        RETURN NULL;
    END IF;

    -- Görünür satırlar arasında çakışma olmasın; global tekillik UNIQUE indekste.
    LOOP
        deneme := deneme + 1;
        sonuc := onek || '-' || hr.crockford_suffix(8);
        EXIT WHEN NOT EXISTS (SELECT 1 FROM hr.users u WHERE u.public_id = sonuc);
        EXIT WHEN deneme >= 10;
    END LOOP;
    RETURN sonuc;
END
$$;

COMMENT ON FUNCTION hr.generate_public_id(uuid) IS
    'Kiracinin prefixinden public_id uretir (OD25): <prefix>-<8xCrockford>.';
"""

HELPERS_REVERSE = """
DROP FUNCTION IF EXISTS hr.generate_public_id(uuid);
DROP FUNCTION IF EXISTS hr.generate_public_id_prefix(text);
DROP FUNCTION IF EXISTS hr.is_reserved_name(text);
DROP FUNCTION IF EXISTS hr.crockford_suffix(integer);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0007_tenant_directory"),
        # `hr.permissions` gerekmez; yalnız yetki fonksiyonları burada kurulur.
    ]

    operations = [
        migrations.RunSQL(sql=HELPERS, reverse_sql=HELPERS_REVERSE),
    ]
