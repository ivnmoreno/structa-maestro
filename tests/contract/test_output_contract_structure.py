"""Contract structure tests validating Canonical Output Record Contract v1.0 against JSON Schema."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import jsonschema

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    DiagnosticIssue,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    ProvenanceEvidence,
    SchemaMetadata,
    SourceLocation,
    SourceMetadata,
    ValidationOutcome,
)


def _build_sample_result() -> ExtractionResult:
    return ExtractionResult(
        status=ProcessingStatus.COMPLETED,
        source=SourceMetadata(
            document_id="doc-001",
            filename="invoice_001.pdf",
            media_type="application/pdf",
        ),
        schema_metadata=SchemaMetadata(schema_id="invoice-schema", schema_version="1.0"),
        records=[
            ExtractedRecord(
                record_id="record-001",
                status=ProcessingStatus.COMPLETED,
                fields={
                    "invoice_number": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value="INV-2026-001",
                        value_type=FieldType.STRING,
                        confidence=ConfidenceAssessment(
                            value=0.96,
                            scale="0-1",
                            method="extraction-assessment",
                            calibrated=False,
                        ),
                        provenance=[
                            ProvenanceEvidence(
                                source_document_id="doc-001",
                                location=SourceLocation(page=1),
                                evidence="Invoice Number: INV-2026-001",
                                relationship=EvidenceRelationship.DIRECT,
                                truncated=False,
                            )
                        ],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "total_amount": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=Decimal("1250.50"),
                        value_type=FieldType.DECIMAL,
                        confidence=ConfidenceAssessment(value=0.92, calibrated=False),
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "issue_date": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=date(2026, 3, 15),
                        value_type=FieldType.DATE,
                        confidence=ConfidenceAssessment(value=0.91, calibrated=False),
                        provenance=[],
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "notes": ExtractedField(
                        presence=FieldPresence.MISSING,
                        value=None,
                        value_type=FieldType.STRING,
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


def test_canonical_result_conforms_to_json_schema(contract_json_schema: dict[str, Any]) -> None:
    result = _build_sample_result()
    payload = result.to_dict()
    jsonschema.validate(instance=payload, schema=contract_json_schema)
    assert payload["contract_version"] == "1.0"
    assert "schema" in payload
    assert payload["schema"]["schema_id"] == "invoice-schema"


def test_failed_empty_records_result_conforms_to_json_schema(
    contract_json_schema: dict[str, Any],
) -> None:
    result = ExtractionResult(
        status=ProcessingStatus.FAILED,
        source=SourceMetadata(document_id="doc-err", filename="bad.bin", media_type=None),
        schema_metadata=SchemaMetadata(schema_id="invoice-schema", schema_version="1.0"),
        records=[],
        validation=ValidationOutcome(status=ValidationStatus.NOT_VALIDATED),
        errors=[
            DiagnosticIssue(
                code="unsupported_format",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.INPUT,
                message="Unsupported document format.",
            )
        ],
    )
    payload = result.to_dict()
    jsonschema.validate(instance=payload, schema=contract_json_schema)
    assert payload["records"] == []
    assert payload["validation"]["status"] == "not_validated"
