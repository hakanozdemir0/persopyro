# `role_permissions` tablosu için RLS (elle yazılmıştır).
#
# NEDEN AYRI BİR MIGRATION: `tenancy.0002_enable_rls` uygulandıktan SONRA, bu
# tablonun da kiracıya bağlı olması gerektiği anlaşıldı (bir test, başka
# kiracının rolüne izin kaydı eklenebildiğini gösterdi). Uygulanmış bir
# migration'ı değiştirmek migration geçmişini bozar; bu yüzden eksik kalan
# adım ayrı bir migration olarak eklendi.
#
# KURAL: yeni bir kiracıya bağlı tablo eklendiğinde `tenancy.0002_enable_rls`
# listesine eklenir; DAHA ÖNCE uygulanmış veritabanları için buradaki gibi ayrı
# bir migration yazılır (veritabanı başına tek seferlik telafi).

from django.db import migrations

ENABLE = """
ALTER TABLE hr.role_permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.role_permissions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hr.role_permissions;
CREATE POLICY tenant_isolation ON hr.role_permissions
    USING (tenant_id = hr.current_tenant_id())
    WITH CHECK (tenant_id = hr.current_tenant_id());
"""

DISABLE = """
DROP POLICY IF EXISTS tenant_isolation ON hr.role_permissions;
ALTER TABLE hr.role_permissions NO FORCE ROW LEVEL SECURITY;
ALTER TABLE hr.role_permissions DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0003_same_tenant_guard"),
        ("rbac", "0002_rolepermission_tenant"),
    ]

    operations = [
        migrations.RunSQL(sql=ENABLE, reverse_sql=DISABLE),
    ]
