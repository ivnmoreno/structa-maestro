"""Contract serialization round-trip tests (docs/TESTING.md Section 3.4)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    FieldType,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    ProvenanceEvidence,
    SchemaMetadata,
    SourceLocation,
    SourceMetadata,
    ValidationOutcome,
)
from structa_maestro.exceptions import ContractDeserializationError


def test_round_trip_preserves_decimals_dates_unicode_null_empty_and_missing() -> None:
    result = ExtractionResult(
        status=ProcessingStatus.COMPLETED,
        source=SourceMetadata(
            document_id="doc-rt-1",
            filename="faktura_Müller_€100.pdf",
            media_type="application/pdf",
        ),
        schema_metadata=SchemaMetadata(schema_id="rt-schema", schema_version="2.0"),
        records=[
            ExtractedRecord(
                record_id="record-001",
                status=ProcessingStatus.COMPLETED,
                fields={
                    "amount": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=Decimal("001250.50"),
                        value_type=FieldType.DECIMAL,
                        confidence=ConfidenceAssessment(value=0.88, calibrated=False),
                        provenance=[
                            ProvenanceEvidence(
                                source_document_id="doc-rt-1",
                                location=SourceLocation(page=1, char_start=0, char_end=15),
                                evidence="Amount: 1250.50",
                                relationship=EvidenceRelationship.DIRECT,
                                truncated=False,
                            )
                        ],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "issue_date": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=date(2026, 11, 22),
                        value_type=FieldType.DATE,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "unicode_text": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value='Café 東京 — "quoted" \\path\nline2',
                        value_type=FieldType.STRING,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "empty_string": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value="",
                        value_type=FieldType.STRING,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "explicit_null": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=None,
                        value_type=FieldType.STRING,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "missing_field": ExtractedField(
                        presence=FieldPresence.MISSING,
                        value=None,
                        value_type=FieldType.STRING,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "nested": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value={"items": [1, 2, 3], "empty_list": [], "empty_obj": {}},
                        value_type=FieldType.OBJECT,
                        confidence=None,
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                },
                validation=ValidationOutcome(status=ValidationStatus.VALID),
            )
        ],
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )

    serialized = result.to_json()
    restored = ExtractionResult.from_json(serialized)
    assert restored == result
    assert restored.records[0].fields["amount"].value == Decimal("1250.50")
    assert restored.records[0].fields["issue_date"].value == date(2026, 11, 22)
    assert restored.records[0].fields["empty_string"].presence == FieldPresence.PRESENT
    assert restored.records[0].fields["empty_string"].value == ""
    assert restored.records[0].fields["explicit_null"].presence == FieldPresence.PRESENT
    assert restored.records[0].fields["explicit_null"].value is None
    assert restored.records[0].fields["missing_field"].presence == FieldPresence.MISSING
    assert restored.records[0].fields["missing_field"].value is None


def test_deserialization_rejects_unknown_version_and_missing_keys() -> None:
    with pytest.raises(ContractDeserializationError, match="Unsupported contract_version"):
        ExtractionResult.from_dict(
            {
                "contract_version": "9.9",
                "extraction_id": "id-1",
                "status": "completed",
                "source": None,
                "schema": {"schema_id": "s", "schema_version": "1.0"},
                "records": [],
                "validation": {"status": "valid", "errors": []},
                "errors": [],
                "warnings": [],
            }
        )

    with pytest.raises(ContractDeserializationError, match="Missing required canonical"):
        ExtractionResult.from_dict({"contract_version": "1.0"})

    with pytest.raises(ContractDeserializationError, match="Invalid JSON"):
        ExtractionResult.from_json("{not valid json")

    with pytest.raises(ContractDeserializationError):
        ExtractionResult.from_json(json.dumps(["not", "an", "object"]))


def test_cross_field_errors_and_warnings_conform_to_contract_v1_round_trip(
    tmp_path: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    import jsonschema

    from structa_maestro import (
        DeterministicTestStrategy,
        ProposedField,
        ProposedRecord,
        StrategyOutput,
        extract,
    )

    doc = tmp_path / "inv.txt"
    doc.write_text(
        "Issue: 2026-04-20\nDue: 2026-04-15\nSubtotal: 100\nTotal: 99\n", encoding="utf-8"
    )
    schema = {
        "schema_id": "inv-cf-contract",
        "schema_version": "1.0",
        "fields": {
            "issue_date": {"type": "date", "required": True},
            "due_date": {"type": "date", "required": True},
            "subtotal": {"type": "decimal", "required": True},
            "total_amount": {"type": "decimal", "required": True},
        },
        "rules": [
            {
                "rule_id": "issue-lte-due",
                "kind": "comparison",
                "left_field": "issue_date",
                "operator": "lte",
                "right_field": "due_date",
                "severity": "error",
            },
            {
                "rule_id": "advisory-total",
                "kind": "arithmetic",
                "terms": [{"field": "subtotal"}],
                "target_field": "total_amount",
                "severity": "warning",
            },
        ],
    }
    strat = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "issue_date": ProposedField(value=date(2026, 4, 20)),
                        "due_date": ProposedField(value=date(2026, 4, 15)),
                        "subtotal": ProposedField(value=Decimal("100")),
                        "total_amount": ProposedField(value=Decimal("99")),
                    }
                ),
            )
        )
    )
    res = extract(doc, schema, strategy=strat, document_id="doc-cf-1")

    serialized = res.to_json()
    jsonschema.validate(instance=json.loads(serialized), schema=contract_json_schema)
    restored = ExtractionResult.from_json(serialized)
    assert restored == res
    assert restored.validation.status == ValidationStatus.INVALID
    assert any(e.code == "cross_field_comparison_failed" for e in restored.validation.errors)
    assert any(w.code == "cross_field_arithmetic_mismatch" for w in restored.warnings)
    assert any(w.code == "cross_field_arithmetic_mismatch" for w in restored.records[0].warnings)
