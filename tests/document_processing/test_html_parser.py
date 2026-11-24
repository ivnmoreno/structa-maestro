"""Tests for hardened stdlib HTML parser (T003)."""

from __future__ import annotations

from pathlib import Path

from structa_maestro import DocumentFormat, DocumentInput, DocumentLimits
from structa_maestro.contracts.models import SourceMetadata
from structa_maestro.documents.html_parser import parse_html_bytes
from structa_maestro.documents.loader import load_document, load_document_input


def test_html_parser_strips_scripts_styles_comments_and_preserves_headings_and_tables(
    tmp_path: Path,
) -> None:
    html = """<!DOCTYPE html>
<html>
<head>
  <title>Invoice Portal</title>
  <style>.secret-css { color: red; content: "HIDDEN_CSS_TOKEN"; }</style>
  <script>const stolen = "HIDDEN_JS_TOKEN"; alert(1);</script>
</head>
<body>
  <!-- HIDDEN_COMMENT_TOKEN -->
  <noscript>HIDDEN_NOSCRIPT_TOKEN</noscript>
  <template><p>HIDDEN_TEMPLATE_TOKEN</p></template>
  <h1>Invoice Summary</h1>
  <p>Invoice Number: INV-2026-101 &amp; Confirmed</p>
  <p>Vendor: Acme &amp; Co. &#36;1250.50</p>
  <h2>Line Items</h2>
  <table>
    <tr><th>Item</th><th>Amount</th></tr>
    <tr><td>Industrial Pump</td><td>1250.50</td></tr>
  </table>
  <img src="https://example.invalid/pixel.png" alt="ignored" />
</body>
</html>
"""
    p = tmp_path / "invoice.htm"
    p.write_text(html, encoding="utf-8")

    res = load_document(p)
    assert res.document is not None
    assert not res.errors
    # .htm alias MUST NOT trigger spurious extension_mismatch warning
    assert not any(w.code == "extension_mismatch" for w in res.warnings)
    assert res.document.format == DocumentFormat.HTML
    assert res.source.media_type == "text/html"

    text = res.document.full_text
    assert "Invoice Number: INV-2026-101 & Confirmed" in text
    assert "Vendor: Acme & Co. $1250.50" in text
    assert "Industrial Pump | 1250.50" in text

    for forbidden in (
        "HIDDEN_CSS_TOKEN",
        "HIDDEN_JS_TOKEN",
        "HIDDEN_COMMENT_TOKEN",
        "HIDDEN_NOSCRIPT_TOKEN",
        "HIDDEN_TEMPLATE_TOKEN",
        "alert(1)",
    ):
        assert forbidden not in text

    # Heading-driven section tracking & 1-indexed paragraphs
    inv_seg = next(s for s in res.document.segments if "INV-2026-101" in s.text)
    assert inv_seg.heading == "Invoice Summary"
    assert inv_seg.paragraph is not None and inv_seg.paragraph >= 1
    row_seg = next(s for s in res.document.segments if "Industrial Pump" in s.text)
    assert row_seg.heading == "Line Items"
    assert res.document.full_text[row_seg.char_start : row_seg.char_end] == row_seg.text


def test_html_parser_encodings_markup_only_entity_dos_and_extensionless_sniffing() -> None:
    src = SourceMetadata(document_id="h1", filename="doc.html", media_type="text/html")

    # 1. Latin-1 via <meta charset="iso-8859-1">
    latin_html = b'<meta charset="iso-8859-1"><p>Caf\xe9 Total: 42.00</p>'
    parsed_lat = parse_html_bytes(latin_html, src, DocumentLimits())
    assert parsed_lat.document is not None
    assert "Café Total: 42.00" in parsed_lat.document.full_text

    # 2. Markup/script/comment-only HTML -> no_extractable_text
    only_markup = b"<html><script>var x=1;</script><style>p{}</style><!-- c --><div> </div></html>"
    parsed_empty = parse_html_bytes(only_markup, src, DocumentLimits())
    assert parsed_empty.document is None
    assert any(e.code == "no_extractable_text" for e in parsed_empty.errors)

    # 3. Incremental entity-expansion / text-bloat limit -> resource_limit_exceeded
    entity_bloat = b"<p>" + (b"&#x1F600;" * 200) + b"</p>"
    parsed_limit = parse_html_bytes(entity_bloat, src, DocumentLimits(max_decompressed_bytes=128))
    assert parsed_limit.document is None
    assert any(e.code == "resource_limit_exceeded" for e in parsed_limit.errors)

    # 4. Extensionless HTML bytes sniffed cleanly + binary rejected
    sniffed = load_document_input(
        DocumentInput.from_bytes(b"<!DOCTYPE html><html><body><p>Hello Web</p></body></html>")
    )
    assert sniffed.document is not None
    assert sniffed.document.format == DocumentFormat.HTML
    assert sniffed.source.media_type == "text/html"

    bad_bin = parse_html_bytes(b"\x89PNG\r\n\x1a\n\x00\x00", src, DocumentLimits())
    assert bad_bin.document is None
    assert any(e.code == "unsupported_format" for e in bad_bin.errors)
