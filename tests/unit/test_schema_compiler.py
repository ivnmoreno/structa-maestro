"""Unit tests for deterministic strict JSON Schema compilation (Spec 003 US1 / T004)."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.schema_compiler import (
    StrictSchemaIncompatibilityError,
    compile_extraction_output_schema,
)


def _assert_all_objects_closed(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            props = node.get("properties", {})
            assert set(node.get("required", [])) == set(props.keys())
        for val in node.values():
            _assert_all_objects_closed(val)
    elif isinstance(node, list):
        for item in node:
            _assert_all_objects_closed(item)


def test_compile_all_field_types_nested_objects_arrays_enums_and_determinism() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "Invoice Schema / v2.0!",
            "schema_version": "2.0",
            "cardinality": "multiple",
            "fields": {
                "invoice_number": {"type": "string", "required": True, "description": "Invoice ID"},
                "page_count": {"type": "integer", "required": True},
                "total_amount": {"type": "decimal", "required": True, "min_value": 0},
                "paid": {"type": "boolean", "required": False, "nullable": True},
                "issue_date": {"type": "date", "required": True},
                "currency": {
                    "type": "string",
                    "required": True,
                    "allowed_values": ["USD", "EUR", "GBP"],
                },
                "vendor": {
                    "type": "object",
                    "required": True,
                    "properties": {
                        "name": {"type": "string", "required": True},
                        "tax_id": {"type": "string", "required": False, "nullable": True},
                    },
                },
                "line_items": {
                    "type": "array",
                    "required": False,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "required": True},
                            "amount": {"type": "decimal", "required": True},
                        },
                    },
                },
            },
        }
    )

    compiled_a = compile_extraction_output_schema(schema)
    compiled_b = compile_extraction_output_schema(schema)

    assert compiled_a == compiled_b
    assert json.dumps(compiled_a.schema, sort_keys=True) == json.dumps(
        compiled_b.schema, sort_keys=True
    )
    assert compiled_a.strict is True
    assert compiled_a.name == "Invoice_Schema_v2_0"
    assert len(compiled_a.name) <= 64

    # Valid JSON Schema itself and strictly closed at every object level
    jsonschema.Draft202012Validator.check_schema(compiled_a.schema)
    _assert_all_objects_closed(compiled_a.schema)

    # Response format payload shape
    rf = compiled_a.to_response_format()
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "Invoice_Schema_v2_0"

    # A valid conforming extraction payload validates cleanly against the compiled schema
    sample_output = {
        "records": [
            {
                "fields": {
                    "invoice_number": {
                        "presence": "present",
                        "value": "INV-1",
                        "confidence": 0.95,
                        "ambiguous": False,
                        "evidence": [{"quote": "INV-1", "relationship": "direct"}],
                    },
                    "page_count": {
                        "presence": "present",
                        "value": 2,
                        "confidence": 0.9,
                        "ambiguous": False,
                        "evidence": [{"quote": "2 pages", "relationship": "direct"}],
                    },
                    "total_amount": {
                        "presence": "present",
                        "value": "120.50",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "evidence": [{"quote": "120.50", "relationship": "direct"}],
                    },
                    "paid": {
                        "presence": "missing",
                        "value": None,
                        "confidence": None,
                        "ambiguous": False,
                        "evidence": [],
                    },
                    "issue_date": {
                        "presence": "present",
                        "value": "2026-03-15",
                        "confidence": 0.9,
                        "ambiguous": False,
                        "evidence": [{"quote": "2026-03-15", "relationship": "direct"}],
                    },
                    "currency": {
                        "presence": "present",
                        "value": "USD",
                        "confidence": 0.98,
                        "ambiguous": False,
                        "evidence": [{"quote": "USD", "relationship": "direct"}],
                    },
                    "vendor": {
                        "presence": "present",
                        "value": {"name": "Acme", "tax_id": None},
                        "confidence": 0.92,
                        "ambiguous": False,
                        "evidence": [{"quote": "Acme", "relationship": "direct"}],
                    },
                    "line_items": {
                        "presence": "present",
                        "value": [{"description": "Widget", "amount": "120.50"}],
                        "confidence": 0.88,
                        "ambiguous": False,
                        "evidence": [{"quote": "Widget 120.50", "relationship": "direct"}],
                    },
                }
            }
        ]
    }
    jsonschema.validate(instance=sample_output, schema=compiled_a.schema)

    # Extra undeclared field MUST fail compiled schema validation
    sample_output["records"][0]["fields"]["injected_extra"] = {
        "presence": "present",
        "value": "bad",
        "confidence": 1.0,
        "ambiguous": False,
        "evidence": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=sample_output, schema=compiled_a.schema)


def test_compile_rejects_allow_additional_fields_top_level_and_nested() -> None:
    top_open = ExtractionSchema.from_dict(
        {
            "schema_id": "open-top",
            "schema_version": "1.0",
            "allow_additional_fields": True,
            "fields": {"name": {"type": "string", "required": True}},
        }
    )
    with pytest.raises(StrictSchemaIncompatibilityError):
        compile_extraction_output_schema(top_open)

    nested_open = ExtractionSchema.from_dict(
        {
            "schema_id": "open-nested",
            "schema_version": "1.0",
            "fields": {
                "meta": {
                    "type": "object",
                    "allow_additional_fields": True,
                    "properties": {"id": {"type": "string", "required": True}},
                }
            },
        }
    )
    with pytest.raises(StrictSchemaIncompatibilityError):
        compile_extraction_output_schema(nested_open)
