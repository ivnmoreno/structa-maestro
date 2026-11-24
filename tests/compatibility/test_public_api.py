"""Public API compatibility and runnable example smoke tests (CI-016 & CI-020)."""

from __future__ import annotations

import subprocess
import sys
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
