"""Multi-metric golden scoring, multi-record alignment, and repeated-trial aggregation."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from evals.models import (
    CaseOutcome,
    ExpectedRecordSpec,
    FieldScore,
    GoldenCase,
    MatchMode,
    MetricSummary,
    RecordOrderMode,
    TrialDistribution,
    TrialStat,
)
from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import ExtractedField, ExtractedRecord, ExtractionResult

_WS_RE = re.compile(r"\s+")
_AMBIGUITY_WARNING_CODES = frozenset({"ambiguous_source_value", "evidence_value_conflict"})
_DISTRIBUTION_METRICS: tuple[str, ...] = (
    "field_exact_match_accuracy",
    "field_precision",
    "field_recall",
    "field_f1",
    "record_completeness",
    "record_level_accuracy",
    "hallucination_rate",
    "provenance_accuracy",
    "validation_pass_rate",
    "processing_failure_rate",
    "mean_latency_ms",
)


def _collapse_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _compare_values(actual: Any, expected: Any, mode: MatchMode) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected.keys()) != set(actual.keys()):
            return False
        return all(_compare_values(actual[k], expected[k], mode) for k in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return all(_compare_values(a, e, mode) for a, e in zip(actual, expected, strict=True))

    if mode == MatchMode.DECIMAL_EQUAL:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False

    if mode == MatchMode.DATE_ISO:
        a_str = actual.isoformat() if isinstance(actual, date) else str(actual)
        e_str = expected.isoformat() if isinstance(expected, date) else str(expected)
        return a_str == e_str

    if mode == MatchMode.CASEFOLD_WS:
        if isinstance(actual, str) and isinstance(expected, str):
            return _collapse_ws(actual).casefold() == _collapse_ws(expected).casefold()
        return bool(actual == expected)

    # Default: MatchMode.EXACT (strict equality; handles Decimal/date JSON string expectations)
    if isinstance(actual, Decimal) and isinstance(expected, str):
        try:
            return actual == Decimal(expected)
        except InvalidOperation:
            return False
    if isinstance(actual, date) and isinstance(expected, str):
        return actual.isoformat() == expected
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return bool(actual == expected)


def _verify_provenance(
    actual_field: ExtractedField,
    expected_evidence: list[str],
    expected_doc_id: str,
) -> bool:
    if not expected_evidence:
        return True
    for expected_snippet in expected_evidence:
        norm_expected = _collapse_ws(expected_snippet)
        found = any(
            ev.source_document_id == expected_doc_id
            and ev.relationship in {EvidenceRelationship.DIRECT, EvidenceRelationship.INFERRED}
            and norm_expected in _collapse_ws(ev.evidence)
            for ev in actual_field.provenance
        )
        if not found:
            return False
    return True


def _verify_ambiguity_handled(actual_field: ExtractedField | None) -> bool:
    if actual_field is None or actual_field.presence == FieldPresence.MISSING:
        return True
    if any(w.code in _AMBIGUITY_WARNING_CODES for w in actual_field.warnings):
        return True
    return any(
        ev.relationship == EvidenceRelationship.CONFLICTING for ev in actual_field.provenance
    )


def _record_overlap_score(
    actual: ExtractedRecord,
    expected: ExpectedRecordSpec,
    match_key_field: str | None,
) -> tuple[int, int]:
    key_hit = 0
    if match_key_field and match_key_field in expected.fields:
        exp_k = expected.fields[match_key_field]
        act_k = actual.fields.get(match_key_field)
        if (
            act_k is not None
            and act_k.presence == FieldPresence.PRESENT
            and exp_k.presence == FieldPresence.PRESENT
            and _compare_values(act_k.value, exp_k.value, exp_k.match_mode)
        ):
            key_hit = 1

    hits = 0
    for fname, exp_f in expected.fields.items():
        act_f = actual.fields.get(fname)
        if exp_f.presence == FieldPresence.MISSING:
            if act_f is None or act_f.presence == FieldPresence.MISSING:
                hits += 1
        elif (
            act_f is not None
            and act_f.presence == FieldPresence.PRESENT
            and _compare_values(act_f.value, exp_f.value, exp_f.match_mode)
        ):
            hits += 1
    return key_hit, hits


def _align_records(
    actual_records: list[ExtractedRecord],
    expected_records: list[ExpectedRecordSpec],
    order_mode: RecordOrderMode,
    match_key_field: str | None,
) -> list[tuple[ExpectedRecordSpec, ExtractedRecord | None]]:
    if order_mode == RecordOrderMode.ORDERED:
        return [
            (exp_rec, actual_records[idx] if idx < len(actual_records) else None)
            for idx, exp_rec in enumerate(expected_records)
        ]

    # Greedy optimal alignment by (match_key_hit, field_overlap_score)
    remaining_indices = set(range(len(actual_records)))
    aligned: list[tuple[ExpectedRecordSpec, ExtractedRecord | None]] = []
    for exp_rec in expected_records:
        best_idx: int | None = None
        best_score: tuple[int, int] = (-1, -1)
        for idx in sorted(remaining_indices):
            score = _record_overlap_score(actual_records[idx], exp_rec, match_key_field)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is not None:
            remaining_indices.remove(best_idx)
            aligned.append((exp_rec, actual_records[best_idx]))
        else:
            aligned.append((exp_rec, None))
    return aligned


def score_case(
    *,
    case: GoldenCase,
    result: ExtractionResult | None,
    trial_index: int,
    latency_ms: float,
    runtime_error: str | None = None,
) -> CaseOutcome:
    """Score one canonical ExtractionResult against its GoldenCase expectations."""
    if result is None:
        return CaseOutcome(
            case_id=case.case_id,
            category=case.category,
            format=case.format,
            challenge_profiles=list(case.challenge_profiles),
            trial_index=trial_index,
            expected_status=case.expected_status,
            actual_status=ProcessingStatus.FAILED,
            status_matched=False,
            expected_validation=case.expected_validation,
            actual_validation=ValidationStatus.NOT_VALIDATED,
            validation_matched=False,
            error_codes_matched=False,
            runtime_error=runtime_error or "missing_result",
            latency_ms=round(max(0.0, latency_ms), 3),
            field_scores=[],
            record_completeness=0.0,
            record_exact_accuracy=0.0,
            passed=False,
        )

    status_matched = result.status == case.expected_status
    validation_matched = result.validation.status == case.expected_validation

    actual_error_codes = {e.code for e in result.errors}
    for rec in result.records:
        actual_error_codes.update(e.code for e in rec.errors)
        actual_error_codes.update(e.code for e in rec.validation.errors)
    error_codes_matched = all(code in actual_error_codes for code in case.expected_error_codes)

    # Check adversarial forbidden extra fields were not silently accepted as valid
    adversarial_clean = True
    for forbidden in case.forbidden_extra_fields:
        for rec in result.records:
            ef = rec.fields.get(forbidden)
            if (
                ef is not None
                and ef.presence == FieldPresence.PRESENT
                and ef.validation.status.value == "valid"
            ):
                adversarial_clean = False

    aligned = _align_records(
        result.records,
        case.expected_records,
        case.record_order,
        case.match_key_field,
    )

    field_scores: list[FieldScore] = []
    rec_completeness_vals: list[float] = []
    exact_records = 0

    for exp_rec, act_rec in aligned:
        rec_expected_present = 0
        rec_correct_present = 0
        rec_all_ok = True

        if exp_rec.expected_validation is not None and (
            act_rec is None or act_rec.validation.status != exp_rec.expected_validation
        ):
            rec_all_ok = False

        for fname, exp_field in exp_rec.fields.items():
            act_field = act_rec.fields.get(fname) if act_rec is not None else None
            actual_presence = act_field.presence if act_field is not None else FieldPresence.MISSING
            prov_expected = bool(exp_field.expected_evidence)
            ambig_expected = exp_field.expect_ambiguous
            ambig_handled = _verify_ambiguity_handled(act_field) if ambig_expected else True

            if exp_field.presence == FieldPresence.MISSING:
                hallucinated = actual_presence == FieldPresence.PRESENT
                matched = not hallucinated
                prov_verified = True
            else:
                rec_expected_present += 1
                hallucinated = False
                if act_field is None or actual_presence == FieldPresence.MISSING:
                    matched = False
                    prov_verified = not prov_expected
                else:
                    matched = _compare_values(
                        act_field.value, exp_field.value, exp_field.match_mode
                    )
                    if matched:
                        rec_correct_present += 1
                    prov_verified = (
                        _verify_provenance(act_field, exp_field.expected_evidence, case.case_id)
                        if prov_expected
                        else True
                    )

            if (
                not matched
                or hallucinated
                or (prov_expected and not prov_verified)
                or (ambig_expected and not ambig_handled)
            ):
                rec_all_ok = False

            field_scores.append(
                FieldScore(
                    field_name=fname,
                    expected_presence=exp_field.presence,
                    actual_presence=actual_presence,
                    matched=matched,
                    hallucinated=hallucinated,
                    provenance_expected=prov_expected,
                    provenance_verified=prov_verified,
                    ambiguity_expected=ambig_expected,
                    ambiguity_handled=ambig_handled,
                )
            )

        if rec_expected_present > 0:
            rec_completeness_vals.append(rec_correct_present / rec_expected_present)
        if rec_all_ok:
            exact_records += 1

    # Penalize extra unexpected records in multi-record cases
    record_count_ok = len(result.records) == len(case.expected_records)
    rec_comp = (
        sum(rec_completeness_vals) / len(rec_completeness_vals) if rec_completeness_vals else 1.0
    )
    rec_exact = (
        exact_records / len(case.expected_records)
        if case.expected_records
        else (1.0 if not result.records else 0.0)
    )

    all_fields_ok = all(
        fs.matched
        and not fs.hallucinated
        and (not fs.provenance_expected or fs.provenance_verified)
        and (not fs.ambiguity_expected or fs.ambiguity_handled)
        for fs in field_scores
    )
    passed = (
        runtime_error is None
        and status_matched
        and validation_matched
        and error_codes_matched
        and adversarial_clean
        and record_count_ok
        and all_fields_ok
    )

    return CaseOutcome(
        case_id=case.case_id,
        category=case.category,
        format=case.format,
        challenge_profiles=list(case.challenge_profiles),
        trial_index=trial_index,
        expected_status=case.expected_status,
        actual_status=result.status,
        status_matched=status_matched,
        expected_validation=case.expected_validation,
        actual_validation=result.validation.status,
        validation_matched=validation_matched,
        error_codes_matched=error_codes_matched,
        runtime_error=runtime_error,
        latency_ms=round(max(0.0, latency_ms), 3),
        field_scores=field_scores,
        record_completeness=round(rec_comp, 4),
        record_exact_accuracy=round(rec_exact, 4),
        passed=passed,
    )


def compute_metric_summary(outcomes: list[CaseOutcome]) -> MetricSummary:
    """Aggregate zero-denominator-safe metrics across a list of CaseOutcomes."""
    total_cases = len(outcomes)
    total_fields = 0
    expected_present = 0
    expected_missing = 0
    hallucination_count = 0
    expected_provenance = 0
    verified_provenance = 0
    exact_matches = 0
    tp = 0
    fp = 0
    fn = 0
    validation_matches = 0
    runtime_failures = 0

    for outcome in outcomes:
        if outcome.validation_matched:
            validation_matches += 1
        if outcome.runtime_error is not None:
            runtime_failures += 1
        for fs in outcome.field_scores:
            total_fields += 1
            if fs.expected_presence == FieldPresence.MISSING:
                expected_missing += 1
                if fs.hallucinated:
                    hallucination_count += 1
                    fp += 1
                else:
                    exact_matches += 1
            else:
                expected_present += 1
                if fs.provenance_expected:
                    expected_provenance += 1
                    if fs.provenance_verified:
                        verified_provenance += 1
                if fs.actual_presence == FieldPresence.MISSING:
                    fn += 1
                elif fs.matched:
                    tp += 1
                    exact_matches += 1
                else:
                    fp += 1
                    fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    exact_acc = exact_matches / total_fields if total_fields > 0 else 1.0
    false_ext_rate = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    missing_rate = fn / (tp + fn) if (tp + fn) > 0 else 0.0
    halluc_rate = hallucination_count / expected_missing if expected_missing > 0 else 0.0
    prov_acc = verified_provenance / expected_provenance if expected_provenance > 0 else 1.0
    val_rate = validation_matches / total_cases if total_cases > 0 else 1.0
    fail_rate = runtime_failures / total_cases if total_cases > 0 else 0.0
    rec_comp = (
        sum(o.record_completeness for o in outcomes) / total_cases if total_cases > 0 else 1.0
    )
    rec_acc = (
        sum(o.record_exact_accuracy for o in outcomes) / total_cases if total_cases > 0 else 1.0
    )
    mean_lat = sum(o.latency_ms for o in outcomes) / total_cases if total_cases > 0 else 0.0
    max_lat = max((o.latency_ms for o in outcomes), default=0.0)

    return MetricSummary(
        total_cases=total_cases,
        total_fields=total_fields,
        expected_present_fields=expected_present,
        expected_missing_fields=expected_missing,
        hallucination_count=hallucination_count,
        expected_provenance_fields=expected_provenance,
        field_exact_match_accuracy=round(exact_acc, 4),
        field_precision=round(precision, 4),
        field_recall=round(recall, 4),
        field_f1=round(f1, 4),
        record_completeness=round(rec_comp, 4),
        record_level_accuracy=round(rec_acc, 4),
        false_extraction_rate=round(false_ext_rate, 4),
        missing_field_rate=round(missing_rate, 4),
        hallucination_rate=round(halluc_rate, 4),
        provenance_accuracy=round(prov_acc, 4),
        validation_pass_rate=round(val_rate, 4),
        processing_failure_rate=round(fail_rate, 4),
        mean_latency_ms=round(mean_lat, 3),
        max_latency_ms=round(max_lat, 3),
    )


def compute_breakdowns(
    outcomes: list[CaseOutcome],
) -> tuple[dict[str, MetricSummary], dict[str, MetricSummary]]:
    """Compute per-category and per-challenge-profile MetricSummary maps."""
    by_cat_groups: dict[str, list[CaseOutcome]] = {}
    by_prof_groups: dict[str, list[CaseOutcome]] = {}
    for outcome in outcomes:
        by_cat_groups.setdefault(outcome.category.value, []).append(outcome)
        for prof in outcome.challenge_profiles:
            by_prof_groups.setdefault(prof.value, []).append(outcome)

    by_category = {
        cat: compute_metric_summary(items) for cat, items in sorted(by_cat_groups.items())
    }
    by_profile = {
        prof: compute_metric_summary(items) for prof, items in sorted(by_prof_groups.items())
    }
    return by_category, by_profile


def compute_trial_distribution(trial_summaries: list[MetricSummary]) -> TrialDistribution:
    """Compute mean, min, and max across trial summaries for every core metric (FR-014)."""
    dist: TrialDistribution = {}
    for metric_name in _DISTRIBUTION_METRICS:
        vals = [float(getattr(summary, metric_name)) for summary in trial_summaries]
        dist[metric_name] = TrialStat(
            mean=round(sum(vals) / len(vals), 4),
            min=round(min(vals), 4),
            max=round(max(vals), 4),
        )
    return dist
