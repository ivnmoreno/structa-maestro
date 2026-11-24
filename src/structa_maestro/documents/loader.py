"""Safe document input loader enforcing path checks, bounded streams, magic bytes, and limits."""

from __future__ import annotations

import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.docx_parser import parse_docx_bytes
from structa_maestro.documents.eml_parser import parse_eml_bytes
from structa_maestro.documents.html_parser import parse_html_bytes
from structa_maestro.documents.inputs import (
    SUPPORTED_MEDIA_TYPES,
    DocumentInput,
    DocumentInputKind,
    sanitize_filename,
)
from structa_maestro.documents.markdown_parser import parse_markdown_bytes
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.documents.pdf_parser import parse_pdf_bytes
from structa_maestro.documents.txt_parser import (
    ParseResult,
    _decode_text_bytes,
    _looks_binary,
    parse_txt_bytes,
)
from structa_maestro.exceptions import ConfigurationError

_MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    DocumentFormat.HTML: "text/html",
    DocumentFormat.MARKDOWN: "text/markdown",
    DocumentFormat.EML: "message/rfc822",
}

_EXTENSION_TO_FORMAT: dict[str, DocumentFormat] = {
    ".txt": DocumentFormat.TXT,
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".eml": DocumentFormat.EML,
}

_TEXT_FORMATS: frozenset[DocumentFormat] = frozenset(
    {
        DocumentFormat.TXT,
        DocumentFormat.HTML,
        DocumentFormat.MARKDOWN,
        DocumentFormat.EML,
    }
)
_EML_SNIFF_SECONDARY_HEADERS: frozenset[str] = frozenset(
    {"subject", "date", "to", "mime-version", "content-type"}
)
_STREAM_CHUNK_BYTES: int = 65_536


@dataclass(frozen=True, slots=True)
class DocumentLoadResult:
    """Result of document loading and text preparation."""

    source: SourceMetadata
    document: PreparedDocument | None = None
    errors: list[DiagnosticIssue] = field(default_factory=list)
    warnings: list[DiagnosticIssue] = field(default_factory=list)


def _sniff_extensionless_text_format(raw: bytes) -> DocumentFormat:
    """Conservatively sniff unambiguous HTML or RFC 5322 EML on extensionless text payloads."""
    decoded = _decode_text_bytes(raw[:4096])
    if not decoded:
        return DocumentFormat.TXT
    stripped = decoded.lstrip("\ufeff \t\r\n")
    lower_head = stripped[:64].lower()
    if lower_head.startswith("<!doctype html") or lower_head.startswith("<html"):
        return DocumentFormat.HTML

    header_section = stripped.split("\r\n\r\n", 1)[0].split("\n\n", 1)[0]
    header_names: set[str] = set()
    valid_headers = True
    for line in header_section.splitlines():
        if not line:
            continue
        if line[0] in (" ", "\t") and header_names:
            continue
        if ":" not in line:
            valid_headers = False
            break
        key = line.split(":", 1)[0].strip().lower()
        if not key or any(not (ch.isalnum() or ch == "-") for ch in key):
            valid_headers = False
            break
        header_names.add(key)

    if valid_headers and "from" in header_names and (header_names & _EML_SNIFF_SECONDARY_HEADERS):
        return DocumentFormat.EML

    return DocumentFormat.TXT


def _detect_format(
    raw: bytes,
    extension: str,
    media_type_hint: str | None = None,
) -> tuple[DocumentFormat | None, str | None]:
    """Detect actual document format from magic bytes, signatures, and safe hints."""
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

    ext_format = _EXTENSION_TO_FORMAT.get(extension)
    if ext_format is not None:
        if _looks_binary(raw):
            return (
                None,
                f"Input file with extension {extension!r} appears to contain binary data.",
            )
        return ext_format, None

    if extension:
        return None, f"Unsupported document format extension: {extension!r}."

    # Extensionless payload: reject binary bytes before considering text formats
    if _looks_binary(raw):
        return None, "Input payload appears to contain unsupported binary data."

    if media_type_hint is not None:
        hinted = SUPPORTED_MEDIA_TYPES.get(media_type_hint)
        if hinted in _TEXT_FORMATS:
            return hinted, None
        if hinted is not None:
            return (
                None,
                f"Caller media_type hint {media_type_hint!r} does not match detected content.",
            )

    return _sniff_extensionless_text_format(raw), None


def _dispatch_parser(
    detected_format: DocumentFormat,
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    if detected_format == DocumentFormat.TXT:
        return parse_txt_bytes(raw, source, limits)
    if detected_format == DocumentFormat.PDF:
        return parse_pdf_bytes(raw, source, limits)
    if detected_format == DocumentFormat.DOCX:
        return parse_docx_bytes(raw, source, limits)
    if detected_format == DocumentFormat.HTML:
        return parse_html_bytes(raw, source, limits)
    if detected_format == DocumentFormat.MARKDOWN:
        return parse_markdown_bytes(raw, source, limits)
    return parse_eml_bytes(raw, source, limits)


def read_stream_bounded(
    stream: BinaryIO,
    *,
    max_bytes: int,
) -> tuple[bytes | None, DiagnosticIssue | None]:
    """Read binary stream incrementally up to ``max_bytes + 1`` bytes without closing it."""
    if max_bytes < 1:
        raise ConfigurationError("max_bytes must be a positive integer.")

    chunks: list[bytes] = []
    total_read = 0
    cap = max_bytes + 1

    while total_read < cap:
        to_read = min(_STREAM_CHUNK_BYTES, cap - total_read)
        try:
            chunk = stream.read(to_read)
        except (OSError, ValueError):
            return None, DiagnosticIssue(
                code="invalid_input",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.INPUT,
                message="Failed to read bytes from input binary stream.",
            )

        if chunk is None:
            break
        if isinstance(chunk, str) or not isinstance(chunk, (bytes, bytearray, memoryview)):
            return None, DiagnosticIssue(
                code="invalid_input",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.INPUT,
                message="Input stream did not yield binary bytes.",
            )

        raw_chunk = bytes(chunk)
        if not raw_chunk:
            break

        chunks.append(raw_chunk)
        total_read += len(raw_chunk)

        if total_read > max_bytes:
            return None, DiagnosticIssue(
                code="resource_limit_exceeded",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.INPUT,
                message=(
                    f"Input stream exceeds configured max_file_bytes limit ({max_bytes} bytes)."
                ),
            )

    return b"".join(chunks), None


def _load_prepared_from_bytes(
    raw: bytes,
    *,
    doc_id: str,
    safe_filename: str | None,
    limits: DocumentLimits,
    media_type_hint: str | None = None,
) -> DocumentLoadResult:
    """Validate size, sniff magic bytes, reconcile media_type hint, and parse bytes in memory."""
    base_source = SourceMetadata(document_id=doc_id, filename=safe_filename, media_type=None)

    if len(raw) == 0:
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

    if len(raw) > limits.max_file_bytes:
        return DocumentLoadResult(
            source=base_source,
            errors=[
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message=(
                        f"Input document size ({len(raw)} bytes) exceeds configured "
                        f"max_file_bytes limit ({limits.max_file_bytes} bytes)."
                    ),
                )
            ],
        )

    ext = Path(safe_filename).suffix.lower() if safe_filename else ""
    detected_format, detect_err = _detect_format(raw, ext, media_type_hint=media_type_hint)
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

    if media_type_hint is not None:
        expected_format = SUPPORTED_MEDIA_TYPES.get(media_type_hint)
        if expected_format is not None and detected_format != expected_format:
            return DocumentLoadResult(
                source=base_source,
                errors=[
                    DiagnosticIssue(
                        code="unsupported_format",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.INPUT,
                        message=(
                            f"Caller media_type hint {media_type_hint!r} does not match "
                            f"detected content format {_MEDIA_TYPES[detected_format]!r}."
                        ),
                    )
                ],
            )

    warnings: list[DiagnosticIssue] = []
    ext_format = _EXTENSION_TO_FORMAT.get(ext)
    if ext_format is not None and ext_format != detected_format:
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

    parsed = _dispatch_parser(detected_format, raw, source, limits)

    return DocumentLoadResult(
        source=source,
        document=parsed.document,
        errors=list(parsed.errors),
        warnings=warnings + list(parsed.warnings),
    )


def load_document(
    path: str | Path,
    *,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
    filename_override: str | None = None,
    media_type_hint: str | None = None,
) -> DocumentLoadResult:
    """Inspect, validate, and parse a single TXT, text-based PDF, or DOCX document safely."""
    active_limits = limits or DocumentLimits()
    doc_path = Path(path)
    doc_id = document_id.strip() if document_id and document_id.strip() else str(uuid4())
    # Basename only to avoid leaking local host paths in canonical metadata
    safe_filename = (
        filename_override
        if filename_override is not None
        else (doc_path.name if doc_path.name else None)
    )
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

    return _load_prepared_from_bytes(
        raw,
        doc_id=doc_id,
        safe_filename=safe_filename,
        limits=active_limits,
        media_type_hint=media_type_hint,
    )


def load_document_input(
    doc_input: DocumentInput,
    *,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
) -> DocumentLoadResult:
    """Load and prepare any DocumentInput (path, bytes, plain text, or binary stream) safely."""
    if not isinstance(doc_input, DocumentInput):
        raise ConfigurationError("doc_input must be a DocumentInput instance.")

    active_limits = limits or DocumentLimits()
    if document_id is not None:
        if not isinstance(document_id, str) or not document_id.strip():
            raise ConfigurationError("document_id must be a non-empty string or None.")
        resolved_doc_id = document_id.strip()
    elif doc_input.document_id is not None:
        resolved_doc_id = doc_input.document_id
    else:
        resolved_doc_id = str(uuid4())

    safe_filename = sanitize_filename(doc_input.filename)

    if doc_input.kind == DocumentInputKind.PATH:
        if doc_input.path is None:
            raise ConfigurationError("PATH DocumentInput is missing a path.")
        return load_document(
            doc_input.path,
            limits=active_limits,
            document_id=resolved_doc_id,
            filename_override=safe_filename,
            media_type_hint=doc_input.media_type,
        )

    if doc_input.kind == DocumentInputKind.BYTES:
        raw_bytes = doc_input.content if doc_input.content is not None else b""
        return _load_prepared_from_bytes(
            raw_bytes,
            doc_id=resolved_doc_id,
            safe_filename=safe_filename,
            limits=active_limits,
            media_type_hint=doc_input.media_type,
        )

    if doc_input.kind == DocumentInputKind.TEXT:
        raw_text_bytes = (doc_input.text or "").encode("utf-8")
        empty_source = SourceMetadata(
            document_id=resolved_doc_id,
            filename=safe_filename,
            media_type=None,
        )
        if len(raw_text_bytes) == 0:
            return DocumentLoadResult(
                source=empty_source,
                errors=[
                    DiagnosticIssue(
                        code="empty_document",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.INPUT,
                        message="Input plain-text document is empty.",
                    )
                ],
            )
        if len(raw_text_bytes) > active_limits.max_file_bytes:
            return DocumentLoadResult(
                source=empty_source,
                errors=[
                    DiagnosticIssue(
                        code="resource_limit_exceeded",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.INPUT,
                        message=(
                            f"Input plain-text size ({len(raw_text_bytes)} bytes) exceeds "
                            f"configured max_file_bytes limit ({active_limits.max_file_bytes} "
                            "bytes)."
                        ),
                    )
                ],
            )

        target_format = DocumentFormat.TXT
        if doc_input.media_type is not None:
            hinted_fmt = SUPPORTED_MEDIA_TYPES.get(doc_input.media_type)
            if hinted_fmt not in _TEXT_FORMATS:
                return DocumentLoadResult(
                    source=empty_source,
                    errors=[
                        DiagnosticIssue(
                            code="unsupported_format",
                            severity=IssueSeverity.ERROR,
                            stage=PipelineStage.INPUT,
                            message=(
                                f"Binary media_type {doc_input.media_type!r} is not supported "
                                "for DocumentInput.from_text(); use DocumentInput.from_bytes()."
                            ),
                        )
                    ],
                )
            target_format = hinted_fmt
        elif safe_filename is not None:
            ext_fmt = _EXTENSION_TO_FORMAT.get(Path(safe_filename).suffix.lower())
            if ext_fmt in _TEXT_FORMATS:
                target_format = ext_fmt

        base_source = SourceMetadata(
            document_id=resolved_doc_id,
            filename=safe_filename,
            media_type=_MEDIA_TYPES[target_format],
        )
        parsed = _dispatch_parser(target_format, raw_text_bytes, base_source, active_limits)
        return DocumentLoadResult(
            source=base_source,
            document=parsed.document,
            errors=list(parsed.errors),
            warnings=list(parsed.warnings),
        )

    # DocumentInputKind.STREAM
    if doc_input.stream is None:
        raise ConfigurationError("STREAM DocumentInput is missing a binary stream.")
    raw_stream_bytes, read_err = read_stream_bounded(
        doc_input.stream,
        max_bytes=active_limits.max_file_bytes,
    )
    if read_err is not None or raw_stream_bytes is None:
        err_issue = read_err or DiagnosticIssue(
            code="invalid_input",
            severity=IssueSeverity.ERROR,
            stage=PipelineStage.INPUT,
            message="Failed to read input binary stream.",
        )
        return DocumentLoadResult(
            source=SourceMetadata(
                document_id=resolved_doc_id,
                filename=safe_filename,
                media_type=None,
            ),
            errors=[err_issue],
        )

    return _load_prepared_from_bytes(
        raw_stream_bytes,
        doc_id=resolved_doc_id,
        safe_filename=safe_filename,
        limits=active_limits,
        media_type_hint=doc_input.media_type,
    )
