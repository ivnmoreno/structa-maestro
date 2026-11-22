"""Multi-metric golden dataset evaluator (docs/TESTING.md Sections 5.6 & 18)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from structa_maestro import (
    EvidenceRelationship,
    ExtractionResult,
    FieldPresence,
)


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Structured multi-metric report for a golden dataset evaluation run."""

    dataset_version: str
    strategy_name: str
    executed_at: str
    total_cases: int
    field_exact_match_accuracy: float
    field_precision: float
    field_recall: float
    field_f1: float
    record_completeness: float
    provenance_accuracy: float
    validation_pass_rate: float
    hallucination_rate: float


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, Decimal) and isinstance(expected, str):
        return actual == Decimal(expected)
    if isinstance(actual, date) and isinstance(expected, str):
        return actual.isoformat() == expected
    return bool(actual == expected)


def evaluate_golden_results(
    *,
    dataset_version: str,
    strategy_name: str,
    cases: list[dict[str, Any]],
    results: list[ExtractionResult],
) -> EvaluationReport:
    """Compute extraction quality, provenance integrity, validation, and hallucination metrics."""
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    hallucinations = 0
    expected_missing_total = 0
    exact_matches = 0
    total_fields = 0
    prov_verified = 0
    prov_expected = 0
    validation_matches = 0
    record_completeness_scores: list[float] = []

    for case, res in zip(cases, results, strict=True):
        if res.validation.status.value == case["expected_validation"]:
            validation_matches += 1

        exp_records = case["expected_records"]
        for rec_idx, exp_rec in enumerate(exp_records):
            actual_rec = res.records[rec_idx] if rec_idx < len(res.records) else None
            exp_fields = exp_rec["fields"]
            rec_expected_present = 0
            rec_actual_present_correct = 0

            for fname, f_exp in exp_fields.items():
                total_fields += 1
                exp_presence = f_exp["presence"]
                exp_val = f_exp["value"]
                actual_field = actual_rec.fields.get(fname) if actual_rec is not None else None

                if exp_presence == "missing":
                    expected_missing_total += 1
                    if actual_field is None or actual_field.presence == FieldPresence.MISSING:
                        exact_matches += 1
                    else:
                        # Fabricated value when information was missing!
                        false_positives += 1
                        hallucinations += 1
                else:
                    rec_expected_present += 1
                    if f_exp.get("evidence"):
                        prov_expected += 1
                    if actual_field is None or actual_field.presence == FieldPresence.MISSING:
                        false_negatives += 1
                    elif _values_equal(actual_field.value, exp_val):
                        true_positives += 1
                        exact_matches += 1
                        rec_actual_present_correct += 1
                        if f_exp.get("evidence") and any(
                            ev.relationship == EvidenceRelationship.DIRECT
                            and f_exp["evidence"] in ev.evidence
                            for ev in actual_field.provenance
                        ):
                            prov_verified += 1
                    else:
                        false_positives += 1
                        false_negatives += 1

            if rec_expected_present > 0:
                record_completeness_scores.append(rec_actual_present_correct / rec_expected_present)

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0
        else 1.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0
        else 1.0
    )
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    exact_acc = exact_matches / total_fields if total_fields > 0 else 1.0
    rec_comp = (
        sum(record_completeness_scores) / len(record_completeness_scores)
        if record_completeness_scores
        else 1.0
    )
    prov_acc = prov_verified / prov_expected if prov_expected > 0 else 1.0
    val_rate = validation_matches / len(cases) if cases else 1.0
    halluc_rate = hallucinations / expected_missing_total if expected_missing_total > 0 else 0.0

    return EvaluationReport(
        dataset_version=dataset_version,
        strategy_name=strategy_name,
        executed_at=datetime.now(UTC).isoformat(),
        total_cases=len(cases),
        field_exact_match_accuracy=round(exact_acc, 4),
        field_precision=round(precision, 4),
        field_recall=round(recall, 4),
        field_f1=round(f1, 4),
        record_completeness=round(rec_comp, 4),
        provenance_accuracy=round(prov_acc, 4),
        validation_pass_rate=round(val_rate, 4),
        hallucination_rate=round(halluc_rate, 4),
    )
