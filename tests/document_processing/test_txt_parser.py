"""TXT parser tests covering encodings, BOM, line endings, Unicode, and empty inputs."""

from __future__ import annotations

from pathlib import Path

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.documents.loader import load_document


def test_txt_utf8_and_line_segments(tmp_path: Path) -> None:
    p = tmp_path / "sample.txt"
    p.write_text("Line one\r\nLine two\nLine three", encoding="utf-8")
    loaded = load_document(p)
    assert loaded.document is not None
    assert loaded.errors == []
    doc = loaded.document
    assert doc.format == DocumentFormat.TXT
    assert doc.source.media_type == "text/plain"
    assert doc.full_text == "Line one\nLine two\nLine three"
    assert len(doc.segments) == 3
    assert doc.segments[0].line_start == 1
    assert doc.segments[2].line_start == 3


def test_txt_encodings_bom_utf16_latin1(tmp_path: Path) -> None:
    bom = tmp_path / "bom.txt"
    bom.write_bytes("Café €100".encode("utf-8-sig"))
    res_bom = load_document(bom)
    assert res_bom.document is not None
    assert res_bom.document.full_text == "Café €100"

    u16 = tmp_path / "u16.txt"
    u16.write_bytes("Invoice: 00123".encode("utf-16"))
    res_u16 = load_document(u16)
    assert res_u16.document is not None
    assert "00123" in res_u16.document.full_text

    latin = tmp_path / "latin.txt"
    latin.write_bytes("Müller Strasse".encode("cp1252"))
    res_latin = load_document(latin)
    assert res_latin.document is not None
    assert "Müller" in res_latin.document.full_text


def test_txt_empty_and_binary_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    res_empty = load_document(empty)
    assert res_empty.document is None
    assert any(e.code == "empty_document" for e in res_empty.errors)

    ws = tmp_path / "ws.txt"
    ws.write_text("   \n\t  ", encoding="utf-8")
    res_ws = load_document(ws)
    assert res_ws.document is None
    assert any(e.code in {"empty_document", "no_extractable_text"} for e in res_ws.errors)

    binary = tmp_path / "fake.txt"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00\x01")
    res_bin = load_document(binary, limits=DocumentLimits())
    assert res_bin.document is None
    assert any(e.code in {"unsupported_format", "decoding_error"} for e in res_bin.errors)
