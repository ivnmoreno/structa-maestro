"""End-to-end extraction pipeline integration tests for single-record and multi-record flows."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionEngine,
    ExtractionResult,
    FieldPresence,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
)


def test_single_record_complete_valid_extraction(
    fixtures_dir: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
) -> None:
    doc_path = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "vendor_name": ProposedField(
                            value="Acme Industrial Supply Ltd.",
                            confidence=0.94,
                            evidence=(
                                ProposedEvidence(quote="Vendor: Acme Industrial Supply Ltd."),
                            ),
                        ),
                        "issue_date": ProposedField(
                            value=date(2026, 3, 15),
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote="Issue Date: 2026-03-15"),),
                        ),
                        "total_amount": ProposedField(
                            value=Decimal("1250.50"),
                            confidence=0.97,
                            evidence=(ProposedEvidence(quote="Total Amount: 1250.50"),),
                        ),
                        "currency": ProposedField(
                            value="USD",
                            confidence=0.99,
                            evidence=(ProposedEvidence(quote="Currency: USD"),),
                        ),
                    }
                ),
            )
        )
    )
    engine = ExtractionEngine(strategy=strategy)
    result = engine.extract(doc_path, invoice_schema_dict, document_id="doc-001")

    jsonschema.validate(instance=result.to_dict(), schema=contract_json_schema)
    assert result.status == ProcessingStatus.COMPLETED
    assert result.validation.status == ValidationStatus.VALID
    assert len(result.records) == 1
    rec = result.records[0]
    assert rec.fields["invoice_number"].value == "INV-2026-001"
    assert rec.fields["notes"].presence == FieldPresence.MISSING
    assert rec.fields["notes"].value is None
    assert rec.fields["notes"].validation.status == ValidationStatus.VALID
    assert ExtractionResult.from_json(result.to_json()) == result


def test_single_record_completed_extraction_with_invalid_values_preserves_distinction(
    fixtures_dir: Path,
    invoice_schema_dict: dict[str, Any],
) -> None:
    doc_path = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="BAD-ID",
                            confidence=0.99,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "vendor_name": ProposedField(value="Acme Industrial Supply Ltd."),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("-10.00")),
                        # currency intentionally omitted -> missing required!
                    }
                ),
            )
        )
    )
    engine = ExtractionEngine(strategy=strategy)
    result = engine.extract(doc_path, invoice_schema_dict)

    # Processing completed, validation invalid — strictly distinct!
    assert result.status == ProcessingStatus.COMPLETED
    assert result.validation.status == ValidationStatus.INVALID
    assert result.records[0].fields["currency"].presence == FieldPresence.MISSING
    assert result.records[0].fields["currency"].validation.status == ValidationStatus.INVALID
    assert result.records[0].fields["total_amount"].validation.status == ValidationStatus.INVALID


def test_multi_record_extraction_independent_validation_and_empty_collection(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "people.txt"
    doc.write_text(
        "Employee: Alice Smith | ID: EMP-001\n"
        "Employee: Bob Jones | ID: EMP-002\n"
        "Employee: Carol | ID: BAD\n",
        encoding="utf-8",
    )
    multi_schema = {
        "schema_id": "employee-list",
        "schema_version": "1.0",
        "cardinality": "multiple",
        "fields": {
            "name": {"type": "string", "required": True},
            "employee_id": {"type": "string", "required": True, "pattern": r"^EMP-\d{3}$"},
        },
    }
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "name": ProposedField(
                            value="Alice Smith",
                            evidence=(ProposedEvidence(quote="Employee: Alice Smith"),),
                        ),
                        "employee_id": ProposedField(
                            value="EMP-001",
                            evidence=(ProposedEvidence(quote="ID: EMP-001"),),
                        ),
                    }
                ),
                ProposedRecord(
                    fields={
                        "name": ProposedField(value="Bob Jones"),
                        "employee_id": ProposedField(value="EMP-002"),
                    }
                ),
                ProposedRecord(
                    fields={
                        "name": ProposedField(value="Carol"),
                        "employee_id": ProposedField(value="BAD"),
                    }
                ),
            )
        )
    )

    res = ExtractionEngine(strategy=strategy).extract(doc, multi_schema)
    assert res.status == ProcessingStatus.COMPLETED
    assert len(res.records) == 3
    assert [r.record_id for r in res.records] == ["record-001", "record-002", "record-003"]
    assert res.records[0].validation.status == ValidationStatus.VALID
    assert res.records[1].validation.status == ValidationStatus.VALID
    assert res.records[2].validation.status == ValidationStatus.INVALID
    assert res.validation.status == ValidationStatus.INVALID

    # Empty multi-record result is valid & never fabricates placeholder records
    empty_res = ExtractionEngine(
        strategy=DeterministicTestStrategy(StrategyOutput(records=()))
    ).extract(doc, multi_schema)
    assert empty_res.status == ProcessingStatus.COMPLETED
    assert empty_res.records == []
    assert empty_res.validation.status == ValidationStatus.VALID
