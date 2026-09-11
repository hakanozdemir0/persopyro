# Kiracı çözümleme fonksiyonları (SECURITY DEFINER) — elle yazılmıştır.
#
# SORUN: Kiracı tablosu RLS ile korunur (`id = current_tenant_id()`). Bu yüzden
# "hangi kiracı bu slug'a/alana adına ait?" sorusu normal yoldan sorulamaz —
# giriş öncesi bağlam yoktur. Klasik tavuk-yumurta problemi.
#
# ÇÖZÜM: Yalnızca `slug`/`domain` → kiracı kimliği eşlemesi yapan, başka hiçbir
# veri döndürmeyen iki adet SECURITY DEFINER fonksiyon. Fonksiyonlar kiracı
# tablosunda RLS'i atlar ama yalnızca TEK BİR UUID döndürür; kiracı verisi
# sızdırmaz. Sızabilecek tek bilgi "bu slug var mı" sorusunun yanıtıdır
# (alt alan adları zaten kamuya açıktır).
#
# GÜVENLİK: SECURITY DEFINER fonksiyonlarda `SET search_path` ZORUNLUDUR;
# aksi halde arama yolu ele geçirilerek farklı bir nesne çalıştırılabilir.

from django.db import migrations

CREATE_FUNCTIONS = """
CREATE OR REPLACE FUNCTION hr.tenant_id_by_slug(p_slug text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = hr, pg_temp
AS $$
    SELECT t.id FROM hr.tenants t
     WHERE t.slug = p_slug AND t.deleted_at IS NULL
$$;

CREATE OR REPLACE FUNCTION hr.tenant_id_by_domain(p_domain text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = hr, pg_temp
AS $$
    SELECT t.id FROM hr.tenants t
     WHERE t.domain = p_domain AND t.deleted_at IS NULL
$$;

REVOKE ALL ON FUNCTION hr.tenant_id_by_slug(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION hr.tenant_id_by_domain(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hr.tenant_id_by_slug(text) TO persopyro_app;
GRANT EXECUTE ON FUNCTION hr.tenant_id_by_domain(text) TO persopyro_app;

COMMENT ON FUNCTION hr.tenant_id_by_slug(text) IS
    'Slug -> kiracı kimliği (RLS''i yalnızca bu tek satırlık eşleme için aşar).';
COMMENT ON FUNCTION hr.tenant_id_by_domain(text) IS
    'Alan adı -> kiracı kimliği (RLS''i yalnızca bu tek satırlık eşleme için aşar).';
"""

DROP_FUNCTIONS = """
DROP FUNCTION IF EXISTS hr.tenant_id_by_slug(text);
DROP FUNCTION IF EXISTS hr.tenant_id_by_domain(text);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0005_tenant_slug_domain"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_FUNCTIONS, reverse_sql=DROP_FUNCTIONS),
    ]
