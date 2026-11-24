"""Tests for hardened stdlib RFC 5322 (.eml) parser (T007)."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from structa_maestro import DocumentFormat, DocumentInput, DocumentLimits
from structa_maestro.contracts.models import SourceMetadata
from structa_maestro.documents.eml_parser import (
    MAX_EML_NESTING_DEPTH,
    MAX_EML_PARTS,
    parse_eml_bytes,
)
from structa_maestro.documents.loader import load_document, load_document_input


def _make_multipart_email(*, include_plain: bool = True, include_attachment: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "billing@acme.example"
    msg["To"] = "ap@buyer.example"
    msg["Date"] = "Sun, 15 Mar 2026 09:00:00 +0000"
    msg["Subject"] = "Invoice INV-2026-001 — März"
    if include_plain:
        msg.set_content("Invoice Number: INV-2026-001\nTotal Amount: 1250.50\nCurrency: USD\n")
        msg.add_alternative(
            "<html><script>evil()</script><body><p>HTML_ALT_ONLY_MARKER</p></body></html>",
            subtype="html",
        )
    else:
        msg.set_content(
            "<html><head><style>body{color:red}</style></head>"
            "<body><script>steal()</script><h1>Billing Notice</h1>"
            "<p>Invoice Number: INV-2026-002</p><p>Total Amount: 990.00</p></body></html>",
            subtype="html",
        )
    if include_attachment:
        msg.add_attachment(
            b"\x89PNG\r\n\x1a\nSECRET_ATTACHMENT_BYTES",
            maintype="image",
            subtype="png",
            filename="../../evil_secret_attachment.png",
        )
    return msg.as_bytes()


def test_eml_parser_prefers_plain_falls_back_to_html_and_skips_attachments_safely(
    tmp_path: Path,
) -> None:
    # 1. multipart/alternative prefers text/plain over text/html
    alt_path = tmp_path / "alt.eml"
    alt_path.write_bytes(_make_multipart_email(include_plain=True, include_attachment=False))
    res_alt = load_document(alt_path)
    assert res_alt.document is not None
    assert not res_alt.errors
    assert res_alt.document.format == DocumentFormat.EML
    assert res_alt.source.media_type == "message/rfc822"
    assert "From: billing@acme.example" in res_alt.document.full_text
    assert "Subject: Invoice INV-2026-001 — März" in res_alt.document.full_text
    assert "Invoice Number: INV-2026-001" in res_alt.document.full_text
    assert "HTML_ALT_ONLY_MARKER" not in res_alt.document.full_text
    assert res_alt.document.segments[0].heading == "Headers"
    assert res_alt.document.segments[1].heading == "Body"

    # 2. HTML-only EML falls back cleanly to hardened HTML parser (strips scripts/styles)
    html_only = load_document_input(
        DocumentInput.from_bytes(
            _make_multipart_email(include_plain=False, include_attachment=True),
            filename="html_only.eml",
        )
    )
    assert html_only.document is not None
    assert "Invoice Number: INV-2026-002" in html_only.document.full_text
    assert "steal()" not in html_only.document.full_text
    assert "SECRET_ATTACHMENT_BYTES" not in html_only.document.full_text
    assert "evil_secret_attachment" not in html_only.document.full_text
    # Emits structured attachments_skipped warning without leaking unsafe filename or bytes
    att_warnings = [w for w in html_only.warnings if w.code == "attachments_skipped"]
    assert len(att_warnings) == 1
    assert "1" in att_warnings[0].message
    assert "evil_secret_attachment" not in att_warnings[0].message

    # 3. Extensionless RFC 5322 bytes sniffed cleanly as EML
    sniffed = load_document_input(
        DocumentInput.from_bytes(
            b"From: a@example.com\r\nSubject: Hello\r\n\r\nBody text here.\r\n"
        )
    )
    assert sniffed.document is not None
    assert sniffed.document.format == DocumentFormat.EML
    assert sniffed.source.media_type == "message/rfc822"


def test_eml_parser_enforces_part_count_nesting_depth_and_empty_checks() -> None:
    src = SourceMetadata(document_id="e1", filename="bomb.eml", media_type="message/rfc822")

    # 1. Excess MIME parts (> MAX_EML_PARTS) -> resource_limit_exceeded
    many_parts = EmailMessage()
    many_parts["From"] = "a@example.com"
    many_parts["Subject"] = "Many parts"
    many_parts.make_mixed()
    for i in range(MAX_EML_PARTS + 5):
        part = EmailMessage()
        part.set_content(f"part {i}")
        many_parts.attach(part)
    res_parts = parse_eml_bytes(many_parts.as_bytes(), src, DocumentLimits())
    assert res_parts.document is None
    assert any(e.code == "resource_limit_exceeded" for e in res_parts.errors)

    # 2. Excess MIME nesting depth (> MAX_EML_NESTING_DEPTH) -> resource_limit_exceeded
    nested = EmailMessage()
    nested.set_content("deep leaf")
    for _ in range(MAX_EML_NESTING_DEPTH + 3):
        wrapper = EmailMessage()
        wrapper.make_mixed()
        wrapper.attach(nested)
        nested = wrapper
    nested["From"] = "a@example.com"
    nested["Subject"] = "Deep"
    res_depth = parse_eml_bytes(nested.as_bytes(), src, DocumentLimits())
    assert res_depth.document is None
    assert any(e.code == "resource_limit_exceeded" for e in res_depth.errors)

    # 3. Blank EML with no headers and no body -> no_extractable_text
    res_blank = parse_eml_bytes(b"\r\n\r\n   \r\n", src, DocumentLimits())
    assert res_blank.document is None
    assert any(e.code == "no_extractable_text" for e in res_blank.errors)
