"""Shared pytest fixtures and synthetic document generators for structa-maestro."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CONTRACT_SCHEMA_PATH = (
    Path(__file__).parent.parent
    / "specs"
    / "001-structured-extraction-mvp"
    / "contracts"
    / "canonical-output-contract.schema.json"
)


def create_minimal_pdf(path: Path, pages: list[str]) -> Path:
    """Create a valid, minimal text-based PDF file using raw PDF objects (no extra deps)."""

    def _pdf_escape(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    objects: list[bytes] = []
    # 1: Catalog, 2: Pages, 3: Font
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_obj_ids: list[int] = []
    next_id = 4
    page_entries: list[tuple[int, int, bytes]] = []
    for page_text in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)
        lines = _pdf_escape(page_text).split("\n")
        stream_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for idx, line in enumerate(lines):
            if idx == 0:
                stream_lines.append(f"({line}) Tj")
            else:
                stream_lines.append(f"T* ({line}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1", errors="replace")
        page_entries.append((page_id, content_id, stream_bytes))

    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for _page_id, content_id, stream_bytes in page_entries:
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream_bytes)).encode("ascii")
            + b" >>\nstream\n"
            + stream_bytes
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj_num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def create_minimal_docx(
    path: Path,
    paragraphs: list[str],
    *,
    headings: list[str] | None = None,
    tables: list[list[list[str]]] | None = None,
) -> Path:
    """Create a valid DOCX file using python-docx."""
    doc = docx.Document()
    if headings:
        for h in headings:
            doc.add_heading(h, level=1)
    for para in paragraphs:
        doc.add_paragraph(para)
    if tables:
        for table_rows in tables:
            if not table_rows:
                continue
            tbl = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
            for r_idx, row in enumerate(table_rows):
                for c_idx, cell_val in enumerate(row):
                    tbl.cell(r_idx, c_idx).text = cell_val
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def create_zip_bomb_docx(path: Path, uncompressed_bytes: int = 60 * 1024 * 1024) -> Path:
    """Create a tiny compressed DOCX-like ZIP archive whose uncompressed payload exceeds limits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr("word/padding.xml", "A" * uncompressed_bytes)
    return path


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def contract_json_schema() -> dict[str, Any]:
    return json.loads(CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def invoice_schema_dict() -> dict[str, Any]:
    return json.loads(
        (FIXTURES_DIR / "schemas" / "invoice_schema.json").read_text(encoding="utf-8")
    )
