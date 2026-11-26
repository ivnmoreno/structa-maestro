"""Offline example: end-to-end human review & correction workflow with Contract v1.0 & binding.

Demonstrates:
1. Extracting an invoice with an arithmetic cross-field mismatch, low calibrated confidence,
   and an unverified quote.
2. Triaging automatically across all five quality dimensions via `plan_review(...)`.
3. Applying structured human decisions (`CORRECT` + `APPROVE`) via `apply_review(...)` with
   mandatory deterministic single-field & cross-field re-validation and zero input mutation.
4. Emitting a privacy-safe `ReviewAudit` (raw values redacted by default) and strictly binding
   the reviewed Contract v1.0 result into a typed Pydantic v2 model via `bind_records(...)`.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from structa_maestro import (
    ArithmeticRule,
    ArithmeticTerm,
    CalibrationMethod,
    CalibrationSample,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionSchema,
    FieldPresence,
    FieldReviewDecision,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    ReviewAction,
    ReviewPolicy,
    ReviewSubmission,
    RuleSeverity,
    StrategyOutput,
    apply_review,
    bind_records,
    extract,
    fit_calibrator,
    plan_review,
)


class Invoice(BaseModel):
    invoice_number: str = Field(min_length=1)
    subtotal: Decimal = Field(ge=0)
    tax: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)


def main() -> None:
    # 1. Fit a small offline calibrator so confidence triage uses calibrated probabilities
    cal_samples = [
        CalibrationSample(raw_score=round(0.2 + 0.75 * (i / 23), 4), is_correct=(i % 3 != 0))
        for i in range(24)
    ]
    calibrator = fit_calibrator(cal_samples, method=CalibrationMethod.ISOTONIC)

    # 2. Define schema with exact Decimal arithmetic cross-field balance rule
    schema = ExtractionSchema.from_pydantic(
        Invoice,
        schema_id="invoice-review-demo",
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

    doc = DocumentInput.from_text(
        "Invoice INV-2026-777\nSubtotal: 100.00\nTax: 15.00\nTotal: 115.00\n",
        filename="invoice_777.txt",
        document_id="doc-777",
    )

    # Strategy misreads total as 105.00 (violates subtotal + tax == total) with unverified quote
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value="INV-2026-777",
                            confidence=0.95,
                            evidence=(
                                ProposedEvidence(
                                    quote="Invoice INV-2026-777", relationship="direct"
                                ),
                            ),
                        ),
                        "subtotal": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("100.00"),
                            confidence=0.94,
                            evidence=(
                                ProposedEvidence(quote="Subtotal: 100.00", relationship="direct"),
                            ),
                        ),
                        "tax": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("15.00"),
                            confidence=0.92,
                            evidence=(ProposedEvidence(quote="Tax: 15.00", relationship="direct"),),
                        ),
                        "total": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=Decimal("105.00"),
                            confidence=0.55,
                            evidence=(
                                ProposedEvidence(quote="Total: 105.00", relationship="direct"),
                            ),
                        ),
                    }
                ),
            )
        )
    )

    initial = extract(doc, schema, strategy=strategy, calibrator=calibrator)
    print(f"Initial validation: {initial.validation.status.value}")

    # 3. Deterministic triage across all five quality dimensions
    policy = ReviewPolicy(min_confidence=0.80, require_calibrated_confidence=True)
    review_plan = plan_review(initial, policy=policy)
    print(f"Needs review: {review_plan.needs_review} ({review_plan.total_items} items)")
    for item in review_plan.items:
        print(
            f"  scope=({item.record_id}, {item.field_path}) "
            f"reasons={[r.value for r in item.reasons]}"
        )

    # 4. Submit human review corrections & approvals
    submission = ReviewSubmission(
        reviewer_id="reviewer-ops-01",
        field_decisions=(
            FieldReviewDecision(
                record_id="record-001",
                field_path="invoice_number",
                action=ReviewAction.APPROVE,
                reason_note="Verified against header.",
            ),
            FieldReviewDecision(
                record_id="record-001",
                field_path="total",
                action=ReviewAction.CORRECT,
                value=Decimal("115.00"),
                reason_note="Corrected OCR misread from 105.00 to 115.00.",
            ),
        ),
    )

    applied = apply_review(initial, schema, submission)
    print(
        f"After review: status={applied.result.status.value}, "
        f"validation={applied.result.validation.status.value} "
        f"(initial still {initial.validation.status.value})"
    )
    print("Privacy-safe ReviewAudit JSON:")
    print(applied.audit.to_json(indent=2))

    # 5. Bind reviewed valid canonical result into strict typed Pydantic model
    bound = bind_records(applied.result, Invoice)
    print(f"Bound typed model: {bound[0]!r}")


if __name__ == "__main__":
    main()
