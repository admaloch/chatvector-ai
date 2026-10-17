"""Minimal OOXML (.docx) builders for ingestion tests."""

from __future__ import annotations

import io
import zipfile


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _paragraph_xml(text: str) -> str:
    return (
        f'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:r><w:t>{_escape_xml(text)}</w:t></w:r></w:p>"
    )


def _table_xml(rows: list[list[str]]) -> str:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    row_parts: list[str] = []
    for row in rows:
        cells = []
        for cell_text in row:
            cells.append(
                f"<w:tc><w:p><w:r><w:t>{_escape_xml(cell_text)}</w:t></w:r></w:p></w:tc>"
            )
        row_parts.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return f"<w:tbl {ns}>{''.join(row_parts)}</w:tbl>"


def build_minimal_docx(
    *,
    paragraphs: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
) -> bytes:
    body_parts: list[str] = []
    for paragraph in paragraphs or []:
        body_parts.append(_paragraph_xml(paragraph))
    if table_rows:
        body_parts.append(_table_xml(table_rows))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_parts)}<w:sectPr/></w:body></w:document>"
    ).encode("utf-8")

    content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

    root_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    doc_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def build_encrypted_docx_stub() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("EncryptionInfo", b"encrypted-stub")
        archive.writestr("EncryptedPackage", b"encrypted-stub")
    return buffer.getvalue()


def build_zip_bomb_like_docx(*, payload_size: int = 400_000) -> bytes:
    """Highly compressible payload to trigger compression-ratio guards in tests."""
    repetitive = ("A" * 1000).encode("ascii")
    payload = repetitive * (payload_size // len(repetitive))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", payload)
        archive.writestr("_rels/.rels", b"<Relationships/>")
    return buffer.getvalue()
