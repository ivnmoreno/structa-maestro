"""Scoring, hallucination, repeated-trial, baseline gate, and strategy diff tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from evals.comparison import compare_against_baseline, diff_strategies
from evals.dataset import load_dataset
from evals.models import (
    CaseOutcome,
    ChallengeProfile,
    DocumentCategory,
    EvaluationFilter,
    EvaluationReport,
    GoldenDatasetManifest,
    RegressionThresholds,
)
from evals.reference_strategy import ReferenceDeterministicStrategy
from evals.reporting import (
    load_report,
    render_comparison_markdown,
    render_differential_markdown,
    render_report_markdown,
    save_report,
)
from evals.runner import EvaluationRunner
from evals.scoring import compute_metric_summary
from structa_maestro import (
    DeterministicTestStrategy,
    DocumentFormat,
    FieldPresence,
    PreparedDocument,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
)
from structa_maestro.schema.models import ExtractionSchema

EVAL_REPORT_SCHEMA_PATH = (
    Path(__file__).parent.parent / "fixtures" / "schemas" / "evaluation-report.v1.0.schema.json"
)


@pytest.mark.evaluation
def test_zero_denominator_metric_summary_is_finite_and_safe() -> None:
    # 0 outcomes -> finite defaults
    empty = compute_metric_summary([])
    assert empty.total_cases == 0
    assert empty.hallucination_rate == 0.0
    assert empty.field_exact_match_accuracy == 1.0
    assert empty.field_precision == 1.0
    assert empty.field_recall == 1.0
    assert empty.field_f1 == 1.0

    # Malformed-only outcome (0 expected present, 0 expected missing)
    malformed_outcome = CaseOutcome(
        case_id="m-1",
        category=DocumentCategory.INVOICES,
        format=DocumentFormat.PDF,
        challenge_profiles=[ChallengeProfile.MALFORMED_DOCUMENT],
        trial_index=1,
        expected_status=ProcessingStatus.FAILED,
        actual_status=ProcessingStatus.FAILED,
        status_matched=True,
        expected_validation=ValidationStatus.NOT_VALIDATED,
        actual_validation=ValidationStatus.NOT_VALIDATED,
        validation_matched=True,
        error_codes_matched=True,
        runtime_error=None,
        latency_ms=1.5,
        field_scores=[],
        record_completeness=1.0,
        record_exact_accuracy=1.0,
        passed=True,
    )
    summary = compute_metric_summary([malformed_outcome])
    assert summary.expected_missing_fields == 0
    assert summary.hallucination_rate == 0.0
    assert summary.provenance_accuracy == 1.0
    assert summary.validation_pass_rate == 1.0


@pytest.mark.evaluation
def test_seeded_regressions_hallucination_drop_broken_provenance_and_version_mismatch(
    tmp_path: Path,
) -> None:
    manifest = load_dataset(validate_full_corpus=True)
    subset = EvaluationFilter(case_ids=["inv-txt-001", "inv-txt-004"])
    runner = EvaluationRunner(manifest)

    baseline = runner.run(strategy=ReferenceDeterministicStrategy(), filters=subset)

    # Validate report JSON Schema round-trip
    schema: dict[str, Any] = json.loads(EVAL_REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    saved_path = save_report(baseline, tmp_path / "baseline.json")
    restored = load_report(saved_path)
    jsonschema.validate(instance=restored.model_dump(mode="json"), schema=schema)
    assert restored == baseline
    assert "# Golden Evaluation Report" in render_report_markdown(restored)

    # 1. Seeded hallucination on expected-missing field ('notes' & 'currency') MUST fail gate
    ref = ReferenceDeterministicStrategy()

    def _hallucinating_handler(doc: PreparedDocument, sch: ExtractionSchema) -> StrategyOutput:
        base_out = ref.extract_raw(doc, sch)
        rec = base_out.records[0]
        patched = dict(rec.fields)
        patched["notes"] = ProposedField(
            presence=FieldPresence.PRESENT,
            value="HALLUCINATED NOTE",
            confidence=0.95,
        )
        return StrategyOutput(records=(ProposedRecord(fields=patched),))

    halluc_report = runner.run(
        strategy=DeterministicTestStrategy(_hallucinating_handler, name="hallucinator"),
        filters=subset,
    )
    assert halluc_report.summary.hallucination_count >= 1
    assert halluc_report.summary.hallucination_rate > 0.0
    cmp_halluc = compare_against_baseline(halluc_report, baseline)
    assert cmp_halluc.passed is False
    assert any("Hallucination rate increased" in r for r in cmp_halluc.failure_reasons)
    assert any("newly hallucinated" in "; ".join(c.details) for c in cmp_halluc.regressed_cases)
    assert "Baseline Regression Gate: FAIL" in render_comparison_markdown(cmp_halluc)

    # 2. Seeded broken provenance MUST fail gate with field attribution
    def _bad_prov_handler(doc: PreparedDocument, sch: ExtractionSchema) -> StrategyOutput:
        base_out = ref.extract_raw(doc, sch)
        rec = base_out.records[0]
        patched = dict(rec.fields)
        inv = patched["invoice_number"]
        patched["invoice_number"] = ProposedField(
            presence=inv.presence,
            value=inv.value,
            confidence=inv.confidence,
            evidence=(ProposedEvidence(quote="NONEXISTENT QUOTE"),),
        )
        return StrategyOutput(records=(ProposedRecord(fields=patched),))

    bad_prov_report = runner.run(
        strategy=DeterministicTestStrategy(_bad_prov_handler, name="bad-prov"),
        filters=subset,
    )
    cmp_prov = compare_against_baseline(bad_prov_report, baseline)
    assert cmp_prov.passed is False
    assert any("Provenance accuracy dropped" in r for r in cmp_prov.failure_reasons)
    assert any("provenance broken" in "; ".join(c.details) for c in cmp_prov.regressed_cases)

    # 3. Dataset version mismatch MUST fail gate by default
    stale_baseline = EvaluationReport.model_validate(
        {**baseline.model_dump(mode="json"), "dataset_version": "1.0.0"}
    )
    cmp_ver = compare_against_baseline(baseline, stale_baseline)
    assert cmp_ver.passed is False
    assert cmp_ver.dataset_version_match is False
    assert any("Dataset version mismatch" in r for r in cmp_ver.failure_reasons)

    # Allowed only when explicitly overridden
    cmp_ver_allowed = compare_against_baseline(
        baseline, stale_baseline, RegressionThresholds(allow_dataset_version_mismatch=True)
    )
    assert cmp_ver_allowed.passed is True


@pytest.mark.evaluation
def test_repeated_trials_distribution_and_differential_comparison() -> None:
    manifest = load_dataset(validate_full_corpus=True)
    subset = EvaluationFilter(case_ids=["inv-txt-001"])
    runner = EvaluationRunner(manifest)
    ref = ReferenceDeterministicStrategy()

    # Varying strategy across 3 trials: trial 1 perfect, trial 2 drops vendor_name, trial 3 perfect
    call_count = 0

    def _varying_handler(doc: PreparedDocument, sch: ExtractionSchema) -> StrategyOutput:
        nonlocal call_count
        call_count += 1
        base_out = ref.extract_raw(doc, sch)
        if call_count == 2:
            rec = base_out.records[0]
            patched = dict(rec.fields)
            patched["vendor_name"] = ProposedField(presence=FieldPresence.MISSING, value=None)
            return StrategyOutput(records=(ProposedRecord(fields=patched),))
        return base_out

    multi_report = runner.run(
        strategy=DeterministicTestStrategy(_varying_handler, name="varying-llm-sim"),
        trials=3,
        filters=subset,
    )
    assert multi_report.trials == 3
    assert len(multi_report.trial_summaries) == 3
    assert multi_report.trial_distribution is not None
    recall_stat = multi_report.trial_distribution["field_recall"]
    assert recall_stat.max == 1.0
    assert recall_stat.min < 1.0
    assert recall_stat.min < recall_stat.mean < recall_stat.max
    assert "## Repeated Trial Distribution" in render_report_markdown(multi_report)

    # Differential comparison between perfect ref and a degraded strategy
    report_a = runner.run(strategy=ref, filters=subset)
    report_b = runner.run(
        strategy=DeterministicTestStrategy(
            lambda _d, _s: StrategyOutput(records=(ProposedRecord(fields={}),)),
            name="empty-extractor",
        ),
        filters=subset,
    )
    diff = diff_strategies(report_a, report_b)
    assert diff.dataset_version_match is True
    assert diff.metric_deltas["field_recall"] < 0.0
    assert len(diff.divergent_cases) == 1
    assert diff.divergent_cases[0].case_id == "inv-txt-001"
    assert "## Differential Strategy Comparison" in render_differential_markdown(diff)

    # Differential comparison flags dataset_version mismatch with a warning
    mismatched_b = EvaluationReport.model_validate(
        {**report_b.model_dump(mode="json"), "dataset_version": "9.9.9"}
    )
    diff_mismatch = diff_strategies(report_a, mismatched_b)
    assert diff_mismatch.dataset_version_match is False
    assert any("Dataset version mismatch" in w for w in diff_mismatch.warnings)

    # Verify manifest type sanity
    assert isinstance(manifest, GoldenDatasetManifest)
