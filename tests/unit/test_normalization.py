"""Data normalization unit tests covering NRM-001 through NRM-018."""

from __future__ import annotations

from structa_maestro.documents.normalization import (
    normalize_document_text,
    normalize_extracted_string,
)


def test_nrm_line_endings_and_whitespace() -> None:
    raw = "Line 1\r\nLine 2\rLine 3\n"
    assert normalize_document_text(raw) == "Line 1\nLine 2\nLine 3\n"
    assert normalize_extracted_string("  INV-2026-001  ") == "INV-2026-001"
    # NRM-002: Preserve meaningful internal whitespace
    assert normalize_extracted_string("  Acme   Corp  ") == "Acme   Corp"


def test_nrm_unicode_accents_currency_leading_zeros_and_phone() -> None:
    assert normalize_extracted_string("  Müller & Söhne €1,250.50  ") == "Müller & Söhne €1,250.50"
    # NRM-010 & NRM-011: Preserve leading zeros in identifiers & phone numbers
    assert normalize_extracted_string("0004821") == "0004821"
    assert normalize_extracted_string("+49 030 001234") == "+49 030 001234"


def test_nrm_empty_string_non_breaking_space_bidi_and_control_chars() -> None:
    # NRM-012: Empty string NOT silently converted to None
    assert normalize_extracted_string("") == ""
    # NRM-016: Non-breaking spaces normalized safely in document text without losing separation
    assert normalize_document_text("Invoice\u00a0Number:\u00a0INV-001") == "Invoice Number: INV-001"
    # NRM-017: Bidirectional text preserved safely
    bidi = "Name: \u202eABC\u202c"
    assert normalize_document_text(bidi) == bidi
    # NRM-018: Null bytes and dangerous control characters stripped safely from document text
    assert "\x00" not in normalize_document_text("Hello\x00World\tTab\nNext")
    assert "\t" in normalize_document_text("Hello\x00World\tTab\nNext")
