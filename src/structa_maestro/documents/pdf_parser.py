"""Text-based PDF parser using pypdf with page limits, encryption, and image-only detection."""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text
from structa_maestro.documents.txt_parser import ParseResult


def parse_pdf_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse text-based PDF bytes enforcing DocumentLimits.max_pages and max_decompressed_bytes."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input PDF document is empty (zero bytes).",
                ),
            ),
        )

    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
    except (PdfReadError, Exception):
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="PDF document is corrupted or unreadable.",
                ),
            ),
        )

    if reader.is_encrypted:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Encrypted PDF documents are not supported without prior decryption.",
                ),
            ),
        )

    try:
        page_count = len(reader.pages)
    except Exception:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Unable to read PDF page structure.",
                ),
            ),
        )

    if page_count == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="PDF document contains zero pages.",
                ),
            ),
        )

    if page_count > limits.max_pages:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message=(
                        f"PDF page count ({page_count}) exceeds configured "
                        f"max_pages limit ({limits.max_pages})."
                    ),
                ),
            ),
        )

    segments: list[DocumentSegment] = []
    full_parts: list[str] = []
    warnings: list[DiagnosticIssue] = []
    cumulative_bytes = 0
    offset = 0

    for page_num, page in enumerate(reader.pages, start=1):
        try:
            raw_page_text = page.extract_text() or ""
        except Exception:
            warnings.append(
                DiagnosticIssue(
                    code="partial_page_parse_failure",
                    severity=IssueSeverity.WARNING,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message=f"Failed to extract text from PDF page {page_num}.",
                )
            )
            raw_page_text = ""

        normalized = normalize_document_text(raw_page_text).strip()
        cumulative_bytes += len(normalized.encode("utf-8"))
        if cumulative_bytes > limits.max_decompressed_bytes:
            return ParseResult(
                document=None,
                errors=(
                    DiagnosticIssue(
                        code="resource_limit_exceeded",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.DOCUMENT_PROCESSING,
                        message="Extracted PDF text exceeds configured max_decompressed_bytes.",
                    ),
                ),
            )
        if normalized:
            if full_parts:
                offset += 2  # "\n\n" separator
            char_start = offset
            char_end = offset + len(normalized)
            segments.append(
                DocumentSegment(
                    text=normalized,
                    char_start=char_start,
                    char_end=char_end,
                    page=page_num,
                )
            )
            full_parts.append(normalized)
            offset = char_end

    full_text = "\n\n".join(full_parts)
    if not full_text.strip():
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="no_extractable_text",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message=(
                        "PDF contains no extractable text layer "
                        "(image-only or scanned PDFs require OCR, which is outside MVP scope)."
                    ),
                ),
            ),
            warnings=tuple(warnings),
        )

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.PDF,
            full_text=full_text,
            segments=tuple(segments),
            page_count=page_count,
        ),
        warnings=tuple(warnings),
    )
