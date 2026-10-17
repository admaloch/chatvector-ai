"""Safe DOCX (OOXML) text extraction with zip-bomb guards."""

from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from core.config import config

logger = logging.getLogger(__name__)


class DocxExtractionError(ValueError):
    """Raised when DOCX bytes cannot be parsed safely."""

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
DOCX_CONTENT_TYPES = frozenset({DOCX_CONTENT_TYPE})
DOCX_FILENAME_SUFFIXES = (".docx",)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_TAG = lambda name: f"{{{_W_NS}}}{name}"  # noqa: E731

_DOCX_MAIN_PART = "word/document.xml"
_ENCRYPTED_MARKERS = frozenset({"EncryptedPackage", "EncryptionInfo"})

_TABLE_CELL_DELIMITER = "\t"
_TABLE_ROW_DELIMITER = "\n"


@dataclass(frozen=True)
class _ZipInspectionLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_compression_ratio: int


def is_docx_content_type(content_type: str | None) -> bool:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized in DOCX_CONTENT_TYPES


def is_docx_filename(filename: str) -> bool:
    lower = (filename or "").lower()
    return any(lower.endswith(suffix) for suffix in DOCX_FILENAME_SUFFIXES)


def docx_package_has_expected_layout(names: set[str]) -> bool:
    """Return True when the archive looks like an unencrypted Word document package."""
    if _DOCX_MAIN_PART not in names:
        return False
    if names & _ENCRYPTED_MARKERS and _DOCX_MAIN_PART not in names:
        return False
    return True


def is_encrypted_docx_package(names: set[str]) -> bool:
    return bool(names & _ENCRYPTED_MARKERS) and _DOCX_MAIN_PART not in names


def _zip_limits() -> _ZipInspectionLimits:
    return _ZipInspectionLimits(
        max_entries=config.DOCX_MAX_ZIP_ENTRIES,
        max_uncompressed_bytes=config.DOCX_MAX_UNCOMPRESSED_BYTES,
        max_compression_ratio=config.DOCX_MAX_COMPRESSION_RATIO,
    )


def inspect_docx_zip(contents: bytes) -> zipfile.ZipFile:
    """
    Open a DOCX archive after enforcing entry-count, size, and compression-ratio limits.

    The returned ZipFile must be used as a context manager by the caller.
    """
    if not contents.startswith(b"PK"):
        raise DocxExtractionError("Failed to parse DOCX content.")

    limits = _zip_limits()
    try:
        archive = zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile as exc:
        raise DocxExtractionError("Failed to parse DOCX content.") from exc

    try:
        entries = archive.infolist()
    except Exception as exc:
        archive.close()
        raise DocxExtractionError("Failed to parse DOCX content.") from exc

    if len(entries) > limits.max_entries:
        archive.close()
        raise DocxExtractionError("DOCX archive exceeds safe entry limit.")

    total_uncompressed = 0
    for info in entries:
        if info.is_dir():
            continue
        uncompressed = info.file_size
        compressed = max(info.compress_size, 1)
        if uncompressed > limits.max_uncompressed_bytes:
            archive.close()
            raise DocxExtractionError("DOCX archive exceeds safe uncompressed size.")
        ratio = uncompressed // compressed
        if ratio > limits.max_compression_ratio and uncompressed > config.MAX_UPLOAD_SIZE_BYTES:
            archive.close()
            raise DocxExtractionError("DOCX archive exceeds safe compression ratio.")

        total_uncompressed += uncompressed
        if total_uncompressed > limits.max_uncompressed_bytes:
            archive.close()
            raise DocxExtractionError("DOCX archive exceeds safe uncompressed size.")

    return archive


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(_W_TAG("t")))


def _table_text(table: ET.Element) -> str:
    rows: list[str] = []
    for row in table.findall(_W_TAG("tr")):
        cells: list[str] = []
        for cell in row.findall(_W_TAG("tc")):
            cell_parts: list[str] = []
            for paragraph in cell.iter(_W_TAG("p")):
                text = _paragraph_text(paragraph)
                if text:
                    cell_parts.append(text)
            cells.append(" ".join(cell_parts).strip())
        rows.append(_TABLE_CELL_DELIMITER.join(cells))
    return _TABLE_ROW_DELIMITER.join(rows)


def _extract_body_text(document_xml: bytes) -> str:
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise DocxExtractionError("Failed to parse DOCX content.") from exc

    body = root.find(_W_TAG("body"))
    if body is None:
        raise DocxExtractionError("Failed to parse DOCX content.")

    blocks: list[str] = []
    for child in body:
        tag = child.tag
        if tag == _W_TAG("p"):
            text = _paragraph_text(child)
            if text.strip():
                blocks.append(text)
        elif tag == _W_TAG("tbl"):
            table = _table_text(child)
            if table.strip():
                blocks.append(table)
        elif tag == _W_TAG("sectPr"):
            continue

    if not blocks:
        raise DocxExtractionError("No extractable text found in DOCX content.")

    return "\n\n".join(blocks)


def extract_docx_text(contents: bytes, *, filename: str = "") -> str:
    """
    Extract visible text from DOCX bytes with zip-bomb guards.

    Raises DocxExtractionError when the payload is encrypted, corrupt, or unsafe.
    """
    archive = inspect_docx_zip(contents)
    try:
        names = set(archive.namelist())
        if is_encrypted_docx_package(names):
            logger.error(f"Encrypted DOCX file {filename} is not supported")
            raise DocxExtractionError("Encrypted DOCX files are not supported.")
        if _DOCX_MAIN_PART not in names:
            logger.error(f"DOCX file {filename} is missing {_DOCX_MAIN_PART}")
            raise DocxExtractionError("Failed to parse DOCX content.")

        try:
            document_xml = archive.read(_DOCX_MAIN_PART)
        except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
            logger.error(f"Failed to read DOCX main document from {filename}: {exc}")
            raise DocxExtractionError("Failed to parse DOCX content.") from exc

        if b"\x00" in document_xml:
            raise DocxExtractionError("Failed to parse DOCX content.")

        return _extract_body_text(document_xml)
    finally:
        archive.close()
