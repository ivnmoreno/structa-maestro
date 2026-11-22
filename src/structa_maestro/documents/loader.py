"""Safe document input loader enforcing path checks, magic-byte inspection, and limits."""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.docx_parser import parse_docx_bytes
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.documents.pdf_parser import parse_pdf_bytes
from structa_maestro.documents.txt_parser import parse_txt_bytes

_MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
}

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".pdf", ".docx"})


@dataclass(frozen=True, slots=True)
class DocumentLoadResult:
    """Result of document loading and text preparation."""

    source: SourceMetadata
    document: PreparedDocument | None = None
    errors: list[DiagnosticIssue] = field(default_factory=list)
    warnings: list[DiagnosticIssue] = field(default_factory=list)


def _detect_format(raw: bytes, extension: str) -> tuple[DocumentFormat | None, str | None]:
    """Detect actual document format from magic bytes rather than blindly trusting extensions."""
    if raw.startswith(b"%PDF-"):
        return DocumentFormat.PDF, None

    if raw.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
                if "word/document.xml" in zf.namelist():
                    return DocumentFormat.DOCX, None
                return None, "ZIP archive does not contain a DOCX document (word/document.xml)."
        except zipfile.BadZipFile:
            if extension == ".docx":
                return DocumentFormat.DOCX, None
            return None, "Corrupted archive or unsupported binary container."

    if extension == ".pdf":
        return None, "File has .pdf extension but does not start with a valid %PDF header."
    if extension == ".docx":
        return None, "File has .docx extension but is not a valid DOCX archive."
    if extension == ".txt":
        return DocumentFormat.TXT, None

    if extension and extension not in _SUPPORTED_EXTENSIONS:
        return None, f"Unsupported document format extension: {extension!r}."

    # Extensionless file: attempt plain text only if not binary
    return DocumentFormat.TXT, None


def load_document(
    path: str | Path,
    *,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
) -> DocumentLoadResult:
    """Inspect, validate, and parse a single TXT, text-based PDF, or DOCX document safely."""
    active_limits = limits or DocumentLimits()
    doc_path = Path(path)
    doc_id = document_id.strip() if document_id and document_id.strip() else str(uuid4())
    # Basename only to avoid leaking local host paths in canonical metadata
    safe_filename = doc_path.name if doc_path.name else None
    base_source = SourceMetadata(document_id=doc_id, filename=safe_filename, media_type=None)

    if "\x00" in str(path):
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Document path contains invalid null byte.",
                )
            ],
        )

    try:
        exists = doc_path.exists()
    except OSError:
        exists = False

    if not exists:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="file_not_found",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input document path does not exist.",
                )
            ],
        )

    if doc_path.is_dir():
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input path refers to a directory rather than a regular file.",
                )
            ],
        )

    if not doc_path.is_file():
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input path is not a regular readable file.",
                )
            ],
        )

    if not os.access(doc_path, os.R_OK):
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="permission_denied",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input document cannot be read due to file permissions.",
                )
            ],
        )

    try:
        file_size = doc_path.stat().st_size
    except OSError:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Unable to inspect input document file metadata.",
                )
            ],
        )

    if file_size == 0:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input document is empty (zero bytes).",
                )
            ],
        )

    if file_size > active_limits.max_file_bytes:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message=(
                        f"Input document size ({file_size} bytes) exceeds configured "
                        f"max_file_bytes limit ({active_limits.max_file_bytes} bytes)."
                    ),
                )
            ],
        )

    try:
        raw = doc_path.read_bytes()
    except PermissionError:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="permission_denied",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Permission denied while reading input document.",
                )
            ],
        )
    except OSError:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Failed to read input document bytes.",
                )
            ],
        )

    if len(raw) > active_limits.max_file_bytes:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message="Input document grew beyond configured max_file_bytes while reading.",
                )
            ],
        )

    ext = doc_path.suffix.lower()
    detected_format, detect_err = _detect_format(raw, ext)
    if detected_format is None:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="unsupported_format",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message=detect_err or "Unsupported document format.",
                )
            ],
        )

    warnings: list[DiagnosticIssue] = []
    if ext and ext in _SUPPORTED_EXTENSIONS and ext != f".{detected_format.value}":
        warnings.append(
            DiagnosticIssue(
                code="extension_mismatch",
                severity=IssueSeverity.WARNING,
                stage=PipelineStage.INPUT,
                message=(
                    f"File extension {ext!r} does not match detected content format "
                    f"{detected_format.value!r}; processed using detected format."
                ),
            )
        )

    source = SourceMetadata(
        document_id=doc_id,
        filename=safe_filename,
        media_type=_MEDIA_TYPES[detected_format],
    )

    if detected_format == DocumentFormat.TXT:
        parsed = parse_txt_bytes(raw, source, active_limits)
    elif detected_format == DocumentFormat.PDF:
        parsed = parse_pdf_bytes(raw, source, active_limits)
    else:
        parsed = parse_docx_bytes(raw, source, active_limits)

    return DocumentLoadResult(
        source=source,
        document=parsed.document,
        errors=list(parsed.errors),
        warnings=warnings + list(parsed.warnings),
    )
