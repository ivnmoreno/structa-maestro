"""Tests for hardened stdlib Markdown parser (T005)."""

from __future__ import annotations

from pathlib import Path

from structa_maestro import DocumentFormat, DocumentInput, DocumentLimits
from structa_maestro.contracts.models import SourceMetadata
from structa_maestro.documents.loader import load_document, load_document_input
from structa_maestro.documents.markdown_parser import parse_markdown_bytes


def test_markdown_parser_tracks_atx_setext_headings_lines_and_ignores_code_fence_hashes(
    tmp_path: Path,
) -> None:
    md_text = (
        "Incident Report\n"
        "===============\n\n"
        "Summary: Outage resolved in 12 minutes.\n\n"
        "## Root Cause\n\n"
        "```python\n"
        "# NOT A HEADING INSIDE CODE\n"
        "def fix():\n"
        "    return True\n"
        "```\n\n"
        "Database pool exhausted under peak load.\n\n"
        "### Follow-Up Actions\n\n"
        "- Increase pool size to 64\n"
        "- Add saturation alert\n"
    )
    p = tmp_path / "postmortem.markdown"
    p.write_text(md_text, encoding="utf-8")

    res = load_document(p)
    assert res.document is not None
    assert not res.errors
    # .markdown alias MUST NOT trigger spurious extension_mismatch warning
    assert not any(w.code == "extension_mismatch" for w in res.warnings)
    assert res.document.format == DocumentFormat.MARKDOWN
    assert res.source.media_type == "text/markdown"

    summary_seg = next(s for s in res.document.segments if "Outage resolved" in s.text)
    assert summary_seg.heading == "Incident Report"
    assert summary_seg.line_start == 4
    assert summary_seg.line_end == 4
    assert summary_seg.paragraph is not None and summary_seg.paragraph >= 1

    # Code fence '#' comment MUST NOT overwrite 'Root Cause' heading!
    db_seg = next(s for s in res.document.segments if "Database pool exhausted" in s.text)
    assert db_seg.heading == "Root Cause"

    action_seg = next(s for s in res.document.segments if "Increase pool size" in s.text)
    assert action_seg.heading == "Follow-Up Actions"
    assert action_seg.line_start is not None and action_seg.line_end is not None
    assert action_seg.line_end > action_seg.line_start
    assert res.document.full_text[action_seg.char_start : action_seg.char_end] == action_seg.text


def test_markdown_parser_encodings_empty_limits_and_from_text() -> None:
    src = SourceMetadata(document_id="m1", filename="doc.md", media_type="text/markdown")

    # UTF-16 & UTF-8 BOM
    for encoded in (
        "# Heading\n\nValue: 100\n".encode("utf-16"),
        "# Heading\n\nValue: 100\n".encode("utf-8-sig"),
    ):
        parsed = parse_markdown_bytes(encoded, src, DocumentLimits())
        assert parsed.document is not None
        assert "Value: 100" in parsed.document.full_text

    # Whitespace-only -> no_extractable_text
    ws_res = parse_markdown_bytes(b"   \n\t\n  ", src, DocumentLimits())
    assert ws_res.document is None
    assert any(e.code == "no_extractable_text" for e in ws_res.errors)

    # Oversized decompressed limit -> resource_limit_exceeded
    big_res = parse_markdown_bytes(
        b"# Title\n\n" + b"A" * 512, src, DocumentLimits(max_decompressed_bytes=64)
    )
    assert big_res.document is None
    assert any(e.code == "resource_limit_exceeded" for e in big_res.errors)

    # DocumentInput.from_text with media_type="text/markdown"
    in_mem = load_document_input(
        DocumentInput.from_text("# Contract\n\nParty: Beta LLC\n", media_type="text/markdown")
    )
    assert in_mem.document is not None
    assert in_mem.document.format == DocumentFormat.MARKDOWN
    assert in_mem.source.media_type == "text/markdown"
