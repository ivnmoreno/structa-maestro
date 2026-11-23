"""Baseline regression gating and side-by-side differential strategy comparison."""

from __future__ import annotations

from evals.models import (
    CaseDelta,
    CaseOutcome,
    ComparisonReport,
    DifferentialReport,
    EvaluationReport,
    RegressionThresholds,
)

_TRACKED_METRICS: tuple[str, ...] = (
    "field_exact_match_accuracy",
    "field_precision",
    "field_recall",
    "field_f1",
    "record_completeness",
    "record_level_accuracy",
    "false_extraction_rate",
    "missing_field_rate",
    "hallucination_rate",
    "provenance_accuracy",
    "validation_pass_rate",
    "processing_failure_rate",
)


def _index_first_trial_cases(report: EvaluationReport) -> dict[str, CaseOutcome]:
    indexed: dict[str, CaseOutcome] = {}
    for outcome in report.cases:
        if outcome.case_id not in indexed or outcome.trial_index == 1:
            indexed[outcome.case_id] = outcome
    return indexed


def _compute_deltas(candidate: EvaluationReport, baseline: EvaluationReport) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for metric in _TRACKED_METRICS:
        cand_val = float(getattr(candidate.summary, metric))
        base_val = float(getattr(baseline.summary, metric))
        deltas[metric] = round(cand_val - base_val, 4)
    return deltas


def _describe_case_differences(
    base_case: CaseOutcome,
    cand_case: CaseOutcome,
) -> tuple[list[str], list[str]]:
    """Return (regression_details, improvement_details) comparing two outcomes for one case."""
    regressed: list[str] = []
    improved: list[str] = []

    if base_case.status_matched and not cand_case.status_matched:
        regressed.append(
            f"status regressed: {base_case.actual_status.value} -> {cand_case.actual_status.value}"
        )
    elif not base_case.status_matched and cand_case.status_matched:
        improved.append(f"status fixed: {cand_case.actual_status.value}")

    if base_case.validation_matched and not cand_case.validation_matched:
        regressed.append(
            f"validation regressed: {base_case.actual_validation.value} -> "
            f"{cand_case.actual_validation.value}"
        )
    elif not base_case.validation_matched and cand_case.validation_matched:
        improved.append(f"validation fixed: {cand_case.actual_validation.value}")

    base_fields = {f.field_name: f for f in base_case.field_scores}
    cand_fields = {f.field_name: f for f in cand_case.field_scores}
    for fname in sorted(set(base_fields) | set(cand_fields)):
        bf = base_fields.get(fname)
        cf = cand_fields.get(fname)
        if bf is None or cf is None:
            continue
        if not bf.hallucinated and cf.hallucinated:
            regressed.append(f"field '{fname}': newly hallucinated")
        elif bf.hallucinated and not cf.hallucinated:
            improved.append(f"field '{fname}': hallucination eliminated")

        if bf.matched and not cf.matched:
            regressed.append(f"field '{fname}': value mismatch")
        elif not bf.matched and cf.matched:
            improved.append(f"field '{fname}': value now matched")

        if bf.provenance_expected and bf.provenance_verified and not cf.provenance_verified:
            regressed.append(f"field '{fname}': provenance broken")
        elif bf.provenance_expected and not bf.provenance_verified and cf.provenance_verified:
            improved.append(f"field '{fname}': provenance fixed")

    return regressed, improved


def compare_against_baseline(
    candidate: EvaluationReport,
    baseline: EvaluationReport,
    thresholds: RegressionThresholds | None = None,
) -> ComparisonReport:
    """Compare candidate report against a stored baseline and enforce regression thresholds."""
    active_thresholds = thresholds or RegressionThresholds()
    version_match = candidate.dataset_version == baseline.dataset_version
    deltas = _compute_deltas(candidate, baseline)
    failure_reasons: list[str] = []

    if not version_match and not active_thresholds.allow_dataset_version_mismatch:
        failure_reasons.append(
            f"Dataset version mismatch: candidate={candidate.dataset_version!r} vs "
            f"baseline={baseline.dataset_version!r}."
        )

    # Strict anti-hallucination gate (FR-013): reject hallucination increases even if recall rises
    if deltas["hallucination_rate"] > active_thresholds.max_hallucination_increase:
        failure_reasons.append(
            f"Hallucination rate increased by +{deltas['hallucination_rate']:.4f} "
            f"(max allowed: +{active_thresholds.max_hallucination_increase:.4f})."
        )

    drop_checks = (
        ("field_exact_match_accuracy", active_thresholds.max_exact_match_drop, "Exact-match"),
        ("field_f1", active_thresholds.max_f1_drop, "Field F1"),
        ("provenance_accuracy", active_thresholds.max_provenance_drop, "Provenance accuracy"),
        ("validation_pass_rate", active_thresholds.max_validation_drop, "Validation pass rate"),
    )
    for metric_key, max_drop, label in drop_checks:
        drop = round(-deltas[metric_key], 4)
        if drop > max_drop:
            failure_reasons.append(
                f"{label} dropped by -{drop:.4f} (max allowed drop: {max_drop:.4f})."
            )

    if deltas["processing_failure_rate"] > active_thresholds.max_failure_rate_increase:
        failure_reasons.append(
            f"Processing failure rate increased by +{deltas['processing_failure_rate']:.4f} "
            f"(max allowed: +{active_thresholds.max_failure_rate_increase:.4f})."
        )

    base_cases = _index_first_trial_cases(baseline)
    cand_cases = _index_first_trial_cases(candidate)
    regressed_cases: list[CaseDelta] = []
    improved_cases: list[CaseDelta] = []

    for cid in sorted(set(base_cases) & set(cand_cases)):
        reg_details, imp_details = _describe_case_differences(base_cases[cid], cand_cases[cid])
        if reg_details:
            regressed_cases.append(
                CaseDelta(
                    case_id=cid,
                    category=cand_cases[cid].category.value,
                    direction="regressed",
                    details=reg_details,
                )
            )
        if imp_details:
            improved_cases.append(
                CaseDelta(
                    case_id=cid,
                    category=cand_cases[cid].category.value,
                    direction="improved",
                    details=imp_details,
                )
            )

    return ComparisonReport(
        baseline_dataset_version=baseline.dataset_version,
        candidate_dataset_version=candidate.dataset_version,
        dataset_version_match=version_match,
        baseline_strategy=baseline.strategy_name,
        candidate_strategy=candidate.strategy_name,
        thresholds=active_thresholds,
        metric_deltas=deltas,
        regressed_cases=regressed_cases,
        improved_cases=improved_cases,
        passed=len(failure_reasons) == 0,
        failure_reasons=failure_reasons,
    )


def diff_strategies(
    report_a: EvaluationReport,
    report_b: EvaluationReport,
) -> DifferentialReport:
    """Compare two evaluation reports side-by-side and list all case/field divergences (FR-015)."""
    version_match = report_a.dataset_version == report_b.dataset_version
    deltas = _compute_deltas(report_b, report_a)
    warnings: list[str] = []
    if not version_match:
        warnings.append(
            f"Dataset version mismatch: report A={report_a.dataset_version!r} vs "
            f"report B={report_b.dataset_version!r}; deltas may not be directly comparable."
        )

    cases_a = _index_first_trial_cases(report_a)
    cases_b = _index_first_trial_cases(report_b)
    divergent: list[CaseDelta] = []

    for cid in sorted(set(cases_a) | set(cases_b)):
        ca = cases_a.get(cid)
        cb = cases_b.get(cid)
        if ca is None and cb is not None:
            divergent.append(
                CaseDelta(
                    case_id=cid,
                    category=cb.category.value,
                    direction="diverged",
                    details=["present only in report B"],
                )
            )
            continue
        if cb is None and ca is not None:
            divergent.append(
                CaseDelta(
                    case_id=cid,
                    category=ca.category.value,
                    direction="diverged",
                    details=["present only in report A"],
                )
            )
            continue
        if ca is None or cb is None:
            continue

        details: list[str] = []
        if ca.actual_status != cb.actual_status:
            details.append(f"status: A={ca.actual_status.value} vs B={cb.actual_status.value}")
        if ca.actual_validation != cb.actual_validation:
            details.append(
                f"validation: A={ca.actual_validation.value} vs B={cb.actual_validation.value}"
            )

        fa = {f.field_name: f for f in ca.field_scores}
        fb = {f.field_name: f for f in cb.field_scores}
        for fname in sorted(set(fa) | set(fb)):
            left = fa.get(fname)
            right = fb.get(fname)
            if left is None or right is None:
                details.append(f"field '{fname}': evaluated in only one report")
                continue
            if (
                left.matched != right.matched
                or left.hallucinated != right.hallucinated
                or left.provenance_verified != right.provenance_verified
            ):
                details.append(
                    f"field '{fname}': A(matched={left.matched}, halluc={left.hallucinated}, "
                    f"prov={left.provenance_verified}) vs "
                    f"B(matched={right.matched}, halluc={right.hallucinated}, "
                    f"prov={right.provenance_verified})"
                )

        if details:
            divergent.append(
                CaseDelta(
                    case_id=cid,
                    category=ca.category.value,
                    direction="diverged",
                    details=details,
                )
            )

    return DifferentialReport(
        dataset_version_a=report_a.dataset_version,
        dataset_version_b=report_b.dataset_version,
        dataset_version_match=version_match,
        strategy_a=report_a.strategy_name,
        strategy_b=report_b.strategy_name,
        metric_deltas=deltas,
        divergent_cases=divergent,
        warnings=warnings,
    )
