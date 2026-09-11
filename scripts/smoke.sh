#!/usr/bin/env bash
# ============================================================================
# Persopyro — uçtan uca duman testi (geliştirme)
# ============================================================================
# Gerçek HTTP + gerçek veritabanı üzerinden sözleşmeyi sınar:
#   1) Demo kiracı/kullanıcı oluşturur (idempotent) + askıya alınmış kiracı
#   2) Geliştirme sunucusunu başlatır
#   3) /api/health, /api/ready → 200
#   4) /api/v1/auth/token ile gerçek giriş (OD26: `purpose` + `scopes` zorunlu) → 201
#   5) Geçerli token ile çıkış → 204; aynı token tekrar → 401 (iptal izi);
#      süresi geçmiş/geçersiz token → 401
#   6) Askıya alınmış kiracı ile giriş denemesi → 403 (F11)
#   7) Sunucuyu kapatır
#
# Çalıştırma: bash scripts/smoke.sh
# NOT: Yalnızca geliştirme içindir; üretimde bu adımlar gerçek istemciyle yapılır.
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -f .env ] || { echo "HATA: .env yok; önce scripts/bootstrap.sh çalıştırın." >&2; exit 1; }

set -a
# shellcheck disable=SC1091
. ./.env
set +a

# Demo verisi sahip rolle yazılır (uygulama rolünün DDL yetkisi yok; DML'i var
# ama tutarlılık için sahip rol kullanılır).
export POSTGRES_USER="$POSTGRES_OWNER_USER"
export POSTGRES_PASSWORD="$POSTGRES_OWNER_PASSWORD"

PORT="${SMOKE_PORT:-8009}"
DEMO_PASSWORD="${DEMO_PASSWORD:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')}"
export DEMO_PASSWORD

echo "== 1/6 Demo verisi hazırlanıyor =="
.venv/bin/python manage.py shell < scripts/_demo_data.py | tail -1

echo "== 2/6 Geliştirme sunucusu başlatılıyor (port $PORT) =="
.venv/bin/python manage.py runserver "127.0.0.1:$PORT" --noreload >/tmp/persopyro_smoke.log 2>&1 &
SUNUCU_PID=$!
trap 'kill "$SUNUCU_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then break; fi
    sleep 0.5
done

echo "== 3/6 Sağlık uçları =="
echo "    /api/health : $(curl -fsS "http://127.0.0.1:$PORT/api/health")"
echo "    /api/ready  : $(curl -fsS "http://127.0.0.1:$PORT/api/ready")"

# OD26: kapsamsız token üretilemez; `purpose` ve `scopes` zorunludur.
GOVDE="{\"tenant\":\"demo\",\"email\":\"demo@persopyro.test\",\"password\":\"$DEMO_PASSWORD\",\"purpose\":\"smoke testi icin token\",\"scopes\":[\"employee.read\"]}"

echo "== 4/6 Gerçek giriş (token, purpose + scopes ile) =="
YANIT="$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/v1/auth/token" \
    -H 'Content-Type: application/json' -d "$GOVDE")"
TOKEN="$(printf '%s' "$YANIT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["token"])')"
echo "    giriş başarılı, token alındı (${#TOKEN} karakter, düz metin gösterilmedi)"

echo "== 5/6 Token sınaması (iptal izi) =="
KOD_OK="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "http://127.0.0.1:$PORT/api/v1/auth/token/logout" \
    -H "Authorization: Token $TOKEN")"
KOD_IPTAL="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "http://127.0.0.1:$PORT/api/v1/auth/token/logout" \
    -H "Authorization: Token $TOKEN")"
KOD_GECERSIZ="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "http://127.0.0.1:$PORT/api/v1/auth/token/logout" \
    -H "Authorization: Token uydurma-bir-token")"
echo "    geçerli token ile çıkış  : HTTP $KOD_OK (204 beklenir)"
echo "    çıkış sonrası aynı token : HTTP $KOD_IPTAL (401 beklenir — satır silinmez)"
echo "    geçersiz token ile çıkış : HTTP $KOD_GECERSIZ (401 beklenir)"

echo "== 6/6 Askıya alınmış kiracı (F11) =="
KOD_ASKIDA="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    "http://127.0.0.1:$PORT/api/v1/auth/token" \
    -H 'Content-Type: application/json' \
    -d "{\"tenant\":\"askida\",\"email\":\"askida@persopyro.test\",\"password\":\"$DEMO_PASSWORD\",\"purpose\":\"smoke testi askida kiraci\",\"scopes\":[\"employee.read\"]}")"
echo "    askıdaki kiracı ile giriş : HTTP $KOD_ASKIDA (403 beklenir)"

echo
echo "BİTTİ. Demo hesabı (yalnızca yerel geliştirme):"
echo "  kiracı (slug) : demo"
echo "  e-posta       : demo@persopyro.test"
echo "  parola        : $DEMO_PASSWORD"
echo "  (askıdaki kiracı: slug=askida, kullanıcı=askida@persopyro.test — giriş yapamaz)"
