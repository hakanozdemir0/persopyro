#!/usr/bin/env bash
# ============================================================================
# Persopyro — testleri çalıştırır
# ============================================================================
# Testler SAHİP (owner) rolüyle çalışır; çünkü pytest kendi test veritabanını
# oluşturmak zorundadır ve uygulama rolüne bilinçli olarak CREATEDB verilmemiştir.
# Kiracı izolasyonu bu nedenle gevşemez: tablolarda FORCE ROW LEVEL SECURITY
# etkindir, yani tablo sahibi de kiracı sınırını aşamaz
# (kanıt: scripts/02_verify_isolation.sql, iddia A9).
#
# Çalıştırma:  bash scripts/run_tests.sh [pytest argümanları]
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
    echo "HATA: .env bulunamadı. Önce 'bash scripts/bootstrap.sh' çalıştırın." >&2
    exit 1
fi

# .env dosyasını yükle (settings.py de okur; burada testler için rol değiştiriyoruz).
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
export PLATFORM_DB_CONNECTION_USER="$POSTGRES_OWNER_USER"
export PLATFORM_POSTGRES_PASSWORD="${POSTGRES_OWNER_PASSWORD:-}"

exec .venv/bin/pytest "$@"
