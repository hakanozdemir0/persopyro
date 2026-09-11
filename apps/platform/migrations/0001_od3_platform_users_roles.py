# M2 (OD3 kapalı) — platform kullanıcı/rol temeli.
#
# `platform.users`, `platform.roles`, `platform.role_permissions`,
# `platform.user_roles` + `platform.users.public_id` (rezerve `plt` ön eki).
#
# Sınır (OD3): platform kullanıcısı **kiracıya bağlı değildir**; `tenant_id`
# kolonu **yoktur**, kiracı kullanıcı tablosuyla (`hr.users`) birleştirilmez ve
# Django `auth_*` yetki sistemi **kullanılmaz** (`is_staff`/`is_superuser`
# platform yetkisi değildir — OD19).
#
# Yetkiler (M1/M2): şema ve tablolar **yalnızca** platform rolüne açılır;
# `persopyro_app` ve `PUBLIC` hiçbir yetki almaz. Rol oluşturma süper kullanıcı
# gerektirdiği için rol + yetki turu `scripts/03_platform_roles.sql` içindedir;
# bu migration yalnızca şema/tablo/guard tarafını kurar (fresh install'da
# `migrate` tek başına çalışabilsin diye şema burada da oluşturulur).
#
# `platform.users.public_id` biçimi `plt-<8×Crockford>` (OD27/OD28): `plt`
# rezerve listededir, hiçbir kiracı bu ön eki alamaz → ad alanları çakışmaz.

from django.db import migrations

M2_SQL = """
CREATE SCHEMA IF NOT EXISTS platform AUTHORIZATION persopyro_owner;
REVOKE ALL ON SCHEMA platform FROM PUBLIC;

-- Kullanıcılar (SUPERADMIN/ADMIN kimliği) --------------------------------
CREATE TABLE platform.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id varchar(32) NOT NULL DEFAULT ('plt-' || hr.crockford_suffix(8)),
    email varchar(190) NOT NULL,
    full_name varchar(200) NOT NULL DEFAULT '',
    password varchar(255) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'active',
    is_active boolean NOT NULL DEFAULT true,
    failed_login_count integer NOT NULL DEFAULT 0,
    lockout_until timestamptz,
    password_changed_at timestamptz,
    mfa_enabled boolean NOT NULL DEFAULT false,
    mfa_secret_encrypted varchar(255) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    created_by_id uuid REFERENCES platform.users(id) ON DELETE SET NULL,
    CONSTRAINT platform_users_email_key UNIQUE (email),
    CONSTRAINT platform_users_public_id_key UNIQUE (public_id),
    CONSTRAINT platform_users_public_id_format
        CHECK (public_id ~ '^plt-[0-9A-HJKMNP-TV-Z]{8}$'),
    CONSTRAINT platform_users_email_lower CHECK (email = lower(email)),
    CONSTRAINT platform_users_status_valid
        CHECK (status IN ('active', 'inactive', 'archived')),
    CONSTRAINT platform_users_failed_logins_nonneg CHECK (failed_login_count >= 0)
);

COMMENT ON TABLE platform.users IS
    'Platform kimligi (OD3): tenant_id YOKTUR, kiracı kullanicilariyla birlestirilmez, '
    'Django auth_* yetki sistemi kullanilmaz.';

-- Roller (KAPALI küme) ----------------------------------------------------
CREATE TABLE platform.roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(60) NOT NULL,
    name varchar(120) NOT NULL,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_roles_code_key UNIQUE (code),
    CONSTRAINT platform_roles_code_closed CHECK (code IN ('superadmin', 'platform_admin'))
);

COMMENT ON TABLE platform.roles IS
    'Platform rolleri (OD3). Rol kodlari KAPALI kumedir; yeni kod yeni migration gerektirir.';

-- Katalog paylaşılır (hr.permissions, salt okunur); kiracı RBAC'i ile
-- BİRLEŞTİRİLMEZ: bu tablo yalnızca platform rolü -> izin bağını tutar.
CREATE TABLE platform.role_permissions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id uuid NOT NULL REFERENCES platform.roles(id) ON DELETE CASCADE,
    permission_id uuid NOT NULL REFERENCES hr.permissions(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_role_permissions_uniq UNIQUE (role_id, permission_id)
);

-- Kullanıcı -> rol (süreli olabilir) --------------------------------------
CREATE TABLE platform.user_roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    role_id uuid NOT NULL REFERENCES platform.roles(id) ON DELETE CASCADE,
    granted_by_id uuid REFERENCES platform.users(id) ON DELETE SET NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    CONSTRAINT platform_user_roles_uniq UNIQUE (platform_user_id, role_id),
    CONSTRAINT platform_user_roles_expires_valid
        CHECK (expires_at IS NULL OR expires_at > granted_at)
);

-- Rol tanımları (idempotent seed; DEMO KULLANICI BAĞLANMAZ) ---------------
INSERT INTO platform.roles (code, name, description) VALUES
    ('superadmin', 'Platform Süper Yöneticisi',
     'Platform operasyonlarının en yetkili rolü; kapsamı platform.* izinleriyle sinirlidir.'),
    ('platform_admin', 'Platform Yöneticisi',
     'Platform operasyonlarını onay akışıyla yürütür.')
ON CONFLICT (code) DO NOTHING;

-- Yetki turu: PUBLIC ve kiracı runtime rolü bu şemayı GÖREMEZ ------------
REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON SCHEMA platform FROM persopyro_app;
        REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT USAGE ON SCHEMA platform TO persopyro_platform;
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON platform.users, platform.roles, platform.role_permissions,
               platform.user_roles
            TO persopyro_platform;
    END IF;
END
$$;
"""

M2_REVERSE = """
DROP SCHEMA IF EXISTS platform CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        # `hr.crockford_suffix` (OD25/OD28 public ID üreticisi) ve katalog.
        ("tenancy", "0008_od25_public_id_helpers"),
        ("rbac", "0003_seed_permissions"),
    ]

    operations = [
        migrations.RunSQL(sql=M2_SQL, reverse_sql=M2_REVERSE),
    ]
