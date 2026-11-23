"""Full 50-case offline golden regression gate verifying baseline parity and canonical contract."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from evals.comparison import compare_against_baseline
from evals.dataset import DEFAULT_MANIFEST_PATH, load_dataset
from evals.models import GoldenCase, RegressionThresholds
from evals.reference_strategy import ReferenceDeterministicStrategy
from evals.reporting import load_report
from evals.runner import EvaluationRunner
from structa_maestro.contracts.models import ExtractionResult

BASELINE_PATH = (
    Path(__file__).parent.parent.parent / "evals" / "baselines" / "deterministic_v2.0.0.json"
)
EVAL_REPORT_SCHEMA_PATH = (
    Path(__file__).parent.parent / "fixtures" / "schemas" / "evaluation-report.v1.0.schema.json"
)


@pytest.mark.evaluation
def test_50_case_golden_regression_against_committed_baseline(
    contract_json_schema: dict[str, Any],
) -> None:
    report_schema: dict[str, Any] = json.loads(EVAL_REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))

    # C1: Verify committed baseline itself conforms to schema and is an unfiltered full-corpus run
    baseline = load_report(BASELINE_PATH)
    jsonschema.validate(instance=baseline.model_dump(mode="json"), schema=report_schema)
    assert baseline.filters.is_filtered is False
    assert baseline.summary.total_cases == 50

    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=True)
    runner = EvaluationRunner(manifest)
    collected: list[tuple[GoldenCase, ExtractionResult]] = []

    start = time.perf_counter()
    candidate = runner.run(
        strategy=ReferenceDeterministicStrategy(),
        config_version="default",
        trials=1,
        collect_results=collected,
    )
    elapsed_sec = time.perf_counter() - start

    # SC-002: Offline 50-case regression completes under 15s with 100% contract conformance
    assert elapsed_sec < 15.0, f"Offline 50-case golden evaluation took {elapsed_sec:.2f}s >= 15s"
    assert len(collected) == 50
    for _case, result in collected:
        jsonschema.validate(instance=result.to_dict(), schema=contract_json_schema)

    jsonschema.validate(instance=candidate.model_dump(mode="json"), schema=report_schema)
    assert candidate.summary.hallucination_rate == 0.0
    assert candidate.summary.field_exact_match_accuracy == 1.0
    assert candidate.summary.provenance_accuracy == 1.0
    assert candidate.summary.validation_pass_rate == 1.0

    comparison = compare_against_baseline(candidate, baseline, RegressionThresholds())
    assert comparison.passed is True, f"Baseline regression failures: {comparison.failure_reasons}"
    assert comparison.regressed_cases == []
