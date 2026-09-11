# M8 ön koşulu — platform tablolarında soft-delete izi.
#
# NEDEN: M8 (platform authorization servisi) "yalnız aktif kayıt yetki verir" kuralını
# dayatır: pasif/soft-delete edilmiş rol, rol-izin bağı veya rol ataması **yetki
# vermez** (F7 ile aynı ilke). `platform.users` bu kolonları zaten taşır
# (`deleted_at`, `status`, `is_active`); `roles`, `role_permissions` ve `user_roles`
# taşımıyordu.
#
# Proje standardı K25: silme = `deleted_at` (yeni `is_active` kolonu eklenmez).
# Satırlar fiziksel silinmez → denetim izi korunur.
#
# Geri alma: kolon ve indeksler düşürülür (veri kaybı: yalnızca silme işaretleri).

from django.db import migrations

EKLE = """
ALTER TABLE platform.roles           ADD COLUMN deleted_at timestamptz;
ALTER TABLE platform.role_permissions ADD COLUMN deleted_at timestamptz;
ALTER TABLE platform.user_roles      ADD COLUMN deleted_at timestamptz;

-- Çözümleyici (M8) `deleted_at IS NULL` ile filtreleyip FK üzerinden birleşir;
-- PostgreSQL FK için otomatik indeks kurmaz, bu yüzden açıkça eklenir.
CREATE INDEX platform_role_permissions_role_idx
    ON platform.role_permissions (role_id);
CREATE INDEX platform_role_permissions_permission_idx
    ON platform.role_permissions (permission_id);
CREATE INDEX platform_user_roles_user_idx
    ON platform.user_roles (platform_user_id);
CREATE INDEX platform_user_roles_role_idx
    ON platform.user_roles (role_id);
CREATE INDEX platform_roles_deleted_at_idx
    ON platform.roles (deleted_at) WHERE deleted_at IS NOT NULL;
"""

DUSUR = """
DROP INDEX IF EXISTS platform_roles_deleted_at_idx;
DROP INDEX IF EXISTS platform_user_roles_role_idx;
DROP INDEX IF EXISTS platform_user_roles_user_idx;
DROP INDEX IF EXISTS platform_role_permissions_permission_idx;
DROP INDEX IF EXISTS platform_role_permissions_role_idx;
ALTER TABLE platform.user_roles       DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE platform.role_permissions DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE platform.roles            DROP COLUMN IF EXISTS deleted_at;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0003_od20_tenant_registry"),
    ]

    operations = [
        migrations.RunSQL(EKLE, DUSUR),
    ]
