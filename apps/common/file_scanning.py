"""ClamAV clamd INSTREAM scanning for uploaded files."""

from __future__ import annotations

import socket
import struct

from django.conf import settings

CHUNK_SIZE = 64 * 1024
MAX_RESPONSE_BYTES = 8192


class FileScanUnavailableError(RuntimeError):
    """The required malware scanner is missing or could not complete a scan."""


class InfectedUploadError(RuntimeError):
    """ClamAV identified the uploaded file as infected."""


def scan_uploaded_file(uploaded) -> None:  # noqa: ANN001
    """Scan a Django uploaded file, failing closed when scanning is required."""
    if not settings.FILE_SCANNING_REQUIRED:
        return

    host = settings.CLAMAV_HOST.strip()
    if not host:
        raise FileScanUnavailableError("ClamAV is required but CLAMAV_HOST is not configured.")

    try:
        uploaded.seek(0)
        with socket.create_connection(
            (host, settings.CLAMAV_PORT), timeout=settings.CLAMAV_TIMEOUT_SECONDS
        ) as connection:
            connection.settimeout(settings.CLAMAV_TIMEOUT_SECONDS)
            connection.sendall(b"zINSTREAM\0")
            while chunk := uploaded.read(CHUNK_SIZE):
                connection.sendall(struct.pack("!I", len(chunk)))
                connection.sendall(chunk)
            connection.sendall(b"\0\0\0\0")
            response = bytearray()
            while len(response) < MAX_RESPONSE_BYTES and not response.endswith(b"\0"):
                part = connection.recv(1024)
                if not part:
                    break
                response.extend(part)
    except (OSError, TimeoutError) as error:
        raise FileScanUnavailableError("ClamAV scan could not be completed.") from error
    finally:
        uploaded.seek(0)

    result = bytes(response).rstrip(b"\0")
    if result.endswith(b": OK"):
        return
    if result.endswith(b" FOUND"):
        raise InfectedUploadError("ClamAV detected malware in the uploaded file.")
    raise FileScanUnavailableError("ClamAV returned an invalid scan response.")
