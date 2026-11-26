"""Comprehensive unit, contract, Hypothesis, binding, and CLI tests for human review workflow."""

from __future__ import annotations

import io
import json
import math
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

from structa_maestro import (
    MAX_REVIEW_PAYLOAD_BYTES,
    REVIEW_AUDIT_VERSION,
    REVIEW_PLAN_VERSION,
    REVIEW_SUBMISSION_VERSION,
    ArithmeticRule,
    ArithmeticTerm,
    ConfidenceAssessment,
    ConfigurationError,
    DeterministicTestStrategy,
    DiagnosticIssue,
    DocumentInput,
    ExtractionCardinality,
    ExtractionResult,
    ExtractionSchema,
    FieldPresence,
    FieldReviewDecision,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    ProvenanceEvidence,
    RecordReviewDecision,
    ReviewAction,
    ReviewPolicy,
    ReviewReason,
    ReviewSubmission,
    RuleSeverity,
    StrategyOutput,
    ValidationStatus,
    apply_review,
    bind_records,
    extract,
    plan_review,
)
from structa_maestro.cli import run_cli


class _InvoiceModel(BaseModel):
    invoice_number: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    issue_date: date | None = None


def _invoice_schema(
    *, cardinality: ExtractionCardinality = ExtractionCardinality.SINGLE
) -> ExtractionSchema:
    return ExtractionSchema.from_pydantic(
        _InvoiceModel,
        schema_id="inv-review",
        cardinality=cardinality,
        rules=(
            ArithmeticRule(
                rule_id="balance",
                terms=(
                    ArithmeticTerm(field="subtotal", sign=1),
                    ArithmeticTerm(field="tax", sign=1),
                ),
                target_field="total",
                tolerance=Decimal("0.01"),
                severity=RuleSeverity.ERROR,
            ),
        ),
    )


def _sample_doc() -> DocumentInput:
    return DocumentInput.from_text(
        "Invoice INV-100\nSubtotal: 100.00\nTax: 10.00\nTotal: 110.00\nDate: 2026-03-15\n",
        filename="inv.txt",
        document_id="doc-rev-1",
    )


def _clean_record() -> ProposedRecord:
    return ProposedRecord(
        fields={
            "invoice_number": ProposedField(
                presence=FieldPresence.PRESENT,
                value="INV-100",
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Invoice INV-100", relationship="direct"),),
            ),
            "subtotal": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("100.00"),
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Subtotal: 100.00", relationship="direct"),),
            ),
            "tax": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("10.00"),
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Tax: 10.00", relationship="direct"),),
            ),
            "total": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("110.00"),
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Total: 110.00", relationship="direct"),),
            ),
            "issue_date": ProposedField(
                presence=FieldPresence.MISSING,
                value=None,
                confidence=None,
            ),
        }
    )


# =============================================================================
# User Story 1: Deterministic Multi-Dimension Review Triage (`plan_review`)
# =============================================================================


def test_plan_review_clean_result_needs_no_review() -> None:
    schema = _invoice_schema()
    res = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=(_clean_record(),))),
    )
    plan = plan_review(res)
    assert plan.plan_version == REVIEW_PLAN_VERSION
    assert plan.extraction_id == res.extraction_id
    assert plan.needs_review is False
    assert plan.total_items == 0
    assert plan.items == ()
    assert plan.reason_counts == {}
    # Missing optional field (`issue_date`) is NOT falsely flagged even with missing flags on
    strict_plan = plan_review(
        res,
        policy=ReviewPolicy(flag_missing_confidence=True, flag_missing_provenance=True),
    )
    assert strict_plan.needs_review is False
    assert json.loads(plan.to_json())["needs_review"] is False


def test_plan_review_flags_all_five_quality_dimensions_in_canonical_order() -> None:
    schema = _invoice_schema()
    strat = DeterministicTestStrategy(
        StrategyOutput(
            partial=True,
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value="INV-100",
                            confidence=0.60,  # LOW_CONFIDENCE + UNCALIBRATED_CONFIDENCE
                            ambiguous=True,  # AMBIGUOUS_VALUE
                            evidence=(
                                ProposedEvidence(quote="Not in document", relationship="direct"),
                                ProposedEvidence(quote="Total: 110.00", relationship="direct"),
                            ),
                            errors=(
                                DiagnosticIssue(
                                    code="field_err",
                                    severity=IssueSeverity.ERROR,
                                    stage=PipelineStage.EXTRACTION,
                                    message="Field issue.",
                                    field_path="invoice_number",
                                ),
                            ),
                        ),
                        "subtotal": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("100.00"),
                            confidence=None,  # MISSING_CONFIDENCE
                            evidence=(),  # MISSING_PROVENANCE
                        ),
                        "tax": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("10.00"),
                            confidence=80.0,
                            confidence_scale="0-100",  # Non-0-1 skips min_confidence comparison
                        ),
                        "total": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("999.00"),  # Cross-field ArithmeticRule mismatch
                            confidence=0.95,
                        ),
                    }
                ),
            ),
        )
    )
    res = extract(_sample_doc(), schema, strategy=strat)
    before_json = res.to_json()

    policy = ReviewPolicy(
        min_confidence=0.85,
        require_calibrated_confidence=True,
        flag_missing_confidence=True,
        flag_missing_provenance=True,
    )
    plan = plan_review(res, policy=policy)
    assert res.to_json() == before_json  # Zero mutation
    assert plan.needs_review is True

    # Canonical order: result scope -> record scope -> sorted field names
    scopes = [(item.record_id, item.field_path) for item in plan.items]
    assert scopes[0] == (None, None)
    assert scopes[1] == ("record-001", None)
    field_scopes = [fp for rid, fp in scopes[2:] if rid == "record-001"]
    assert field_scopes == sorted(field_scopes)

    by_scope = {(item.record_id, item.field_path): item for item in plan.items}
    assert ReviewReason.PARTIAL_OR_FAILED_STATUS in by_scope[(None, None)].reasons
    assert ReviewReason.PARTIAL_OR_FAILED_STATUS in by_scope[("record-001", None)].reasons
    assert ReviewReason.RECORD_VALIDATION_FAILED in by_scope[("record-001", None)].reasons

    inv_item = by_scope[("record-001", "invoice_number")]
    assert set(inv_item.reasons) >= {
        ReviewReason.FIELD_ERROR,
        ReviewReason.LOW_CONFIDENCE,
        ReviewReason.UNCALIBRATED_CONFIDENCE,
        ReviewReason.UNVERIFIED_PROVENANCE,
        ReviewReason.CONFLICTING_PROVENANCE,
        ReviewReason.AMBIGUOUS_VALUE,
    }
    assert inv_item.provenance_summary is not None
    assert inv_item.provenance_summary.unverified_count == 1
    assert inv_item.provenance_summary.conflicting_count == 1

    sub_item = by_scope[("record-001", "subtotal")]
    assert ReviewReason.MISSING_CONFIDENCE in sub_item.reasons
    assert ReviewReason.MISSING_PROVENANCE in sub_item.reasons

    # Non-0-1 scale skipped min_confidence check (NOT LOW_CONFIDENCE) but caught UNCALIBRATED
    tax_item = by_scope[("record-001", "tax")]
    assert ReviewReason.LOW_CONFIDENCE not in tax_item.reasons
    assert ReviewReason.UNCALIBRATED_CONFIDENCE in tax_item.reasons

    # Determinism check
    assert plan_review(res, policy=policy).to_json() == plan.to_json()


def test_plan_review_flags_result_cardinality_and_rejects_bad_policy() -> None:
    schema = _invoice_schema(cardinality=ExtractionCardinality.SINGLE)
    two_rec_res = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(
            StrategyOutput(records=(_clean_record(), _clean_record()))
        ),
    )
    plan = plan_review(two_rec_res)
    assert plan.needs_review is True
    assert plan.items[0].record_id is None
    assert plan.items[0].field_path is None
    assert ReviewReason.RESULT_VALIDATION_FAILED in plan.items[0].reasons

    with pytest.raises(ConfigurationError):
        ReviewPolicy(min_confidence=1.2)
    with pytest.raises(ConfigurationError):
        ReviewPolicy(min_confidence=math.nan)
    with pytest.raises(ConfigurationError):
        ReviewPolicy(min_confidence=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ReviewPolicy(require_calibrated_confidence="yes")  # type: ignore[arg-type]


# =============================================================================
# User Story 2 & 3: Immutable `apply_review`, Re-Validation, Binding & Audit
# =============================================================================


def test_apply_review_correct_approve_mark_missing_bind_and_privacy_audit() -> None:
    schema = _invoice_schema()
    broken_rec = ProposedRecord(
        fields={
            **_clean_record().fields,
            "invoice_number": ProposedField(
                presence=FieldPresence.PRESENT,
                value="SECRET-RAW-OLD",
                confidence=0.95,
                ambiguous=True,
                evidence=(ProposedEvidence(quote="Invoice INV-100", relationship="direct"),),
                errors=(
                    DiagnosticIssue(
                        code="provenance_association_failed",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.PROVENANCE,
                        message="Transient provenance fault.",
                        field_path="invoice_number",
                    ),
                ),
            ),
            "total": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("999.00"),  # Breaks balance rule
                confidence=0.60,
                evidence=(ProposedEvidence(quote="Total: 110.00", relationship="direct"),),
            ),
            "issue_date": ProposedField(
                presence=FieldPresence.PRESENT,
                value="not-a-date",  # Type mismatch on optional field
                confidence=0.50,
            ),
        }
    )
    initial = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=(broken_rec,))),
    )
    assert initial.status == ProcessingStatus.PARTIAL
    assert initial.validation.status == ValidationStatus.INVALID
    initial_snapshot = initial.to_json()

    submission = ReviewSubmission(
        reviewer_id="ops-reviewer-42",
        field_decisions=(
            FieldReviewDecision(
                record_id="record-001",
                field_path="invoice_number",
                action=ReviewAction.CORRECT,
                value="INV-100",
                preserve_provenance=True,
                reason_note="Confirmed header invoice ID.",
            ),
            FieldReviewDecision(
                record_id="record-001",
                field_path="subtotal",
                action=ReviewAction.APPROVE,
            ),
            FieldReviewDecision(
                record_id="record-001",
                field_path="total",
                action=ReviewAction.CORRECT,
                value=Decimal("110.00"),
                reason_note="Corrected misread total.",
            ),
            FieldReviewDecision(
                record_id="record-001",
                field_path="issue_date",
                action=ReviewAction.MARK_MISSING,
                reason_note="Date not printed cleanly.",
            ),
        ),
    )

    applied = apply_review(initial, schema, submission)
    assert initial.to_json() == initial_snapshot  # Input strictly unmutated

    updated = applied.result
    assert updated.contract_version == "1.0"
    assert updated.status == ProcessingStatus.COMPLETED
    assert updated.validation.status == ValidationStatus.VALID
    assert ExtractionResult.from_json(updated.to_json()) == updated  # Lossless round-trip

    rec = updated.records[0]
    assert rec.status == ProcessingStatus.COMPLETED
    assert rec.validation.status == ValidationStatus.VALID
    # CORRECT + preserve_provenance=True kept existing source-bound quote, cleared errors/warnings
    f_inv = rec.fields["invoice_number"]
    assert f_inv.value == "INV-100"
    assert f_inv.errors == []
    assert f_inv.warnings == []
    assert len(f_inv.provenance) == 1
    assert f_inv.confidence == ConfidenceAssessment(
        value=1.0, scale="0-1", method="human-review", calibrated=False
    )
    # CORRECT + preserve_provenance=False cleared old provenance on changed value (no fabrication)
    f_tot = rec.fields["total"]
    assert f_tot.value == Decimal("110.00")
    assert f_tot.provenance == []
    # MARK_MISSING cleared value, confidence, provenance and re-validated cleanly as optional
    f_date = rec.fields["issue_date"]
    assert f_date.presence == FieldPresence.MISSING
    assert f_date.value is None
    assert f_date.confidence is None
    assert f_date.provenance == []
    assert f_date.validation.status == ValidationStatus.VALID

    # Seamless strict Pydantic v2 binding!
    bound = bind_records(updated, _InvoiceModel)
    assert len(bound) == 1
    assert bound[0] == _InvoiceModel(
        invoice_number="INV-100",
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("110.00"),
        issue_date=None,
    )

    # Privacy-safe ReviewAudit (default redact_values=True)
    audit = applied.audit
    assert audit.audit_version == REVIEW_AUDIT_VERSION
    assert audit.values_redacted is True
    assert audit.reviewer_id == "ops-reviewer-42"
    assert audit.total_decisions == 4
    assert audit.approved_count == 1
    assert audit.corrected_count == 2
    assert audit.marked_missing_count == 1
    assert audit.rejected_record_count == 0
    audit_json = audit.to_json()
    assert "SECRET-RAW-OLD" not in audit_json
    assert "999.00" not in audit_json
    assert all(e.before_value is None and e.after_value is None for e in audit.entries)

    # Opt-in unredacted audit preserves JSON-safe before/after values
    unredacted = apply_review(initial, schema, submission, redact_values=False)
    assert unredacted.audit.values_redacted is False
    inv_entry = unredacted.audit.entries[0]
    assert inv_entry.before_value == "SECRET-RAW-OLD"
    assert inv_entry.after_value == "INV-100"


def test_apply_review_never_coerces_invalid_human_edits_and_rejects_records_honestly() -> None:
    schema = _invoice_schema(cardinality=ExtractionCardinality.SINGLE)
    two_rec_res = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(
            StrategyOutput(records=(_clean_record(), _clean_record()))
        ),
    )
    assert two_rec_res.validation.status == ValidationStatus.INVALID

    # Rejecting duplicate 2nd record resolves SINGLE cardinality cleanly -> VALID
    drop_second = ReviewSubmission(
        record_decisions=(
            RecordReviewDecision(record_id="record-002", reason_note="Duplicate copy."),
        )
    )
    res_one = apply_review(two_rec_res, schema, drop_second)
    assert len(res_one.result.records) == 1
    assert res_one.result.validation.status == ValidationStatus.VALID
    assert res_one.audit.rejected_record_count == 1
    assert res_one.audit.entries[0].after_validation_status is None

    # Rejecting the last remaining record -> INVALID with missing_record
    drop_last = ReviewSubmission(record_decisions=(RecordReviewDecision(record_id="record-001"),))
    res_zero = apply_review(res_one.result, schema, drop_last)
    assert res_zero.result.records == []
    assert res_zero.result.validation.status == ValidationStatus.INVALID
    assert [e.code for e in res_zero.result.validation.errors] == ["missing_record"]

    # Non-coercion: string "110.00" supplied directly without Decimal -> type_mismatch INVALID
    bad_type_sub = ReviewSubmission(
        field_decisions=(
            FieldReviewDecision(
                record_id="record-001",
                field_path="total",
                action=ReviewAction.CORRECT,
                value="110.00",  # String, NOT Decimal!
            ),
        )
    )
    res_bad = apply_review(res_one.result, schema, bad_type_sub)
    assert res_bad.result.validation.status == ValidationStatus.INVALID
    assert res_bad.result.records[0].fields["total"].validation.status == ValidationStatus.INVALID
    assert res_bad.result.records[0].fields["total"].validation.errors[0].code == "type_mismatch"

    # Unresolved result-level errors keep status PARTIAL even after review
    partial_top = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(
            StrategyOutput(
                partial=True,
                errors=(
                    DiagnosticIssue(
                        code="upstream_partial",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.EXTRACTION,
                        message="Upstream partial error.",
                    ),
                ),
                records=(_clean_record(),),
            )
        ),
    )
    still_partial = apply_review(partial_top, schema, ReviewSubmission())
    assert still_partial.result.status == ProcessingStatus.PARTIAL
    assert still_partial.audit.total_decisions == 0


def test_review_submission_serialization_typed_restoration_and_validation(tmp_path: Path) -> None:
    raw_payload = {
        "submission_version": REVIEW_SUBMISSION_VERSION,
        "reviewer_id": "rev-1",
        "field_decisions": [
            {
                "record_id": "record-001",
                "field_path": "total",
                "action": "correct",
                "value": "110.00",
                "value_type": "decimal",
            },
            {
                "record_id": "record-001",
                "field_path": "issue_date",
                "action": "correct",
                "value": "2026-03-15",
                "value_type": "date",
            },
            {
                "record_id": "record-001",
                "field_path": "invoice_number",
                "action": "correct",
                "value": "42",  # No value_type -> remains str "42"
            },
        ],
        "record_decisions": [],
    }
    sub_file = tmp_path / "decisions.json"
    sub_file.write_text(json.dumps(raw_payload), encoding="utf-8")
    loaded = ReviewSubmission.from_file(sub_file)
    assert loaded.field_decisions[0].value == Decimal("110.00")
    assert loaded.field_decisions[1].value == date(2026, 3, 15)
    assert loaded.field_decisions[2].value == "42"

    # Round-trip save & reload
    out_file = tmp_path / "roundtrip.json"
    loaded.to_file(out_file)
    reloaded = ReviewSubmission.from_file(out_file)
    assert reloaded == loaded

    # Eager validation failures
    with pytest.raises(ConfigurationError):
        FieldReviewDecision(
            record_id="record-001",
            field_path="x",
            action=ReviewAction.MARK_MISSING,
            value="non-null",
        )
    with pytest.raises(ConfigurationError):
        FieldReviewDecision(
            record_id="record-001",
            field_path="x",
            action=ReviewAction.APPROVE,
            value="non-null",
        )
    with pytest.raises(ConfigurationError):
        FieldReviewDecision(
            record_id="record-001",
            field_path="x",
            action=ReviewAction.REJECT_RECORD,
        )
    with pytest.raises(ConfigurationError, match="Conflicting"):
        ReviewSubmission(
            field_decisions=(
                FieldReviewDecision(
                    record_id="record-001", field_path="x", action=ReviewAction.APPROVE
                ),
            ),
            record_decisions=(RecordReviewDecision(record_id="record-001"),),
        )
    with pytest.raises(ConfigurationError, match="Duplicate"):
        ReviewSubmission(
            field_decisions=(
                FieldReviewDecision(
                    record_id="record-001", field_path="x", action=ReviewAction.APPROVE
                ),
                FieldReviewDecision(
                    record_id="record-001", field_path="x", action=ReviewAction.MARK_MISSING
                ),
            )
        )
    with pytest.raises(ConfigurationError, match="Unexpected"):
        ReviewSubmission.from_dict({"submission_version": "1.0", "extra_key": True})

    huge = tmp_path / "huge.json"
    huge.write_bytes(b" " * (MAX_REVIEW_PAYLOAD_BYTES + 1))
    with pytest.raises(ConfigurationError, match="maximum size limit"):
        ReviewSubmission.from_file(huge)

    # Unknown record_id fails eagerly in apply_review
    clean_res = extract(
        _sample_doc(),
        _invoice_schema(),
        strategy=DeterministicTestStrategy(StrategyOutput(records=(_clean_record(),))),
    )
    with pytest.raises(ConfigurationError, match="Unknown record_id"):
        apply_review(
            clean_res,
            _invoice_schema(),
            ReviewSubmission(
                field_decisions=(
                    FieldReviewDecision(
                        record_id="record-999",
                        field_path="total",
                        action=ReviewAction.APPROVE,
                    ),
                )
            ),
        )


# =============================================================================
# User Story 4: Offline Thin CLI (`review-plan` & `apply-review`)
# =============================================================================


def test_cli_review_plan_and_apply_review_offline(tmp_path: Path) -> None:
    schema = _invoice_schema()
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(schema.to_json(indent=2), encoding="utf-8")

    # 1. Clean result -> review-plan exits 0
    clean_res = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=(_clean_record(),))),
    )
    clean_path = tmp_path / "clean.json"
    clean_path.write_text(clean_res.to_json(indent=2), encoding="utf-8")

    out0, err0 = io.StringIO(), io.StringIO()
    rc0 = run_cli(["review-plan", str(clean_path), "--pretty"], stdout=out0, stderr=err0)
    assert rc0 == 0, err0.getvalue()
    assert json.loads(out0.getvalue())["needs_review"] is False

    # Same result with --require-calibrated -> needs review (exit 2)
    out_cal, err_cal = io.StringIO(), io.StringIO()
    rc_cal = run_cli(
        ["review-plan", str(clean_path), "--require-calibrated", "--min-confidence", "none"],
        stdout=out_cal,
        stderr=err_cal,
    )
    assert rc_cal == 2, err_cal.getvalue()
    assert json.loads(out_cal.getvalue())["needs_review"] is True

    # 2. Invalid result -> apply-review fixes total via decisions.json -> exit 0 + audit JSON
    broken_rec = ProposedRecord(
        fields={
            **_clean_record().fields,
            "total": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("200.00"),
                confidence=0.60,
            ),
        }
    )
    broken_res = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=(broken_rec,))),
    )
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(broken_res.to_json(indent=2), encoding="utf-8")

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "submission_version": "1.0",
                "reviewer_id": "cli-reviewer",
                "field_decisions": [
                    {
                        "record_id": "record-001",
                        "field_path": "total",
                        "action": "correct",
                        "value": "110.00",
                        "value_type": "decimal",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    audit_path = tmp_path / "audit.json"
    out_apply, err_apply = io.StringIO(), io.StringIO()
    rc_apply = run_cli(
        [
            "apply-review",
            str(broken_path),
            "--schema",
            str(schema_path),
            "--decisions",
            str(decisions_path),
            "--audit-json",
            str(audit_path),
            "--pretty",
        ],
        stdout=out_apply,
        stderr=err_apply,
    )
    assert rc_apply == 0, err_apply.getvalue()
    updated = ExtractionResult.from_json(out_apply.getvalue())
    assert updated.validation.status == ValidationStatus.VALID
    assert updated.records[0].fields["total"].value == Decimal("110.00")
    audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_data["values_redacted"] is True
    assert audit_data["corrected_count"] == 1

    # 3. Error cases -> exit 1 & empty stdout
    for bad_argv in (
        ["review-plan", str(clean_path), "--min-confidence", "bad"],
        ["review-plan", str(tmp_path / "missing.json")],
        [
            "apply-review",
            str(broken_path),
            "--schema",
            str(schema_path),
            "--decisions",
            str(decisions_path),
            "--output",
            str(audit_path),
            "--audit-json",
            str(audit_path),
        ],
    ):
        o_err, e_err = io.StringIO(), io.StringIO()
        assert run_cli(bad_argv, stdout=o_err, stderr=e_err) == 1
        assert o_err.getvalue() == ""
        assert "Error:" in e_err.getvalue()


# =============================================================================
# Property-Based Tests (Hypothesis, 100 examples — SC-003)
# =============================================================================


@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(
    new_val=st.one_of(
        st.decimals(
            min_value=Decimal("0.00"),
            max_value=Decimal("500.00"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        st.text(min_size=1, max_size=20),
        st.booleans(),
    ),
    action=st.sampled_from([ReviewAction.APPROVE, ReviewAction.CORRECT, ReviewAction.MARK_MISSING]),
    preserve_prov=st.booleans(),
)
def test_property_apply_review_immutability_roundtrip_noncoercion_and_redaction(
    new_val: Any,
    action: ReviewAction,
    preserve_prov: bool,
) -> None:
    schema = _invoice_schema()
    secret_marker = "CONFIDENTIAL_TOKEN_987654"
    base_rec = ProposedRecord(
        fields={
            **_clean_record().fields,
            "invoice_number": ProposedField(
                presence=FieldPresence.PRESENT,
                value=secret_marker,
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Invoice INV-100", relationship="direct"),),
            ),
        }
    )
    initial = extract(
        _sample_doc(),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=(base_rec,))),
    )
    before_json = initial.to_json()

    decision = FieldReviewDecision(
        record_id="record-001",
        field_path="total",
        action=action,
        value=new_val if action == ReviewAction.CORRECT else None,
        preserve_provenance=preserve_prov,
    )
    applied = apply_review(initial, schema, ReviewSubmission(field_decisions=(decision,)))

    # 1. Input ExtractionResult is strictly immutable
    assert initial.to_json() == before_json
    # 2. Updated ExtractionResult satisfies Contract v1.0 lossless JSON round-trip
    assert ExtractionResult.from_json(applied.result.to_json()) == applied.result
    # 3. Default audit redacts raw values
    assert applied.audit.values_redacted is True
    assert secret_marker not in applied.audit.to_json()
    assert all(e.before_value is None and e.after_value is None for e in applied.audit.entries)
    # 4. Strict non-coercion on CORRECT: strings/booleans for DECIMAL total MUST be INVALID
    if action == ReviewAction.CORRECT and isinstance(new_val, (str, bool)):
        assert (
            applied.result.records[0].fields["total"].validation.status == ValidationStatus.INVALID
        )
        assert applied.result.validation.status == ValidationStatus.INVALID
    # 5. MARK_MISSING on required total MUST be INVALID with missing_required
    if action == ReviewAction.MARK_MISSING:
        f_tot = applied.result.records[0].fields["total"]
        assert f_tot.presence == FieldPresence.MISSING
        assert f_tot.value is None
        assert f_tot.validation.status == ValidationStatus.INVALID
        assert f_tot.validation.errors[0].code == "missing_required"
    # 6. No fabricated provenance on changed CORRECT values when preserve_provenance=False
    if action == ReviewAction.CORRECT and not preserve_prov and new_val != Decimal("110.00"):
        assert applied.result.records[0].fields["total"].provenance == []
    # 7. All remaining provenance entries stay bound to source document
    if applied.result.source is not None:
        doc_id = applied.result.source.document_id
        for rec in applied.result.records:
            for field in rec.fields.values():
                assert all(
                    isinstance(ev, ProvenanceEvidence) and ev.source_document_id == doc_id
                    for ev in field.provenance
                )
