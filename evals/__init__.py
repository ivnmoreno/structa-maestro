"""Repository-internal golden evaluation harness for structa-maestro (excluded from wheel)."""

from __future__ import annotations

from evals.comparison import compare_against_baseline, diff_strategies
from evals.dataset import DEFAULT_MANIFEST_PATH, filter_cases, load_dataset, validate_dataset
from evals.materializer import materialize_document
from evals.models import (
    REPORT_VERSION,
    CaseDelta,
    CaseOutcome,
    ChallengeProfile,
    ComparisonReport,
    DifferentialReport,
    DocumentCategory,
    DocumentRecipe,
    EvaluationFilter,
    EvaluationReport,
    ExpectedFieldSpec,
    ExpectedRecordSpec,
    FieldScore,
    GoldenCase,
    GoldenDatasetManifest,
    MatchMode,
    MaterializationKind,
    MetricSummary,
    RecordOrderMode,
    RegressionThresholds,
    TrialDistribution,
    TrialStat,
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

__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "REPORT_VERSION",
    "CaseDelta",
    "CaseOutcome",
    "ChallengeProfile",
    "ComparisonReport",
    "DifferentialReport",
    "DocumentCategory",
    "DocumentRecipe",
    "EvaluationFilter",
    "EvaluationReport",
    "EvaluationRunner",
    "ExpectedFieldSpec",
    "ExpectedRecordSpec",
    "FieldScore",
    "GoldenCase",
    "GoldenDatasetManifest",
    "MatchMode",
    "MaterializationKind",
    "MetricSummary",
    "RecordOrderMode",
    "ReferenceDeterministicStrategy",
    "RegressionThresholds",
    "TrialDistribution",
    "TrialStat",
    "compare_against_baseline",
    "diff_strategies",
    "filter_cases",
    "load_dataset",
    "load_report",
    "materialize_document",
    "render_comparison_markdown",
    "render_differential_markdown",
    "render_report_markdown",
    "save_report",
    "validate_dataset",
]
