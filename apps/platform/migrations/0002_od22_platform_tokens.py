# M3 (OD22 kapalı) — `platform.tokens`.
#
# OD22 birebir: ham token SAKLANMAZ (yalnızca SHA-256 özeti, benzersiz); sahip
# (`platform_user_id`) ve `purpose` zorunlu; `scope` boş olamaz ve joker yasak;
# `token_kind` kapalı küme; `expires_at` NOT NULL (**süresiz token yok**);
# `revoked_at` iptal izi; `last_used_at`; **`service` token superadmin izin
# kümesiyle üretilemez** (yetki yükseltme yasağı — tetikleyiciyle).
#
# TTL sınırları DB'de de dayatılır (uygulama katmanı + test ek olarak):
# `interactive` ≤ 24 saat, `service` ≤ 90 gün. Uzun ömürlü/sınırsız SUPERADMIN
# token yasaktır.
#
# Platform tablosu olduğu için kiracı RLS politikalarıyla ilgisi yoktur; token
# **hiçbir koşulda** tenant RLS sınırını aşmaz (platform rolü NOBYPASSRLS).

from django.db import migrations

M3_SQL = """
CREATE TABLE platform.tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_user_id uuid NOT NULL REFERENCES platform.users(id) ON DELETE CASCADE,
    token_kind varchar(16) NOT NULL DEFAULT 'interactive',
    purpose text NOT NULL,
    scope text[] NOT NULL,
    token_hash varchar(64) NOT NULL,
    label varchar(80) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    revoked_reason varchar(32),
    last_used_at timestamptz,
    created_by_id uuid REFERENCES platform.users(id) ON DELETE SET NULL,
    CONSTRAINT platform_tokens_hash_key UNIQUE (token_hash),
    CONSTRAINT platform_tokens_kind_valid
        CHECK (token_kind IN ('interactive', 'service')),
    CONSTRAINT platform_tokens_purpose_min_length
        CHECK (length(btrim(purpose)) >= 10),
    CONSTRAINT platform_tokens_scope_not_empty
        CHECK (cardinality(scope) >= 1),
    CONSTRAINT platform_tokens_scope_no_wildcard
        CHECK (NOT ('*' = ANY (scope))),
    CONSTRAINT platform_tokens_expires_after_created
        CHECK (expires_at > created_at),
    CONSTRAINT platform_tokens_ttl_within_bounds
        CHECK (
            (token_kind = 'interactive'
             AND expires_at <= created_at + interval '24 hours')
            OR (token_kind = 'service'
                AND expires_at <= created_at + interval '90 days')
        ),
    CONSTRAINT platform_tokens_revoked_pair
        CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL)),
    CONSTRAINT platform_tokens_revoked_reason_valid
        CHECK (revoked_reason IS NULL OR revoked_reason IN (
            'logout', 'owner_disabled', 'admin_revoke', 'security_incident',
            'policy_migration', 'role_changed', 'mfa_changed', 'password_changed'
        ))
);

COMMENT ON TABLE platform.tokens IS
    'Platform API token (OD22): yalnizca ozet saklanir; suresiz token yoktur; '
    'service token superadmin izin kumesiyle uretilemez.';

-- İptal/süre taraması için kısmi indeks.
CREATE INDEX platform_tokens_active_idx
    ON platform.tokens (expires_at) WHERE revoked_at IS NULL;

-- Guard: `service` token superadmin izin kümesiyle üretilemez (OD22/6) -------
CREATE OR REPLACE FUNCTION platform.tokens_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    superadmin_izin_sayisi integer;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- Değişmezlik: kapsam/amaç/tür/süre/özet/sahip alanları değişemez.
        IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
           OR NEW.platform_user_id IS DISTINCT FROM OLD.platform_user_id
           OR NEW.scope IS DISTINCT FROM OLD.scope
           OR NEW.purpose IS DISTINCT FROM OLD.purpose
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.token_kind IS DISTINCT FROM OLD.token_kind
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'platform.tokens: kapsam/amac/tur/sure ve sahiplik alanlari degistirilemez (OD22); degisiklik = iptal + yeniden uretim';
        END IF;
        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
            RAISE EXCEPTION
                'platform.tokens: iptal tek yonludur; revoked_at tekrar NULL yapilamaz (OD22)';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.token_kind = 'service' THEN
        SELECT count(*) INTO superadmin_izin_sayisi
          FROM platform.role_permissions rp
          JOIN platform.roles r ON r.id = rp.role_id
         WHERE r.code = 'superadmin';

        IF superadmin_izin_sayisi > 0
           AND NOT EXISTS (
                SELECT 1
                  FROM unnest(NEW.scope) AS s(kod)
                 WHERE s.kod NOT IN (
                        SELECT p.code
                          FROM platform.role_permissions rp
                          JOIN platform.roles r ON r.id = rp.role_id
                          JOIN hr.permissions p ON p.id = rp.permission_id
                         WHERE r.code = 'superadmin'
                 )
           ) THEN
            RAISE EXCEPTION
                'platform.tokens: service token superadmin izin kumesiyle uretilemez (OD22/6)';
        END IF;
    END IF;

    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_platform_tokens_guard ON platform.tokens;
CREATE TRIGGER trg_platform_tokens_guard
    BEFORE INSERT OR UPDATE ON platform.tokens
    FOR EACH ROW EXECUTE FUNCTION platform.tokens_guard();

-- Yetki turu: yalnızca platform rolü --------------------------------------
REVOKE ALL ON platform.tokens FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON platform.tokens FROM persopyro_app;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON platform.tokens TO persopyro_platform;
    END IF;
END
$$;
"""

M3_REVERSE = """
DROP TRIGGER IF EXISTS trg_platform_tokens_guard ON platform.tokens;
DROP FUNCTION IF EXISTS platform.tokens_guard();
DROP TABLE IF EXISTS platform.tokens;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("platform", "0001_od3_platform_users_roles"),
    ]

    operations = [
        migrations.RunSQL(sql=M3_SQL, reverse_sql=M3_REVERSE),
    ]
