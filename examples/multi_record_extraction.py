"""Multi-record extraction and partial-result handling example."""

from __future__ import annotations

import tempfile
from pathlib import Path

from structa_maestro import (
    DeterministicTestStrategy,
    DiagnosticIssue,
    ExtractionEngine,
    IssueSeverity,
    PipelineStage,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)


def main() -> None:
    schema = {
        "schema_id": "roster-schema",
        "schema_version": "1.0",
        "cardinality": "multiple",
        "fields": {
            "name": {"type": "string", "required": True},
            "employee_id": {"type": "string", "required": True, "pattern": r"^EMP-\d{3}$"},
        },
    }

    strategy = DeterministicTestStrategy(
        StrategyOutput(
            partial=True,
            records=(
                ProposedRecord(
                    fields={
                        "name": ProposedField(
                            value="Alice Smith",
                            confidence=0.97,
                            evidence=(ProposedEvidence(quote="Alice Smith (EMP-001)"),),
                        ),
                        "employee_id": ProposedField(
                            value="EMP-001",
                            confidence=0.98,
                            evidence=(ProposedEvidence(quote="Alice Smith (EMP-001)"),),
                        ),
                    }
                ),
                ProposedRecord(
                    fields={"name": ProposedField(value="Bob Jones")},
                    partial=True,
                    errors=(
                        DiagnosticIssue(
                            code="partial_record",
                            severity=IssueSeverity.ERROR,
                            stage=PipelineStage.EXTRACTION,
                            message="Employee ID unreadable for second entry.",
                        ),
                    ),
                ),
            ),
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        doc_path = Path(tmp) / "roster.txt"
        doc_path.write_text("Alice Smith (EMP-001)\nBob Jones (ID illegible)\n", encoding="utf-8")
        result = ExtractionEngine(strategy=strategy).extract(doc_path, schema)
        print(f"Processing status: {result.status.value}")
        print(f"Validation status: {result.validation.status.value}")
        for rec in result.records:
            print(
                f"  {rec.record_id}: status={rec.status.value} valid={rec.validation.status.value}"
            )


if __name__ == "__main__":
    main()
