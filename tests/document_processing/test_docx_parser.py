"""DOCX parser tests covering paragraphs, headings, tables, ZIP bombs, and corruption."""

from __future__ import annotations

from pathlib import Path

from conftest import create_minimal_docx, create_zip_bomb_docx
from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.documents.loader import load_document


def test_docx_extracts_headings_paragraphs_and_tables(tmp_path: Path) -> None:
    docx_path = create_minimal_docx(
        tmp_path / "invoice.docx",
        paragraphs=["Invoice Number: INV-2026-001", "Vendor: Acme Supply"],
        headings=["Commercial Invoice"],
        tables=[[["Item", "Total"], ["Widget", "1250.50"]]],
    )
    loaded = load_document(docx_path)
    assert loaded.document is not None
    assert loaded.errors == []
    doc = loaded.document
    assert doc.format == DocumentFormat.DOCX
    assert "INV-2026-001" in doc.full_text
    assert "1250.50" in doc.full_text
    assert any(seg.paragraph is not None for seg in doc.segments)


def test_docx_zip_bomb_and_corrupted_rejected(tmp_path: Path) -> None:
    bomb = create_zip_bomb_docx(tmp_path / "bomb.docx", uncompressed_bytes=2 * 1024 * 1024)
    res_bomb = load_document(bomb, limits=DocumentLimits(max_decompressed_bytes=512 * 1024))
    assert res_bomb.document is None
    assert any(e.code == "resource_limit_exceeded" for e in res_bomb.errors)

    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"PK\x03\x04corrupted zip archive bytes")
    res_corrupt = load_document(corrupt)
    assert res_corrupt.document is None
    assert any(e.code in {"corrupted_document", "unsupported_format"} for e in res_corrupt.errors)
