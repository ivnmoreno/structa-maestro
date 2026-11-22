"""Provenance association, source location mapping, and evidence integrity verification."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
)
from structa_maestro.contracts.models import (
    DiagnosticIssue,
    ProvenanceEvidence,
    SourceLocation,
)
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.strategies.base import ProposedEvidence

_MAX_EXCERPT_CHARS = 500
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ProvenanceAssociationResult:
    """Verified provenance entries and provenance-stage warnings for one extracted field."""

    evidence: list[ProvenanceEvidence] = field(default_factory=list)
    warnings: list[DiagnosticIssue] = field(default_factory=list)


def _collapse_ws(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _find_span(full_text: str, quote: str) -> tuple[int, int] | None:
    if not quote or not quote.strip():
        return None
    idx = full_text.find(quote)
    if idx != -1:
        return idx, idx + len(quote)

    # Fallback: whitespace-tolerant search preserving original offsets
    tokens = _collapse_ws(quote).split(" ")
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(tok) for tok in tokens)
    match = re.search(pattern, full_text)
    if match is not None:
        return match.start(), match.end()
    return None


def _location_from_span(
    segments: tuple[DocumentSegment, ...],
    char_start: int,
    char_end: int,
) -> SourceLocation:
    overlapping = [
        seg for seg in segments if seg.char_end >= char_start and seg.char_start <= char_end
    ]
    if not overlapping:
        return SourceLocation(char_start=char_start, char_end=char_end)

    first = overlapping[0]
    last = overlapping[-1]
    line_start = first.line_start
    line_end = last.line_end if last.line_end is not None else first.line_end
    if line_start is not None and line_end is not None and line_end < line_start:
        line_end = line_start

    return SourceLocation(
        page=first.page,
        paragraph=first.paragraph,
        line_start=line_start,
        line_end=line_end,
        char_start=char_start,
        char_end=char_end,
        section=first.heading,
    )


def _scalar_value_supported_by_excerpt(value: Any, excerpt: str) -> bool:
    """Verify whether a direct supporting excerpt plausibly supports a scalar extracted value."""
    if value is None or isinstance(value, (bool, dict, list)):
        return True
    norm_excerpt = _collapse_ws(excerpt).lower()
    if isinstance(value, str):
        val = _collapse_ws(value).lower()
        if not val:
            return True
        return val in norm_excerpt
    if isinstance(value, Decimal):
        candidates = {str(value), format(value.normalize(), "f")}
        stripped_excerpt = norm_excerpt.replace(",", "")
        return any(c.lower() in stripped_excerpt for c in candidates)
    if isinstance(value, int):
        stripped_excerpt = norm_excerpt.replace(",", "")
        return str(value) in stripped_excerpt
    if isinstance(value, date):
        return value.isoformat() in norm_excerpt or str(value.year) in norm_excerpt
    return True


def _parse_relationship(raw: str) -> EvidenceRelationship:
    try:
        return EvidenceRelationship(raw.lower())
    except ValueError:
        return EvidenceRelationship.DIRECT


class ProvenanceAssociator:
    """Verifies candidate source quotes against PreparedDocument without fabricating locations."""

    def __init__(self, *, max_excerpt_chars: int = _MAX_EXCERPT_CHARS) -> None:
        self._max_excerpt_chars = max_excerpt_chars

    def associate(
        self,
        *,
        document: PreparedDocument,
        proposed_evidence: tuple[ProposedEvidence, ...],
        field_name: str,
        presence: FieldPresence,
        value: Any,
        record_id: str,
    ) -> ProvenanceAssociationResult:
        """Associate and verify zero, one, or multiple evidence passages for one field."""
        if not proposed_evidence:
            return ProvenanceAssociationResult()

        doc_id = document.source.document_id
        verified: list[ProvenanceEvidence] = []
        warnings: list[DiagnosticIssue] = []

        for item in proposed_evidence:
            # PRO-010: Reject evidence explicitly attributed to a foreign document
            if item.source_document_id is not None and item.source_document_id != doc_id:
                warnings.append(
                    DiagnosticIssue(
                        code="cross_document_evidence_rejected",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.PROVENANCE,
                        message=(
                            f"Rejected provenance referencing foreign document for field "
                            f"'{field_name}'."
                        ),
                        record_id=record_id,
                        field_path=field_name,
                    )
                )
                continue

            quote = item.quote.strip()
            if not quote:
                continue

            requested_rel = _parse_relationship(item.relationship)
            span = _find_span(document.full_text, quote)

            if span is None:
                # Quote does not appear in source document — never fabricate a location!
                excerpt, truncated = self._bound_excerpt(quote)
                verified.append(
                    ProvenanceEvidence(
                        source_document_id=doc_id,
                        location=None,
                        evidence=excerpt,
                        relationship=EvidenceRelationship.UNVERIFIED,
                        truncated=truncated,
                    )
                )
                warnings.append(
                    DiagnosticIssue(
                        code="unverified_evidence",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.PROVENANCE,
                        message=(
                            f"Proposed evidence for field '{field_name}' could not be located "
                            "in the source document."
                        ),
                        record_id=record_id,
                        field_path=field_name,
                    )
                )
                continue

            char_start, char_end = span
            original_excerpt = document.full_text[char_start:char_end]
            excerpt, truncated = self._bound_excerpt(original_excerpt)
            location = _location_from_span(document.segments, char_start, char_end)

            relationship = requested_rel
            if (
                presence == FieldPresence.PRESENT
                and requested_rel == EvidenceRelationship.DIRECT
                and not _scalar_value_supported_by_excerpt(value, original_excerpt)
            ):
                relationship = EvidenceRelationship.CONFLICTING
                warnings.append(
                    DiagnosticIssue(
                        code="evidence_value_conflict",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.PROVENANCE,
                        message=(
                            f"Cited direct evidence for field '{field_name}' does not match "
                            "the extracted value."
                        ),
                        record_id=record_id,
                        field_path=field_name,
                    )
                )

            verified.append(
                ProvenanceEvidence(
                    source_document_id=doc_id,
                    location=location,
                    evidence=excerpt,
                    relationship=relationship,
                    truncated=truncated,
                )
            )

        return ProvenanceAssociationResult(evidence=verified, warnings=warnings)

    def _bound_excerpt(self, text: str) -> tuple[str, bool]:
        if len(text) <= self._max_excerpt_chars:
            return text, False
        return text[: self._max_excerpt_chars], True
