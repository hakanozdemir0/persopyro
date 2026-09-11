#!/usr/bin/env bash
# ============================================================================
# Persopyro — veritabanı migration'ları (sahip rolle)
# ============================================================================
# Migration'lar DDL yapar; uygulama rolünün (persopyro_app) hr şemasında CREATE
# yetkisi YOKTUR. Bu yüzden migration'lar sahip rolle (persopyro_owner) çalışır.
#
# Migration sonrası yetkiler tazelenir: yeni tablolar varsayılan yetkilerle
# otomatik olarak uygulama rolüne açılır, ancak `audit_logs` için UPDATE/DELETE
# iptali her seferinde uygulanmalıdır (denetim kaydı değiştirilemez).
#
# Çalıştırma: bash scripts/migrate.sh [migrate argümanları]
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
    echo "HATA: .env bulunamadı. Önce 'bash scripts/bootstrap.sh' çalıştırın." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ -z "${POSTGRES_OWNER_USER:-}" ]; then
    echo "HATA: .env içinde POSTGRES_OWNER_USER tanımlı değil." >&2
    exit 1
fi

export POSTGRES_USER="$POSTGRES_OWNER_USER"
export POSTGRES_PASSWORD="${POSTGRES_OWNER_PASSWORD:-}"

echo "== Migration'lar çalıştırılıyor (rol: $POSTGRES_USER) =="
.venv/bin/python manage.py migrate "$@"

echo "== Cache tablosu hazırlanıyor (createcachetable) =="
.venv/bin/python manage.py createcachetable

echo "== Uygulama rolü yetkileri tazeleniyor =="
if command -v psql >/dev/null 2>&1; then
    PGPASSWORD="$POSTGRES_PASSWORD" psql \
        -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5432}" \
        -U "$POSTGRES_USER" -d "${POSTGRES_DB:-persopyro}" \
        -q -v ON_ERROR_STOP=1 -f scripts/02_grant_app_privileges.sql
    echo "    tamam"
else
    echo "    ATLANDI: psql bulunamadı (yetkiler daha sonra tazelenmelidir)" >&2
fi

echo
echo "BİTTİ. Sıradaki komut: bash scripts/run_tests.sh"
