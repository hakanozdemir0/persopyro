# Tenant API token modeli (OD26 — kapalı; bulgu F8) — ELLE yazılmış migration.
#
# Otomatik üretilen kısım (kolonlar + Django CHECK'leri) `makemigrations` çıktısıyla
# aynıdır; ek olarak OD26'nın VERİTABANI seviyesinde dayattığı şu maddeler ham SQL
# ile uygulanır (Django bunları ifade edemez):
#
#   * F10 veri göçü: kapsamsız mevcut token'lar **iptal edilir** — "tüm izinler"
#     sayılmaz (fail-closed). Sentineller: `scopes='{}'`,
#     `purpose='policy_migration (kapsamsız dönem)'`, `revoked_reason='policy_migration'`.
#   * M29 ek CHECK'ler: `purpose` en az 10 karakter, dolu kapsam (yalnızca iptal
#     edilmiş satırda boş olabilir), joker (`*`) yasak.
#   * M29 iki tetikleyici: (a) kapsam doğrulama (katalog + `platform.*` yasağı +
#     tekrar yasağı), (b) değişmezlik + **tek yönlü iptal**.
#   * M30 yetki turu: uygulama rolü `api_tokens` üzerinde UPDATE'i yalnızca
#     `last_used_at`/`revoked_at`/`revoked_reason` ile yapabilir; DELETE yetkisi
#     geri alınır (silme yolu kaldırıldı — iptal izi korunur).
#
# Uygulanmış migration dosyaları DEĞİŞTİRİLMEDİ; bu yeni bir migration'dır.
# Aynı yetki turu `scripts/02_grant_app_privileges.sql` içinde de tutulur: o betik
# migrate sonrası yeniden çalıştığı için aksi halde tüm tablolara verilen geniş DML
# yetkisi bu daraltmayı sessizce geri alırdı (ölçüldü).

import django.contrib.postgres.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# F10 — mevcut (kapsamsız) token'ların iptali. Kapsamsız token HİÇBİR koşulda
# "tüm izinler" sayılmaz; kullanıcı yeniden giriş yapıp kapsamlı token üretir.
F10_DATA_MIGRATION = """
UPDATE hr.api_tokens
   SET revoked_at      = now(),
       revoked_reason  = 'policy_migration',
       scopes          = '{}',
       purpose         = CASE
                             WHEN length(btrim(purpose)) >= 10 THEN purpose
                             ELSE 'policy_migration (kapsamsız dönem)'
                         END
 WHERE revoked_at IS NULL;
"""

# M29 — veritabanı seviyesinde ek CHECK'ler (Django'nun ifade edemedikleri).
M29_CHECKS = """
ALTER TABLE hr.api_tokens
    ADD CONSTRAINT api_tokens_purpose_min_length
        CHECK (length(btrim(purpose)) >= 10);
ALTER TABLE hr.api_tokens
    ADD CONSTRAINT api_tokens_scopes_not_empty
        CHECK (cardinality(scopes) >= 1 OR revoked_at IS NOT NULL);
ALTER TABLE hr.api_tokens
    ADD CONSTRAINT api_tokens_scopes_no_wildcard
        CHECK (NOT ('*' = ANY (scopes)));
"""

# M29 — iki tetikleyici: (a) kapsam doğrulama, (b) değişmezlik + tek yönlü iptal.
#
# NEDEN VERİTABANI: uygulama katmanı atlanabilir (yönetim kabuğu, elle SQL, göç
# betiği). Kapsam ve iptal tek yönlülüğü, "yetki kayması ve kanıtsız iptal olmaz"
# kuralının son savunma hattıdır. Kapsam doğrulaması YALNIZCA aktif satırlarda
# çalışır: iptal edilmiş satır sentinel taşır (`scopes='{}'`).
M29_TRIGGERS = """
CREATE OR REPLACE FUNCTION hr.api_tokens_guard() RETURNS trigger AS $$
DECLARE
    kod text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.token_hash IS DISTINCT FROM OLD.token_hash
           OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
           OR NEW.user_id  IS DISTINCT FROM OLD.user_id
           OR NEW.scopes   IS DISTINCT FROM OLD.scopes
           OR NEW.purpose  IS DISTINCT FROM OLD.purpose
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.token_kind IS DISTINCT FROM OLD.token_kind
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION
                'api_tokens: kapsam/amac/tur/sure ve sahiplik alanlari degistirilemez (OD26); degisiklik = iptal + yeniden uretim';
        END IF;

        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
            RAISE EXCEPTION
                'api_tokens: iptal tek yonludur; revoked_at tekrar NULL yapilamaz (OD26)';
        END IF;
    END IF;

    IF NEW.revoked_at IS NULL THEN
        IF NEW.scopes IS NULL OR cardinality(NEW.scopes) < 1 THEN
            RAISE EXCEPTION 'api_tokens: en az bir kapsam (scope) zorunludur (OD26)';
        END IF;

        IF (SELECT count(*) FROM unnest(NEW.scopes) AS s(kod))
           <> (SELECT count(DISTINCT s.kod) FROM unnest(NEW.scopes) AS s(kod)) THEN
            RAISE EXCEPTION 'api_tokens: ayni kapsam birden fazla verilemez (OD26)';
        END IF;

        FOREACH kod IN ARRAY NEW.scopes LOOP
            IF kod IS NULL OR btrim(kod) = '' THEN
                RAISE EXCEPTION 'api_tokens: bos kapsam kabul edilmez (OD26)';
            END IF;
            IF kod LIKE 'platform.%' THEN
                RAISE EXCEPTION
                    'api_tokens: platform.* ad alani kiracı tokeninda yasaktir (OD26): %', kod;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM hr.permissions p WHERE p.code = kod) THEN
                RAISE EXCEPTION 'api_tokens: kapsam izin katalogunda yok (OD26): %', kod;
            END IF;
        END LOOP;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS api_tokens_guard_ins ON hr.api_tokens;
CREATE TRIGGER api_tokens_guard_ins
    BEFORE INSERT OR UPDATE ON hr.api_tokens
    FOR EACH ROW EXECUTE FUNCTION hr.api_tokens_guard();
"""

M29_TRIGGERS_REVERSE = """
DROP TRIGGER IF EXISTS api_tokens_guard_ins ON hr.api_tokens;
DROP FUNCTION IF EXISTS hr.api_tokens_guard();
"""

M29_CHECKS_REVERSE = """
ALTER TABLE hr.api_tokens DROP CONSTRAINT IF EXISTS api_tokens_scopes_no_wildcard;
ALTER TABLE hr.api_tokens DROP CONSTRAINT IF EXISTS api_tokens_scopes_not_empty;
ALTER TABLE hr.api_tokens DROP CONSTRAINT IF EXISTS api_tokens_purpose_min_length;
"""

# M30 — yetki turu (uygulama rolü): UPDATE yalnızca izinli üç kolonda; DELETE yok.
M30_GRANTS = """
REVOKE UPDATE, DELETE, TRUNCATE ON hr.api_tokens FROM "{role}";
GRANT SELECT, INSERT ON hr.api_tokens TO "{role}";
GRANT UPDATE (last_used_at, revoked_at, revoked_reason) ON hr.api_tokens TO "{role}";
"""

M30_GRANTS_REVERSE = """
REVOKE SELECT, INSERT, UPDATE ON hr.api_tokens FROM "{role}";
GRANT SELECT, INSERT, UPDATE, DELETE ON hr.api_tokens TO "{role}";
"""


def uygula_f10(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """F10 veri göçü: mevcut token'ları iptal et (kapsamsız dönem kapanır)."""
    with schema_editor.connection.cursor() as cur:
        cur.execute(F10_DATA_MIGRATION)


def yetki_turu(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """M30: uygulama rolünün `api_tokens` üzerindeki yazma yüzeyini daralt."""
    rol = schema_editor.connection.ops.quote_name(settings.APP_DB_ROLE)
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            'REVOKE UPDATE, DELETE, TRUNCATE ON hr.api_tokens FROM {}'.format(rol)
        )
        cur.execute('GRANT SELECT, INSERT ON hr.api_tokens TO {}'.format(rol))
        cur.execute(
            "GRANT UPDATE (last_used_at, revoked_at, revoked_reason) "
            "ON hr.api_tokens TO {}".format(rol)
        )


def yetki_turu_reverse(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    rol = schema_editor.connection.ops.quote_name(settings.APP_DB_ROLE)
    with schema_editor.connection.cursor() as cur:
        cur.execute('REVOKE SELECT, INSERT, UPDATE ON hr.api_tokens FROM {}'.format(rol))
        cur.execute(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON hr.api_tokens TO {}'.format(rol)
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_apitoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="iptal zamanı"),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="revoked_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("logout", "Çıkış"),
                    ("owner_disabled", "Sahibi pasif/silinmiş"),
                    ("tenant_disabled", "Kiracı askıda/iptal/silinmiş"),
                    ("admin_revoke", "Yönetici iptali"),
                    ("security_incident", "Güvenlik olayı"),
                    ("policy_migration", "Politika göçü (kapsamsız dönem)"),
                ],
                max_length=32,
                null=True,
                verbose_name="iptal nedeni",
            ),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="token_kind",
            field=models.CharField(
                choices=[("interactive", "Etkileşimli (insan)"), ("service", "Servis/entegrasyon")],
                default="interactive",
                max_length=16,
                verbose_name="token türü",
            ),
        ),
        migrations.AddField(
            model_name="apitoken",
            name="purpose",
            field=models.TextField(verbose_name="amaç/gerekçe", default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="apitoken",
            name="scopes",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=120),
                default=list,
                help_text="Sahibinin izinlerinin ALT KÜMESİ; boş olamaz (iptal edilmiş satır hariç).",
                size=None,
                verbose_name="izin kapsamı",
            ),
        ),
        # F10 — zorunlu alanlar ve CHECK'ler kurulmadan ÖNCE var olan satırlar iptal edilir.
        migrations.RunPython(uygula_f10, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="apitoken",
            constraint=models.CheckConstraint(
                condition=models.Q(("token_kind__in", ["interactive", "service"])),
                name="api_tokens_kind_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="apitoken",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("revoked_at__isnull", True), ("revoked_reason__isnull", True)),
                    models.Q(("revoked_at__isnull", False), ("revoked_reason__isnull", False)),
                    _connector="OR",
                ),
                name="api_tokens_revoked_pair",
            ),
        ),
        migrations.AddConstraint(
            model_name="apitoken",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("revoked_reason__isnull", True),
                    ("revoked_reason__in", [
                        "logout",
                        "owner_disabled",
                        "tenant_disabled",
                        "admin_revoke",
                        "security_incident",
                        "policy_migration",
                    ]),
                    _connector="OR",
                ),
                name="api_tokens_revoked_reason_valid",
            ),
        ),
        migrations.RunSQL(
            sql=M29_CHECKS,
            reverse_sql=M29_CHECKS_REVERSE,
        ),
        migrations.RunSQL(
            sql=M29_TRIGGERS,
            reverse_sql=M29_TRIGGERS_REVERSE,
        ),
        migrations.RunPython(yetki_turu, yetki_turu_reverse),
    ]
