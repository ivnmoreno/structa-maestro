"""Hardened stdlib-only RFC 5322 (.eml) parser with MIME-bomb limits & safe attachment skipping."""

from __future__ import annotations

from email import policy as email_policy
from email.message import EmailMessage, Message
from email.parser import BytesParser

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.html_parser import parse_html_bytes
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text
from structa_maestro.documents.txt_parser import ParseResult, _decode_text_bytes, _looks_binary

MAX_EML_PARTS = 128
MAX_EML_NESTING_DEPTH = 16

_EXTRACTED_HEADERS: tuple[str, ...] = ("From", "To", "Cc", "Date", "Subject")


class _EmlLimitExceeded(Exception):
    """Sentinel aborting EML traversal immediately when MIME or text limits are exceeded."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _clean_header_value(raw_val: object) -> str:
    text = str(raw_val).replace("\r", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def _is_attachment_part(part: Message) -> bool:
    disposition = (part.get_content_disposition() or "").lower()
    if disposition == "attachment":
        return True
    if part.get_filename() is not None and disposition != "inline":
        return True
    ctype = part.get_content_type().lower()
    return not ctype.startswith("text/") and not part.is_multipart()


def _extract_part_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8", errors="replace")
    return b""


def _decode_plain_part(part: Message, limits: DocumentLimits) -> str:
    raw_bytes = _extract_part_bytes(part)
    if not raw_bytes:
        return ""
    if len(raw_bytes) > limits.max_decompressed_bytes:
        raise _EmlLimitExceeded(
            "Decoded EML body part exceeds configured max_decompressed_bytes limit."
        )
    charset = part.get_content_charset()
    decoded: str | None = None
    if charset:
        try:
            decoded = raw_bytes.decode(charset, errors="replace")
        except LookupError:
            decoded = None
    if decoded is None:
        decoded = _decode_text_bytes(raw_bytes)
    if decoded is None:
        decoded = raw_bytes.decode("utf-8", errors="replace")
    normalized = normalize_document_text(decoded)
    if len(normalized.encode("utf-8")) > limits.max_decompressed_bytes:
        raise _EmlLimitExceeded(
            "Prepared EML body text exceeds configured max_decompressed_bytes limit."
        )
    return normalized


def _decode_html_part(
    part: Message,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> list[tuple[str, str]]:
    raw_bytes = _extract_part_bytes(part)
    if not raw_bytes:
        return []
    if len(raw_bytes) > limits.max_decompressed_bytes:
        raise _EmlLimitExceeded(
            "Decoded EML HTML part exceeds configured max_decompressed_bytes limit."
        )
    parsed = parse_html_bytes(raw_bytes, source, limits)
    if parsed.document is None:
        for err in parsed.errors:
            if err.code == "resource_limit_exceeded":
                raise _EmlLimitExceeded(err.message)
        return []
    return [(seg.text, seg.heading or "Body") for seg in parsed.document.segments]


def parse_eml_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse RFC 5322 .eml bytes safely into Headers + Body DocumentSegments."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input EML document is empty (zero bytes).",
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
                    message="Input file appears to contain binary data rather than an EML email.",
                ),
            ),
        )

    try:
        msg: EmailMessage = BytesParser(policy=email_policy.default).parsebytes(raw)
    except Exception:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="unsupported_format",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Unable to parse input bytes as an RFC 5322 EML message.",
                ),
            ),
        )

    part_count = 0
    skipped_attachments = 0
    accumulated_bytes = 0
    raw_blocks: list[tuple[str, str]] = []

    def _charge(text: str) -> None:
        nonlocal accumulated_bytes
        accumulated_bytes += len(text.encode("utf-8"))
        if accumulated_bytes > limits.max_decompressed_bytes:
            raise _EmlLimitExceeded(
                "Prepared EML text exceeds configured max_decompressed_bytes limit."
            )

    # 1. Extract normalized key headers into the "Headers" section
    header_lines: list[str] = []
    for hdr_name in _EXTRACTED_HEADERS:
        hdr_val = msg.get(hdr_name)
        if hdr_val is not None:
            cleaned = _clean_header_value(hdr_val)
            if cleaned:
                header_lines.append(f"{hdr_name}: {cleaned}")
    if header_lines:
        header_block = "\n".join(header_lines)
        _charge(header_block)
        raw_blocks.append((header_block, "Headers"))

    def _collect_part(part: Message, depth: int) -> list[tuple[str, str]]:
        nonlocal part_count, skipped_attachments
        part_count += 1
        if part_count > MAX_EML_PARTS:
            raise _EmlLimitExceeded(
                f"EML MIME part count exceeds maximum safety limit ({MAX_EML_PARTS} parts)."
            )
        if depth > MAX_EML_NESTING_DEPTH:
            raise _EmlLimitExceeded(
                f"EML MIME nesting depth exceeds maximum safety limit "
                f"({MAX_EML_NESTING_DEPTH} levels)."
            )

        if part.is_multipart():
            children = part.get_payload()
            if not isinstance(children, list):
                return []
            subtype = part.get_content_subtype().lower()
            if subtype == "alternative":
                plain_blocks: list[tuple[str, str]] = []
                html_blocks: list[tuple[str, str]] = []
                for child in children:
                    if not isinstance(child, Message):
                        continue
                    if _is_attachment_part(child):
                        skipped_attachments += 1
                        continue
                    ctype = child.get_content_type().lower()
                    if child.is_multipart():
                        plain_blocks.extend(_collect_part(child, depth + 1))
                    elif ctype == "text/plain":
                        part_count += 1
                        if part_count > MAX_EML_PARTS:
                            raise _EmlLimitExceeded(
                                f"EML MIME part count exceeds maximum safety limit "
                                f"({MAX_EML_PARTS} parts)."
                            )
                        text = _decode_plain_part(child, limits)
                        for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
                            plain_blocks.append((para, "Body"))
                    elif ctype == "text/html":
                        part_count += 1
                        if part_count > MAX_EML_PARTS:
                            raise _EmlLimitExceeded(
                                f"EML MIME part count exceeds maximum safety limit "
                                f"({MAX_EML_PARTS} parts)."
                            )
                        html_blocks.extend(_decode_html_part(child, source, limits))
                    else:
                        skipped_attachments += 1
                return plain_blocks if plain_blocks else html_blocks

            collected: list[tuple[str, str]] = []
            for child in children:
                if not isinstance(child, Message):
                    continue
                if _is_attachment_part(child):
                    skipped_attachments += 1
                    continue
                collected.extend(_collect_part(child, depth + 1))
            return collected

        if _is_attachment_part(part):
            skipped_attachments += 1
            return []

        ctype = part.get_content_type().lower()
        if ctype == "text/plain":
            text = _decode_plain_part(part, limits)
            return [(para, "Body") for para in (p.strip() for p in text.split("\n\n")) if para]
        if ctype == "text/html":
            return _decode_html_part(part, source, limits)
        skipped_attachments += 1
        return []

    try:
        body_blocks = _collect_part(msg, depth=0)
        for text, section in body_blocks:
            _charge(text)
            raw_blocks.append((text, section))
    except _EmlLimitExceeded as exc:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message=exc.detail,
                ),
            ),
        )

    warnings: list[DiagnosticIssue] = []
    if skipped_attachments > 0:
        warnings.append(
            DiagnosticIssue(
                code="attachments_skipped",
                severity=IssueSeverity.WARNING,
                stage=PipelineStage.DOCUMENT_PROCESSING,
                message=(
                    f"Skipped {skipped_attachments} non-text or file attachment(s) "
                    "while parsing EML message."
                ),
            )
        )

    if not raw_blocks:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="no_extractable_text",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="EML message contains no extractable headers or text body.",
                ),
            ),
            warnings=tuple(warnings),
        )

    segments: list[DocumentSegment] = []
    full_parts: list[str] = []
    offset = 0
    para_idx = 0
    for text, heading in raw_blocks:
        normalized_block = normalize_document_text(text).strip()
        if not normalized_block:
            continue
        para_idx += 1
        if full_parts:
            offset += 2  # "\n\n" separator
        char_start = offset
        char_end = char_start + len(normalized_block)
        full_parts.append(normalized_block)
        segments.append(
            DocumentSegment(
                text=normalized_block,
                char_start=char_start,
                char_end=char_end,
                paragraph=para_idx,
                heading=heading,
            )
        )
        offset = char_end

    if not full_parts:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="no_extractable_text",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="EML message contains no extractable headers or text body.",
                ),
            ),
            warnings=tuple(warnings),
        )

    full_text = "\n\n".join(full_parts)
    if len(full_text.encode("utf-8")) > limits.max_decompressed_bytes:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Prepared EML text exceeds configured max_decompressed_bytes limit.",
                ),
            ),
            warnings=tuple(warnings),
        )

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.EML,
            full_text=full_text,
            segments=tuple(segments),
            page_count=None,
        ),
        warnings=tuple(warnings),
    )
