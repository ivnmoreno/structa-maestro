"""Public API compatibility and runnable example smoke tests (CI-016 & CI-020)."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import structa_maestro as sm


def test_public_api_exports_and_version() -> None:
    assert isinstance(sm.__version__, str) and sm.__version__
    for symbol in (
        "CONTRACT_VERSION",
        "ExtractionEngine",
        "extract",
        "extract_batch",
        "aextract",
        "aextract_batch",
        "BatchDocumentInput",
        "BatchOptions",
        "BatchSummary",
        "BatchExtractionResult",
        "ExtractionSchema",
        "FieldSpec",
        "FieldConstraints",
        "ExtractionResult",
        "ExtractedRecord",
        "ExtractedField",
        "ConfidenceAssessment",
        "CALIBRATION_ARTIFACT_VERSION",
        "DEFAULT_CALIBRATION_MIN_SAMPLES",
        "MAX_CALIBRATION_ARTIFACT_BYTES",
        "MIN_CALIBRATION_SAMPLES_FLOOR",
        "BinningStrategy",
        "CalibrationBin",
        "CalibrationMetadata",
        "CalibrationMethod",
        "CalibrationMetrics",
        "CalibrationModel",
        "CalibrationSample",
        "ConfidenceCalibrator",
        "ConfidenceNormalizationResult",
        "ConfidenceNormalizer",
        "HistogramParameters",
        "IsotonicParameters",
        "PlattParameters",
        "compute_calibration_metrics",
        "fit_calibrator",
        "ProvenanceEvidence",
        "SourceLocation",
        "ValidationOutcome",
        "DiagnosticIssue",
        "DocumentInput",
        "DocumentInputKind",
        "DocumentLimits",
        "SecretValue",
        "ExtractionStrategy",
        "AsyncExtractionStrategy",
        "OpenAICompatibleLLMStrategy",
        "AsyncOpenAICompatibleLLMStrategy",
        "AnthropicLLMStrategy",
        "AsyncAnthropicLLMStrategy",
        "DeterministicTestStrategy",
        "AsyncDeterministicTestStrategy",
        "StructuredOutputMode",
        "InputBudgetOverflowPolicy",
        "RetryPolicy",
        "InputBudgetPolicy",
        "CompiledOutputSchema",
        "compile_extraction_output_schema",
        "ComparisonOperator",
        "ConditionOperator",
        "RuleSeverity",
        "ConditionalPresenceKind",
        "GroupPresenceKind",
        "ComparisonRule",
        "ArithmeticTerm",
        "ArithmeticRule",
        "FieldCondition",
        "ConditionalPresenceRule",
        "GroupPresenceRule",
        "ValidationRule",
        "SchemaDefinitionError",
        "ConfigurationError",
        "ContractDeserializationError",
        "RecordBindingError",
        "bind_record",
        "bind_records",
        "MAX_REVIEW_PAYLOAD_BYTES",
        "REVIEW_AUDIT_VERSION",
        "REVIEW_PLAN_VERSION",
        "REVIEW_SUBMISSION_VERSION",
        "FieldReviewDecision",
        "ProvenanceSummary",
        "RecordReviewDecision",
        "ReviewAction",
        "ReviewApplicationResult",
        "ReviewAudit",
        "ReviewAuditEntry",
        "ReviewItem",
        "ReviewPlan",
        "ReviewPolicy",
        "ReviewReason",
        "ReviewSubmission",
        "apply_review",
        "plan_review",
        "AsyncExtractionObserver",
        "BatchProgress",
        "ExtractionEvent",
        "ExtractionEventKind",
        "ExtractionObserver",
        "InMemoryTelemetryObserver",
        "StageTiming",
        "StageTimingSummary",
        "StrategyTelemetry",
        "StructuredLoggingObserver",
        "TelemetrySummary",
        "TokenUsage",
    ):
        assert hasattr(sm, symbol), f"Missing public symbol: {symbol}"


def test_runnable_examples_execute_cleanly() -> None:
    examples_dir = Path(__file__).parent.parent.parent / "examples"
    for script in sorted(examples_dir.glob("*.py")):
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert proc.returncode == 0, f"Example {script.name} failed:\n{proc.stderr}"


def test_zero_lean_runtime_dependency_and_formal_excluded_from_packaging() -> None:
    repo_root = Path(__file__).parent.parent.parent
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject["project"]["dependencies"]
    dep_names = {d.split(">=")[0].split("<")[0].split("==")[0].strip().lower() for d in deps}
    assert dep_names == {"pydantic", "pypdf", "python-docx", "httpx"}

    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert wheel_packages == ["src/structa_maestro"]
    sdist_includes = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert all("formal" not in entry for entry in sdist_includes)

    # Confirm production package never imports or invokes Lean / Lake
    src_dir = repo_root / "src" / "structa_maestro"
    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        assert "structa-formal-oracle" not in text
        assert "StructaFormal" not in text
