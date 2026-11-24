"""Hardened stdlib-only Markdown parser with ATX/Setext heading and line/paragraph provenance."""

from __future__ import annotations

import re

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text
from structa_maestro.documents.txt_parser import ParseResult, _decode_text_bytes, _looks_binary

_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def parse_markdown_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse raw Markdown bytes into a PreparedDocument with section, paragraph & line locations."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input Markdown document is empty (zero bytes).",
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
                    message="Input file appears to contain binary data rather than Markdown.",
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
                    message="Unable to decode Markdown document with supported text encodings.",
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
                    message=(
                        "Prepared Markdown text exceeds configured max_decompressed_bytes limit."
                    ),
                ),
            ),
        )

    if not normalized.strip():
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="no_extractable_text",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Markdown document contains no extractable text (whitespace only).",
                ),
            ),
        )

    lines = normalized.split("\n")
    line_offsets: list[tuple[int, int]] = []
    pos = 0
    for line in lines:
        line_offsets.append((pos, pos + len(line)))
        pos += len(line) + 1

    segments: list[DocumentSegment] = []
    current_heading: str | None = None
    in_fence_char: str | None = None
    in_fence_len = 0
    para_idx = 0

    block_start_idx: int | None = None

    def _flush_block(end_idx: int) -> None:
        nonlocal block_start_idx, para_idx
        if block_start_idx is None:
            return
        start_char = line_offsets[block_start_idx][0]
        end_char = line_offsets[end_idx][1]
        text_slice = normalized[start_char:end_char]
        if text_slice.strip():
            para_idx += 1
            segments.append(
                DocumentSegment(
                    text=text_slice,
                    char_start=start_char,
                    char_end=end_char,
                    paragraph=para_idx,
                    line_start=block_start_idx + 1,
                    line_end=end_idx + 1,
                    heading=current_heading,
                )
            )
        block_start_idx = None

    idx = 0
    total_lines = len(lines)
    while idx < total_lines:
        line = lines[idx]

        # Track fenced code blocks (``` or ~~~) so '#' lines inside code are never headings
        fence_match = _FENCE_RE.match(line)
        if fence_match is not None:
            marker = fence_match.group(1)
            fchar, flen = marker[0], len(marker)
            if in_fence_char is None:
                in_fence_char = fchar
                in_fence_len = flen
                if block_start_idx is None:
                    block_start_idx = idx
            elif fchar == in_fence_char and flen >= in_fence_len and not line.strip(fchar + " \t"):
                in_fence_char = None
                in_fence_len = 0
            idx += 1
            continue

        if in_fence_char is not None:
            idx += 1
            continue

        if not line.strip():
            _flush_block(idx - 1)
            idx += 1
            continue

        # ATX heading (# .. ######)
        atx = _ATX_HEADING_RE.match(line)
        if atx is not None:
            _flush_block(idx - 1)
            heading_text = atx.group(2).strip()
            if heading_text:
                current_heading = heading_text
            para_idx += 1
            start_char, end_char = line_offsets[idx]
            segments.append(
                DocumentSegment(
                    text=line,
                    char_start=start_char,
                    char_end=end_char,
                    paragraph=para_idx,
                    line_start=idx + 1,
                    line_end=idx + 1,
                    heading=current_heading,
                )
            )
            idx += 1
            continue

        # Setext heading (current non-empty line followed by === or ---)
        if (
            idx + 1 < total_lines
            and _SETEXT_UNDERLINE_RE.match(lines[idx + 1]) is not None
            and not line.lstrip().startswith(("-", "*", "+", ">", "|"))
        ):
            _flush_block(idx - 1)
            heading_text = line.strip()
            if heading_text:
                current_heading = heading_text
            para_idx += 1
            start_char = line_offsets[idx][0]
            end_char = line_offsets[idx + 1][1]
            segments.append(
                DocumentSegment(
                    text=normalized[start_char:end_char],
                    char_start=start_char,
                    char_end=end_char,
                    paragraph=para_idx,
                    line_start=idx + 1,
                    line_end=idx + 2,
                    heading=current_heading,
                )
            )
            idx += 2
            continue

        if block_start_idx is None:
            block_start_idx = idx
        idx += 1

    _flush_block(total_lines - 1)

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.MARKDOWN,
            full_text=normalized,
            segments=tuple(segments),
            page_count=None,
        )
    )
