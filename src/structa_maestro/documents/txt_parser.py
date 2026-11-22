"""Multi-encoding TXT parser with line-number DocumentSegment tracking."""

from __future__ import annotations

from dataclasses import dataclass

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text

_KNOWN_BINARY_SIGNATURES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\x7fELF",
    b"MZ",
    b"\x1f\x8b\x08",
    b"Rar!",
    b"7z\xbc\xaf\x27\x1c",
    b"\x00\x00\x01\x00",
)


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Outcome of a single document parser invocation."""

    document: PreparedDocument | None
    errors: tuple[DiagnosticIssue, ...] = ()
    warnings: tuple[DiagnosticIssue, ...] = ()


def _looks_binary(raw: bytes) -> bool:
    for sig in _KNOWN_BINARY_SIGNATURES:
        if raw.startswith(sig):
            return True
    # UTF-16 BOM legitimately contains null bytes
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    if b"\x00" in raw[:8192]:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
    return (non_text / len(sample)) > 0.25


def _decode_text_bytes(raw: bytes) -> str | None:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def parse_txt_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse raw TXT bytes into a PreparedDocument with 1-indexed line DocumentSegments."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input TXT document is empty (zero bytes).",
                ),
            ),
        )

    if _looks_binary(raw):
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="unsupported_format",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input file appears to contain binary data rather than plain text.",
                ),
            ),
        )

    decoded = _decode_text_bytes(raw)
    if decoded is None:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="decoding_error",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Unable to decode TXT document with supported text encodings.",
                ),
            ),
        )

    normalized = normalize_document_text(decoded)
    if len(normalized.encode("utf-8")) > limits.max_decompressed_bytes:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Prepared TXT text exceeds configured max_decompressed_bytes limit.",
                ),
            ),
        )

    if not normalized.strip():
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="TXT document contains no extractable text (whitespace only).",
                ),
            ),
        )

    segments: list[DocumentSegment] = []
    offset = 0
    lines = normalized.split("\n")
    for idx, line in enumerate(lines, start=1):
        char_start = offset
        char_end = offset + len(line)
        segments.append(
            DocumentSegment(
                text=line,
                char_start=char_start,
                char_end=char_end,
                line_start=idx,
                line_end=idx,
            )
        )
        offset = char_end + 1  # account for '\n'

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.TXT,
            full_text=normalized,
            segments=tuple(segments),
            page_count=None,
        )
    )
