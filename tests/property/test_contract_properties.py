"""Hypothesis property-based tests for contract round-trip, validation determinism, and schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from structa_maestro import (
    ConfidenceAssessment,
    DeterministicValidator,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    ExtractionSchema,
    FieldPresence,
    FieldType,
    ProcessingStatus,
    SchemaDefinitionError,
    SchemaMetadata,
    SourceMetadata,
    ValidationOutcome,
    ValidationStatus,
)


@pytest.mark.property
@settings(max_examples=40)
@given(
    text_val=st.text(max_size=80),
    dec_val=st.decimals(min_value=-100000, max_value=100000, allow_nan=False, allow_infinity=False),
    date_val=st.dates(min_value=date(1970, 1, 1), max_value=date(2099, 12, 31)),
    conf_val=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property_canonical_serialization_round_trip(
    text_val: str, dec_val: Decimal, date_val: date, conf_val: float
) -> None:
    result = ExtractionResult(
        status=ProcessingStatus.COMPLETED,
        source=SourceMetadata(document_id="doc-prop"),
        schema_metadata=SchemaMetadata(schema_id="prop", schema_version="1.0"),
        records=[
            ExtractedRecord(
                record_id="record-001",
                status=ProcessingStatus.COMPLETED,
                fields={
                    "text": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=text_val,
                        value_type=FieldType.STRING,
                        confidence=ConfidenceAssessment(value=conf_val, calibrated=False),
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "amount": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=dec_val,
                        value_type=FieldType.DECIMAL,
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                    "when": ExtractedField(
                        presence=FieldPresence.PRESENT,
                        value=date_val,
                        value_type=FieldType.DATE,
                        validation=ValidationOutcome(status=ValidationStatus.VALID),
                    ),
                },
                validation=ValidationOutcome(status=ValidationStatus.VALID),
            )
        ],
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )

    restored = ExtractionResult.from_json(result.to_json())
    assert restored == result


@pytest.mark.property
@settings(max_examples=40)
@given(
    candidate=st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-100, max_value=100),
        st.text(max_size=30),
    )
)
def test_property_validation_is_strictly_deterministic(candidate: object) -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "det",
            "schema_version": "1.0",
            "fields": {"n": {"type": "integer", "required": True, "min_value": 0, "max_value": 50}},
        }
    )
    validator = DeterministicValidator()
    fields = {"n": (FieldPresence.PRESENT, candidate)}
    r1 = validator.validate_record(fields, schema, record_id="record-001")
    r2 = validator.validate_record(fields, schema, record_id="record-001")
    assert r1 == r2


@pytest.mark.property
@settings(max_examples=30)
@given(
    bad_type=st.text(min_size=1, max_size=20).filter(
        lambda s: (
            s.lower()
            not in {
                "string",
                "str",
                "integer",
                "int",
                "decimal",
                "number",
                "boolean",
                "bool",
                "date",
                "array",
                "object",
            }
        )
    )
)
def test_property_invalid_schema_types_always_rejected(bad_type: str) -> None:
    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_dict({"schema_id": "bad", "fields": {"f": {"type": bad_type}}})
