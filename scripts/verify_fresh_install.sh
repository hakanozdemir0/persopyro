#!/usr/bin/env bash
# ============================================================================
# Persopyro — SIFIRDAN KURULUM DOĞRULAMASI (disposable/geçici veritabanı)
# ============================================================================
# Ne yapar: gerçek geliştirme/üretim veritabanına DOKUNMADAN geçici bir veritabanı
# kurar, migration'ları sıfırdan uygular, yetkileri ölçer, gerçek HTTP ile sınar,
# tam test suite'i o veritabanının test kopyasında çalıştırır ve **her durumda**
# geçici veritabanını/process'i temizler.
#
# Çalıştırma:  bash scripts/verify_fresh_install.sh
#
# Ortam değişkenleri:
#   VERIFY_DB_PREFIX (persopyro_verify_)  geçici DB adı ön eki
#   VERIFY_PORT      (8013)               geçici runserver portu
#   SUPER_USER_CMD   ("sudo -u postgres") platform rolü adımı için süper kullanıcı
#                                         komutu; boş verilirse o adım atlanır
#   PYTHON           (proje .venv'i)      kullanılacak python
#   SKIP_TESTS       ("0")                "1" ise tam test suite atlanır (hızlı sürüm)
#   KEEP_DB          ("0")                "1" ise geçici DB silinmez (hata ayıklama)
#
# GÜVENLİK: bu betik yalnızca `${VERIFY_DB_PREFIX}*` desenine uyan veritabanlarını
# oluşturur/siler; `.env` içindeki `POSTGRES_DB` hedefi asla ezilmez ve silinmez.
# Django komutlarına hedef veritabanı **açıkça** verilir (DJANGO_ENV_FILE=/dev/null).
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DB_PREFIX="${VERIFY_DB_PREFIX:-persopyro_verify_}"
VERIFY_PORT="${VERIFY_PORT:-8013}"
KEEP_DB="${KEEP_DB:-0}"
SKIP_TESTS="${SKIP_TESTS:-0}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"

BOLD=""; RESET=""
if [ -t 1 ]; then BOLD="$(printf '\033[1m')"; RESET="$(printf '\033[0m')"; fi

adim() { printf '\n%s== %s ==%s\n' "$BOLD" "$1" "$RESET"; }
bilgi() { printf '    %s\n' "$1"; }

# --- Ortam ----------------------------------------------------------------
[ -f .env ] || { echo "HATA: .env bulunamadı; önce 'bash scripts/bootstrap.sh'." >&2; exit 1; }

set -a
# shellcheck disable=SC1091
. ./.env
set +a

PG_HOST="${POSTGRES_HOST:-127.0.0.1}"
PG_PORT="${POSTGRES_PORT:-5432}"
OWNER_ROLE="${POSTGRES_OWNER_USER:?POSTGRES_OWNER_USER tanımlı değil}"
OWNER_PASS="${POSTGRES_OWNER_PASSWORD:?POSTGRES_OWNER_PASSWORD tanımlı değil}"
APP_ROLE="${POSTGRES_USER:?POSTGRES_USER tanımlı değil}"
APP_PASS="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD tanımlı değil}"
PLATFORM_ROLE="${PLATFORM_ROLE:-persopyro_platform}"
CANLI_DB="${POSTGRES_DB:?POSTGRES_DB tanımlı değil}"

# Taşınabilirlik: sanal ortam varsa onu, yoksa PATH'teki python'u kullan.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    if [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="python3"; fi
fi
command -v psql >/dev/null 2>&1 || { echo "HATA: psql bulunamadı." >&2; exit 1; }

FRESH_DB="${DB_PREFIX}$(date -u +%Y%m%d%H%M%S)_$RANDOM"
TEST_DB="test_${FRESH_DB}"
ENVFILE=""
SUNUCU_PID=""
SMOKE_LOG="$(mktemp "${TMPDIR:-/tmp}/persopyro_verify_smoke.XXXXXX.log")"
PSQL_ROOT=(psql -h "$PG_HOST" -p "$PG_PORT" -U "$OWNER_ROLE" -v ON_ERROR_STOP=1 -q)

# --- Güvenlik kalkanı ------------------------------------------------------
hedef_db_dogrula() {  # hedef_db_dogrula <dbname>
    local db="$1"
    case "$db" in
        "${DB_PREFIX}"*) ;;
        *) echo "HATA: '$db' bu betiğin hedef desenine uymuyor (güvenlik)." >&2; exit 1 ;;
    esac
    if [ "$db" = "$CANLI_DB" ]; then
        echo "HATA: canlı veritabanı ($CANLI_DB) hedef alınamaz." >&2
        exit 1
    fi
}
hedef_db_dogrula "$FRESH_DB"

# --- Temizlik (hata olsa da çalışır) ---------------------------------------
temizlik() {
    local kod=$?
    local deneme kalan
    set +e
    if [ -n "$SUNUCU_PID" ]; then
        kill "$SUNUCU_PID" 2>/dev/null
        wait "$SUNUCU_PID" 2>/dev/null
    fi
    # Yedek kalkan: sarmalayıcı fonksiyon yüzünden PID kayması olursa, geçici
    # portta hâlâ dinleyen runserver kalmış olabilir (benzersiz port → güvenli).
    pkill -f "runserver 127.0.0.1:${VERIFY_PORT} --noreload" 2>/dev/null
    sleep 1
    if [ "$KEEP_DB" = "1" ]; then
        echo "UYARI: KEEP_DB=1 → geçici veritabanları silinmedi: $FRESH_DB / $TEST_DB" >&2
    else
        # Bağlantılar kapanana kadar kısa bir yeniden deneme (WITH FORCE yalnızca
        # kendi rolümüzün süreçlerini sonlandırabilir).
        for deneme in 1 2 3 4 5; do
            PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d postgres \
                -c "DROP DATABASE IF EXISTS \"$TEST_DB\" WITH (FORCE)" >/dev/null 2>&1
            PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d postgres \
                -c "DROP DATABASE IF EXISTS \"$FRESH_DB\" WITH (FORCE)" >/dev/null 2>&1
            kalan="$(PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d postgres -tAc \
                "SELECT count(*) FROM pg_database WHERE datname LIKE '${DB_PREFIX}%'")"
            [ "${kalan:-1}" = "0" ] && break
            sleep 2
        done
        echo
        echo "Temizlik: kalan geçici veritabanı = ${kalan:-?} (0 beklenir)"
    fi
    if [ "$kod" != "0" ] && [ -s "$SMOKE_LOG" ]; then
        echo "--- runserver günlüğü (son 20 satır) ---" >&2
        tail -20 "$SMOKE_LOG" >&2
    fi
    [ -n "$ENVFILE" ] && rm -f "$ENVFILE"
    [ -n "$SMOKE_LOG" ] && rm -f "$SMOKE_LOG"
    exit "$kod"
}
trap temizlik EXIT

# --- Hedefi AÇIKÇA veren Django sarmalayıcısı ------------------------------
# `.env` POSTGRES_DB'yi ihraç ettiği için "DJANGO_ENV_FILE=/dev/null + açık env"
# kullanılır; aksi halde komut sessizce CANLI veritabanına gidebilir (ölçüldü).
SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
ENCRYPTION="$(python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
DEMO_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"

django_owner() {
    DJANGO_ENV_FILE=/dev/null DJANGO_SECRET_KEY="$SECRET" DJANGO_DEBUG=1 \
        DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1" \
        POSTGRES_DB="$FRESH_DB" POSTGRES_USER="$OWNER_ROLE" POSTGRES_PASSWORD="$OWNER_PASS" \
        POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" \
        FIELD_ENCRYPTION_KEY="$ENCRYPTION" DEMO_PASSWORD="$DEMO_PASSWORD" \
        "$PYTHON" "$@"
}

django_app_server() {
    # `exec`: arka planda çalıştırıldığında `$!` GERÇEK python sürecinin PID'i
    # olsun (sarmalayıcı alt kabuk değil) — temizlik bunu varsayar.
    exec env DJANGO_ENV_FILE=/dev/null DJANGO_SECRET_KEY="$SECRET" DJANGO_DEBUG=1 \
        DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1" \
        POSTGRES_DB="$FRESH_DB" POSTGRES_USER="$APP_ROLE" POSTGRES_PASSWORD="$APP_PASS" \
        POSTGRES_HOST="$PG_HOST" POSTGRES_PORT="$PG_PORT" \
        FIELD_ENCRYPTION_KEY="$ENCRYPTION" \
        "$PYTHON" "$@"
}

# --- Kontrol yardımcıları ---------------------------------------------------
HATA_VAR=0
bekle() {  # bekle <açıklama> <gerçek> <beklenen>
    if [ "$2" = "$3" ]; then
        bilgi "OK   $1 = $2"
    else
        bilgi "FAIL $1 = $2 (beklenen $3)"
        HATA_VAR=1
    fi
}

sql_deger() {  # sql_deger <dbname> <sql>
    PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d "$1" -tAc "$2"
}

# `hr.*` FORCE RLS'tir: kiracı verisi okuyan ölçümler önce bağlam kurmalı (tek işlemde).
sql_demo() {  # sql_demo <sql>  — demo kiracısı bağlamında çalıştırır
    sql_deger "$FRESH_DB" \
        "SELECT set_config('app.tenant_id', (SELECT tenant_id::text FROM hr.tenant_directory WHERE slug='demo'), true); $1" |
        tail -1
}

kontrol_metni() {  # kontrol_metni <açıklama> <çıktı> <beklenen parça>
    case "$2" in
        *"$3"*) bilgi "OK   $1" ;;
        *) bilgi "FAIL $1 (beklenen içerik: '$3')"; HATA_VAR=1 ;;
    esac
}

echo "Geçici veritabanı : $FRESH_DB (canlı veritabanı: $CANLI_DB — dokunulmaz)"
echo "Roller            : owner=$OWNER_ROLE app=$APP_ROLE platform=$PLATFORM_ROLE"

# ============================================================================
adim "0/8 Geçici veritabanı oluşturuluyor"
PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d postgres \
    -c "CREATE DATABASE \"$FRESH_DB\" OWNER \"$OWNER_ROLE\" ENCODING 'UTF8'"
PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d "$FRESH_DB" \
    -c "CREATE EXTENSION IF NOT EXISTS pgcrypto" \
    -c "CREATE SCHEMA IF NOT EXISTS hr AUTHORIZATION $OWNER_ROLE"
bilgi "oluşturuldu"

adim "1/8 Migration'lar SIFIRDAN uygulanıyor (rol: $OWNER_ROLE)"
django_owner manage.py migrate 2>&1 | tail -4

adim "2/8 Cache tablosu + yetki scriptleri"
django_owner manage.py createcachetable
PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d "$FRESH_DB" \
    -f scripts/02_grant_app_privileges.sql >/dev/null
bilgi "02_grant_app_privileges.sql uygulandı"

PLATFORM_ADIM="atlandı (rol yok)"
if PGPASSWORD="$OWNER_PASS" "${PSQL_ROOT[@]}" -d "$FRESH_DB" -tAc \
        "SELECT 1 FROM pg_roles WHERE rolname = '$PLATFORM_ROLE'" | grep -q 1; then
    PLATFORM_ADIM="rol mevcut"
else
    bilgi "UYARI: '$PLATFORM_ROLE' rolü yok → platform yetki adımı atlanıyor."
    bilgi "       Oluşturmak için (süper kullanıcı): psql -d $FRESH_DB -f scripts/03_platform_roles.sql"
fi
if [ -n "${SUPER_USER_CMD-sudo -u postgres}" ] && [ -f scripts/03_platform_roles.sql ]; then
    read -r -a SUPER <<< "${SUPER_USER_CMD-sudo -u postgres}"
    if "${SUPER[@]}" psql -d "$FRESH_DB" -q -v ON_ERROR_STOP=1 \
            -f scripts/03_platform_roles.sql >/dev/null 2>&1; then
        PLATFORM_ADIM="rol + yetkiler (03_platform_roles.sql)"
    else
        bilgi "UYARI: '$PLATFORM_ROLE' rolü oluşturulamadı (süper kullanıcı erişimi yok)."
        bilgi "       Elle: sudo -u postgres psql -d <db> -f scripts/03_platform_roles.sql"
    fi
fi
bilgi "platform rol adımı: $PLATFORM_ADIM"

adim "3/8 Şema ve yetki ölçümü"
HAYIR="hayir"
VAR="var"
tablo_var() {  # tablo_var <dbname> <şema.tablo>
    local v
    v="$(sql_deger "$1" "SELECT to_regclass('$2') IS NOT NULL")"
    [ "$v" = "t" ] && echo "$VAR" || echo "$HAYIR"
}

# OD19: Django teknik tabloları (app rolünde hiç yetki olmamalı; SELECT dahil)
for t in auth_permission auth_group auth_group_permissions django_admin_log; do
    if [ "$(tablo_var "$FRESH_DB" "hr.$t")" = "$VAR" ]; then
        for y in SELECT INSERT UPDATE DELETE TRUNCATE; do
            bekle "app rolü hr.$t $y" \
                "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.$t','$y')")" "f"
        done
    else
        bilgi "BİLGİ hr.$t yok (admin uygulaması kurulu değil — OD19 beklenen sonuç)"
    fi
done

# M30: tenant token tablosu daraltması
if [ "$(tablo_var "$FRESH_DB" "hr.api_tokens")" = "$VAR" ]; then
    bekle "app rolü hr.api_tokens tablo UPDATE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.api_tokens','UPDATE')")" "f"
    bekle "app rolü hr.api_tokens DELETE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.api_tokens','DELETE')")" "f"
    bekle "app rolü hr.api_tokens(last_used_at) UPDATE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_column_privilege('$APP_ROLE','hr.api_tokens','last_used_at','UPDATE')")" "t"
fi

# Platform şeması: uygulama rolü GÖREMEZ (şema yetkisi yok)
if [ "$(sql_deger "$FRESH_DB" "SELECT to_regnamespace('platform') IS NOT NULL")" = "t" ]; then
    bekle "app rolü platform şema USAGE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_schema_privilege('$APP_ROLE','platform','USAGE')")" "f"
    for t in users roles role_permissions user_roles tokens tenant_registry; do
        if [ "$(tablo_var "$FRESH_DB" "platform.$t")" = "$VAR" ]; then
            for y in SELECT INSERT UPDATE DELETE; do
                bekle "app rolü platform.$t $y" \
                    "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','platform.$t','$y')")" "f"
            done
        fi
    done
fi

# RLS değişmedi mi (tenant tabloları: FORCE + politika)
for t in tenants users roles user_roles user_permission_overrides audit_logs role_permissions; do
    bekle "hr.$t FORCE RLS" \
        "$(sql_deger "$FRESH_DB" "SELECT relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relname='$t'")" "t"
    bekle "hr.$t tenant_isolation politikası" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_policies WHERE schemaname='hr' AND tablename='$t' AND policyname='tenant_isolation'")" "1"
done

adim "4/8 Platform rolü ölçümü"
if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ]; then
    bekle "platform rolü SUPERUSER değil" \
        "$(sql_deger "$FRESH_DB" "SELECT rolsuper FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" "f"
    bekle "platform rolü BYPASSRLS değil" \
        "$(sql_deger "$FRESH_DB" "SELECT rolbypassrls FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" "f"
    bekle "platform rolü platform şema USAGE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_schema_privilege('$PLATFORM_ROLE','platform','USAGE')")" "t"
    bekle "platform rolü RLS'i atlayamaz (hr.tenants)" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.tenants','SELECT')")" "f"
    for t in users roles role_permissions user_roles tokens tenant_registry; do
        if [ "$(tablo_var "$FRESH_DB" "platform.$t")" = "$VAR" ]; then
            bekle "platform rolü platform.$t SELECT" \
                "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','platform.$t','SELECT')")" "t"
        fi
    done
else
    bilgi "ATLANDI: '$PLATFORM_ROLE' rolü yok → platform rol ölçümü yapılamadı."
fi

adim "4b/8 M6-M8 platform yetkilendirme durumu (katalog, roller, seed)"
if [ "$(tablo_var "$FRESH_DB" "hr.permissions")" = "$VAR" ]; then
    bekle "platform izin kodları (6)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions WHERE code LIKE 'platform.%'")" "6"
    bekle "app rolü hr.permissions SALT OKUNUR (M6)" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.permissions','INSERT')")" "f"
    bekle "app rolü hr.permissions SELECT" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.permissions','SELECT')")" "t"
fi
if [ "$(tablo_var "$FRESH_DB" "platform.role_permissions")" = "$VAR" ]; then
    bekle "superadmin izin bağı (6)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM platform.role_permissions rp JOIN platform.roles r ON r.id = rp.role_id WHERE r.code = 'superadmin'")" "6"
    bekle "platform_admin izin bağı (4)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM platform.role_permissions rp JOIN platform.roles r ON r.id = rp.role_id WHERE r.code = 'platform_admin'")" "4"
    bekle "platform_admin rol atama izni YOK (self-elevation)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM platform.role_permissions rp JOIN platform.roles r ON r.id = rp.role_id JOIN hr.permissions p ON p.id = rp.permission_id WHERE r.code = 'platform_admin' AND p.code = 'platform.role.assign'")" "0"
fi
if [ "$(tablo_var "$FRESH_DB" "platform.users")" = "$VAR" ]; then
    bekle "seed edilmiş platform kullanıcısı yok (M9)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM platform.users")" "0"
fi
if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ] \
    && [ "$(tablo_var "$FRESH_DB" "hr.permissions")" = "$VAR" ]; then
    bekle "platform rolü hr.permissions SELECT (allowlist)" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.permissions','SELECT')")" "t"
    bekle "platform rolü hr.permissions yazamaz" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.permissions','INSERT')")" "f"
fi

adim "5/8 manage.py check + makemigrations --check + denetim komutları"
kontrol_metni "manage.py check" "$(django_owner manage.py check 2>&1 || true)" "no issues"
kontrol_metni "makemigrations --check" \
    "$(django_owner manage.py makemigrations --check --dry-run 2>&1 || true)" "No changes detected"
kontrol_metni "audit_is_staff" "$(django_owner manage.py audit_is_staff 2>&1 || true)" "invariantı sağlanıyor"
if [ "$(tablo_var "$FRESH_DB" "hr.users")" = "$VAR" ]; then
    bekle "hr.users.public_id format kısıtı" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.users'::regclass AND conname='users_public_id_format'")" "1"
fi
if [ "$(tablo_var "$FRESH_DB" "hr.tenants")" = "$VAR" ]; then
    bekle "hr.tenants.public_id_prefix format kısıtı" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.tenants'::regclass AND conname='tenants_public_id_prefix_format'")" "1"
fi

adim "6/8 Demo verisi + gerçek HTTP (roller: app)"
django_owner manage.py shell < scripts/_demo_data.py | tail -1

adim "6b/8 M7-M9 tenant RBAC scope durumu (hedef rol seti, kapsam tipleri, guard)"
if [ "$(tablo_var "$FRESH_DB" "hr.roles")" = "$VAR" ]; then
    bekle "eski 'manager' rolü YOK (M7)" \
        "$(sql_demo "SELECT count(*) FROM hr.roles WHERE code = 'manager'")" "0"
    bekle "hedef rol seti (M7)" \
        "$(sql_demo "SELECT string_agg(code, ',' ORDER BY code) FROM hr.roles")" \
        "dept_manager,employee,hr_manager,tenant_admin,tenant_officer"
    bekle "kapsam tipleri (M8: OD5)" \
        "$(sql_demo "SELECT string_agg(code || ':' || scope_type, ',' ORDER BY code) FROM hr.roles")" \
        "dept_manager:UNIT,employee:SELF,hr_manager:TENANT,tenant_admin:TENANT,tenant_officer:TENANT"
    bekle "tenant_admin izinleri (hedef katalog)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'tenant_admin'")" "29"
    bekle "dept_manager hedef izin seti (§6.2 DY)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'dept_manager'")" "14"
    bekle "tenant_officer hedef izin seti (§6.2 ŞYT)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'tenant_officer'")" "19"
    bekle "scope_type kapalı küme kısıtı (M8)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.roles'::regclass AND conname='roles_scope_type_valid'")" "1"
    bekle "scope_org_unit_id kolonu (M9)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND table_name='user_roles' AND column_name='scope_org_unit_id'")" "1"
    bekle "kapsam içeren benzersizlik (M9)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.user_roles'::regclass AND conname='user_roles_user_role_scope_uniq'")" "1"
    bekle "eski (user_id, role_id) benzersizliği kaldırıldı" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.user_roles'::regclass AND conname='user_roles_uniq'")" "0"
    bekle "kapsam guard tetikleyicisi (M9)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger WHERE tgrelid='hr.user_roles'::regclass AND tgname='trg_user_role_scope_guard'")" "1"
    # M9'un eskiden ertelenen kısmı artık M10 ile tamamlandı (bkz. adım 6d).
    bilgi "M9 ertelenen kısım (FK + alt ağaç) → adım 6d'de doğrulanıyor (M10 sonrası)"
fi

adim "6d/8 M10-M13 organizasyon/çalışan modeli (tablo, FK, tetikleyici, RLS, kapsam)"
if [ "$(tablo_var "$FRESH_DB" "hr.departments")" = "$VAR" ]; then
    for tablo in departments positions employees employee_personal_data employee_manager; do
        bekle "hr.$tablo RLS + FORCE" \
            "$(sql_deger "$FRESH_DB" "SELECT relrowsecurity::text || '/' || relforcerowsecurity::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relname='$tablo'")" \
            "true/true"
        bekle "hr.$tablo tenant_isolation politikası" \
            "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_policies WHERE schemaname='hr' AND tablename='$tablo' AND policyname='tenant_isolation'")" "1"
    done
    bekle "hr şemasında FORCE RLS tablo sayısı (7 + 5 yeni)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relkind='r' AND c.relforcerowsecurity")" "12"
    bekle "OD27: company_id/legal_entity_id kolonu YOK" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND column_name IN ('company_id','legal_entity_id')")" "0"
    bekle "M9 tamamlama: user_roles.scope_org_unit_id → departments FK" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.user_roles'::regclass AND contype='f' AND confrelid='hr.departments'::regclass")" "1"
    bekle "M11: ağaç döngü/derinlik tetikleyicisi" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger WHERE tgrelid='hr.departments'::regclass AND tgname='trg_departments_tree_guard'")" "1"
    bekle "M11: (tenant_id, parent_id) indeksi" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND tablename='departments' AND indexname='departments_tenant_parent_idx'")" "1"
    bekle "M11: OD24 path/closure kolonu YOK" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND table_name='departments' AND column_name IN ('path','tree_path','lft','rgt')")" "0"
    # Kısmi benzersizlikler Django'da **kısmi UNIQUE indeks** olarak oluşur (pg_indexes).
    bekle "M13: tek aktif birincil yönetici (kısmi benzersiz indeks)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND tablename='employee_manager' AND indexname='employee_manager_primary_uniq'")" "1"
    bekle "M13: kısmi indeks koşulu (primary + açık + silinmemiş)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname='employee_manager_primary_uniq' AND indexdef LIKE '%relation_type%primary%' AND indexdef LIKE '%effective_to IS NULL%' AND indexdef LIKE '%deleted_at IS NULL%'")" "1"
    bekle "M13: kendini yönetici seçme yasağı (CHECK)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.employee_manager'::regclass AND conname='employee_manager_not_self'")" "1"
    bekle "kiracı-içi FK guard'ları (organizasyon + kapsam)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgname IN ('trg_departments_parent_id_same_tenant','trg_departments_manager_employee_id_same_tenant','trg_employees_department_id_same_tenant','trg_employees_position_id_same_tenant','trg_employee_personal_data_employee_id_same_tenant','trg_employee_manager_manager_employee_id_same_tenant','trg_user_roles_scope_org_unit_id_same_tenant') AND NOT t.tgisinternal")" "7"

    # Yetki turu (M10–M13): app rolü SELECT/INSERT/UPDATE alır, DELETE ALMAZ (OD21).
    for tablo in departments positions employees employee_personal_data employee_manager; do
        bekle "app rolü hr.$tablo SELECT/INSERT/UPDATE" \
            "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.$tablo','SELECT')::text || has_table_privilege('$APP_ROLE','hr.$tablo','INSERT')::text || has_table_privilege('$APP_ROLE','hr.$tablo','UPDATE')::text")" "truetruetrue"
        bekle "app rolü hr.$tablo DELETE ALAMAZ (OD21 soft-delete)" \
            "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.$tablo','DELETE')")" "f"
    done
    if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ]; then
        for tablo in departments positions employees employee_personal_data employee_manager; do
            bekle "platform rolü hr.$tablo erişemez" \
                "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.$tablo','SELECT')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','INSERT')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','UPDATE')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','DELETE')::text")" \
                "falsefalsefalsefalse"
        done
    fi

    # Kapsam çözümleyici + izin ∧ kapsam (gerçek servis koduyla, demo verisi üzerinden).
    PROBE="$(django_owner manage.py shell < scripts/_scope_probe.py 2>&1 | tail -1)"
    kontrol_metni "kapsam sondası" "$PROBE" "KAPSAM_PROBE finans_birim=2 kardes_haric=1"
    kontrol_metni "kapsam sondası: alt ağaç erişimi" "$PROBE" "kapsam_ici_erisim=1"
    kontrol_metni "kapsam sondası: kardeş birim DENY" "$PROBE" "kapsam_disi_erisim=0"
    kontrol_metni "kapsam sondası: K2/K3 kapsamdan gelmez" "$PROBE" "kisisel_veri=0"
    kontrol_metni "kapsam sondası: TENANT rolü kiracı geneli" "$PROBE" "tenant_geneli=1"
fi

adim "6c/8 Gerçek HTTP (roller: app)"

django_app_server manage.py runserver "127.0.0.1:$VERIFY_PORT" --noreload \
    >"$SMOKE_LOG" 2>&1 &
SUNUCU_PID=$!

hazir=0
for _ in $(seq 1 $((TIMEOUT_SECONDS * 2))); do
    if curl -fsS "http://127.0.0.1:$VERIFY_PORT/api/health" >/dev/null 2>&1; then hazir=1; break; fi
    sleep 0.5
done
[ "$hazir" = "1" ] || { echo "HATA: geliştirme sunucusu ayağa kalkmadı." >&2; exit 1; }

kod() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
bekle "GET  /api/health" "$(kod "http://127.0.0.1:$VERIFY_PORT/api/health")" "200"
bekle "GET  /api/ready" "$(kod "http://127.0.0.1:$VERIFY_PORT/api/ready")" "200"
bekle "GET  /admin/ (OD19)" "$(kod "http://127.0.0.1:$VERIFY_PORT/admin/")" "404"

YANIT_DOSYA="$(mktemp)"
GOVDE="{\"tenant\":\"demo\",\"email\":\"demo@persopyro.test\",\"password\":\"$DEMO_PASSWORD\",\"purpose\":\"fresh install dogrulama\",\"scopes\":[\"employee.read\"]}"
KOD_TOKEN="$(curl -s -o "$YANIT_DOSYA" -w '%{http_code}' -X POST \
    "http://127.0.0.1:$VERIFY_PORT/api/v1/auth/token" \
    -H 'Content-Type: application/json' -d "$GOVDE")"
bekle "POST /api/v1/auth/token" "$KOD_TOKEN" "201"
TOKEN="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["token"])' "$YANIT_DOSYA")"
rm -f "$YANIT_DOSYA"
bilgi "token alındı (${#TOKEN} karakter, düz metin gösterilmedi)"
bekle "POST /auth/token/logout" \
    "$(kod -X POST "http://127.0.0.1:$VERIFY_PORT/api/v1/auth/token/logout" -H "Authorization: Token $TOKEN")" "204"
bekle "logout sonrası aynı token" \
    "$(kod -X POST "http://127.0.0.1:$VERIFY_PORT/api/v1/auth/token/logout" -H "Authorization: Token $TOKEN")" "401"

kill "$SUNUCU_PID" 2>/dev/null || true
wait "$SUNUCU_PID" 2>/dev/null || true
SUNUCU_PID=""

adim "7/8 Tam test suite (temiz DB üzerinden)"
if [ "$SKIP_TESTS" = "1" ]; then
    bilgi "ATLANDI (SKIP_TESTS=1)"
else
    django_owner -m pytest -q 2>&1 | tail -3
fi

adim "8/8 Sonuç"
if [ "$HATA_VAR" = "0" ]; then
    echo "SONUÇ: OK — sıfırdan kurulum doğrulandı (canlı veritabanı değiştirilmedi)."
else
    echo "SONUÇ: BAŞARISIZ — yukarıdaki FAIL satırlarına bakın." >&2
    exit 1
fi
