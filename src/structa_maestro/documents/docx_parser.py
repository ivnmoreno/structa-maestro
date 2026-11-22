"""ZIP-bomb-guarded DOCX parser extracting paragraphs, headings, and table text."""

from __future__ import annotations

import io
import zipfile

import docx

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text
from structa_maestro.documents.txt_parser import ParseResult


def parse_docx_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse raw DOCX bytes with pre-open ZIP-bomb and archive-integrity guards."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input DOCX document is empty (zero bytes).",
                ),
            ),
        )

    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                return ParseResult(
                    document=None,
                    errors=(
                        DiagnosticIssue(
                            code="unsupported_format",
                            severity=IssueSeverity.ERROR,
                            stage=PipelineStage.DOCUMENT_PROCESSING,
                            message=(
                                "ZIP archive is not a valid DOCX container "
                                "(missing word/document.xml)."
                            ),
                        ),
                    ),
                )
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > limits.max_decompressed_bytes:
                return ParseResult(
                    document=None,
                    errors=(
                        DiagnosticIssue(
                            code="resource_limit_exceeded",
                            severity=IssueSeverity.ERROR,
                            stage=PipelineStage.DOCUMENT_PROCESSING,
                            message=(
                                "DOCX uncompressed archive size exceeds configured "
                                f"max_decompressed_bytes limit ({limits.max_decompressed_bytes})."
                            ),
                        ),
                    ),
                )
    except zipfile.BadZipFile:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="DOCX container is corrupted or not a valid ZIP archive.",
                ),
            ),
        )
    except Exception:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Unable to inspect DOCX container safely.",
                ),
            ),
        )

    try:
        document = docx.Document(io.BytesIO(raw))
    except Exception:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="corrupted_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="DOCX document structure is corrupted or unreadable.",
                ),
            ),
        )

    segments: list[DocumentSegment] = []
    full_parts: list[str] = []
    current_heading: str | None = None
    paragraph_index = 0
    cumulative_bytes = 0
    offset = 0

    for para in document.paragraphs:
        text = normalize_document_text(para.text).strip()
        if not text:
            continue
        style_name = para.style.name if para.style is not None else ""
        if style_name and style_name.lower().startswith("heading"):
            current_heading = text
        paragraph_index += 1
        cumulative_bytes += len(text.encode("utf-8"))
        if cumulative_bytes > limits.max_decompressed_bytes:
            return ParseResult(
                document=None,
                errors=(
                    DiagnosticIssue(
                        code="resource_limit_exceeded",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.DOCUMENT_PROCESSING,
                        message="Extracted DOCX text exceeds configured max_decompressed_bytes.",
                    ),
                ),
            )
        if full_parts:
            offset += 1  # '\n'
        char_start = offset
        char_end = offset + len(text)
        segments.append(
            DocumentSegment(
                text=text,
                char_start=char_start,
                char_end=char_end,
                paragraph=paragraph_index,
                heading=current_heading,
            )
        )
        full_parts.append(text)
        offset = char_end

    for table in document.tables:
        for row in table.rows:
            row_cells = [normalize_document_text(c.text).strip() for c in row.cells]
            row_text = " | ".join(cell for cell in row_cells if cell)
            if not row_text:
                continue
            paragraph_index += 1
            cumulative_bytes += len(row_text.encode("utf-8"))
            if cumulative_bytes > limits.max_decompressed_bytes:
                return ParseResult(
                    document=None,
                    errors=(
                        DiagnosticIssue(
                            code="resource_limit_exceeded",
                            severity=IssueSeverity.ERROR,
                            stage=PipelineStage.DOCUMENT_PROCESSING,
                            message=(
                                "Extracted DOCX text exceeds configured max_decompressed_bytes."
                            ),
                        ),
                    ),
                )
            if full_parts:
                offset += 1
            char_start = offset
            char_end = offset + len(row_text)
            segments.append(
                DocumentSegment(
                    text=row_text,
                    char_start=char_start,
                    char_end=char_end,
                    paragraph=paragraph_index,
                    heading=current_heading,
                )
            )
            full_parts.append(row_text)
            offset = char_end

    full_text = "\n".join(full_parts)
    if not full_text.strip():
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="no_extractable_text",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="DOCX document contains no extractable text.",
                ),
            ),
        )

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.DOCX,
            full_text=full_text,
            segments=tuple(segments),
            page_count=None,
        )
    )
