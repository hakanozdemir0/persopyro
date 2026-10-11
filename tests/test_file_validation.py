"""Structural validation for uploaded documents."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.file_validation import (
    InvalidUploadStructureError,
    validate_document_structure,
)


def _minimal_pdf() -> bytes:
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for number, body in enumerate(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>",
        ),
        start=1,
    ):
        offsets.append(sum(map(len, chunks)))
        chunks.append(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = sum(map(len, chunks))
    chunks.append(b"xref\n0 4\n0000000000 65535 f \n")
    chunks.extend(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    chunks.append(
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return b"".join(chunks)


def _ooxml_docx() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<document xmlns="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>',
        )
    return stream.getvalue()


def test_valid_pdf_is_parsed_and_upload_stream_is_rewound() -> None:
    upload = SimpleUploadedFile("valid.pdf", _minimal_pdf())

    validate_document_structure(upload)

    assert upload.read().startswith(b"%PDF-1.4")


def test_pdf_with_fake_header_is_rejected() -> None:
    upload = SimpleUploadedFile("broken.pdf", b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(InvalidUploadStructureError):
        validate_document_structure(upload)


def test_ooxml_document_checks_archive_and_required_xml() -> None:
    upload = SimpleUploadedFile("valid.docx", _ooxml_docx())

    validate_document_structure(upload)

    malformed = SimpleUploadedFile("broken.docx", b"PK\x03\x04not a valid archive")
    with pytest.raises(InvalidUploadStructureError):
        validate_document_structure(malformed)


def test_rtf_and_csv_structure_are_checked() -> None:
    validate_document_structure(SimpleUploadedFile("valid.rtf", b"{\\rtf1 Test}"))
    validate_document_structure(SimpleUploadedFile("valid.csv", b"name,code\nTest,1\n"))

    with pytest.raises(InvalidUploadStructureError):
        validate_document_structure(SimpleUploadedFile("broken.rtf", b"{\\rtf1 Test"))
