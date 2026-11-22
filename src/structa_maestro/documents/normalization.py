"""Lossless permitted text normalization preserving Unicode, whitespace, and leading zeros."""

from __future__ import annotations

import re

# Strip ASCII control characters other than tab (\t), newline (\n), and carriage return (\r)
_DANGEROUS_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_document_text(text: str) -> str:
    """Normalize line endings and non-breaking spaces without corrupting meaningful content."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = _DANGEROUS_CONTROL_RE.sub("", normalized)
    return normalized


def normalize_extracted_string(value: str) -> str:
    """Trim surrounding whitespace while preserving internal whitespace and leading zeros."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")
    normalized = _DANGEROUS_CONTROL_RE.sub("", normalized)
    return normalized.strip()
