-- ============================================================================
-- Persopyro — 02: Kiracı izolasyonu (RLS) doğrulaması
-- ============================================================================
-- Çalıştırılması gereken rol: SÜPER KULLANICI.
-- Gerçek hr şemasına dokunmaz; geçici bir "spike" şeması kurar, iddiaları
-- kanıtlar ve şemayı siler. Tekrar çalıştırılabilir.
--
-- Kanıtlanan iddialar:
--   A1  Uygulama rolü superuser değil, RLS'i atlayamıyor, şemada CREATE yok
--   A2  Uygulama rolü tablo sahibi değil
--   A3  RLS etkin ve FORCE etkin
--   A4  Kiracı bağlamı yokken hiçbir satır görünmez (fail-closed)
--   A5  A kiracısı bağlamında yalnızca A kiracısının satırı görünür
--   A6  A kiracısı, B kiracısı adına kayıt YAZAMAZ (WITH CHECK)
--   A7  A kiracısı, B kiracısının satırını GÜNCELLEYEMEZ/SİLEMEZ
--   A8  Kiracı bağlamı işlem sonunda sıfırlanır (kalıcı sızıntı yok)
--   A9  FORCE sayesinde tablo sahibi rol bile kiracı sınırını aşamaz
-- ============================================================================

\set ON_ERROR_STOP on

\echo '--- spike semasi kuruluyor (gercek hr semasina dokunulmaz) ---'

DROP SCHEMA IF EXISTS spike CASCADE;

SET ROLE persopyro_owner;

CREATE SCHEMA spike AUTHORIZATION persopyro_owner;

-- Kiracı bağlamını okuyan yardımcı fonksiyon (gerçek tasarımdaki
-- hr.current_tenant_id() ile aynı mantık).
CREATE FUNCTION spike.current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid
$$;

CREATE TABLE spike.tenant (
    id   uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE spike.record (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES spike.tenant(id),
    note      text NOT NULL
);

-- RLS: etkin + FORCE (FORCE olmadan tablo sahibi politikaları atlar).
ALTER TABLE spike.tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE spike.tenant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spike.tenant
    USING (id = spike.current_tenant_id())
    WITH CHECK (id = spike.current_tenant_id());

ALTER TABLE spike.record ENABLE ROW LEVEL SECURITY;
ALTER TABLE spike.record FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spike.record
    USING (tenant_id = spike.current_tenant_id())
    WITH CHECK (tenant_id = spike.current_tenant_id());

GRANT USAGE ON SCHEMA spike TO persopyro_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA spike TO persopyro_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA spike TO persopyro_app;

-- Tohumlama: RLS FORCE etkin olduğu için sahibi rol bile bağlam kurmadan yazamaz.
DO $$
DECLARE
    t1 uuid := '11111111-1111-1111-1111-111111111111';
    t2 uuid := '22222222-2222-2222-2222-222222222222';
BEGIN
    PERFORM set_config('app.tenant_id', t1::text, true);
    INSERT INTO spike.tenant (id, name) VALUES (t1, 'Kiraci A');
    PERFORM set_config('app.tenant_id', t2::text, true);
    INSERT INTO spike.tenant (id, name) VALUES (t2, 'Kiraci B');
    PERFORM set_config('app.tenant_id', t1::text, true);
    INSERT INTO spike.record (tenant_id, note) VALUES (t1, 'A kaydi');
    PERFORM set_config('app.tenant_id', t2::text, true);
    INSERT INTO spike.record (tenant_id, note) VALUES (t2, 'B kaydi');
END
$$;

\echo '--- iddialar UYGULAMA ROLU ile sinaniyor (SET ROLE persopyro_app) ---'

SET ROLE persopyro_app;

BEGIN;

-- A4: kiracı bağlamı yokken hiçbir satır görünmez.
DO $$
DECLARE n bigint;
BEGIN
    PERFORM set_config('app.tenant_id', '', true);
    SELECT count(*) INTO n FROM spike.record;
    IF n <> 0 THEN
        RAISE EXCEPTION 'BASARISIZ (A4): baglam yokken % kayit gorundu (0 bekleniyordu)', n;
    END IF;
    SELECT count(*) INTO n FROM spike.tenant;
    IF n <> 0 THEN
        RAISE EXCEPTION 'BASARISIZ (A4): baglam yokken % kiraci gorundu (0 bekleniyordu)', n;
    END IF;
END
$$;

-- A5: A bağlamında yalnızca A satırı görünür.
DO $$
DECLARE
    n bigint;
    v_note text;
BEGIN
    PERFORM set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
    SELECT count(*) INTO n FROM spike.record;
    IF n <> 1 THEN
        RAISE EXCEPTION 'BASARISIZ (A5): A baglaminda % kayit gorundu (1 bekleniyordu)', n;
    END IF;
    SELECT note INTO v_note FROM spike.record;
    IF v_note <> 'A kaydi' THEN
        RAISE EXCEPTION 'BASARISIZ (A5): A baglaminda yanlis kayit gorunuyor: %', v_note;
    END IF;
END
$$;

-- A6: başka kiracı adına yazma reddedilir.
DO $$
DECLARE
    engellendi boolean := false;
BEGIN
    PERFORM set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
    BEGIN
        INSERT INTO spike.record (tenant_id, note)
        VALUES ('22222222-2222-2222-2222-222222222222', 'sizma denemesi');
    EXCEPTION WHEN insufficient_privilege THEN
        engellendi := true;
    END;
    IF NOT engellendi THEN
        RAISE EXCEPTION 'BASARISIZ (A6): A kiracisi B kiracisi adina kayit yazabildi';
    END IF;
END
$$;

-- A7: başka kiracının satırı güncellenemez/silinemez.
DO $$
DECLARE etkilenen int;
BEGIN
    PERFORM set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true);
    UPDATE spike.record SET note = 'degistirildi'
     WHERE tenant_id = '22222222-2222-2222-2222-222222222222';
    GET DIAGNOSTICS etkilenen = ROW_COUNT;
    IF etkilenen <> 0 THEN
        RAISE EXCEPTION 'BASARISIZ (A7): B kiracisinin % satiri guncellendi', etkilenen;
    END IF;
    DELETE FROM spike.record
     WHERE tenant_id = '22222222-2222-2222-2222-222222222222';
    GET DIAGNOSTICS etkilenen = ROW_COUNT;
    IF etkilenen <> 0 THEN
        RAISE EXCEPTION 'BASARISIZ (A7): B kiracisinin % satiri silindi', etkilenen;
    END IF;
END
$$;

-- A1/A2: yetki sinirlari.
DO $$
DECLARE r record;
BEGIN
    SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
      INTO r FROM pg_roles WHERE rolname = current_user;
    IF r.rolsuper THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu superuser';
    END IF;
    IF r.rolbypassrls THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu RLS i atlayabiliyor';
    END IF;
    IF r.rolcreatedb THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu veritabani olusturabiliyor';
    END IF;
    IF r.rolcreaterole THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu rol olusturabiliyor';
    END IF;
    IF has_schema_privilege(current_user, 'hr', 'CREATE') THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu hr semasinda CREATE yetkisine sahip';
    END IF;
    IF has_schema_privilege(current_user, 'spike', 'CREATE') THEN
        RAISE EXCEPTION 'BASARISIZ (A1): uygulama rolu spike semasinda CREATE yetkisine sahip';
    END IF;
    IF pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_class
            WHERE oid = 'spike.record'::regclass)) = current_user THEN
        RAISE EXCEPTION 'BASARISIZ (A2): uygulama rolu tablonun sahibi';
    END IF;
    SELECT relrowsecurity, relforcerowsecurity INTO r
      FROM pg_class WHERE oid = 'spike.record'::regclass;
    IF NOT r.relrowsecurity THEN
        RAISE EXCEPTION 'BASARISIZ (A3): RLS etkin degil';
    END IF;
    IF NOT r.relforcerowsecurity THEN
        RAISE EXCEPTION 'BASARISIZ (A3): FORCE RLS etkin degil (sahibi rol RLS i atlar)';
    END IF;
END
$$;

COMMIT;

-- A8: işlem bittiğinde kiracı bağlamı kaybolmalı.
-- NOT (spike bulgusu): işlem sonunda ayar NULL'a değil BOŞ STRING'e döner
-- (PostgreSQL, SET LOCAL ile tanışan özel parametreyi '' değerine döndürür).
-- Bu yüzden tasarımdaki NULLIF(current_setting(...), '') yaklaşımı zorunludur.
-- Ölçüm, politikaların gerçekten kullandığı yardımcı fonksiyon üzerinden yapılır.
DO $$
BEGIN
    IF spike.current_tenant_id() IS NOT NULL THEN
        RAISE EXCEPTION
            'BASARISIZ (A8): kiraci baglami islem sonunda sifirlanmadi (deger: %)',
            current_setting('app.tenant_id', TRUE);
    END IF;
END
$$;

\echo '--- A9: tablo sahibi rolun de siniri asamadigi dogrulaniyor (FORCE RLS) ---'

SET ROLE persopyro_owner;

DO $$
DECLARE n bigint;
BEGIN
    PERFORM set_config('app.tenant_id', '', true);
    SELECT count(*) INTO n FROM spike.record;
    IF n <> 0 THEN
        RAISE EXCEPTION 'BASARISIZ (A9): FORCE RLS sahibi rolu korumadi (% kayit gorundu)', n;
    END IF;
END
$$;

RESET ROLE;

\echo '--- spike semasi temizleniyor ---'
DROP SCHEMA IF EXISTS spike CASCADE;

\echo 'SONUC: KIRACI IZOLASYONU DOGRULAMASI BASARILI (A1-A9)'
