#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(id -un)" != "hakan" ]]; then
    echo "Bu betiği hakan kullanıcısıyla çalıştır: bash scripts/enable_cloudflare_preview.sh" >&2
    exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/gunicorn" ]]; then
    echo "Gunicorn bulunamadı: $PROJECT_DIR/.venv/bin/gunicorn" >&2
    exit 1
fi

sudo install -o root -g root -m 0644 \
    "$PROJECT_DIR/deploy/nginx/persopyro.site.conf" \
    /etc/nginx/sites-available/persopyro
sudo ln -sfn /etc/nginx/sites-available/persopyro /etc/nginx/sites-enabled/persopyro
sudo rm -f /etc/nginx/sites-enabled/default

sudo install -o root -g root -m 0644 \
    "$PROJECT_DIR/deploy/systemd/persopyro-web.service" \
    /etc/systemd/system/persopyro-web.service

sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now persopyro-web
sudo systemctl restart persopyro-web

for attempt in {1..20}; do
    if curl --fail --silent http://127.0.0.1:8000/api/health >/dev/null; then
        break
    fi
    if [[ "$attempt" == 20 ]]; then
        echo "PersoPyro /api/health yanıt vermedi; servis günlüğü:" >&2
        sudo journalctl -u persopyro-web -n 80 --no-pager >&2
        exit 1
    fi
    sleep 1
done

sudo systemctl reload nginx

echo "PersoPyro servisi:"
sudo systemctl --no-pager --full status persopyro-web
echo "Nginx servisi:"
sudo systemctl --no-pager --full status nginx
