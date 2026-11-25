"""Layer 1: Always-on pure-Python & Hypothesis conformance tests against the formal model.

Runs in every `pytest` invocation without requiring Lean 4 / Lake installed.
Verifies empirical conformance between `structa_maestro`'s authoritative Python implementation
and the formalized Contract v1.0 invariants and deterministic pipeline-assembly rules.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.conformance.reference_model import (
    build_confidence_vectors,
    build_extracted_field_vectors,
    build_extraction_result_vectors,
    build_source_location_vectors,
    build_validation_outcome_vectors,
    formal_assemble_overall_status,
    formal_assemble_record_status,
    formal_make_result_cardinality_status,
    is_valid_confidence_dict,
    is_valid_extracted_field_dict,
    is_valid_extraction_result_dict,
    is_valid_source_location_dict,
    is_valid_validation_outcome_dict,
)

from structa_maestro.builder.result_builder import ResultBuilder
from structa_maestro.confidence.normalizer import ConfidenceNormalizer
from structa_maestro.contracts.enums import (
    DocumentFormat,
    EvidenceRelationship,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    DiagnosticIssue,
    ExtractedField,
    ExtractionResult,
    SourceLocation,
    SourceMetadata,
    ValidationOutcome,
)
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.provenance.associator import ProvenanceAssociator
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import (
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)
from structa_maestro.validation.validator import DeterministicValidator


def _make_doc(
    doc_id: str = "doc-001",
    text: str = "Invoice INV-2026-001 Total 100",
) -> PreparedDocument:
    return PreparedDocument(
        source=SourceMetadata(document_id=doc_id, filename="invoice.txt", media_type="text/plain"),
        format=DocumentFormat.TXT,
        full_text=text,
        segments=(
            DocumentSegment(
                text=text,
                char_start=0,
                char_end=len(text),
                line_start=1,
                line_end=1,
                paragraph=1,
            ),
        ),
    )


def _simple_schema(cardinality: str = "single") -> ExtractionSchema:
    return ExtractionSchema.from_dict(
        {
            "schema_id": "conformance-schema",
            "schema_version": "1.0",
            "cardinality": cardinality,
            "fields": {
                "code": {"type": "string", "required": True},
                "note": {"type": "string", "required": False, "nullable": True},
            },
        }
    )


@pytest.mark.parametrize(("payload", "expected"), build_source_location_vectors())
def test_conformance_source_location_vectors(payload: dict[str, Any], expected: bool) -> None:
    assert is_valid_source_location_dict(payload) is expected
    if expected:
        loc = SourceLocation.model_validate(payload)
        assert is_valid_source_location_dict(loc.model_dump())
    else:
        with pytest.raises(ValidationError):
            SourceLocation.model_validate(payload)


@pytest.mark.parametrize(("payload", "expected"), build_confidence_vectors())
def test_conformance_confidence_vectors(payload: dict[str, Any], expected: bool) -> None:
    assert is_valid_confidence_dict(payload) is expected
    if expected:
        conf = ConfidenceAssessment.model_validate(payload)
        assert is_valid_confidence_dict(conf.model_dump())
    else:
        with pytest.raises(ValidationError):
            ConfidenceAssessment.model_validate(payload)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_conformance_confidence_rejects_non_finite_on_zero_one_scale(non_finite: float) -> None:
    with pytest.raises(ValidationError):
        ConfidenceAssessment(value=non_finite, scale="0-1", method="llm", calibrated=False)


@pytest.mark.parametrize(("payload", "expected"), build_validation_outcome_vectors())
def test_conformance_validation_outcome_vectors(payload: dict[str, Any], expected: bool) -> None:
    assert is_valid_validation_outcome_dict(payload) is expected
    if expected:
        out = ValidationOutcome.model_validate(payload)
        assert is_valid_validation_outcome_dict(out.model_dump(mode="json"))
    else:
        with pytest.raises(ValidationError):
            ValidationOutcome.model_validate(payload)


@pytest.mark.parametrize(("payload", "expected"), build_extracted_field_vectors())
def test_conformance_extracted_field_vectors(payload: dict[str, Any], expected: bool) -> None:
    assert is_valid_extracted_field_dict(payload) is expected
    if expected:
        ef = ExtractedField.model_validate(payload)
        assert is_valid_extracted_field_dict(ef.model_dump(mode="json"))
    else:
        with pytest.raises(ValidationError):
            ExtractedField.model_validate(payload)


@pytest.mark.parametrize(("payload", "expected"), build_extraction_result_vectors())
def test_conformance_extraction_result_vectors(payload: dict[str, Any], expected: bool) -> None:
    assert is_valid_extraction_result_dict(payload) is expected
    if expected:
        res = ExtractionResult.model_validate(payload)
        assert is_valid_extraction_result_dict(res.to_dict())
    else:
        with pytest.raises(ValidationError):
            ExtractionResult.model_validate(payload)


def test_conformance_missing_vs_explicit_null_vs_empty_string_distinct() -> None:
    ok = ValidationOutcome(status=ValidationStatus.VALID)
    f_missing = ExtractedField(presence=FieldPresence.MISSING, value=None, validation=ok)
    f_null = ExtractedField(presence=FieldPresence.PRESENT, value=None, validation=ok)
    f_empty = ExtractedField(presence=FieldPresence.PRESENT, value="", validation=ok)
    assert f_missing != f_null
    assert f_null != f_empty
    assert f_missing != f_empty


def test_conformance_deterministic_validator_invalid_always_has_errors() -> None:
    schema = _simple_schema()
    validator = DeterministicValidator()
    # Invalid record: missing required field `code`
    invalid_res = validator.validate_record({}, schema, record_id="record-001")
    assert invalid_res.record_outcome.status == ValidationStatus.INVALID
    assert len(invalid_res.record_outcome.errors) >= 1
    assert all(
        e.severity == IssueSeverity.ERROR and e.stage == PipelineStage.VALIDATION
        for e in invalid_res.record_outcome.errors
    )

    # Valid record: required `code` present, optional nullable `note` explicitly null
    valid_res = validator.validate_record(
        {
            "code": (FieldPresence.PRESENT, "INV-1"),
            "note": (FieldPresence.PRESENT, None),
        },
        schema,
        record_id="record-001",
    )
    assert valid_res.record_outcome.status == ValidationStatus.VALID
    assert valid_res.record_outcome.errors == []

    # Pre-validation failure with zero records yields NOT_VALIDATED
    pre_val = validator.validate_result_cardinality(
        [], schema, has_records=False, failed_before_validation=True
    )
    assert pre_val.status == ValidationStatus.NOT_VALIDATED
    assert pre_val.errors == []


def test_conformance_result_builder_exhaustive_status_matrix() -> None:
    """Exhaustively checks all 64 (strategy_failed, strategy_partial, has_result_errors,
    has_records, any_record_not_completed, has_validation_errors) combinations against the
    formal ResultBuilder assembly rules."""
    doc = _make_doc()
    schema = _simple_schema(cardinality="multiple")
    builder = ResultBuilder()
    err_issue = DiagnosticIssue(
        code="ext_err",
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.EXTRACTION,
        message="Processing error.",
    )

    for (
        strategy_failed,
        strategy_partial,
        has_result_errors,
        has_records,
        any_record_not_completed,
        has_validation_errors,
    ) in itertools.product([False, True], repeat=6):
        if not has_records and (any_record_not_completed or has_validation_errors):
            # Impossible combination when records == () under cardinality="multiple"
            continue

        records: tuple[ProposedRecord, ...]
        if has_records:
            fields: dict[str, ProposedField] = (
                {} if has_validation_errors else {"code": ProposedField(value="INV-2026-001")}
            )
            records = (ProposedRecord(fields=fields, partial=any_record_not_completed),)
        else:
            records = ()

        output = StrategyOutput(
            records=records,
            failed=strategy_failed,
            partial=strategy_partial,
            errors=(err_issue,) if has_result_errors else (),
        )
        result = builder.build(document=doc, schema=schema, strategy_output=output)

        expected_ps = formal_assemble_overall_status(
            strategy_failed=strategy_failed,
            strategy_partial=strategy_partial,
            has_result_errors=has_result_errors,
            has_records=has_records,
            any_record_not_completed=any_record_not_completed,
        )
        expected_vs = formal_make_result_cardinality_status(
            failed_before_validation=strategy_failed,
            has_records=has_records,
            has_validation_errors=has_validation_errors,
        )

        assert result.status == expected_ps
        assert result.validation.status == expected_vs
        assert is_valid_extraction_result_dict(result.to_dict())


def test_conformance_result_builder_record_status_exhaustive_matrix() -> None:
    """Exhaustively checks all 16 record-level status flag combinations."""
    doc = _make_doc()
    schema = _simple_schema()
    builder = ResultBuilder()
    rec_err = DiagnosticIssue(
        code="rec_err",
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.EXTRACTION,
        message="Record error.",
    )
    field_err = DiagnosticIssue(
        code="field_err",
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.EXTRACTION,
        message="Field error.",
    )

    for rec_failed, rec_partial, has_rec_err, has_field_err in itertools.product(
        [False, True], repeat=4
    ):
        proposed = ProposedRecord(
            fields={
                "code": ProposedField(
                    value="INV-2026-001",
                    errors=(field_err,) if has_field_err else (),
                )
            },
            failed=rec_failed,
            partial=rec_partial,
            errors=(rec_err,) if has_rec_err else (),
        )
        res = builder.build(
            document=doc,
            schema=schema,
            strategy_output=StrategyOutput(records=(proposed,)),
        )
        expected = formal_assemble_record_status(
            record_failed=rec_failed,
            record_partial=rec_partial,
            has_record_errors=has_rec_err,
            has_field_errors=has_field_err,
        )
        assert res.records[0].status == expected
        # Record-level FAILED still validates independently to VALID here!
        assert res.records[0].validation.status == ValidationStatus.VALID


def test_conformance_provenance_associator_rules() -> None:
    doc = _make_doc(doc_id="doc-001", text="Invoice INV-2026-001 Total 100")
    assoc = ProvenanceAssociator()

    # 1. Foreign document evidence rejected
    res_foreign = assoc.associate(
        document=doc,
        proposed_evidence=(
            ProposedEvidence(quote="Invoice INV-2026-001", source_document_id="doc-FOREIGN"),
        ),
        field_name="code",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert res_foreign.evidence == []
    assert any(w.code == "cross_document_evidence_rejected" for w in res_foreign.warnings)

    # 2. Unlocated quote -> UNVERIFIED with location=None
    res_unloc = assoc.associate(
        document=doc,
        proposed_evidence=(ProposedEvidence(quote="Not present anywhere in text"),),
        field_name="code",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert len(res_unloc.evidence) == 1
    ev = res_unloc.evidence[0]
    assert ev.source_document_id == "doc-001"
    assert ev.relationship == EvidenceRelationship.UNVERIFIED
    assert ev.location is None

    # 3. Located direct quote conflicting with scalar value -> CONFLICTING with location
    res_conflict = assoc.associate(
        document=doc,
        proposed_evidence=(ProposedEvidence(quote="Invoice INV-2026-001", relationship="direct"),),
        field_name="code",
        presence=FieldPresence.PRESENT,
        value="DIFFERENT-999",
        record_id="record-001",
    )
    assert len(res_conflict.evidence) == 1
    assert res_conflict.evidence[0].relationship == EvidenceRelationship.CONFLICTING
    assert res_conflict.evidence[0].location is not None


@pytest.mark.property
@settings(max_examples=60)
@given(
    page=st.one_of(st.none(), st.integers(min_value=-2, max_value=10)),
    paragraph=st.one_of(st.none(), st.integers(min_value=-2, max_value=10)),
    line_start=st.one_of(st.none(), st.integers(min_value=-2, max_value=20)),
    line_end=st.one_of(st.none(), st.integers(min_value=-2, max_value=20)),
    char_start=st.one_of(st.none(), st.integers(min_value=-2, max_value=50)),
    char_end=st.one_of(st.none(), st.integers(min_value=-2, max_value=50)),
)
def test_property_conformance_source_location(
    page: int | None,
    paragraph: int | None,
    line_start: int | None,
    line_end: int | None,
    char_start: int | None,
    char_end: int | None,
) -> None:
    payload = {
        "page": page,
        "paragraph": paragraph,
        "line_start": line_start,
        "line_end": line_end,
        "char_start": char_start,
        "char_end": char_end,
    }
    expected = is_valid_source_location_dict(payload)
    if expected:
        loc = SourceLocation.model_validate(payload)
        assert loc.page == page
    else:
        with pytest.raises(ValidationError):
            SourceLocation.model_validate(payload)


@pytest.mark.property
@settings(max_examples=60)
@given(
    raw_value=st.one_of(
        st.none(),
        st.booleans(),
        st.text(max_size=10),
        st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False),
        st.sampled_from([float("nan"), float("inf"), float("-inf")]),
    ),
    scale=st.sampled_from(["0-1", "  ", "0-100", "logits"]),
)
def test_property_conformance_confidence_normalizer(raw_value: Any, scale: str) -> None:
    normalizer = ConfidenceNormalizer()
    res = normalizer.normalize(raw_value=raw_value, scale=scale)
    if raw_value is None:
        assert res.confidence is None
        return

    assert res.confidence is not None
    assert res.confidence.calibrated is False
    assert is_valid_confidence_dict(res.confidence.model_dump())

    clean_scale = scale.strip() if scale.strip() else "0-1"
    is_numeric = not isinstance(raw_value, bool) and isinstance(raw_value, (int, float))
    if not is_numeric:
        assert res.confidence.value is None
        assert len(res.warnings) == 1
    else:
        fval = float(raw_value)
        out_of_bounds = clean_scale == "0-1" and not (0.0 <= fval <= 1.0)
        if math.isnan(fval) or math.isinf(fval) or out_of_bounds:
            assert res.confidence.value is None
            assert len(res.warnings) == 1
        else:
            assert res.confidence.value == round(fval, 6)
            assert res.warnings == []
