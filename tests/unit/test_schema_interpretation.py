"""Schema interpretation unit tests covering SCH-001 through SCH-028."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from structa_maestro.contracts.enums import ExtractionCardinality, FieldType
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec


def test_sch_001_to_015_valid_scalar_nested_array_enum_nullable_and_constraints() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "full-schema",
            "schema_version": "1.2",
            "cardinality": "multiple",
            "fields": {
                "name": {
                    "type": "string",
                    "required": True,
                    "min_length": 2,
                    "max_length": 50,
                    "pattern": r"^[A-Za-z ]+$",
                },
                "count": {"type": "integer", "required": True, "min_value": 1, "max_value": 100},
                "amount": {"type": "decimal", "required": False, "min_value": "0.00"},
                "active": {"type": "boolean", "required": True},
                "signed_on": {"type": "date", "required": False, "nullable": True},
                "status": {"type": "string", "allowed_values": ["open", "closed"]},
                "tags": {
                    "type": "array",
                    "required": False,
                    "min_items": 1,
                    "max_items": 5,
                    "items": {"type": "string"},
                },
                "matrix": {
                    "type": "array",
                    "required": False,
                    "items": {"type": "array", "items": {"type": "integer"}},
                },
                "address": {
                    "type": "object",
                    "required": False,
                    "properties": {
                        "city": {"type": "string", "required": True},
                        "zip_code": {"type": "string", "required": False},
                    },
                },
            },
        }
    )

    assert schema.schema_id == "full-schema"
    assert schema.cardinality == ExtractionCardinality.MULTIPLE
    assert schema.allow_additional_fields is False
    assert schema.fields["name"].field_type == FieldType.STRING
    assert schema.fields["count"].field_type == FieldType.INTEGER
    assert schema.fields["amount"].constraints.min_value == Decimal("0.00")
    assert schema.fields["active"].field_type == FieldType.BOOLEAN
    assert schema.fields["signed_on"].field_type == FieldType.DATE
    assert schema.fields["signed_on"].nullable is True
    assert schema.fields["status"].constraints.allowed_values == ("open", "closed")
    assert schema.fields["matrix"].items is not None
    assert schema.fields["matrix"].items.field_type == FieldType.ARRAY
    assert schema.fields["address"].properties is not None
    assert "city" in schema.fields["address"].properties


def test_json_schema_subset_interop() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "title": "json-schema-invoice",
            "type": "object",
            "additionalProperties": False,
            "required": ["invoice_no"],
            "properties": {
                "invoice_no": {"type": "string", "minLength": 3, "maxLength": 20},
                "due_date": {"type": ["string", "null"], "format": "date"},
                "total": {"type": "number", "minimum": 0, "maximum": 10000},
            },
        }
    )
    assert schema.fields["invoice_no"].required is True
    assert schema.fields["due_date"].required is False
    assert schema.fields["due_date"].nullable is True
    assert schema.fields["due_date"].field_type == FieldType.DATE
    assert schema.fields["total"].field_type == FieldType.DECIMAL


def test_sch_017_to_028_invalid_schemas_raise_schema_definition_error(tmp_path: Path) -> None:
    # SCH-017: Invalid JSON syntax
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad json", encoding="utf-8")
    with pytest.raises(SchemaDefinitionError, match="Invalid JSON"):
        ExtractionSchema.from_json_file(bad_json)

    # SCH-018: Unsupported schema type
    with pytest.raises(SchemaDefinitionError, match="Unsupported schema type"):
        ExtractionSchema.from_dict({"schema_id": "s", "fields": {"x": {"type": "binary_blob"}}})

    # SCH-019: Duplicate field definitions in JSON file
    dup_json = tmp_path / "dup.json"
    dup_json.write_text(
        '{"schema_id": "s", "fields": {"a": {"type": "string"}, "a": {"type": "integer"}}}',
        encoding="utf-8",
    )
    with pytest.raises(SchemaDefinitionError, match="Duplicate key"):
        ExtractionSchema.from_json_file(dup_json)

    # SCH-021: Recursive $ref rejected
    with pytest.raises(SchemaDefinitionError, match="Recursive"):
        ExtractionSchema.from_dict({"schema_id": "s", "fields": {"node": {"$ref": "#"}}})

    # SCH-022: Deeply nested schema exceeding max_depth rejected
    deep: dict[str, object] = {"type": "string"}
    for _ in range(5):
        deep = {"type": "object", "properties": {"child": deep}}
    with pytest.raises(SchemaDefinitionError, match="nesting depth"):
        ExtractionSchema.from_dict({"schema_id": "s", "max_depth": 3, "fields": {"root": deep}})

    # SCH-023: Unknown attributes rejected
    with pytest.raises(SchemaDefinitionError, match="Unknown"):
        ExtractionSchema.from_dict(
            {"schema_id": "s", "fields": {"x": {"type": "string", "bogus_attr": 123}}}
        )

    # SCH-024: Conflicting constraints rejected
    with pytest.raises(SchemaDefinitionError, match="Conflicting numeric bounds"):
        FieldConstraints(min_value=10, max_value=5)
    with pytest.raises(SchemaDefinitionError, match="Conflicting length bounds"):
        FieldConstraints(min_length=10, max_length=2)
    with pytest.raises(SchemaDefinitionError, match="Invalid regex"):
        FieldConstraints(pattern="[unclosed")
    with pytest.raises(SchemaDefinitionError, match="incompatible"):
        FieldSpec(
            name="flag", field_type=FieldType.BOOLEAN, constraints=FieldConstraints(min_value=1)
        )

    # SCH-026 / SCH-027: Empty schema or schema with no properties
    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_dict({})
    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_dict({"schema_id": "empty", "fields": {}})
