"""Provider-independent ExtractionStrategy protocols and internal strategy DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from structa_maestro.contracts.enums import FieldPresence
from structa_maestro.contracts.models import DiagnosticIssue
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema


@dataclass(frozen=True, slots=True)
class ProposedEvidence:
    """Raw source evidence quote proposed by an extraction strategy before verification."""

    quote: str
    source_document_id: str | None = None
    page: int | None = None
    paragraph: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    section: str | None = None
    relationship: str = "direct"


@dataclass(frozen=True, slots=True)
class ProposedField:
    """Raw extracted field value and quality hints proposed by a strategy."""

    presence: FieldPresence = FieldPresence.PRESENT
    value: Any = None
    confidence: float | None = None
    confidence_method: str | None = None
    confidence_scale: str = "0-1"
    evidence: tuple[ProposedEvidence, ...] = field(default_factory=tuple)
    ambiguous: bool = False
    warnings: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)
    errors: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProposedRecord:
    """Raw extracted record proposed by a strategy."""

    fields: dict[str, ProposedField] = field(default_factory=dict)
    partial: bool = False
    failed: bool = False
    errors: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)
    warnings: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    """Provider-independent intermediate output returned by any ExtractionStrategy."""

    records: tuple[ProposedRecord, ...] = field(default_factory=tuple)
    partial: bool = False
    failed: bool = False
    errors: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)
    warnings: tuple[DiagnosticIssue, ...] = field(default_factory=tuple)
    default_confidence_method: str = "extraction-assessment"


@runtime_checkable
class ExtractionStrategy(Protocol):
    """Replaceable synchronous extraction strategy decoupled from Canonical Output Contract."""

    @property
    def name(self) -> str:
        """Stable human-readable name identifying this strategy."""
        ...

    def extract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        """Extract raw candidate records from a prepared document according to schema."""
        ...


@runtime_checkable
class AsyncExtractionStrategy(Protocol):
    """Replaceable native asyncio extraction strategy decoupled from Canonical Output Contract."""

    @property
    def name(self) -> str:
        """Stable human-readable name identifying this strategy."""
        ...

    async def aextract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        """Asynchronously extract raw candidate records from a prepared document."""
        ...
