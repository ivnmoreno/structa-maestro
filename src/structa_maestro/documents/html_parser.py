"""Hardened stdlib-only HTML parser stripping scripts/styles and preserving heading/table blocks."""

from __future__ import annotations

import codecs
import re
from html.parser import HTMLParser

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import DocumentFormat, IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.documents.normalization import normalize_document_text
from structa_maestro.documents.txt_parser import ParseResult, _decode_text_bytes, _looks_binary

_SKIP_TAGS: frozenset[str] = frozenset({"script", "style", "noscript", "template"})
_HEADING_TAGS: frozenset[str] = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "aside",
        "nav",
        "li",
        "ul",
        "ol",
        "blockquote",
        "pre",
        "dt",
        "dd",
        "hr",
        "figure",
        "figcaption",
        "address",
        "title",
    }
)
_CELL_TAGS: frozenset[str] = frozenset({"td", "th"})
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]+charset\s*=\s*['\"]?\s*([A-Za-z0-9._\-]+)",
    re.IGNORECASE,
)
_WS_COLLAPSE_RE = re.compile(r"[ \t\r\n\f\v]+")


class _DecompressedLimitExceeded(Exception):
    """Internal sentinel aborting HTML parsing immediately when decoded bytes exceed limit."""


def _decode_html_bytes(raw: bytes) -> str | None:
    """Decode HTML bytes honoring BOMs, UTF-8, safe <meta charset> hints, and Latin-1 fallback."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return _decode_text_bytes(raw)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    match = _META_CHARSET_RE.search(raw[:4096])
    if match is not None:
        raw_enc = match.group(1).decode("ascii", errors="ignore").strip()
        try:
            codec_info = codecs.lookup(raw_enc)
            return raw.decode(codec_info.name)
        except (LookupError, UnicodeDecodeError):
            pass

    return _decode_text_bytes(raw)


class _SafeHTMLTextExtractor(HTMLParser):
    """Stdlib HTMLParser discarding executable/non-visible tags and bounding decoded output."""

    def __init__(self, *, max_decompressed_bytes: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max_bytes = max_decompressed_bytes
        self._byte_count = 0
        self._skip_depth = 0
        self._in_heading = False
        self._heading_buf: list[str] = []
        self._current_heading: str | None = None
        self._in_row = False
        self._in_cell = False
        self._row_cells: list[str] = []
        self._cell_buf: list[str] = []
        self._block_buf: list[str] = []
        self.blocks: list[tuple[str, str | None]] = []

    def _charge_bytes(self, text: str) -> None:
        self._byte_count += len(text.encode("utf-8"))
        if self._byte_count > self._max_bytes:
            raise _DecompressedLimitExceeded

    def _flush_block(self) -> None:
        if not self._block_buf:
            return
        raw_text = "".join(self._block_buf)
        self._block_buf.clear()
        cleaned = _WS_COLLAPSE_RE.sub(" ", raw_text).strip()
        if cleaned:
            self.blocks.append((cleaned, self._current_heading))

    def _flush_cell(self) -> None:
        if not self._cell_buf:
            return
        cleaned = _WS_COLLAPSE_RE.sub(" ", "".join(self._cell_buf)).strip()
        self._cell_buf.clear()
        if cleaned:
            self._row_cells.append(cleaned)

    def _flush_row(self) -> None:
        self._flush_cell()
        if self._row_cells:
            row_text = " | ".join(self._row_cells)
            self._row_cells.clear()
            self.blocks.append((row_text, self._current_heading))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        tag_l = tag.lower()
        if tag_l in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if tag_l in _HEADING_TAGS:
            self._flush_block()
            self._in_heading = True
            self._heading_buf.clear()
        elif tag_l == "tr":
            self._flush_block()
            self._flush_row()
            self._in_row = True
        elif tag_l in _CELL_TAGS:
            if not self._in_row:
                self._flush_block()
                self._in_row = True
            self._flush_cell()
            self._in_cell = True
        elif tag_l == "br":
            if self._in_cell:
                self._cell_buf.append(" ")
            elif self._in_heading:
                self._heading_buf.append(" ")
            else:
                self._flush_block()
        elif tag_l in _BLOCK_TAGS:
            self._flush_block()

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in _SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if tag_l in _HEADING_TAGS:
            if self._in_heading:
                heading_text = _WS_COLLAPSE_RE.sub(" ", "".join(self._heading_buf)).strip()
                self._heading_buf.clear()
                self._in_heading = False
                if heading_text:
                    self._current_heading = heading_text
                    self.blocks.append((heading_text, heading_text))
        elif tag_l in _CELL_TAGS:
            self._flush_cell()
            self._in_cell = False
        elif tag_l == "tr":
            self._flush_row()
            self._in_row = False
            self._in_cell = False
        elif tag_l in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not data:
            return
        self._charge_bytes(data)
        if self._in_heading:
            self._heading_buf.append(data)
        elif self._in_cell:
            self._cell_buf.append(data)
        else:
            self._block_buf.append(data)

    def handle_comment(self, data: str) -> None:
        # Discard all HTML comments completely
        _ = data

    def finalize(self) -> list[tuple[str, str | None]]:
        if self._in_heading and self._heading_buf:
            heading_text = _WS_COLLAPSE_RE.sub(" ", "".join(self._heading_buf)).strip()
            self._heading_buf.clear()
            self._in_heading = False
            if heading_text:
                self._current_heading = heading_text
                self.blocks.append((heading_text, heading_text))
        self._flush_row()
        self._flush_block()
        return self.blocks


def parse_html_bytes(
    raw: bytes,
    source: SourceMetadata,
    limits: DocumentLimits,
) -> ParseResult:
    """Parse raw HTML bytes into a PreparedDocument with heading & paragraph DocumentSegments."""
    if len(raw) == 0:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="empty_document",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Input HTML document is empty (zero bytes).",
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
                    message="Input file appears to contain binary data rather than HTML.",
                ),
            ),
        )

    decoded = _decode_html_bytes(raw)
    if decoded is None:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="decoding_error",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Unable to decode HTML document with supported text encodings.",
                ),
            ),
        )

    extractor = _SafeHTMLTextExtractor(max_decompressed_bytes=limits.max_decompressed_bytes)
    try:
        extractor.feed(decoded)
        extractor.close()
        raw_blocks = extractor.finalize()
    except _DecompressedLimitExceeded:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Decoded HTML text exceeds configured max_decompressed_bytes limit.",
                ),
            ),
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
            offset += 1  # '\n' separator
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
                    message=(
                        "HTML document contains no extractable visible text "
                        "(markup, scripts, styles, comments, or whitespace only)."
                    ),
                ),
            ),
        )

    full_text = "\n".join(full_parts)
    if len(full_text.encode("utf-8")) > limits.max_decompressed_bytes:
        return ParseResult(
            document=None,
            errors=(
                DiagnosticIssue(
                    code="resource_limit_exceeded",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Prepared HTML text exceeds configured max_decompressed_bytes limit.",
                ),
            ),
        )

    return ParseResult(
        document=PreparedDocument(
            source=source,
            format=DocumentFormat.HTML,
            full_text=full_text,
            segments=tuple(segments),
            page_count=None,
        )
    )
