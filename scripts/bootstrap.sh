#!/usr/bin/env bash
# ============================================================================
# Persopyro — veritabanı ön kurulumu (tek komut)
# ============================================================================
# Ne yapar:
#   1) Rolleri, veritabanını, hr şemasını ve yetkileri kurar (süper kullanıcı).
#   2) Uygulama ve migration rolleri için RASTGELE parola üretip uygular.
#   3) Parolaları ve Django secret'ını .env dosyasına yazar (git'e girmez).
#   4) Kiracı izolasyonu (RLS) doğrulamasını çalıştırır.
#   5) Uygulama rolüyle TCP üzerinden bağlantıyı sınar.
#
# Çalıştırma:  bash scripts/bootstrap.sh
# Gerekli   : sudo (sistem kümesinde postgres kullanıcısına geçmek için)
#
# Ortam değişkenleriyle özelleştirme (varsayılanlar parantez içinde):
#   PG_HOST (127.0.0.1)   PG_PORT (5432)
#   SUPER_USER_CMD ("sudo -u postgres")   ENV_FILE (<proje kökü>/.env)
#   DB_NAME (persopyro)   OWNER_ROLE (persopyro_owner)   APP_ROLE (persopyro_app)
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PG_HOST="${PG_HOST:-127.0.0.1}"
PG_PORT="${PG_PORT:-5432}"
DB_NAME="${DB_NAME:-persopyro}"
OWNER_ROLE="${OWNER_ROLE:-persopyro_owner}"
APP_ROLE="${APP_ROLE:-persopyro_app}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

# Süper kullanıcı komutu (sudo gerektirmeyen ortamlar için boş bırakılabilir).
# "-" kullanılıyor (":-" değil): açıkça boş verilen değer varsayılanı ezsin.
if [ -n "${SUPER_USER_CMD-sudo -u postgres}" ]; then
    read -r -a SUPER <<< "${SUPER_USER_CMD-sudo -u postgres}"
else
    SUPER=()
fi

# Varsayılan olmayan bir kümede (ör. farklı port) çalışıyorsanız ek psql seçenekleri.
SUPER_PSQL_OPTS="${SUPER_PSQL_OPTS:-}"
if [ -n "$SUPER_PSQL_OPTS" ]; then
    read -r -a SUPER_OPTS <<< "$SUPER_PSQL_OPTS"
else
    SUPER_OPTS=()
fi

gen_secret() {
    python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
}

echo "== 0/5 Süper kullanıcı erişimi sınanıyor =="
if ! SUPER_ERR="$("${SUPER[@]}" psql "${SUPER_OPTS[@]}" -d postgres -q -c 'SELECT 1' 2>&1)"; then
    echo "HATA: süper kullanıcı olarak PostgreSQL'e bağlanılamadı." >&2
    echo "      Denenen komut: ${SUPER[*]:-(doğrudan)} psql ${SUPER_OPTS[*]:-} -d postgres" >&2
    echo "      Çıktı: $SUPER_ERR" >&2
    echo "      İpucu: 'sudo -u postgres psql -d postgres -c \"SELECT 1\"' çalışıyor mu?" >&2
    exit 1
fi
echo "    tamam"

echo "== 1a/5 Roller, veritabanı ve eklentiler =="
"${SUPER[@]}" psql "${SUPER_OPTS[@]}" -d postgres -q -v ON_ERROR_STOP=1 \
    < "$ROOT/scripts/01_bootstrap.sql"

echo "== 1b/5 hr şeması ve uygulama rolü yetkileri =="
"${SUPER[@]}" psql "${SUPER_OPTS[@]}" -d "$DB_NAME" -q -v ON_ERROR_STOP=1 \
    < "$ROOT/scripts/02_grant_app_privileges.sql"

echo "== 2/5 Parolalar üretiliyor ve uygulanıyor =="
OWNER_PW="$(gen_secret)"
APP_PW="$(gen_secret)"
# Parolalar argv'ye yazılmaz; psql'e stdin üzerinden verilir.
{
    printf "ALTER ROLE %s WITH PASSWORD '%s';\n" "$OWNER_ROLE" "$OWNER_PW"
    printf "ALTER ROLE %s WITH PASSWORD '%s';\n" "$APP_ROLE" "$APP_PW"
} | "${SUPER[@]}" psql "${SUPER_OPTS[@]}" -d postgres -q -v ON_ERROR_STOP=1

echo "== 3/5 .env yazılıyor -> $ENV_FILE =="
if [ -e "$ENV_FILE" ]; then
    BAK="$ENV_FILE.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$ENV_FILE" "$BAK"
    echo "    mevcut .env yedeklendi: $BAK"
fi
DJANGO_SECRET_KEY="$(gen_secret)"
umask 077
cat > "$ENV_FILE" <<ENVEOF
# Persopyro yerel ortam değişkenleri — scripts/bootstrap.sh tarafından üretildi.
# Bu dosya git'e girmez (.gitignore). Değerleri elle değiştirebilirsiniz.
DJANGO_SECRET_KEY="$DJANGO_SECRET_KEY"
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS="localhost,127.0.0.1"
LOG_LEVEL="INFO"

POSTGRES_DB="$DB_NAME"
POSTGRES_USER="$APP_ROLE"
POSTGRES_PASSWORD="$APP_PW"
POSTGRES_HOST="$PG_HOST"
POSTGRES_PORT="$PG_PORT"
POSTGRES_CONN_MAX_AGE=60

# Testler için sahip rolü (pytest kendi test veritabanını oluşturur).
POSTGRES_OWNER_USER="$OWNER_ROLE"
POSTGRES_OWNER_PASSWORD="$OWNER_PW"

Q_WORKERS=2

EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL="Persopyro <no-reply@example.com>"

S3_ENDPOINT=""
S3_REGION=""
S3_BUCKET=""
S3_ACCESS_KEY=""
S3_SECRET_KEY=""
S3_SIGNED_URL_TTL_SECONDS=300

SENTRY_DSN=""
ENVEOF
echo "    tamam (izinler 0600)"

echo "== 4/5 Kiracı izolasyonu (RLS) doğrulaması =="
"${SUPER[@]}" psql "${SUPER_OPTS[@]}" -d "$DB_NAME" -q -v ON_ERROR_STOP=1 \
    < "$ROOT/scripts/03_verify_isolation.sql"

echo "== 5/5 Uygulama rolüyle TCP bağlantı sınaması =="
if command -v psql >/dev/null 2>&1; then
    # Django'nun kullandığı bağlantı seçeneğiyle (search_path) aynı koşullar sınanır.
    PGOPTIONS="-c search_path=hr,public" \
    PGPASSWORD="$APP_PW" psql -h "$PG_HOST" -p "$PG_PORT" -U "$APP_ROLE" -d "$DB_NAME" -tAc \
        "SELECT current_user || ' | superuser: ' || (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)::text || ' | search_path: ' || current_setting('search_path')"
    echo "    tamam"
else
    echo "    ATLANDI: psql bulunamadı"
fi

echo
echo "BİTTİ. Özet:"
echo "  Veritabanı      : $DB_NAME (şema: hr)"
echo "  Uygulama rolü   : $APP_ROLE  (parola .env içinde)"
echo "  Migration rolü  : $OWNER_ROLE (parola .env içinde)"
echo "  Ortam dosyası   : $ENV_FILE"
echo
echo "Sıradaki komut: bash scripts/run_tests.sh"
