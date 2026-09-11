-- ============================================================================
-- Persopyro — 01: Roller, veritabanı ve eklentiler
-- ============================================================================
-- Çalıştırılması gereken rol: SÜPER KULLANICI (sistem kümesinde postgres).
-- Tekrar çalıştırılabilir (idempotent).
--
-- Rol ayrımı (docs/ARCHITECTURE.md bölüm 5.3):
--   persopyro_owner : şema/tablo sahibi; YALNIZCA migration için kullanılır.
--   persopyro_app   : uygulama rolü. Tablo sahibi DEĞİL, superuser DEĞİL,
--                     RLS'i atlayamaz. Uygulama bu rolle bağlanır.
-- Bu ayrım zorunludur: PostgreSQL'de tablo sahibi ve superuser RLS'i atlar.
--
-- Bu dosya psql'e özgüdür (\connect). Şema yetkileri ayrı dosyadadır:
--   02_grant_app_privileges.sql (saf SQL; her veritabanında çalıştırılabilir)
-- ============================================================================

\set ON_ERROR_STOP on

-- 1) Roller -----------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_owner') THEN
        CREATE ROLE persopyro_owner LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
        CREATE ROLE persopyro_app LOGIN;
    END IF;
END
$$;

-- Uygulama rolü asla yükseltilmiş yetki taşımaz.
ALTER ROLE persopyro_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE persopyro_owner NOSUPERUSER NOCREATEROLE NOBYPASSRLS;

-- owner: test veritabanlarının oluşturulabilmesi için CREATEDB.
-- (Üretimde testler ayrı kümede/veritabanında çalıştırılır; gerekirse
--  ALTER ROLE persopyro_owner NOCREATEDB; ile kapatılır.)
ALTER ROLE persopyro_owner CREATEDB;

-- 2) Veritabanı -------------------------------------------------------------
SELECT 'CREATE DATABASE persopyro OWNER persopyro_owner ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'persopyro')
\gexec

-- Veritabanı seviyesi: PUBLIC'e hiçbir şey bırakma.
REVOKE ALL ON DATABASE persopyro FROM PUBLIC;
GRANT CONNECT ON DATABASE persopyro TO persopyro_owner, persopyro_app;

-- 3) Veritabanına geç -------------------------------------------------------
\connect persopyro

-- 4) Eklentiler -------------------------------------------------------------
-- pgcrypto: crypt()/digest() için (mevcut tasarımda da kullanılıyor).
-- NOT: uuid-ossp gerekmez; gen_random_uuid() PostgreSQL 13'ten beri çekirdekte.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

\echo 'OK: 01_bootstrap.sql tamamlandi (roller, veritabani, eklentiler)'
\echo '    Sıradaki adım: 02_grant_app_privileges.sql (hr şeması ve yetkiler)'
