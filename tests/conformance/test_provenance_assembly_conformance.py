"""Layer 1: Always-on pure-Python & Hypothesis conformance tests for Provenance & ResultBuilder.

Verifies empirical conformance between `provenance/associator.py`, `builder/result_builder.py`,
and the formal specifications in `formal/StructaFormal/ProvenanceAssociation.lean` and
`ResultAssembly.lean` without requiring Lean 4 installed.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conformance.provenance_assembly_reference import (
    build_curated_result_builder_scenarios,
    build_sample_prepared_document,
    build_span_location_vectors,
)

from structa_maestro.builder.result_builder import ResultBuilder
from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.documents.models import DocumentSegment
from structa_maestro.provenance.associator import (
    ProvenanceAssociator,
    _location_from_span,
    _parse_relationship,
)
from structa_maestro.strategies.base import ProposedEvidence


class _FaultyProvenanceAssociator(ProvenanceAssociator):
    """Test double injecting provenance exceptions on selected `(record_id, field_name)` pairs."""

    def __init__(self, failing_fields: set[tuple[int, str]]) -> None:
        super().__init__()
        self._failing_fields = failing_fields

    def associate(self, **kwargs: Any) -> Any:
        rec_id = str(kwargs["record_id"])
        rec_idx = int(rec_id.split("-")[1]) - 1
        fname = str(kwargs["field_name"])
        if (rec_idx, fname) in self._failing_fields:
            raise RuntimeError("Injected provenance fault")
        return super().associate(**kwargs)


@pytest.mark.parametrize(("name", "segments", "cs", "ce"), build_span_location_vectors())
def test_conformance_location_from_span_curated(
    name: str,
    segments: tuple[DocumentSegment, ...],
    cs: int,
    ce: int,
) -> None:
    loc = _location_from_span(segments, cs, ce)
    assert loc.char_start == cs
    assert loc.char_end == ce
    if loc.line_start is not None and loc.line_end is not None:
        assert loc.line_start <= loc.line_end, f"Line clamping violated for {name}"


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(
    cs=st.integers(min_value=0, max_value=100),
    delta=st.integers(min_value=0, max_value=50),
    l1=st.integers(min_value=1, max_value=50),
    l2=st.integers(min_value=1, max_value=50),
    text=st.text(max_size=80),
    max_chars=st.integers(min_value=0, max_value=40),
)
def test_property_conformance_span_clamping_and_bound_excerpt(
    cs: int,
    delta: int,
    l1: int,
    l2: int,
    text: str,
    max_chars: int,
) -> None:
    ce = cs + delta
    segs = (
        DocumentSegment(text="a", char_start=0, char_end=cs, page=1, line_start=l1, line_end=l1),
        DocumentSegment(
            text="b", char_start=cs, char_end=ce + 10, page=2, line_start=l2, line_end=l2
        ),
    )
    loc = _location_from_span(segs, cs, ce)
    assert loc.char_start == cs and loc.char_end == ce
    assert loc.line_start is not None and loc.line_end is not None
    assert loc.line_start <= loc.line_end

    assoc = ProvenanceAssociator(max_excerpt_chars=max_chars)
    bounded, truncated = assoc._bound_excerpt(text)
    assert len(bounded) <= max_chars
    assert truncated is (len(text) > max_chars)


def test_conformance_provenance_associator_all_branches() -> None:
    doc = build_sample_prepared_document()
    assoc = ProvenanceAssociator(max_excerpt_chars=12)
    assert _parse_relationship("unknown_rel") == EvidenceRelationship.DIRECT

    res = assoc.associate(
        document=doc,
        proposed_evidence=(
            ProposedEvidence(quote="Invoice INV-100", source_document_id="other-doc"),
            ProposedEvidence(quote="   \t  "),
            ProposedEvidence(quote="Not in document at all", relationship="direct"),
            ProposedEvidence(quote="Total: 110.00", relationship="direct"),
            ProposedEvidence(quote="Total: 110.00", relationship="inferred"),
        ),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-100",
        record_id="record-001",
    )
    assert [ev.relationship for ev in res.evidence] == [
        EvidenceRelationship.UNVERIFIED,
        EvidenceRelationship.CONFLICTING,
        EvidenceRelationship.INFERRED,
    ]
    assert all(ev.source_document_id == doc.source.document_id for ev in res.evidence)
    assert res.evidence[0].location is None
    assert all(len(ev.evidence) <= 12 for ev in res.evidence)
    assert [w.code for w in res.warnings] == [
        "cross_document_evidence_rejected",
        "unverified_evidence",
        "evidence_value_conflict",
    ]
    assert all(
        w.severity == IssueSeverity.WARNING and w.stage == PipelineStage.PROVENANCE
        for w in res.warnings
    )


def test_conformance_result_builder_curated_firewall_and_contract_invariants() -> None:
    for (
        name,
        doc,
        schema,
        strat_out,
        doc_warns,
        failing_fields,
    ) in build_curated_result_builder_scenarios():
        prov = _FaultyProvenanceAssociator(failing_fields) if failing_fields else None
        builder = ResultBuilder(provenance_associator=prov)
        res1 = builder.build(
            document=doc,
            schema=schema,
            strategy_output=strat_out,
            document_warnings=doc_warns,
            extraction_id="ext-conformance-001",
        )
        res2 = builder.build(
            document=doc,
            schema=schema,
            strategy_output=strat_out,
            document_warnings=doc_warns,
            extraction_id="ext-conformance-001",
        )
        assert res1 == res2, f"[{name}] ResultBuilder.build non-deterministic"
        assert res1.contract_version == "1.0"
        record_ids = [r.record_id for r in res1.records]
        assert len(record_ids) == len(set(record_ids)), f"[{name}] duplicate record IDs"

        for rec in res1.records:
            for fname, field in rec.fields.items():
                if field.presence == FieldPresence.MISSING:
                    assert field.value is None, f"[{name}] MISSING field {fname} leaked non-null"
                if field.confidence is not None:
                    assert field.confidence.calibrated is False
                for ev in field.provenance:
                    assert ev.source_document_id == doc.source.document_id

        if name == "empty_failed_synthesizes_extraction_failed":
            assert res1.status == ProcessingStatus.FAILED
            assert res1.validation.status == ValidationStatus.NOT_VALIDATED
            assert [e.code for e in res1.errors] == ["extraction_failed"]
        elif name == "provenance_fault_isolated_forces_partial":
            assert res1.status == ProcessingStatus.PARTIAL
            assert res1.records[0].status == ProcessingStatus.PARTIAL
            assert res1.records[0].fields["invoice_number"].provenance == []
            assert [e.code for e in res1.records[0].fields["invoice_number"].errors] == [
                "provenance_association_failed"
            ]
            assert len(res1.records[0].fields["total"].provenance) == 1
        elif name == "validation_warning_propagates_non_validation_stays_record_scoped":
            assert any(w.code == "missing_required_group" for w in res1.warnings)
            assert not any(w.code == "record_hint" for w in res1.warnings)
            assert any(w.code == "record_hint" for w in res1.records[0].warnings)
