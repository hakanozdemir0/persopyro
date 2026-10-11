"""ClamAV INSTREAM upload scanning behavior."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.file_scanning import (
    FileScanUnavailableError,
    InfectedUploadError,
    scan_uploaded_file,
)


class FakeClamConnection:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        return None

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, _size: int) -> bytes:
        response, self.response = self.response, b""
        return response


def test_clamav_clean_response_scans_stream_and_rewinds(monkeypatch, settings) -> None:
    settings.FILE_SCANNING_REQUIRED = True
    settings.CLAMAV_HOST = "clamav.internal"
    uploaded = SimpleUploadedFile("record.txt", b"confidential employee file")
    fake = FakeClamConnection(b"stream: OK\0")
    monkeypatch.setattr(
        "apps.common.file_scanning.socket.create_connection", lambda *_a, **_kw: fake
    )

    scan_uploaded_file(uploaded)

    assert b"zINSTREAM\0" in fake.sent
    assert b"confidential employee file" in fake.sent
    assert uploaded.read() == b"confidential employee file"


def test_clamav_detected_file_is_rejected(monkeypatch, settings) -> None:
    settings.FILE_SCANNING_REQUIRED = True
    settings.CLAMAV_HOST = "clamav.internal"
    uploaded = SimpleUploadedFile("record.txt", b"EICAR test string")
    fake = FakeClamConnection(b"stream: Eicar-Test-Signature FOUND\0")
    monkeypatch.setattr(
        "apps.common.file_scanning.socket.create_connection", lambda *_a, **_kw: fake
    )

    with pytest.raises(InfectedUploadError):
        scan_uploaded_file(uploaded)

    assert uploaded.read() == b"EICAR test string"


def test_required_scanner_without_host_fails_closed(settings) -> None:
    settings.FILE_SCANNING_REQUIRED = True
    settings.CLAMAV_HOST = ""

    with pytest.raises(FileScanUnavailableError):
        scan_uploaded_file(SimpleUploadedFile("record.txt", b"private"))


def test_unreachable_scanner_fails_closed(monkeypatch, settings) -> None:
    settings.FILE_SCANNING_REQUIRED = True
    settings.CLAMAV_HOST = "clamav.internal"

    def unreachable(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("apps.common.file_scanning.socket.create_connection", unreachable)
    with pytest.raises(FileScanUnavailableError):
        scan_uploaded_file(SimpleUploadedFile("record.txt", b"private"))
