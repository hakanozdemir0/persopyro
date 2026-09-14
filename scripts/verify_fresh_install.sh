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
    exec env DJANGO_ENV_FILE=/dev/null DJANGO_SECRET_KEY="$SECRET" \
        DJANGO_DEBUG="${SUNUCU_DEBUG:-1}" \
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

kontrol_yok() {  # kontrol_yok <açıklama> <çıktı> <olmaması gereken parça>
    case "$2" in
        *"$3"*) bilgi "FAIL $1 (beklenmeyen içerik: '$3')"; HATA_VAR=1 ;;
        *) bilgi "OK   $1" ;;
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
    # İzin sayıları M14 + P1-A (OD61) + P5a V1 (fazla mesai) sonrası hedef katalogtur:
    # kiracı tarafı kod sayısı 29 → 33 → 41 → 45 (platform.* kodları kiracı rollerine verilmez).
    bekle "tenant_admin izinleri (hedef katalog, P5a sonrası)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'tenant_admin'")" "45"
    bekle "dept_manager hedef izin seti (§6.2 DY, P5a sonrası)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'dept_manager'")" "21"
    bekle "tenant_officer hedef izin seti (§6.2 ŞYT, P5a sonrası)" \
        "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id = rp.role_id WHERE r.code = 'tenant_officer'")" "27"
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
    bekle "hr şemasında FORCE RLS tablo sayısı (P5a sonrası: 24)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relkind='r' AND c.relforcerowsecurity")" "24"
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

adim "6e/8 M14 izin modülü (tablo, guard, RLS, politika/hakediş, onay akışı)"
if [ "$(tablo_var "$FRESH_DB" "hr.leave_types")" = "$VAR" ]; then
    for tablo in leave_types leave_policies leave_policy_rules leave_balances \
        leave_balance_entries leave_requests approval_steps; do
        bekle "tablo hr.$tablo" "$(tablo_var "$FRESH_DB" "hr.$tablo")" "$VAR"
        bekle "hr.$tablo RLS+FORCE+politika" \
            "$(sql_deger "$FRESH_DB" "SELECT ((c.relrowsecurity AND c.relforcerowsecurity)::text || ':' || (SELECT count(*) FROM pg_policies p WHERE p.schemaname='hr' AND p.tablename='$tablo' AND p.policyname='tenant_isolation')) FROM pg_class c WHERE c.oid='hr.$tablo'::regclass")" \
            "true:1"
    done
    bekle "kiracı verisi taşıyan FORCE RLS tablosu (12 → 19 → 23 → 24)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relkind='r' AND c.relforcerowsecurity")" "24"

    # M14 veritabanı guard'ları (döngü/negatif bakiye/durum makinesi/onay sırası).
    for tetik in trg_leave_policy_rules_overlap trg_leave_balances_nonnegative \
        trg_leave_requests_state trg_approval_steps_order; do
        bekle "guard tetikleyicisi $tetik" \
            "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger WHERE tgname='$tetik' AND NOT tgisinternal")" "1"
    done
    bekle "leave_requests kiracı guard'ı" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger WHERE tgname='trg_leave_requests_employee_id_same_tenant' AND NOT tgisinternal")" "1"
    bekle "leave_balances (çalışan,tür,dönem) kısmi benzersizliği" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname='leave_balances_employee_type_period_uniq'")" "1"
    bekle "leave_requests durum kapalı küme CHECK" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conrelid='hr.leave_requests'::regclass AND conname='leave_requests_status_valid'")" "1"
    bekle "leave_types kodsuz sistem türü yok" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.leave_types WHERE is_system AND code = ''")" "0"

    # Yetki turu: soft-delete tablolarında runtime silme yok; defter append-only.
    bekle "app rolü izin tablolarında DELETE alamaz (OD21)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM unnest(ARRAY['leave_types','leave_policies','leave_policy_rules','leave_balances','leave_requests','approval_steps']) AS t WHERE has_table_privilege('persopyro_app','hr.'||t,'DELETE')")" "0"
    bekle "app rolü bakiye defterinde UPDATE/DELETE alamaz (append-only)" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('persopyro_app','hr.leave_balance_entries','UPDATE')::text || has_table_privilege('persopyro_app','hr.leave_balance_entries','DELETE')::text")" \
        "falsefalse"
    if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ]; then
        for tablo in leave_types leave_policies leave_policy_rules leave_balances \
            leave_balance_entries leave_requests approval_steps; do
            bekle "platform rolü hr.$tablo erişemez" \
                "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.$tablo','SELECT')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','INSERT')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','UPDATE')::text || has_table_privilege('$PLATFORM_ROLE','hr.$tablo','DELETE')::text")" \
                "falsefalsefalsefalse"
        done
    else
        bilgi "UYARI: '$PLATFORM_ROLE' rolü yok → platform erişim yasağı denetimi atlandı."
    fi

    # İzin kataloğu: M14 ile eklenen dört kod ve matris izin sayıları.
    bekle "izin kataloğu M14 kodları" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions WHERE code IN ('leave.request.cancel','leave.request.approve.override','leave.balance.read','leave.balance.manage')")" "4"
    bekle "izin kataloğu toplam (P5a sonrası)" "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions")" "51"
    for cift in "tenant_admin:45" "tenant_officer:27" "hr_manager:33" "dept_manager:21" "employee:11"; do
        rol="${cift%%:*}"
        adet="${cift##*:}"
        bekle "varsayılan rol izin sayısı ($rol)" \
            "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id=rp.role_id WHERE r.code='$rol' AND r.deleted_at IS NULL")" "$adet"
    done

    # Uçtan uca akış sondası: tür → politika → hakediş → talep → onay → bakiye.
    IZIN1="$(django_owner manage.py shell < scripts/_leave_probe.py 2>&1 | tail -1)"
    IZIN2="$(django_owner manage.py shell < scripts/_leave_probe.py 2>&1 | tail -1)"
    kontrol_metni "izin sondası: varsayılan türler" "$IZIN1" "IZIN_PROBE turler=7"
    kontrol_metni "izin sondası: hakediş" "$IZIN1" "hakedis=14.0"
    kontrol_metni "izin sondası: onaylandı" "$IZIN1" "durum=approved"
    kontrol_metni "izin sondası: bakiye bir kez tükendi" "$IZIN1" "kullanilan=3.0"
    kontrol_metni "izin sondası: defter kayıtları" "$IZIN1" "defter=3"
    bekle "izin sondası idempotent (ikinci çalıştırma aynı sonuç)" \
        "$([ "$IZIN1" = "$IZIN2" ] && echo 1 || echo 0)" "1"
fi

adim "6c/8 Gerçek HTTP + manuel test arayüzü (roller: app)"

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

# --- 6c-1) Manuel test arayüzü (`/test-ui/`) — geliştirme modu (DJANGO_DEBUG=1) ---
bilgi "manuel test arayüzü kontrolleri (6c-1): geliştirme modunda AÇIK"
bekle "GET  /test-ui/login/" "$(kod "http://127.0.0.1:$VERIFY_PORT/test-ui/login/")" "200"
bekle "GET  /test-ui/api/me (oturum yok → 403)" \
    "$(kod "http://127.0.0.1:$VERIFY_PORT/test-ui/api/me")" "403"

# Oturum açma: çerez kavanozu + CSRF başlığı (tarayıcıdaki akışın aynısı).
CEREZ="$(mktemp)"
curl -fsS -c "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/login/" >/dev/null
TABAN2="http://127.0.0.1:$VERIFY_PORT"

csrf_basligi() {  # giriş/çıkış CSRF token'ını döndürür → her POST'ta yeniden okunur
    awk '$6 == "csrftoken" {print $7}' "$CEREZ" | tail -1
}
[ -n "$(csrf_basligi)" ] || { echo "HATA: csrftoken çerezi alınamadı." >&2; exit 1; }

oturum_ac() {  # $1=eposta → giriş (HTTP kodu döner)
    curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "http://127.0.0.1:$VERIFY_PORT/api/v1/auth/login" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d "{\"tenant\":\"demo\",\"email\":\"$1\",\"password\":\"$DEMO_PASSWORD\"}"
}
oturum_kapat() {
    curl -s -o /dev/null -b "$CEREZ" -c "$CEREZ" \
        -X POST "http://127.0.0.1:$VERIFY_PORT/api/v1/auth/logout" -H "X-CSRFToken: $(csrf_basligi)"
}

bekle "POST /auth/login (demo yönetici)" "$(oturum_ac "demo@persopyro.test")" "200"
bekle "GET  /test-ui/api/me (oturum var)" \
    "$(kod -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/me")" "200"
bekle "GET  /test-ui/api/dashboard" \
    "$(kod -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/dashboard")" "200"
kontrol_metni "GET /test-ui/api/departments (Finans görünür)" \
    "$(curl -fsS -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/departments")" "Finans"
bekle "GET  /test-ui/api/employees" \
    "$(kod -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/employees")" "200"
oturum_kapat

# --- 6c-2) Kapsam görünürlüğü: Finans müdürü --------------------------------------
bilgi "kapsam testi (6c-2): Finans müdürü = Finans + Muhasebe, İK HARİÇ"
bekle "POST /auth/login (finans müdürü)" "$(oturum_ac "finans.mudur@persopyro.test")" "200"
CALISAN_JSON="$(curl -fsS -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/employees")"
BEKLEYEN_JSON="$(curl -fsS -b "$CEREZ" "http://127.0.0.1:$VERIFY_PORT/test-ui/api/leave-requests?scope=pending")"
kontrol_metni "kapsam: Muhasebe çalışanı GÖRÜNÜR" "$CALISAN_JSON" "DEMO-002"
kontrol_yok "kapsam: İK çalışanı GÖRÜNMEZ" "$CALISAN_JSON" "DEMO-003"
kontrol_metni "kapsam: Muhasebe talebi onay havuzunda" "$BEKLEYEN_JSON" "IZIN_TESTI_MUHASEBE"
kontrol_yok "kapsam: İK talebi onay havuzunda DEĞİL" "$BEKLEYEN_JSON" "IZIN_TESTI_IK"
oturum_kapat

# --- 6c-3) Kritik düzeltmeler: çıkış, bakiye görünümü, unvan/rol, taşıma seçenekleri ---
bilgi "kritik düzeltmeler (6c-3): çıkış + bakiye + unvan/rol + taşıma seçenekleri"
bekle "POST /auth/login (demo yönetici)" "$(oturum_ac "demo@persopyro.test")" "200"

CALISAN_JSON="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees")"
# NOT: beklenen parçalar ASCII-güvenli seçilir (yanıt UTF-8 birleştirilmiş olabilir).
kontrol_metni "unvan (position) listede" "$CALISAN_JSON" '"position":"Y'
kontrol_metni "Türkçe rol adı listede" "$CALISAN_JSON" "Departman Y"
kontrol_metni "yıllık izin hakedişi görünür" "$CALISAN_JSON" '"entitled_days":"14.0"'

# Çıkış: sunucudaki oturum GERÇEKTEN kapanmalı (bildirilen hata).
CIKIS_KOD="$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
    -X POST "$TABAN2/api/v1/auth/logout" -H "X-CSRFToken: $(csrf_basligi)")"
bekle "POST /auth/logout (CSRF başlıklı)" "$CIKIS_KOD" "204"
bekle "çıkış sonrası /test-ui/api/me (403)" \
    "$(kod -b "$CEREZ" "$TABAN2/test-ui/api/me")" "403"
bekle "çıkış sonrası panel girişe yönlenir (302)" \
    "$(kod -b "$CEREZ" "$TABAN2/test-ui/")" "302"

# Taşıma seçenekleri: hedef listesi kendini ve alt ağacı içermez (sunucu üretir).
bekle "POST /auth/login (demo yönetici)" "$(oturum_ac "demo@persopyro.test")" "200"
DEP_JSON="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/departments")"
FINANS_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["code"]=="FIN"))' "$DEP_JSON")"
HEDEF_JSON="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/departments?moves_for=$FINANS_ID")"
# Yalnızca seçenek listesi ölçülür (yanıt ağacın tamamını da içerir).
HEDEF_LISTE="$("$PYTHON" -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["meta"]["move_options"]))' "$HEDEF_JSON")"
kontrol_metni "taşıma hedeflerinde İK var" "$HEDEF_LISTE" "nsan Kaynaklar"
kontrol_yok "taşıma hedeflerinde kendisi yok" "$HEDEF_LISTE" '"code":"FIN"'
kontrol_yok "taşıma hedeflerinde alt birim yok" "$HEDEF_LISTE" '"code":"MUH"'

# Unvan (pozisyon) ve izin türü ekleme — gerçek servis + denetim.
bekle "POST /test-ui/api/positions (201)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "$TABAN2/test-ui/api/positions" -H 'Content-Type: application/json' \
        -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"code":"VERIFY-UZ","name":"Dogrulama Uzmani"}')" "201"
kontrol_metni "lookups'ta yeni unvan" \
    "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/lookups")" "Dogrulama Uzmani"
TUR_YANIT="$(curl -s -b "$CEREZ" -c "$CEREZ" -X POST "$TABAN2/test-ui/api/leave-types" \
    -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
    -d '{"code":"verify_izni","name":"Dogrulama Izni","category":"other"}')"
kontrol_metni "izin türü eklendi" "$TUR_YANIT" "verify_izni"
kontrol_metni "talep formunda görünüyor" \
    "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/lookups")" "verify_izni"

# Departman yeniden adlandırma (isim/kod değişikliği).
IK_BIRIM_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["code"]=="IK"))' "$DEP_JSON")"
kontrol_metni "POST /departments/<id>/update" \
    "$(curl -s -b "$CEREZ" -c "$CEREZ" -X POST "$TABAN2/test-ui/api/departments/$IK_BIRIM_ID/update" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"name":"Dogrulama Birimi","code":"IK-D","reason":"fresh install"}')" \
    "Dogrulama Birimi"

# Rol yetkileri: okuma + guard'lar + aynı kümeyi geri yazma.
DM_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["code"]=="dept_manager"))' "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/roles")")"
ADMIN_ROL_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["code"]=="tenant_admin"))' "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/roles")")"
YETKI_JSON="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/roles/$DM_ID/permissions")"
kontrol_metni "yetki ekrani (modül etiketi)" "$YETKI_JSON" "alışanlar"
kontrol_yok "yetki ekraninda platform izni yok" "$YETKI_JSON" "platform.tenant.read"
MEVCUT_KODLAR="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(json.dumps([p["code"] for m in d["modules"] for p in m["permissions"] if p["secili"]]))' "$YETKI_JSON")"
bekle "POST rol izinleri (platform izni → 400)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "$TABAN2/test-ui/api/roles/$DM_ID/permissions" -H 'Content-Type: application/json' \
        -H "X-CSRFToken: $(csrf_basligi)" -d '{"permission_codes":["platform.tenant.read"]}')" "400"
bekle "POST rol izinleri (kendi rolü → 403)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "$TABAN2/test-ui/api/roles/$ADMIN_ROL_ID/permissions" -H 'Content-Type: application/json' \
        -H "X-CSRFToken: $(csrf_basligi)" -d '{"permission_codes":["employee.read"]}')" "403"
bekle "POST rol izinleri (ayni kume → 200)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "$TABAN2/test-ui/api/roles/$DM_ID/permissions" -H 'Content-Type: application/json' \
        -H "X-CSRFToken: $(csrf_basligi)" -d "{\"permission_codes\":$MEVCUT_KODLAR,\"reason\":\"fresh install dogrulama\"}")" "200"

# Rol atama / kaldırma (mock değil, gerçek servis + denetim).
CALISAN2_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["employee_no"]=="DEMO-002"))' "$CALISAN_JSON")"
HR_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if x["code"]=="hr_manager"))' "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/roles")")"
ATAMA_YANIT="$(curl -s -b "$CEREZ" -c "$CEREZ" -X POST "$TABAN2/test-ui/api/employees/$CALISAN2_ID/roles" \
    -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
    -d "{\"role_id\":\"$HR_ID\",\"reason\":\"fresh install dogrulama\"}")"
ATAMA_ID="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["data"]["id"])' "$ATAMA_YANIT")"
bilgi "rol ataması yapıldı (denetim: role.assign)"
bekle "POST /role-assignments/<id>/revoke (200)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "$TABAN2/test-ui/api/role-assignments/$ATAMA_ID/revoke" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"reason":"dogrulama bitti"}')" "200"
oturum_kapat

# --- 6c-3b) Yetki: çalışan rolü departman oluşturamaz (403) ----------------------
bekle "POST /auth/login (çalışan rolü)" "$(oturum_ac "personel@persopyro.test")" "200"
bekle "POST /test-ui/api/departments (yetkisiz → 403)" \
    "$(curl -s -o /dev/null -w '%{http_code}' -b "$CEREZ" -c "$CEREZ" \
        -X POST "http://127.0.0.1:$VERIFY_PORT/test-ui/api/departments" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"code":"IZINSIZ","name":"Olmamali"}')" "403"
oturum_kapat
# --- 6c-3c) Rol kataloğu/yetki ekranı sınırları + çalışan dizini (PII'siz) ---
bilgi "yetki sınırları (6c-5): çalışan ne rol listesini görür ne yetki düzenler"
bekle "POST /auth/login (çalışan rolü)" "$(oturum_ac "personel@persopyro.test")" "200"
bekle "GET  /test-ui/api/roles (çalışan → 403)" \
    "$(kod -b "$CEREZ" "$TABAN2/test-ui/api/roles")" "403"
CALISAN_LISTE="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees")"
kontrol_metni "çalışan yalnız kendi kaydını görür" "$CALISAN_LISTE" '"self_only":true'
kontrol_metni "çalışan listesinde tek kayıt (kendisi)" "$CALISAN_LISTE" '"kendi_kaydim":true'
kontrol_yok "başka çalışan kaydı dönmez" "$CALISAN_LISTE" 'DEMO-003'
BASKASI_ID="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["data"][0]["id"])' "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees")")"
kontrol_metni "kendi bakiye ucu açık" \
    "$(kod -b "$CEREZ" "$TABAN2/test-ui/api/employees/$BASKASI_ID/balances")" "200"
oturum_kapat

# --- 6c-3d) Hesap/kişisel veri/rol (bildirim 5. tur) --------------------------
# Ölçülen: (1) yönetici kişisel veriyi görür, (2) departman yöneticisi GÖREMEZ,
# (3) yönetici yeni rol oluşturur, departman yöneticisi oluşturamaz,
# (4) çalışanın menüsünde yetki yönetimi ve kişisel veri KAPALI.
bilgi "hesap/PII/rol (6c-6): PII yalnız yetkiyle, rol oluşturma yalnız role.manage"
bekle "POST /auth/login (kiracı yöneticisi)" "$(oturum_ac "demo@persopyro.test")" "200"
YON_LISTE="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees")"
CALISAN_ID="$("$PYTHON" -c 'import json,sys; d=json.loads(sys.argv[1])["data"]; print(next(x["id"] for x in d if not x.get("dizin")))' "$YON_LISTE")"
kontrol_metni "yönetici kişisel veriyi görür (6c-6)" \
    "$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees/$CALISAN_ID/personal")" \
    '"emergency_contact_relation"'
bekle "POST /test-ui/api/roles/create (yönetici → 201)" \
    "$(kod -b "$CEREZ" -X POST "$TABAN2/test-ui/api/roles/create" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"name":"Doğrulama Rolü","code":"vrf_rol","scope_type":"UNIT"}')" "201"

# --- 6c-8) Kayıt formu: bütün kişisel veriler + kan grubu (K3, şifreli) ---------
# Ölçülen: (1) kayıt formu K2 alanlarının tamamını + kan grubunu sorar,
# (2) çalışan + bütün kişisel veri TEK istekte yazılır, (3) kan grubu okunabilir,
# (4) veritabanında ŞİFRELİ durur (Fernet), (5) geçersiz değer 400 (fail-closed).
# NOT: Bu adım **açık olan kiracı yöneticisi oturumunu** kullanır; ayrı giriş
# yapılmaz (`/auth/login` hız sınırı nedeniyle ek oturum 429 üretiyordu).
bilgi "kayıt formu + kan grubu (6c-8): bütün bilgiler, K3 şifreli saklanır"
FORM_SAYFA="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/")"
kontrol_metni "kayıt formu: kan grubu alanı (6c-8)" "$FORM_SAYFA" 'id="emp-kan"'
kontrol_metni "kayıt formu: kimlik no alanı (6c-8)" "$FORM_SAYFA" 'id="emp-kimlik"'
kontrol_metni "kayıt formu: IBAN alanı (6c-8)" "$FORM_SAYFA" 'id="emp-iban"'
BIRIM_ID="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/departments" |
    "$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0]["id"])')"
YENI_CALISAN="$(curl -fsS -b "$CEREZ" -X POST "$TABAN2/test-ui/api/employees" \
    -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
    -d "{\"employee_no\":\"VRF-9\",\"department_id\":\"$BIRIM_ID\",\"hire_date\":\"2026-01-05\",
         \"email\":\"vrfa9@ornek.test\",\"password\":\"Gizli-Parola-123\",
         \"first_name\":\"Doğrulama\",\"last_name\":\"Kişisi\",\"national_id\":\"12345678901\",
         \"blood_type\":\"0+\",\"personal_phone\":\"05550000000\",\"address\":\"Ankara\"}")"
YENI_ID="$(printf '%s' "$YENI_CALISAN" |
    "$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"]["id"])')"
kontrol_metni "çalışan tek istekte oluşturuldu (6c-8)" "$YENI_CALISAN" 'VRF-9'
PII_YANIT="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/employees/$YENI_ID/personal")"
# Boşluk farkından bağımsız ölçüm (JSON ayırıcı biçimi değişebilir).
PII_SIK="$(printf '%s' "$PII_YANIT" | tr -d ' ')"
kontrol_metni "kan grubu kodu kaydedildi (6c-8)" "$PII_SIK" '"blood_type":"0+"'
kontrol_metni "kan grubu etiketi okundu (6c-8)" "$PII_YANIT" '0 Rh+'
kontrol_metni "kimlik no kaydedildi (6c-8)" "$PII_SIK" '"national_id":"12345678901"'
HAM_KAN="$(sql_demo "SELECT blood_type FROM hr.employee_personal_data WHERE employee_id='$YENI_ID'")"
case "$HAM_KAN" in
    gAAAAA*) bilgi "OK   kan grubu veritabanında ŞİFRELİ (6c-8)" ;;
    *) bilgi "FAIL kan grubu düz metin görünüyor (6c-8): '$HAM_KAN'"; HATA_VAR=1 ;;
esac
bekle "geçersiz kan grubu → 400 (6c-8)" \
    "$(kod -b "$CEREZ" -X POST "$TABAN2/test-ui/api/employees/$YENI_ID/personal" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"blood_type":"XY"}')" "400"

# --- 6c-9) Otomatik çalışan no (eşzamanlı) + detay çekmecesi kuralları ---------
# Ölçülen: (1) çalışan numarası istemciden gönderilmeden üretilir, (2) **iki
# PARALEL** kayıt farklı numara alır (concurrency), (3) arayüzde sağdan açılan
# detay çekmecesi ve "aynı çalışana tekrar tıkla → kapat" kararı bulunur,
# (4) karar fonksiyonunun anlamı node ile doğrulanır.
bilgi "otomatik çalışan no + detay çekmecesi (6c-9)"
TMPD="$(mktemp -d)"
PARALEL_PID=""
DEP_ID="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/departments" |
    "$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0]["id"])')"
for n in 1 2; do
    curl -s -b "$CEREZ" -X POST "$TABAN2/test-ui/api/employees" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d "{\"department_id\":\"$DEP_ID\",\"hire_date\":\"2026-06-01\",\"email\":\"paralel$n@ornek.test\",\"password\":\"Gizli-Parola-123\",\"first_name\":\"Paralel$n\",\"last_name\":\"Kayit\"}" \
        >"$TMPD/paralel_$n.json" &
    PARALEL_PID="$PARALEL_PID $!"
done
# Yalnız bu iki curl beklenir: çıplak `wait`, arka plandaki runserver'ı da
# bekleyip betiği kilitler.
wait $PARALEL_PID
NO1="$("$PYTHON" -c 'import json,sys; print(json.loads(open(sys.argv[1]).read())["data"]["employee_no"])' "$TMPD/paralel_1.json" 2>/dev/null || echo "")"
NO2="$("$PYTHON" -c 'import json,sys; print(json.loads(open(sys.argv[1]).read())["data"]["employee_no"])' "$TMPD/paralel_2.json" 2>/dev/null || echo "")"
case "$NO1$NO2" in
    EMP-*EMP-*)
        if [ "$NO1" != "$NO2" ]; then
            bilgi "OK   paralel kayıtlar farklı numara aldı ($NO1 / $NO2)"
        else
            bilgi "FAIL paralel kayıtlar AYNI numarayı aldı: $NO1"; HATA_VAR=1
        fi
        ;;
    *) bilgi "FAIL otomatik çalışan numarası üretilemedi: '$NO1' / '$NO2'"; HATA_VAR=1 ;;
esac
SAYFA_CEK="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/")"
kontrol_metni "detay çekmecesi düğümü (6c-9)" "$SAYFA_CEK" 'id="detail-panel"'
kontrol_metni "detay kapatma kuralı (6c-9)" "$SAYFA_CEK" 'function cekmeceKarari('
kontrol_metni "kalan izin kolonu (6c-9)" "$SAYFA_CEK" '>Kalan izin</th>'
if command -v node >/dev/null 2>&1; then
    CEK_JS="$TMPD/cekmece.js"
    "$PYTHON" - "$CEK_JS" "$ROOT/apps/testui/templates/testui/app.html" <<'PYCEK'
import pathlib
import re
import sys

hedef, kaynak = sys.argv[1], sys.argv[2]
html = pathlib.Path(kaynak).read_text(encoding="utf-8")
eslesme = re.search(r"function cekmeceKarari\([^)]*\)\s*\{[^}]*\}", html)
if not eslesme:
    raise SystemExit("cekmeceKarari bulunamadi")
test = """
const sonuc = [cekmeceKarari(null, "a"), cekmeceKarari("a", "a"), cekmeceKarari("a", "b")];
const beklenen = ["a", null, "b"];
if (JSON.stringify(sonuc) !== JSON.stringify(beklenen)) {
  console.error("BEKLENMEYEN", sonuc);
  process.exit(1);
}
"""
pathlib.Path(hedef).write_text(eslesme.group(0) + test, encoding="utf-8")
PYCEK
    if node "$CEK_JS" >/dev/null 2>&1; then
        bilgi "OK   detay çekmecesi kuralı: aynı çalışan kapatır, farklı çalışan değiştirir"
    else
        bilgi "FAIL detay çekmecesi kuralı (node)"; HATA_VAR=1
    fi
else
    bilgi "ATLA detay çekmecesi kuralı (node kurulu değil)"
fi

# --- 6c-10) İzin Bakiyeleri ekranı + zorunlu giriş hesabı ---------------------
# Ölçülen: (1) giriş hesabı olmadan çalışan açılamaz (400), (2) bakiye ekranı
# türetilen değerleri ve **sonraki hakediş** tarihini döner, (3) gerekçesiz
# düzeltme reddedilir (servis katmanı kuralı).
bilgi "izin bakiyeleri + zorunlu giriş hesabı (6c-10)"
DEP_ID2="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/departments" |
    "$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0]["id"])')"
bekle "hesapsız çalışan reddi → 400 (6c-10)" \
    "$(kod -b "$CEREZ" -X POST "$TABAN2/test-ui/api/employees" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d "{\"department_id\":\"$DEP_ID2\",\"hire_date\":\"2026-06-01\"}")" "400"
BAKIYE_YANIT="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/balances")"
kontrol_metni "bakiye ekranı ucu (6c-10)" "$BAKIYE_YANIT" 'available_days'
kontrol_metni "sonraki hakediş alanı (6c-10)" "$BAKIYE_YANIT" 'sonraki_hakedis_metin'
CALISAN_ID2="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0]["employee_id"])' <<<"$BAKIYE_YANIT")"
TUR_ID2="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.stdin.read())["data"][0]["leave_type_id"])' <<<"$BAKIYE_YANIT")"
bekle "gerekçesiz bakiye düzeltmesi → 400 (6c-10)" \
    "$(kod -b "$CEREZ" -X POST "$TABAN2/test-ui/api/balances/adjust" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d "{\"employee_id\":\"$CALISAN_ID2\",\"leave_type_id\":\"$TUR_ID2\",\"delta_days\":\"1.0\"}")" "400"
rm -rf "$TMPD"

oturum_kapat

bekle "POST /auth/login (departman yöneticisi)" "$(oturum_ac "finans.mudur@persopyro.test")" "200"
bekle "GET  /employees/<id>/personal (dept_manager → 403)" \
    "$(kod -b "$CEREZ" "$TABAN2/test-ui/api/employees/$CALISAN_ID/personal")" "403"
bekle "POST /test-ui/api/roles/create (dept_manager → 403)" \
    "$(kod -b "$CEREZ" -X POST "$TABAN2/test-ui/api/roles/create" \
        -H 'Content-Type: application/json' -H "X-CSRFToken: $(csrf_basligi)" \
        -d '{"name":"Yasak","code":"vrf_yasak","scope_type":"UNIT"}')" "403"
oturum_kapat

bekle "POST /auth/login (çalışan)" "$(oturum_ac "personel@persopyro.test")" "200"
CALISAN_ME="$(curl -fsS -b "$CEREZ" "$TABAN2/test-ui/api/me")"
kontrol_metni "çalışan menüsü: yetki yönetimi kapalı" "$CALISAN_ME" '"roles_manage":false'
kontrol_metni "çalışan menüsü: kişisel veri kapalı" "$CALISAN_ME" '"pii_gorunur":false'
oturum_kapat

rm -f "$CEREZ"

kill "$SUNUCU_PID" 2>/dev/null || true
wait "$SUNUCU_PID" 2>/dev/null || true
SUNUCU_PID=""

# --- 6c-11) P1-A: People lifecycle şeması + izin altyapısı --------------------
bilgi "P1-A şeması (6c-11): yeni tablolar, kolonlar, CHECK/index, RLS, izin katalogu"

YENI_TABLO_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname='hr' AND c.relname IN ('workplaces','employee_employment_terms','employee_assignments','form_field_definitions')")"
bekle "P1-A yeni tablo sayısı (6c-11)" "$YENI_TABLO_SAYISI" "4"

YENI_KOLON_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND ((table_name='employees' AND column_name IN ('notice_end_date','termination_reason_code','termination_note')) OR (table_name='users' AND column_name='auth_version'))")"
bekle "P1-A yeni kolon sayısı (6c-11)" "$YENI_KOLON_SAYISI" "4"

# DİKKAT: Django `CheckConstraint` → `pg_constraint`, `UniqueConstraint` → **benzersiz index**
# (`pg_indexes`) olarak oluşur; ikisi ayrı ölçülür (ölçülerek düzeltildi).
P1A_CHECK_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE connamespace='hr'::regnamespace AND conname IN ('employees_notice_after_termination','employees_termination_reason_valid','employees_terminated_fields_required','workplaces_country_code_format','workplaces_timezone_not_blank','workplaces_type_valid','workplaces_status_valid','ffd_field_code_known','ffd_form_code_valid','ffd_lock_level_valid','ffd_data_class_valid','ffd_locked_fields_forced','ffd_k3_not_required','eet_date_range_valid','eet_employment_type_valid','eet_working_model_valid','eet_contract_type_valid','eet_contract_range_valid','ea_date_range_valid')")"
bekle "P1-A CHECK kısıtı sayısı (6c-11)" "$P1A_CHECK_SAYISI" "19"

P1A_UNIQ_INDEX_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname IN ('employees_user_active_uniq','workplaces_tenant_code_uniq','eet_employee_open_uniq','ea_employee_open_uniq','ffd_tenant_form_field_uniq')")"
bekle "P1-A kısmi benzersizlik index sayısı (6c-11)" "$P1A_UNIQ_INDEX_SAYISI" "5"

P1A_INDEX_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname IN ('workplaces_tenant_status_idx','workplaces_tenant_country_idx','eet_tenant_emp_from_idx','eet_tenant_workplace_idx','ea_tenant_emp_from_idx','ea_tenant_dept_idx','ea_tenant_position_idx','ffd_tenant_form_sort_idx')")"
bekle "P1-A index sayısı (6c-11)" "$P1A_INDEX_SAYISI" "8"

RLS_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity AND n.nspname IN ('hr','public')")"
bekle "RLS + FORCE tablo sayısı (6c-11 + P5a)" "$RLS_SAYISI" "24"

IZIN_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions")"
bekle "izin katalogu toplamı (6c-11 + P5a)" "$IZIN_SAYISI" "51"

for rol_ve_sayi in "tenant_admin:45" "hr_manager:33" "tenant_officer:27" "dept_manager:21" "employee:11"; do
    ROL="${rol_ve_sayi%%:*}"; BEKLENEN="${rol_ve_sayi##*:}"
    SAYI="$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id=rp.role_id WHERE r.code='$ROL' AND r.deleted_at IS NULL")"
    bekle "$ROL izin sayısı (6c-11)" "$SAYI" "$BEKLENEN"
done

FORM_ALAN_SAYISI="$(sql_demo "SELECT count(*) FROM hr.form_field_definitions")"
bekle "form alan yapılandırması (6c-11)" "$FORM_ALAN_SAYISI" "33"

ACIK_ATAMA_SAYISI="$(sql_demo "SELECT count(*) FROM (SELECT employee_id FROM hr.employee_assignments WHERE effective_to IS NULL AND deleted_at IS NULL GROUP BY employee_id HAVING count(*) > 1) x")"
bekle "çoklu açık atama (0 olmalı, 6c-11)" "$ACIK_ATAMA_SAYISI" "0"

AUTH_VERSION="$(sql_demo "SELECT count(*) FROM hr.users WHERE auth_version <> 0")"
bekle "auth_version sıfır olmayan kullanıcı (6c-11)" "$AUTH_VERSION" "0"

# Platform rolü (varsa) yeni HR tablolarına erişemez.
if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ]; then
    PLATFORM_ERISIM="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM unnest(ARRAY['workplaces','employee_employment_terms','employee_assignments','form_field_definitions']) t(tablo) WHERE has_table_privilege('$PLATFORM_ROLE', 'hr.' || t.tablo, 'SELECT') OR has_table_privilege('$PLATFORM_ROLE', 'hr.' || t.tablo, 'INSERT')")"
    bekle "platform rolü yeni tablolara erişemiyor (6c-11)" "$PLATFORM_ERISIM" "0"
else
    bilgi "ATLANDI platform rolü yok: $PLATFORM_ROLE (6c-11)"
fi

# Uygulama rolü: DELETE verilmez (OD21 soft-delete).
if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$APP_ROLE'")" = "1" ]; then
    APP_DELETE="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM unnest(ARRAY['workplaces','employee_employment_terms','employee_assignments','form_field_definitions']) t(tablo) WHERE has_table_privilege('$APP_ROLE', 'hr.' || t.tablo, 'DELETE')")"
    bekle "uygulama rolünde DELETE yok (6c-11)" "$APP_DELETE" "0"
fi

# --- 6f/8) P5a V1: fazla mesai (talep + onay temeli) + izin hakkı akışı --------
# Kapsam: tablo/RLS/FORCE/politika, CHECK'ler, durum + kiracı guard tetikleyicileri,
# uygulama rolünün **asgari** yetkisi (SELECT/INSERT/UPDATE; DELETE/TRUNCATE yok),
# platform rolünün erişimsizliği, izin kodları + rol izin sayıları, migration zinciri
# ve uçtan uca sonda (izin hakkı yazımı + fazla mesai onayı; idempotent).
bilgi "P5a V1 (6f): fazla mesai tablosu, guard'lar, yetkiler, izin kodları, sonda"

if [ "$(tablo_var "$FRESH_DB" "hr.overtime_requests")" = "$VAR" ]; then
    bekle "hr.overtime_requests RLS + FORCE + politika" \
        "$(sql_deger "$FRESH_DB" "SELECT ((c.relrowsecurity AND c.relforcerowsecurity)::text || ':' || (SELECT count(*) FROM pg_policies p WHERE p.schemaname='hr' AND p.tablename='overtime_requests' AND p.policyname='tenant_isolation')) FROM pg_class c WHERE c.oid='hr.overtime_requests'::regclass")" \
        "true:1"

    # CHECK kısıtları: Django Meta kısıtları + ham SQL ile eklenen iki kısıt
    # (açıklama uzunluğu, sunucu süre hesabı uyumu).
    P5A_CHECK="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE connamespace='hr'::regnamespace AND conname IN ('overtime_requests_time_order','overtime_requests_minutes_positive','overtime_requests_minutes_max','overtime_requests_status_valid','overtime_requests_compensation_valid','overtime_requests_cancel_reason','overtime_requests_description_length','overtime_requests_minutes_match')")"
    bekle "overtime CHECK kısıtı sayısı (6f)" "$P5A_CHECK" "8"
    bekle "süre uyum kısıtı (sunucu hesabı = DB)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conname='overtime_requests_minutes_match' AND pg_get_constraintdef(oid) LIKE '%total_minutes%'")" "1"
    bekle "açıklama uzunluk kısıtı (>= 5)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_constraint WHERE conname='overtime_requests_description_length'")" "1"
    bekle "aynı gün/aralık kısmi benzersizliği" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname='overtime_requests_slot_uniq'")" "1"

    for tetik in trg_overtime_requests_state trg_overtime_requests_employee_id_same_tenant \
        trg_overtime_requests_created_by_id_same_tenant trg_overtime_requests_decided_by_id_same_tenant; do
        bekle "fazla mesai tetikleyicisi $tetik" \
            "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger WHERE tgname='$tetik' AND NOT tgisinternal")" "1"
    done

    # Uygulama rolü: asgari DML (SELECT/INSERT/UPDATE), silme yok (OD21 soft-delete).
    bekle "app rolü hr.overtime_requests SELECT/INSERT/UPDATE" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.overtime_requests','SELECT')::text || has_table_privilege('$APP_ROLE','hr.overtime_requests','INSERT')::text || has_table_privilege('$APP_ROLE','hr.overtime_requests','UPDATE')::text")" \
        "truetruetrue"
    bekle "app rolü hr.overtime_requests DELETE/TRUNCATE ALAMAZ" \
        "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$APP_ROLE','hr.overtime_requests','DELETE')::text || has_table_privilege('$APP_ROLE','hr.overtime_requests','TRUNCATE')::text")" \
        "falsefalse"
    if [ "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_roles WHERE rolname='$PLATFORM_ROLE'")" = "1" ]; then
        bekle "platform rolü hr.overtime_requests erişemez" \
            "$(sql_deger "$FRESH_DB" "SELECT has_table_privilege('$PLATFORM_ROLE','hr.overtime_requests','SELECT')::text || has_table_privilege('$PLATFORM_ROLE','hr.overtime_requests','INSERT')::text || has_table_privilege('$PLATFORM_ROLE','hr.overtime_requests','UPDATE')::text || has_table_privilege('$PLATFORM_ROLE','hr.overtime_requests','DELETE')::text")" \
            "falsefalsefalsefalse"
    else
        bilgi "ATLANDI platform rolü yok: $PLATFORM_ROLE (6f)"
    fi

    # İzin kodları + rol dağılımı (P5a deltası).
    bekle "fazla mesai izin kodları (4)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions WHERE code IN ('overtime.request.create','overtime.request.read','overtime.request.approve','overtime.request.cancel')")" "4"
    for cift in "tenant_admin:45" "hr_manager:33" "tenant_officer:27" "dept_manager:21" "employee:11"; do
        rol="${cift%%:*}"; adet="${cift##*:}"
        bekle "P5a sonrası rol izin sayısı ($rol)" \
            "$(sql_demo "SELECT count(*) FROM hr.role_permissions rp JOIN hr.roles r ON r.id=rp.role_id WHERE r.code='$rol' AND r.deleted_at IS NULL")" "$adet"
    done
    # Özel role verilebilirlik: onay izni devredilebilir (OD9 listesinde değildir).
    bekle "overtime.request.approve devredilemez DEĞİL (OD9)" \
        "$(sql_demo "SELECT count(*) FROM hr.permissions WHERE code='overtime.request.approve'")" "1"

    # İzin hakkı: dönem başına tek hakediş kaydı (kısmi benzersizlik) korunuyor.
    bekle "izin defteri dönem-hakediş benzersizliği (entitlement invariant)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname='leave_balance_entries_accrual_uniq'")" "1"

    # Migration zinciri: P5a V1 migration'ları uygulanmış olmalı (sıfırdan kurulum).
    P5A_MIGRATION="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.django_migrations WHERE (app='overtime' AND name IN ('0001_p5a_overtime_schema','0002_p5a_overtime_guards','0003_p5a_overtime_rls','0004_p5a_overtime_same_tenant_guards')) OR (app='rbac' AND name='0013_p5a_overtime_permissions') OR (app='leave' AND name='0005_p5a_overtime_approval_subject')")"
    bekle "P5a V1 migration sayısı (6f)" "$P5A_MIGRATION" "6"
    bekle "onay konusu 'overtime_request' kolonu (approval_steps)" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND table_name='approval_steps' AND column_name='subject_kind'")" "1"

    # Uçtan uca sonda (idempotent): izin hakkı + fazla mesai onayı.
    FM1="$(django_owner manage.py shell < scripts/_overtime_probe.py 2>&1 | tail -1)"
    FM2="$(django_owner manage.py shell < scripts/_overtime_probe.py 2>&1 | tail -1)"
    kontrol_metni "fazla mesai sondası: izin hakkı yazıldı" "$FM1" "hak=14.0 kalan=14.0"
    kontrol_metni "fazla mesai sondası: tek hakediş kaydı" "$FM1" "accrual=1"
    kontrol_metni "fazla mesai sondası: talep onaylandı" "$FM1" "fm=approved"
    kontrol_metni "fazla mesai sondası: süre sunucu hesabı" "$FM1" "sure=90"
    bekle "fazla mesai sondası idempotent (ikinci çalıştırma aynı durum)" \
        "$([ "$FM1" = "$FM2" ] && echo 1 || echo 0)" "1"
else
    bilgi "FAIL hr.overtime_requests tablosu YOK (P5a V1 uygulanmamış)"
    HATA_VAR=1
fi

# --- 6g) P1-B: Employee lifecycle servisleri -----------------------------------
# Kapsam: auth_version şeması (users + api token snapshot), lifecycle CHECK yapısı,
# tek-current kısıtları, lifecycle audit action'ları, management command import,
# RLS/FORCE/runtime privileges (P1-A adımlarıyla birlikte) ve izin kataloğu.
bilgi "P1-B (6g): lifecycle servisleri invariantları"

bekle "users.auth_version kolonu (6g): NOT NULL + CHECK >=0" \
        "$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns c JOIN pg_constraint p ON p.conrelid='hr.users'::regclass WHERE c.table_schema='hr' AND c.table_name='users' AND c.column_name='auth_version' AND c.is_nullable='NO' AND (p.conname LIKE '%auth_version%')")" "2"

TOKEN_SNAPSHOT_KOLONU="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM information_schema.columns WHERE table_schema='hr' AND table_name='api_tokens' AND column_name='auth_version_snapshot'")"
bekle "api_tokens.auth_version_snapshot kolonu (6g)" "$TOKEN_SNAPSHOT_KOLONU" "1"

LIFECYCLE_CHECKS="$(sql_deger "$FRESH_DB" "SELECT (SELECT count(*) FROM pg_constraint WHERE connamespace='hr'::regnamespace AND conname IN ('employees_status_valid','employees_terminated_fields_required','employees_termination_reason_valid','employees_notice_after_termination','ea_date_range_valid','eet_date_range_valid','employee_manager_not_self','employee_manager_date_range_valid')) + (SELECT count(*) FROM pg_indexes WHERE schemaname='hr' AND indexname IN ('employees_user_active_uniq','ea_employee_open_uniq','eet_employee_open_uniq'))")"
bekle "lifecycle CHECK/kısıt sayısı (6g)" "$LIFECYCLE_CHECKS" "11"

LIFECYCLE_PERMS="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM hr.permissions WHERE code IN ('employee.suspend','employee.terminate','employee.restore','employee.history.read','employee.delete')")"
bekle "lifecycle izin kodları (6g)" "$LIFECYCLE_PERMS" "5"

# Management command + lifecycle modül importları: tırnak yuvalaması olmadan,
# geçici python dosyası üzerinden (bash parse güvenliği).
P1B_IMPORT_PY="$(mktemp)"
"$PYTHON" - "$P1B_IMPORT_PY" <<'PY1B'
import pathlib
import sys

pathlib.Path(sys.argv[1]).write_text(
    """
from django.core.management import get_commands
from apps.organization.history import employee_history
from apps.organization.lifecycle import (
    IZINLI_GECISLER, suspend_employee, restore_employee,
    terminate_employee, archive_employee, change_manager,
)
from apps.accounts.auth_version import oturum_auth_version_dogrula

cmds = get_commands()
assert 'process_employee_terminations' in cmds, 'command yuklu degil'
assert IZINLI_GECISLER['archived'] == frozenset()
assert 'archived' not in IZINLI_GECISLER['active']
print('IMPORT_OK')
""",
    encoding="utf-8",
)
PY1B
IMPORT_OUT="$(django_owner manage.py shell < "$P1B_IMPORT_PY" 2>&1 | tail -1)"
rm -f "$P1B_IMPORT_PY"
kontrol_metni "lifecycle modülleri + management command (6g)" "$IMPORT_OUT" "IMPORT_OK"

# P1-A guard'ları (kiracılar arası referans koruması) lifecycle tablolarında kurulu.
GUARD_SAYISI="$(sql_deger "$FRESH_DB" "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='hr' AND c.relname IN ('employee_assignments','employee_employment_terms','workplaces','employees') AND t.tgname LIKE '%same_tenant%' AND NOT t.tgisinternal")"
bekle "lifecycle tabloları cross-tenant guard tetikleyicileri (6g)" "$GUARD_SAYISI" "10"

# --- 6c-7) Arayüz JS sözdizimi (node varsa) -----------------------------------
# Neden: şablon içindeki gömülü JS yalnız tarayıcıda çalışır; bozuk bir şablon
# dizesi panelin sessizce boş kalmasına yol açar (yaşandı). `node --check` bunu
# kurulum doğrulamasında yakalar. Node yoksa adım atlanır (bilgi verilir).
if command -v node >/dev/null 2>&1; then
    bilgi "arayüz JS sözdizimi (6c-7): gömülü betik node ile denetleniyor"
    # NOT: `node --check` uzantısız dosyayı reddeder (ERR_UNKNOWN_FILE_EXTENSION) →
    # geçici dosya `.js` uzantısıyla oluşturulur.
    JS_GECICI="$(mktemp)".js
    "$PYTHON" - "$JS_GECICI" "$ROOT/apps/testui/templates/testui/app.html" <<'PYJS'
import pathlib
import re
import sys

hedef, kaynak = sys.argv[1], sys.argv[2]
html = pathlib.Path(kaynak).read_text(encoding="utf-8")
bloklar = re.findall(r"<script>(.*?)</script>", html, re.S)
pathlib.Path(hedef).write_text(bloklar[-1] if bloklar else "", encoding="utf-8")
PYJS
    if node --check "$JS_GECICI" >/dev/null 2>&1; then
        bilgi "OK   arayüz JS sözdizimi geçerli"
    else
        bilgi "FAIL arayüz JS sözdizimi bozuk (node --check)"
        HATA_VAR=1
    fi
    rm -f "$JS_GECICI"
else
    bilgi "ATLA arayüz JS sözdizimi (node kurulu değil)"
fi

# --- 6c-4) Üretim varsayılanı: arayüz KAPALI (DJANGO_DEBUG=0) ---------------------
bilgi "üretim varsayılanı (6c-4): /test-ui/* kapalı olmalı"
# NOT: `DJANGO_SECURE_SSL_REDIRECT=0` yalnızca yerel HTTP üzerinden ölçüm içindir;
# kapı ayarı (`TEST_UI_ENABLED`) bundan bağımsızdır ve DEBUG'a bağlıdır.
SUNUCU_DEBUG=0 DJANGO_SECURE_SSL_REDIRECT=0 django_app_server manage.py runserver "127.0.0.1:$VERIFY_PORT" --noreload \
    >"$SMOKE_LOG" 2>&1 &
SUNUCU_PID=$!
hazir=0
for _ in $(seq 1 $((TIMEOUT_SECONDS * 2))); do
    if curl -fsS "http://127.0.0.1:$VERIFY_PORT/api/health" >/dev/null 2>&1; then hazir=1; break; fi
    sleep 0.5
done
[ "$hazir" = "1" ] || { echo "HATA: üretim modu sunucusu ayağa kalkmadı." >&2; exit 1; }
bekle "GET  /test-ui/login/ (kapalı → 404)" "$(kod "http://127.0.0.1:$VERIFY_PORT/test-ui/login/")" "404"
bekle "GET  /test-ui/api/me (kapalı → 404)" "$(kod "http://127.0.0.1:$VERIFY_PORT/test-ui/api/me")" "404"
bekle "GET  /admin/ (kapalı → 404)" "$(kod "http://127.0.0.1:$VERIFY_PORT/admin/")" "404"
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
