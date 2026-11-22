"""PDF parser tests covering multi-page extraction, page limits, image-only, and corruption."""

from __future__ import annotations

from pathlib import Path

from conftest import create_minimal_pdf
from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.documents.loader import load_document


def test_pdf_multipage_preserves_page_segments(tmp_path: Path) -> None:
    pdf_path = create_minimal_pdf(
        tmp_path / "invoice.pdf",
        ["Invoice Number: INV-2026-001", "Total Amount: 1250.50"],
    )
    loaded = load_document(pdf_path)
    assert loaded.document is not None
    assert loaded.errors == []
    doc = loaded.document
    assert doc.format == DocumentFormat.PDF
    assert doc.page_count == 2
    assert doc.source.media_type == "application/pdf"
    assert any(seg.page == 1 and "INV-2026-001" in seg.text for seg in doc.segments)
    assert any(seg.page == 2 and "1250.50" in seg.text for seg in doc.segments)


def test_pdf_exceeding_page_limit_rejected(tmp_path: Path) -> None:
    pdf_path = create_minimal_pdf(tmp_path / "long.pdf", ["Page 1", "Page 2", "Page 3"])
    loaded = load_document(pdf_path, limits=DocumentLimits(max_pages=2))
    assert loaded.document is None
    assert any(e.code == "resource_limit_exceeded" for e in loaded.errors)


def test_pdf_no_extractable_text_and_corrupted_pdf(tmp_path: Path) -> None:
    blank_pdf = create_minimal_pdf(tmp_path / "blank.pdf", [""])
    res_blank = load_document(blank_pdf)
    assert res_blank.document is None
    assert any(e.code == "no_extractable_text" for e in res_blank.errors)

    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4\nnot valid pdf objects %%EOF")
    res_corrupt = load_document(corrupt)
    assert res_corrupt.document is None
    assert any(e.code == "corrupted_document" for e in res_corrupt.errors)
