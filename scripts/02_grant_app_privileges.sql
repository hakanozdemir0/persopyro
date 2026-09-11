-- ============================================================================
-- Persopyro — 02: hr şeması ve uygulama rolünün yetkileri
-- ============================================================================
-- Çalıştırılması gereken rol: persopyro_owner (veya süper kullanıcı).
-- Tekrar çalıştırılabilir (idempotent).
--
-- Bu dosya SAF SQL'dir: psql'e özgü komut içermez, böylece
--   * üretimde migration sonrası,
--   * test veritabanında (tests/conftest.py),
--   * herhangi bir istemciyle (psycopg) çalıştırılabilir.
--
-- KURAL: Uygulama rolü (persopyro_app) tablo sahibi olmamalı ve DDL yetkisi
-- taşımamalıdır; aksi halde RLS sessizce etkisiz hale gelir.
-- ============================================================================

-- 1) hr şeması ---------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS hr AUTHORIZATION persopyro_owner;

REVOKE ALL ON SCHEMA hr FROM PUBLIC;
GRANT USAGE ON SCHEMA hr TO persopyro_app;   -- CREATE verilmez: DDL yalnızca migration'da
GRANT CREATE ON SCHEMA hr TO persopyro_owner;

-- public şeması: uygulama oraya nesne koymaz.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- 2) Varsayılan yetkiler ----------------------------------------------------
-- Migration'ların ileride oluşturacağı nesnelere uygulama rolü erişebilsin.
ALTER DEFAULT PRIVILEGES FOR ROLE persopyro_owner IN SCHEMA hr
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO persopyro_app;
ALTER DEFAULT PRIVILEGES FOR ROLE persopyro_owner IN SCHEMA hr
    GRANT USAGE, SELECT ON SEQUENCES TO persopyro_app;
ALTER DEFAULT PRIVILEGES FOR ROLE persopyro_owner IN SCHEMA hr
    GRANT EXECUTE ON FUNCTIONS TO persopyro_app;
-- ENUM/domain tipleri için gerekli: USAGE verilmezse uygulama rolü tipi kullanamaz.
ALTER DEFAULT PRIVILEGES FOR ROLE persopyro_owner IN SCHEMA hr
    GRANT USAGE ON TYPES TO persopyro_app;

-- 3) Mevcut nesnelerin yetkileri --------------------------------------------
-- (İlk kurulumda hr boştur; tekrar çalıştırmalarda mevcut nesneleri kapsar.)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA hr TO persopyro_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA hr TO persopyro_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA hr TO persopyro_app;

-- ENUM/domain tipleri: "ALL TYPES IN SCHEMA" biçimi PostgreSQL'de yoktur,
-- bu yüzden mevcut tipler döngüyle yetkilendirilir.
DO $$
DECLARE t record;
BEGIN
    FOR t IN
        SELECT n.nspname AS schema_name, ty.typname AS type_name
          FROM pg_type ty
          JOIN pg_namespace n ON n.oid = ty.typnamespace
         WHERE n.nspname = 'hr'
           AND ty.typtype IN ('e', 'd')
    LOOP
        EXECUTE format('GRANT USAGE ON TYPE %I.%I TO persopyro_app',
                       t.schema_name, t.type_name);
    END LOOP;
END
$$;

-- 4) Denetim kaydı, dizin ve token koruması ---------------------------------
-- Denetim kaydı yalnızca eklenip okunabilir; UPDATE/DELETE/TRUNCATE verilmez.
DO $$
BEGIN
    IF to_regclass('hr.audit_logs') IS NOT NULL THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON hr.audit_logs FROM persopyro_app;
        GRANT INSERT, SELECT ON hr.audit_logs TO persopyro_app;
    END IF;
END
$$;

-- API token'ları (M30): satır SİLİNMEZ (iptal = `revoked_at` işareti; iptalin izi
-- kalır) ve yalnızca `last_used_at`/`revoked_at`/`revoked_reason` güncellenebilir.
-- Bu blok, yukarıdaki "ALL TABLES" satırından SONRA gelir; aksi halde geniş DML
-- yetkisi bu daraltmayı sessizce geri alırdı (ölçüldü).
DO $$
BEGIN
    IF to_regclass('hr.api_tokens') IS NOT NULL THEN
        REVOKE UPDATE, DELETE, TRUNCATE ON hr.api_tokens FROM persopyro_app;
        GRANT SELECT, INSERT ON hr.api_tokens TO persopyro_app;
        GRANT UPDATE (last_used_at, revoked_at, revoked_reason)
            ON hr.api_tokens TO persopyro_app;
    END IF;
END
$$;

-- 5) Global izin kataloğu (M6) ----------------------------------------------
-- `hr.permissions` **paylaşılan katalogdur** ve salt okunurdur: kiracı runtime yolu
-- kod ekleyemez/güncelleyemez/silemez. "ALL TABLES" satırı geniş DML verdiği için
-- bu daraltma ondan SONRA gelir (ölçüldü: öncesinde app rolü INSERT/UPDATE/DELETE
-- yetkisine sahipti — yetki yükseltme yüzeyi).
-- Aynı kural `apps/rbac/migrations/0004_od3_platform_permissions.py` içinde de vardır.
DO $$
BEGIN
    IF to_regclass('hr.permissions') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
            ON hr.permissions FROM persopyro_app;
        GRANT SELECT ON hr.permissions TO persopyro_app;
    END IF;
END
$$;

-- 6) Django teknik yetki tabloları (OD19 / M22b) ------------------------------
-- `auth_permission`, `auth_group`, `auth_group_permissions` ve `django_admin_log`
-- Persopyro'nun yetki kaynağı DEĞİLDİR; Django Admin kaldırıldı ve uygulama bu
-- tabloları çalışma anında okumaz/yazmaz. Uygulama rolünün TÜM yetkileri geri
-- alınır (`REVOKE ALL`) — "ALL TABLES" satırından SONRA gelir, aksi halde geniş DML
-- yetkisi bu daraltmayı sessizce geri alırdı.
-- Aynı kural `apps/accounts/migrations/0004_od19_is_staff_ve_django_auth_yetkileri.py`
-- içinde de vardır (iki yer bilinçli olarak hizalıdır).
-- NOT: `django_admin_log` yeni kurulumlarda hiç OLUŞMAZ (admin uygulaması yok);
-- bu yüzden her tablo `to_regclass` ile korunur.
DO $$
DECLARE tablo text;
BEGIN
    FOREACH tablo IN ARRAY ARRAY[
        'auth_permission', 'auth_group', 'auth_group_permissions', 'django_admin_log'
    ] LOOP
        IF to_regclass('hr.' || tablo) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON hr.%I FROM persopyro_app', tablo);
        END IF;
    END LOOP;
END
$$;

-- Kiracı dizini (slug/domain eşlemesi) yalnızca OKUNUR. Yazma yetkisi verilmez;
-- dizini tetikleyici (SECURITY DEFINER) günceller. Aksi halde bir kiracı başka
-- kiracının alan adı eşlemesini ele geçirebilirdi.
DO $$
BEGIN
    IF to_regclass('hr.tenant_directory') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON hr.tenant_directory FROM persopyro_app;
        GRANT SELECT ON hr.tenant_directory TO persopyro_app;
    END IF;
END
$$;

-- 7) Organizasyon/çalışan tabloları (M10–M13) --------------------------------
-- OD21: bu tablolarda silme **soft-delete**tir (`deleted_at` + `status`); çalışan,
-- birim, pozisyon ve kişisel veri kayıtları runtime yolunda **fiziksel olarak
-- silinmez** (kişisel veri saklama/silme işlemleri ayrı, denetimli bir yönetsel
-- yoldur). Bu yüzden uygulama rolünden DELETE ve TRUNCATE geri alınır.
-- DİKKAT: Bu blok, "ALL TABLES" satırından SONRA gelir (aksi halde geniş DML
-- yetkisi bu daraltmayı sessizce geri alırdı — api_tokens/permissions dersinde
-- ölçüldü).
-- `employee_personal_data` (K2/K3): SELECT/INSERT/UPDATE verilir ama DELETE
-- VERİLMEZ → ON DELETE CASCADE zinciri de (employees silinemez) tetiklenemez.
DO $$
DECLARE tablo text;
BEGIN
    FOREACH tablo IN ARRAY ARRAY[
        'departments', 'positions', 'employees',
        'employee_personal_data', 'employee_manager'
    ] LOOP
        IF to_regclass('hr.' || tablo) IS NOT NULL THEN
            EXECUTE format(
                'REVOKE DELETE, TRUNCATE ON hr.%I FROM persopyro_app', tablo);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON hr.%I TO persopyro_app', tablo);
        END IF;
    END LOOP;
END
$$;

-- 6) Platform şeması (OD3/M1–M5) --------------------------------------------
-- AYRICALIKLI SINIR: `persopyro_app` (kiracı runtime yolu) platform şemasını
-- GÖREMEZ ve oradaki hiçbir tabloda yetkiye sahip olamaz. Bu bölüm bilinçli
-- olarak **allowlist'tir**: `GRANT ALL ON ALL TABLES IN SCHEMA platform`
-- KULLANILMAZ ve platform için `ALTER DEFAULT PRIVILEGES` TANIMLANMAZ (gelecekte
-- eklenecek platform tabloları sessizce geniş yetki almamalı). Platform rolünün
-- yetkileri `scripts/03_platform_roles.sql` içinde (süper kullanıcı) verilir.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'platform') THEN
        REVOKE ALL ON SCHEMA platform FROM PUBLIC;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'persopyro_app') THEN
            REVOKE ALL ON SCHEMA platform FROM persopyro_app;
            REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM persopyro_app;
            REVOKE ALL ON ALL FUNCTIONS IN SCHEMA platform FROM persopyro_app;
        END IF;
    END IF;
END
$$;
