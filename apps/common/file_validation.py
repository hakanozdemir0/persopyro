"""Structural checks for document formats accepted by employee upload endpoints."""

from __future__ import annotations

import csv
import io
import shutil
import subprocess
import zipfile
from pathlib import PurePosixPath

import olefile
from defusedxml import ElementTree

MAX_ARCHIVE_MEMBERS = 2_000
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
PDF_TIMEOUT_SECONDS = 10

OOXML_MAIN_PARTS = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
ODF_MIMETYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}
OLE_MAIN_STREAMS = {
    "doc": "worddocument",
    "xls": "workbook",
    "ppt": "powerpoint document",
}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "heic", "heif"}
TEXT_EXTENSIONS = {"txt", "csv"}


class InvalidUploadStructureError(ValueError):
    """An uploaded file's bytes do not form the declared supported format."""


class FileValidationUnavailableError(RuntimeError):
    """A required system parser is missing or unavailable."""


def validate_document_structure(uploaded) -> None:  # noqa: ANN001
    """Reject malformed, mismatched or resource-heavy PDF/Office documents."""
    suffix = uploaded.name.rsplit(".", 1)[-1].lower() if "." in uploaded.name else ""
    if suffix in IMAGE_EXTENSIONS:
        return
    uploaded.seek(0)
    try:
        if suffix == "pdf":
            _validate_pdf(uploaded.read())
        elif suffix in OOXML_MAIN_PARTS or suffix in ODF_MIMETYPES:
            _validate_zip_document(uploaded, suffix)
        elif suffix in OLE_MAIN_STREAMS:
            _validate_ole_document(uploaded, suffix)
        elif suffix == "rtf":
            _validate_rtf(uploaded.read())
        elif suffix in TEXT_EXTENSIONS:
            _validate_text(uploaded.read(), suffix)
    finally:
        uploaded.seek(0)


def _validate_pdf(contents: bytes) -> None:
    if not contents.startswith(b"%PDF-") or b"%%EOF" not in contents[-2048:]:
        raise InvalidUploadStructureError("PDF imzası veya bitiş kaydı geçersiz.")
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is None:
        raise FileValidationUnavailableError("PDF doğrulayıcısı kurulu değil.")
    try:
        result = subprocess.run(  # noqa: S603 - fixed system parser, argv only, no shell
            [pdfinfo, "-"],
            input=contents,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PDF_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FileValidationUnavailableError("PDF doğrulayıcısı çalıştırılamadı.") from error
    if result.returncode != 0:
        raise InvalidUploadStructureError("PDF dosyası yapısal olarak geçersiz.")


def _validate_zip_document(uploaded, suffix: str) -> None:  # noqa: ANN001
    try:
        with zipfile.ZipFile(uploaded) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise InvalidUploadStructureError("Office arşivindeki dosya sayısı geçersiz.")
            total_size = 0
            names = set()
            for member in members:
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                    raise InvalidUploadStructureError("Office arşivinde geçersiz yol bulundu.")
                normalized = member.filename.casefold()
                if normalized in names:
                    raise InvalidUploadStructureError("Office arşivinde yinelenen dosya bulundu.")
                names.add(normalized)
                if member.flag_bits & 0x1:
                    raise InvalidUploadStructureError("Şifreli Office dosyaları desteklenmiyor.")
                total_size += member.file_size
                if member.file_size > MAX_MEMBER_BYTES:
                    raise InvalidUploadStructureError("Office iç dosya boyutu sınırı aşıyor.")
                if member.file_size and (
                    member.compress_size == 0
                    or member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise InvalidUploadStructureError("Office arşiv sıkıştırma oranı geçersiz.")
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise InvalidUploadStructureError("Office açılmış toplam boyut sınırını aşıyor.")
            if archive.testzip() is not None:
                raise InvalidUploadStructureError("Office arşivinde bozuk içerik bulundu.")

            member_names = {item.filename for item in members}
            if suffix in OOXML_MAIN_PARTS:
                main_part = OOXML_MAIN_PARTS[suffix]
                required = {"[Content_Types].xml", "_rels/.rels", main_part}
                if not required.issubset(member_names):
                    raise InvalidUploadStructureError("Office belgesinin ana bölümleri eksik.")
                for xml_name in ("[Content_Types].xml", "_rels/.rels", main_part):
                    ElementTree.fromstring(archive.read(xml_name))
            else:
                if not members or members[0].filename != "mimetype":
                    raise InvalidUploadStructureError("OpenDocument mimetype kaydı eksik.")
                if members[0].compress_type != zipfile.ZIP_STORED:
                    raise InvalidUploadStructureError("OpenDocument mimetype kaydı sıkıştırılmış.")
                expected = ODF_MIMETYPES[suffix].encode("ascii")
                if archive.read("mimetype") != expected or "content.xml" not in member_names:
                    raise InvalidUploadStructureError(
                        "OpenDocument türü veya ana içeriği geçersiz."
                    )
                ElementTree.fromstring(archive.read("content.xml"))
    except InvalidUploadStructureError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise InvalidUploadStructureError("Office dosyası yapısal olarak geçersiz.") from error


def _validate_ole_document(uploaded, suffix: str) -> None:  # noqa: ANN001
    uploaded.seek(0)
    try:
        if not olefile.isOleFile(uploaded):
            raise InvalidUploadStructureError("Office dosyası OLE imzasıyla eşleşmiyor.")
        uploaded.seek(0)
        with olefile.OleFileIO(uploaded) as compound:
            streams = {"/".join(parts).casefold() for parts in compound.listdir()}
            if OLE_MAIN_STREAMS[suffix] not in streams:
                raise InvalidUploadStructureError("Office belgesinin ana içeriği bulunamadı.")
            if compound.parsing_issues:
                raise InvalidUploadStructureError("Office dosyasında bozuk OLE yapısı bulundu.")
    except InvalidUploadStructureError:
        raise
    except (OSError, ValueError, olefile.OleFileError) as error:
        raise InvalidUploadStructureError("Office dosyası yapısal olarak geçersiz.") from error
    finally:
        uploaded.seek(0)


def _validate_rtf(contents: bytes) -> None:
    if not contents.startswith((b"{\\rtf", b"{\\urtf")):
        raise InvalidUploadStructureError("RTF başlığı geçersiz.")
    depth = 0
    index = 0
    while index < len(contents):
        byte = contents[index]
        if byte == 0x5C:
            index += 1
            if index >= len(contents):
                raise InvalidUploadStructureError("RTF sonundaki kaçış karakteri eksik.")
            if 0x41 <= contents[index] <= 0x5A or 0x61 <= contents[index] <= 0x7A:
                word_start = index
                while index < len(contents) and (
                    0x41 <= contents[index] <= 0x5A or 0x61 <= contents[index] <= 0x7A
                ):
                    index += 1
                control_word = contents[word_start:index].lower()
                negative = index < len(contents) and contents[index] == 0x2D
                if negative:
                    index += 1
                number_start = index
                while index < len(contents) and 0x30 <= contents[index] <= 0x39:
                    index += 1
                if control_word == b"bin" and number_start < index:
                    binary_length = int(contents[number_start:index])
                    if negative or index + binary_length > len(contents):
                        raise InvalidUploadStructureError("RTF ikili içerik uzunluğu geçersiz.")
                    index += binary_length
                elif index < len(contents) and contents[index] == 0x20:
                    index += 1
                continue
            index += 1
            continue
        if byte == 0x7B:
            depth += 1
        elif byte == 0x7D:
            depth -= 1
            if depth < 0:
                raise InvalidUploadStructureError("RTF blok yapısı geçersiz.")
        index += 1
    if depth != 0:
        raise InvalidUploadStructureError("RTF kapanış bloğu eksik.")


def _validate_text(contents: bytes, suffix: str) -> None:
    decoded = None
    for encoding in ("utf-8-sig", "utf-16", "cp1254"):
        try:
            decoded = contents.decode(encoding)
            break
        except UnicodeError:
            continue
    if decoded is None or "\x00" in decoded:
        raise InvalidUploadStructureError("Metin dosyası biçimi geçersiz.")
    if suffix == "csv":
        try:
            for _row in csv.reader(io.StringIO(decoded), strict=True):
                pass
        except csv.Error as error:
            raise InvalidUploadStructureError("CSV yapısı geçersiz.") from error
