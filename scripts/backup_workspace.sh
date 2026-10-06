#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT_DIR="${ROOT}/backups"
OUT_FILE="${OUT_DIR}/persopyro-${STAMP}.tar.gz"

mkdir -p "${OUT_DIR}"
tar \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./ENV.zip' \
  --exclude='./backups' \
  --exclude='./.env' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  -czf "${OUT_FILE}" -C "${ROOT}" .

printf 'Yedek oluşturuldu: %s\n' "${OUT_FILE}"
