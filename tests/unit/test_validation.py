"""Deterministic schema validation unit tests (FR-024..FR-026, Clarification Q4)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from structa_maestro.contracts.enums import (
    FieldPresence,
    FieldType,
    ValidationStatus,
)
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.validation.validator import DeterministicValidator


def _invoice_schema(*, allow_additional_fields: bool = False) -> ExtractionSchema:
    return ExtractionSchema.from_dict(
        {
            "schema_id": "inv",
            "schema_version": "1.0",
            "allow_additional_fields": allow_additional_fields,
            "fields": {
                "invoice_number": {
                    "type": "string",
                    "required": True,
                    "min_length": 4,
                    "max_length": 20,
                    "pattern": r"^INV-\d+$",
                },
                "count": {"type": "integer", "required": True, "min_value": 1, "max_value": 10},
                "amount": {"type": "decimal", "required": True, "min_value": "0.00"},
                "paid": {"type": "boolean", "required": True},
                "issue_date": {"type": "date", "required": True},
                "currency": {"type": "string", "required": True, "allowed_values": ["USD", "EUR"]},
                "notes": {"type": "string", "required": False, "nullable": True},
                "tags": {
                    "type": "array",
                    "required": False,
                    "min_items": 1,
                    "max_items": 3,
                    "items": {"type": "string", "min_length": 2},
                },
                "vendor": {
                    "type": "object",
                    "required": False,
                    "properties": {
                        "name": {"type": "string", "required": True},
                        "tax_id": {"type": "string", "required": False},
                    },
                },
            },
        }
    )


def test_valid_record_passes_and_is_deterministic() -> None:
    schema = _invoice_schema()
    validator = DeterministicValidator()
    fields = {
        "invoice_number": (FieldPresence.PRESENT, "INV-100"),
        "count": (FieldPresence.PRESENT, 2),
        "amount": (FieldPresence.PRESENT, Decimal("99.95")),
        "paid": (FieldPresence.PRESENT, True),
        "issue_date": (FieldPresence.PRESENT, date(2026, 1, 10)),
        "currency": (FieldPresence.PRESENT, "EUR"),
        "notes": (FieldPresence.MISSING, None),
        "tags": (FieldPresence.PRESENT, ["vip", "q1"]),
        "vendor": (FieldPresence.PRESENT, {"name": "Acme"}),
    }

    first = validator.validate_record(fields, schema, record_id="record-001")
    second = validator.validate_record(fields, schema, record_id="record-001")
    assert first == second
    assert first.record_outcome.status == ValidationStatus.VALID
    assert first.record_outcome.errors == []


def test_missing_required_type_mismatch_no_silent_coercion_and_constraints() -> None:
    schema = _invoice_schema()
    validator = DeterministicValidator()
    fields = {
        "invoice_number": (FieldPresence.MISSING, None),
        # bool MUST NOT pass integer check; str "2" MUST NOT be silently coerced to int!
        "count": (FieldPresence.PRESENT, True),
        "amount": (FieldPresence.PRESENT, Decimal("-5.00")),
        "paid": (FieldPresence.PRESENT, "yes"),
        "issue_date": (FieldPresence.PRESENT, "not-a-date"),
        "currency": (FieldPresence.PRESENT, "JPY"),
        "notes": (FieldPresence.PRESENT, None),  # nullable optional -> valid!
        "tags": (FieldPresence.PRESENT, ["x"]),  # item min_length=2 violated
        "vendor": (FieldPresence.PRESENT, {"name": "Acme", "secret_extra": "boom"}),
    }

    res = validator.validate_record(fields, schema, record_id="record-001")
    assert res.record_outcome.status == ValidationStatus.INVALID
    codes = {(e.field_path, e.code) for e in res.record_outcome.errors}
    assert ("invoice_number", "missing_required") in codes
    assert ("count", "type_mismatch") in codes
    assert ("amount", "constraint_violation") in codes
    assert ("paid", "type_mismatch") in codes
    assert ("issue_date", "type_mismatch") in codes
    assert ("currency", "constraint_violation") in codes
    assert ("tags[0]", "constraint_violation") in codes
    assert ("vendor.secret_extra", "unexpected_field") in codes
    assert res.field_outcomes["notes"].status == ValidationStatus.VALID


def test_unexpected_extra_fields_rejected_by_default_and_allowed_when_opted_in() -> None:
    strict_schema = _invoice_schema(allow_additional_fields=False)
    permissive_schema = _invoice_schema(allow_additional_fields=True)
    validator = DeterministicValidator()

    base_fields = {
        "invoice_number": (FieldPresence.PRESENT, "INV-100"),
        "count": (FieldPresence.PRESENT, 2),
        "amount": (FieldPresence.PRESENT, Decimal("10.00")),
        "paid": (FieldPresence.PRESENT, False),
        "issue_date": (FieldPresence.PRESENT, date(2026, 2, 1)),
        "currency": (FieldPresence.PRESENT, "USD"),
        "unexpected_extra": (FieldPresence.PRESENT, "injected"),
    }

    strict_res = validator.validate_record(base_fields, strict_schema, record_id="record-001")
    assert strict_res.record_outcome.status == ValidationStatus.INVALID
    assert any(
        e.code == "unexpected_field" and e.field_path == "unexpected_extra"
        for e in strict_res.record_outcome.errors
    )

    permissive_res = validator.validate_record(
        base_fields, permissive_schema, record_id="record-001"
    )
    assert permissive_res.record_outcome.status == ValidationStatus.VALID


def test_empty_string_and_disallowed_null_and_enum_checks() -> None:
    schema = _invoice_schema()
    validator = DeterministicValidator()
    fields = {
        "invoice_number": (FieldPresence.PRESENT, ""),  # empty string fails min_length & pattern
        "count": (FieldPresence.PRESENT, None),  # non-nullable field -> disallowed_null
        "amount": (FieldPresence.PRESENT, Decimal("1.00")),
        "paid": (FieldPresence.PRESENT, True),
        "issue_date": (FieldPresence.PRESENT, date(2026, 1, 1)),
        "currency": (FieldPresence.PRESENT, "USD"),
    }
    res = validator.validate_record(fields, schema, record_id="record-001")
    assert res.field_outcomes["invoice_number"].status == ValidationStatus.INVALID
    assert any(e.code == "disallowed_null" for e in res.field_outcomes["count"].errors)
    assert FieldType.STRING.value == "string"
