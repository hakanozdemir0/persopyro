# Kiracı izolasyonu: RLS politikaları (elle yazılmıştır).
#
# Django'nun model/migration katmanı Row Level Security'yi ifade edemez; bu
# yüzden ham SQL ile uygulanır. Tüm kiracıya bağlı tablolar RLS + FORCE ile
# korunur:
#   * ENABLE  → politikalar uygulanır
#   * FORCE   → TABLO SAHİBİ rol bile politikaları atlayamaz (kritik!)
#
# Politika türleri:
#   * `id = current_tenant_id()`      → kiracı tablosunun kendisi
#   * `tenant_id = current_tenant_id()` → diğer tüm kiracıya bağlı tablolar
#
# Kapsam dışı (bilinçli): `permissions` ve `role_permissions` tanım tablolarıdır,
# kiracı verisi içermez; erişim yolu korunan `roles` tablosundan geçer
# (bkz. docs/DATABASE.md bölüm 3).
#
# Yeni bir kiracıya bağlı tablo eklendiğinde bu listeye EKLENMELİDİR. Aksi halde
# izolasyon uygulama katmanına kalır — bunu yakalayan test:
# tests/test_tenant_isolation.py::test_tum_tenant_tablolarinda_rls_etkin

from django.db import migrations

# RLS uygulanacak tablolar ve politikanın dayandığı sütun.
RLS_TABLES: dict[str, str] = {
    "tenants": "id",
    "users": "tenant_id",
    "roles": "tenant_id",
    "role_permissions": "tenant_id",
    "user_roles": "tenant_id",
    "user_permission_overrides": "tenant_id",
    "audit_logs": "tenant_id",
}


def _enable_sql(table: str, column: str) -> str:
    return f"""
ALTER TABLE hr.{table} ENABLE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
CREATE POLICY tenant_isolation ON hr.{table}
    USING ({column} = hr.current_tenant_id())
    WITH CHECK ({column} = hr.current_tenant_id());
"""


def _disable_sql(table: str) -> str:
    return f"""
DROP POLICY IF EXISTS tenant_isolation ON hr.{table};
ALTER TABLE hr.{table} NO FORCE ROW LEVEL SECURITY;
ALTER TABLE hr.{table} DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):

    # Diğer uygulamaların tabloları oluştuktan SONRA çalışır.
    dependencies = [
        ("tenancy", "0001_initial"),
        ("accounts", "0001_initial"),
        ("rbac", "0001_initial"),
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="".join(_enable_sql(table, column) for table, column in RLS_TABLES.items()),
            reverse_sql="".join(_disable_sql(table) for table in RLS_TABLES),
        ),
    ]
