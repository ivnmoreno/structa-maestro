"""Internal prepared document models (data-model.md Section 2.3)."""

from __future__ import annotations

from dataclasses import dataclass, field

from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.contracts.models import SourceMetadata


@dataclass(frozen=True, slots=True)
class DocumentSegment:
    """Normalized text slice carrying source location metadata (data-model.md Section 2.3)."""

    text: str
    char_start: int
    char_end: int
    page: int | None = None
    paragraph: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    heading: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    """Internal representation of parsed document text and structural location map."""

    source: SourceMetadata
    format: DocumentFormat
    full_text: str
    segments: tuple[DocumentSegment, ...] = field(default_factory=tuple)
    page_count: int | None = None
