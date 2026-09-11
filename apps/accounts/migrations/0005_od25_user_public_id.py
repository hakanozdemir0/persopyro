# OD25/OD28 (kapalı) — `hr.users.public_id` (M5, kullanıcı tarafı).
#
# Sıra: kolon **nullable** eklenir → mevcut kullanıcılar için kiracının
# `public_id_prefix`inden üretilir (çakışmada yeniden denenir) → `NOT NULL` →
# **global** benzersiz indeks → format `CHECK` → değişmezlik tetikleyicisi ve
# yeni kullanıcılar için `BEFORE INSERT` atama tetikleyicisi.
#
# `hr.users` FORCE RLS ile korunduğu için geri doldurma **kiracı bağlamı
# içinde** yapılır (kiracı listesi RLS'siz `hr.tenant_directory`'den okunur).
#
# NOT: tekillik fonksiyon içinde garanti edilemez (FORCE RLS nedeniyle fonksiyon
# yalnızca kiracı bağlamındaki satırları görür); gerçek garanti global benzersiz
# indekstir. Çakışma olasılığı: 32^8 ≈ 1.1e12 → pratikte yok; olsa bile INSERT
# fail-closed olarak reddedilir (sessiz çakışma üretilmez).

from django.db import migrations, models


def public_id_geri_doldur(apps, schema_editor) -> None:  # noqa: ANN001, ARG001
    """Mevcut kullanıcılara `public_id` atar (kiracı bağlamı içinde)."""
    with schema_editor.connection.cursor() as cur:
        cur.execute("SELECT tenant_id FROM hr.tenant_directory ORDER BY slug")
        kiraci_idleri = [satir[0] for satir in cur.fetchall()]

        kullanilan: set[str] = set()
        for tenant_id in kiraci_idleri:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            cur.execute(
                "SELECT id, public_id FROM hr.users WHERE public_id IS NOT NULL",
            )
            for _, mevcut in cur.fetchall():
                kullanilan.add(mevcut)

            cur.execute("SELECT id FROM hr.users WHERE public_id IS NULL ORDER BY id")
            eksikler = [satir[0] for satir in cur.fetchall()]

            for user_id in eksikler:
                atanan = None
                for deneme in range(10):
                    cur.execute("SELECT hr.generate_public_id(%s)", [str(tenant_id)])
                    aday = cur.fetchone()[0]
                    if aday is None:
                        raise RuntimeError(
                            "public_id üretilemedi: kiracının public_id_prefix'i yok "
                            f"(tenant={tenant_id}). Önce tenancy 0009 uygulanmalı."
                        )
                    if aday not in kullanilan:
                        atanan = aday
                        break
                if atanan is None:
                    raise RuntimeError(
                        f"public_id üretilemedi (çakışma): kullanıcı {user_id}"
                    )
                cur.execute(
                    "UPDATE hr.users SET public_id = %s WHERE id = %s",
                    [atanan, str(user_id)],
                )
                kullanilan.add(atanan)


KISITLAR = """
ALTER TABLE hr.users
    ADD CONSTRAINT users_public_id_format
        CHECK (public_id ~ '^[a-z0-9]{3,20}-[0-9A-HJKMNP-TV-Z]{8}$');

-- Global benzersizlik (OD2/OD25): public ID tüm sistemde tekil.
CREATE UNIQUE INDEX users_public_id_uniq_idx ON hr.users (public_id);

-- Yeni kullanıcıda otomatik üretim ------------------------------------------
CREATE OR REPLACE FUNCTION hr.users_public_id_assign()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
BEGIN
    IF NEW.public_id IS NULL OR btrim(NEW.public_id) = '' THEN
        NEW.public_id := hr.generate_public_id(NEW.tenant_id);
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_users_public_id_assign ON hr.users;
CREATE TRIGGER trg_users_public_id_assign
    BEFORE INSERT ON hr.users
    FOR EACH ROW EXECUTE FUNCTION hr.users_public_id_assign();

-- Değişmezlik (OD25/3): public_id bir kez atandıktan sonra DEĞİŞMEZ ----------
CREATE OR REPLACE FUNCTION hr.users_public_id_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = hr, pg_temp
AS $$
BEGIN
    IF OLD.public_id IS NOT NULL
       AND NEW.public_id IS DISTINCT FROM OLD.public_id THEN
        RAISE EXCEPTION 'hr.users: public_id degistirilemez (OD25/3)';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_users_public_id_guard ON hr.users;
CREATE TRIGGER trg_users_public_id_guard
    BEFORE UPDATE ON hr.users
    FOR EACH ROW EXECUTE FUNCTION hr.users_public_id_guard();
"""

KISITLAR_REVERSE = """
DROP TRIGGER IF EXISTS trg_users_public_id_guard ON hr.users;
DROP FUNCTION IF EXISTS hr.users_public_id_guard();
DROP TRIGGER IF EXISTS trg_users_public_id_assign ON hr.users;
DROP FUNCTION IF EXISTS hr.users_public_id_assign();
DROP INDEX IF EXISTS hr.users_public_id_uniq_idx;
ALTER TABLE hr.users DROP CONSTRAINT IF EXISTS users_public_id_format;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_od19_is_staff_ve_django_auth_yetkileri"),
        ("tenancy", "0009_od25_public_id_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="public_id",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "OD25: <kiracı-prefix>-<8×Crockford>; DB tetikleyicisi üretir, "
                    "sonradan değiştirilemez."
                ),
                max_length=32,
                null=True,
                verbose_name="public ID",
            ),
        ),
        migrations.RunPython(public_id_geri_doldur, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="public_id",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    "OD25: <kiracı-prefix>-<8×Crockford>; DB tetikleyicisi üretir, "
                    "sonradan değiştirilemez."
                ),
                max_length=32,
                verbose_name="public ID",
            ),
        ),
        migrations.RunSQL(sql=KISITLAR, reverse_sql=KISITLAR_REVERSE),
    ]
