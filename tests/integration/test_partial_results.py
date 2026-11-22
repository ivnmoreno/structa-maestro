"""Partial-result preservation integration tests (docs/TESTING.md Section 8.5)."""

from __future__ import annotations

from pathlib import Path

from structa_maestro import (
    DeterministicTestStrategy,
    DiagnosticIssue,
    ExtractionEngine,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
)


def test_four_valid_records_preserved_when_fifth_record_fails(tmp_path: Path) -> None:
    doc = tmp_path / "batch_items.txt"
    doc.write_text(
        "Item: A-1\nItem: A-2\nItem: A-3\nItem: A-4\nItem: [CORRUPT]\n",
        encoding="utf-8",
    )
    schema = {
        "schema_id": "items",
        "schema_version": "1.0",
        "cardinality": "multiple",
        "fields": {"code": {"type": "string", "required": True, "pattern": r"^A-\d+$"}},
    }
    valid_records = [
        ProposedRecord(
            fields={
                "code": ProposedField(
                    value=f"A-{i}",
                    confidence=0.95,
                    evidence=(ProposedEvidence(quote=f"Item: A-{i}"),),
                )
            }
        )
        for i in range(1, 5)
    ]
    records = (
        *valid_records,
        ProposedRecord(
            fields={},
            failed=True,
            errors=(
                DiagnosticIssue(
                    code="record_extraction_failed",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.EXTRACTION,
                    message="Failed to parse fifth record.",
                ),
            ),
        ),
    )

    res = ExtractionEngine(
        strategy=DeterministicTestStrategy(StrategyOutput(records=records, partial=True))
    ).extract(doc, schema)

    assert res.status == ProcessingStatus.PARTIAL
    assert len(res.records) == 5
    for idx in range(4):
        assert res.records[idx].status == ProcessingStatus.COMPLETED
        assert res.records[idx].validation.status == ValidationStatus.VALID
        assert res.records[idx].fields["code"].value == f"A-{idx + 1}"
    assert res.records[4].status == ProcessingStatus.FAILED
    assert res.records[4].validation.status == ValidationStatus.INVALID
    assert len(res.records[4].errors) == 1
