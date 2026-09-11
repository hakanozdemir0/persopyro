-- ============================================================================
-- Persopyro — 03: platform şeması, rolü ve yetkileri (OD1/OD3/OD20/OD22)
-- ============================================================================
-- Çalıştırılması gereken rol: SÜPER KULLANICI (CREATE ROLE için zorunlu).
--   Örnek:  sudo -u postgres psql -d persopyro -f scripts/03_platform_roles.sql
-- Tekrar çalıştırılabilir (idempotent).
--
-- Bu dosya SAF SQL'dir (psql'e özgü komut içermez): üretimde, test
-- veritabanında ve CI'da aynı şekilde çalıştırılabilir.
--
-- NEDEN AYRI SCRIPT: `CREATE ROLE` yalnızca süper kullanıcı/CREATEROLE ile
-- yapılabilir; migration rolü (`persopyro_owner`) `NOCREATEROLE`'dur. Şema ve
-- tablolar ise `migrate` ile oluşur (fresh install'da tek başına çalışsın diye);
-- bu betik rolleri ve YETKİ TURUNU uygular.
--
-- GÜVENLİK MODELİ (docs/DATABASE.md B.1/D.4):
--   * `persopyro_app` (kiracı runtime yolu): `platform` şemasında **hiç yetki
--     yok** → kiracı isteğinden platform verisine yol yoktur (test: T17).
--   * `persopyro_platform`: `platform` şemasında tam DML; `hr` şemasında
--     yalnızca **izin kataloğu** (`hr.permissions`, global tanım, RLS yok)
--     okunur. Kiracı tablolarına bu pakette **hiç yetki verilmez** (RLS'e tabi
--     erişim, erişim-onay katmanıyla birlikte ayrı iş kalemidir — şema varlığı
--     erişim yetkisi DEĞİLDİR).
--   * `NOSUPERUSER` + `NOBYPASSRLS`: platform rolü kiracı RLS'ini atlayamaz.
--   * `GRANT ALL ON ALL TABLES IN SCHEMA platform` KULLANILMAZ; yetki turu
--     **tablo bazlı allowlist**tir (aşağıdaki döngü). Yeni bir platform tablosu
--     eklendiğinde bu liste güncellenmelidir (bilinçli: geniş varsayılan yok).
--   * `ALTER DEFAULT PRIVILEGES` platform şeması için **tanımlanmaz** (gelecekte
--     oluşacak tabloların sessizce geniş yetki almasını engellemek için).
-- ============================================================================

-- 1) Platform rolü -----------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_platform') THEN
        CREATE ROLE persopyro_platform LOGIN;
    END IF;
END
$$;

-- Yükseltilmiş yetki YOK: süper kullanıcı değil, RLS'i atlayamaz, rol/DB yaratamaz.
ALTER ROLE persopyro_platform NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

-- NOT: parola bu betikte ATANMAZ (koda/repoya sır yazılmaz). Üretimde:
--   ALTER ROLE persopyro_platform WITH PASSWORD '<secret-store>';

-- 2) Şema --------------------------------------------------------------------
-- Şema migration ile de oluşturulur; burada idempotent olarak garanti edilir.
CREATE SCHEMA IF NOT EXISTS platform AUTHORIZATION persopyro_owner;

REVOKE ALL ON SCHEMA platform FROM PUBLIC;
GRANT USAGE ON SCHEMA platform TO persopyro_platform;

-- Kiracı runtime rolü platform şemasını GÖREMEZ.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        REVOKE ALL ON SCHEMA platform FROM persopyro_app;
    END IF;
END
$$;

-- 3) Tablo bazlı yetki turu (allowlist) --------------------------------------
-- Aşağıdaki liste, platform şemasındaki TÜM nesneleri açıkça sayar.
DO $$
DECLARE
    tablo text;
    platform_tablolari constant text[] := ARRAY[
        'users', 'roles', 'role_permissions', 'user_roles', 'tokens', 'tenant_registry'
    ];
BEGIN
    FOREACH tablo IN ARRAY platform_tablolari LOOP
        IF to_regclass('platform.' || tablo) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON platform.%I FROM PUBLIC', tablo);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON platform.%I TO persopyro_platform',
                tablo
            );
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
                EXECUTE format('REVOKE ALL ON platform.%I FROM persopyro_app', tablo);
            END IF;
        END IF;
    END LOOP;
END
$$;

-- Platform şemasındaki fonksiyonlar: yalnızca platform rolü çalıştırabilir.
DO $$
DECLARE
    fonksiyon record;
BEGIN
    FOR fonksiyon IN
        SELECT p.oid::regprocedure AS imza
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'platform'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC', fonksiyon.imza);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO persopyro_platform', fonksiyon.imza);
    END LOOP;
END
$$;

-- 4) Platform rolünün `hr` şemasındaki yetkileri (asgari) -------------------
-- Yalnızca GLOBAL izin kataloğu okunur (kiracı verisi değildir; RLS uygulanmaz).
-- Kiracı tabloları (`users`, `tenants`, `audit_logs`, ...) için yetki VERİLMEZ:
-- platform → tenant erişimi yalnızca erişim-onay (AccessGrant) katmanıyla ve
-- denetim kaydıyla açılır (OD1/OD4; ayrı iş kalemi).
GRANT USAGE ON SCHEMA hr TO persopyro_platform;
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NOT NULL THEN
        REVOKE ALL ON hr.permissions FROM persopyro_platform;
        GRANT SELECT ON hr.permissions TO persopyro_platform;
    END IF;
END
$$;

-- 5) Kiracı runtime yolunun platform'da yetkisi olmadığını doğrula ----------
-- (Yetki listesi boş olmalı; bu blok yalnızca doğrulama amaçlıdır.)
DO $$
DECLARE
    sizan integer := 0;
    kayit record;
BEGIN
    FOR kayit IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'platform' AND c.relkind = 'r'
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app')
           AND (has_table_privilege('persopyro_app', 'platform.' || kayit.relname, 'SELECT')
                OR has_table_privilege('persopyro_app', 'platform.' || kayit.relname, 'INSERT')
                OR has_table_privilege('persopyro_app', 'platform.' || kayit.relname, 'UPDATE')
                OR has_table_privilege('persopyro_app', 'platform.' || kayit.relname, 'DELETE')) THEN
            sizan := sizan + 1;
        END IF;
    END LOOP;

    IF sizan > 0 THEN
        RAISE EXCEPTION 'GUVENLIK: persopyro_app, platform semasinda % tabloya yetkili!', sizan;
    END IF;
END
$$;
