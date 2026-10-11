#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu kurulum root yetkisi gerektirir." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y clamav clamav-daemon poppler-utils

clamd_conf=/etc/clamav/clamd.conf
if [[ ! -f "${clamd_conf}" ]]; then
  echo "${clamd_conf} bulunamadı." >&2
  exit 1
fi

sed -i -E '/^[[:space:]]*(TCPSocket|TCPAddr|StreamMaxLength|MaxFileSize)[[:space:]]/d' "${clamd_conf}"
cat >>"${clamd_conf}" <<'EOF'

# PersoPyro web worker'ları için yalnızca loopback üzerinden tarama.
TCPSocket 3310
TCPAddr 127.0.0.1
StreamMaxLength 60M
MaxFileSize 60M
EOF

systemctl enable clamav-freshclam.service
systemctl restart clamav-freshclam.service
systemctl enable clamav-daemon.service
systemctl restart clamav-daemon.service

python3 - <<'PY'
import socket
import time

deadline = time.monotonic() + 30
last_error = None
while time.monotonic() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", 3310), timeout=2):
            break
    except OSError as error:
        last_error = error
        time.sleep(1)
else:
    raise SystemExit(f"clamd 127.0.0.1:3310 üzerinde açılmadı: {last_error}")
PY

DJANGO_SETTINGS_MODULE=config.settings \
FILE_SCANNING_REQUIRED=1 \
CLAMAV_HOST=127.0.0.1 \
CLAMAV_PORT=3310 \
CLAMAV_TIMEOUT_SECONDS=30 \
/home/hakan/persopyro/.venv/bin/python - <<'PY'
import django
from django.core.files.uploadedfile import SimpleUploadedFile

django.setup()

from apps.common.file_scanning import InfectedUploadError, scan_uploaded_file

scan_uploaded_file(SimpleUploadedFile("clamav-clean-check.txt", b"PersoPyro scanner check"))
eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
try:
    scan_uploaded_file(SimpleUploadedFile("clamav-eicar-check.txt", eicar))
except InfectedUploadError:
    pass
else:
    raise SystemExit("clamd EICAR test imzasını algılamadı.")
PY

echo "ClamAV clamd loopback:3310 üzerinde hazır; temiz ve EICAR taraması geçti."
