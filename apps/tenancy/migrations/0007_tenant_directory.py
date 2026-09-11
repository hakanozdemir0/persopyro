# Kiracı dizini (slug/domain → kiracı kimliği) — elle yazılmıştır.
#
# SORUN (ölçülmüş): Kiracı tablosu `FORCE ROW LEVEL SECURITY` ile korunur; FORCE
# tablo SAHİBİNİ de bağlar. Bu yüzden SECURITY DEFINER bir fonksiyon bile (tanım
# gereği sahibi olarak çalışsa da) kiracı satırını GÖREMEZ: "hangi kiracı bu
# slug'a ait?" sorusu yanıtsız kalır. Giriş akışı buna bağlıdır.
#
# ÇÖZÜM: Yalnızca eşleme tutan, RLS UYGULANMAYAN bir dizin tablosu:
#   * İçinde kiracı verisi yoktur; yalnızca `tenant_id`, `slug`, `domain`.
#   * Uygulama rolü yalnızca SELECT yapabilir (INSERT/UPDATE/DELETE YOK).
#     Dizin satırlarını yalnızca tetikleyici (SECURITY DEFINER, şema sahibi
#     olarak çalışır) yazar. Böylece bir kiracı, başkasının slug/domain kaydını
#     ele geçirip yönlendirmeyi değiştiremez.
#   * Sızabilecek bilgi yalnızca slug/alan adı listesidir; bunlar zaten kamuya
#     açıktır (alt alan adları).
#
# Bu tablo bilinçli olarak bir Django modeli DEĞİLDİR (iş nesnesi değil, arama
# dizinidir); erişim `apps/tenancy/context.py` içinden ham SQL ile yapılır.
# RLS yapısal testi, modeli olmadığı için bu tabloyu kapsamaz; güvenlik
# özellikleri (RLS yok + uygulama rolü yazamaz) ayrı testlerle doğrulanır.

from django.db import migrations

CREATE_DIRECTORY = """
CREATE TABLE hr.tenant_directory (
    tenant_id  uuid PRIMARY KEY REFERENCES hr.tenants(id) ON DELETE CASCADE,
    slug       varchar(60)  NOT NULL UNIQUE,
    domain     varchar(190) UNIQUE,
    updated_at timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE hr.tenant_directory IS
    'slug/domain -> kiracı kimliği eşlemesi. RLS yok (kiracı verisi içermez); '
    'uygulama rolü yalnızca SELECT yapabilir.';

-- Mevcut kiracılar için geri doldurma (backfill).
INSERT INTO hr.tenant_directory (tenant_id, slug, domain)
SELECT id, slug, domain FROM hr.tenants WHERE deleted_at IS NULL;

-- Dizini güncel tutan tetikleyici. Şema sahibi olarak çalışır (SECURITY DEFINER),
-- böylece uygulama rolünün dizine yazma yetkisi olmasına gerek kalmaz.
CREATE OR REPLACE FUNCTION hr.sync_tenant_directory() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = hr, pg_temp
AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM hr.tenant_directory WHERE tenant_id = OLD.id;
        RETURN OLD;
    END IF;

    IF NEW.deleted_at IS NOT NULL THEN
        DELETE FROM hr.tenant_directory WHERE tenant_id = NEW.id;
        RETURN NEW;
    END IF;

    INSERT INTO hr.tenant_directory (tenant_id, slug, domain, updated_at)
    VALUES (NEW.id, NEW.slug, NEW.domain, now())
    ON CONFLICT (tenant_id) DO UPDATE
        SET slug = EXCLUDED.slug, domain = EXCLUDED.domain, updated_at = now();
    RETURN NEW;
END
$fn$;

CREATE TRIGGER trg_tenants_sync_directory
    AFTER INSERT OR UPDATE OF slug, domain, deleted_at OR DELETE ON hr.tenants
    FOR EACH ROW EXECUTE FUNCTION hr.sync_tenant_directory();

-- Yetkiler: uygulama rolü yalnızca okur.
REVOKE ALL ON hr.tenant_directory FROM PUBLIC;
GRANT SELECT ON hr.tenant_directory TO persopyro_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON hr.tenant_directory FROM persopyro_app;

-- Artık kullanılmayan (ve FORCE RLS nedeniyle çalışmayan) arama fonksiyonları.
DROP FUNCTION IF EXISTS hr.tenant_id_by_slug(text);
DROP FUNCTION IF EXISTS hr.tenant_id_by_domain(text);
"""

DROP_DIRECTORY = """
DROP TRIGGER IF EXISTS trg_tenants_sync_directory ON hr.tenants;
DROP FUNCTION IF EXISTS hr.sync_tenant_directory();
DROP TABLE IF EXISTS hr.tenant_directory;

CREATE OR REPLACE FUNCTION hr.tenant_id_by_slug(p_slug text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = hr, pg_temp
AS $$
    SELECT t.id FROM hr.tenants t WHERE t.slug = p_slug AND t.deleted_at IS NULL
$$;

CREATE OR REPLACE FUNCTION hr.tenant_id_by_domain(p_domain text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = hr, pg_temp
AS $$
    SELECT t.id FROM hr.tenants t WHERE t.domain = p_domain AND t.deleted_at IS NULL
$$;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0006_tenant_lookup_functions"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_DIRECTORY, reverse_sql=DROP_DIRECTORY),
    ]
