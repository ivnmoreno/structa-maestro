"""Pure-Python schema well-formedness & tagged RuntimeValue conformance helpers.

Mirrors `formal/StructaFormal/SchemaModels.lean` and `DeterministicValidation.lean`, providing:
- Lossless Python -> tagged `RuntimeValue` JSON encoding for `structa-formal-oracle`
- `ExtractionSchema` -> Lean oracle JSON serialization
- Curated valid/invalid/boundary schema, non-coercion, nested OBJECT/ARRAY, extra-field,
  and SINGLE/MULTIPLE cardinality test vectors.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import ExtractionCardinality, FieldPresence, FieldType
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec


def encode_runtime_value(value: Any) -> dict[str, Any]:
    """Encode any Python runtime value into the lossless tagged JSON format for `Oracle.lean`."""
    if value is None:
        return {"kind": "null"}
    if isinstance(value, bool):
        return {"kind": "boolean", "value": value}
    if isinstance(value, int):
        return {"kind": "integer", "value": value}
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            return {"kind": "non_finite_decimal"}
        return {"kind": "decimal", "value": float(value) if value == int(value) else str(value)}
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return {"kind": "non_finite_decimal"}
        return {"kind": "decimal", "value": value}
    if isinstance(value, datetime):
        return {"kind": "datetime"}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"kind": "string", "value": value}
    if isinstance(value, list):
        return {"kind": "array", "value": [encode_runtime_value(elem) for elem in value]}
    if isinstance(value, dict):
        return {
            "kind": "object",
            "value": {str(k): encode_runtime_value(v) for k, v in value.items()},
        }
    return {"kind": "other"}


def encode_field_constraints(c: FieldConstraints) -> dict[str, Any]:
    """Encode a `FieldConstraints` object to the `Oracle.lean` JSON representation."""
    out: dict[str, Any] = {}
    if c.min_value is not None:
        out["min_value"] = float(c.min_value) if isinstance(c.min_value, Decimal) else c.min_value
    if c.max_value is not None:
        out["max_value"] = float(c.max_value) if isinstance(c.max_value, Decimal) else c.max_value
    if c.min_length is not None:
        out["min_length"] = c.min_length
    if c.max_length is not None:
        out["max_length"] = c.max_length
    if c.min_items is not None:
        out["min_items"] = c.min_items
    if c.max_items is not None:
        out["max_items"] = c.max_items
    if c.pattern is not None:
        out["pattern"] = c.pattern
    if c.allowed_values is not None:
        out["allowed_values"] = [encode_runtime_value(v) for v in c.allowed_values]
    return out


def encode_field_spec(spec: FieldSpec) -> dict[str, Any]:
    """Encode a `FieldSpec` tree to the `Oracle.lean` JSON representation."""
    return {
        "name": spec.name,
        "field_type": spec.field_type.value,
        "required": spec.required,
        "nullable": spec.nullable,
        "constraints": encode_field_constraints(spec.constraints),
        "properties": (
            {k: encode_field_spec(v) for k, v in spec.properties.items()}
            if spec.properties is not None
            else None
        ),
        "property_order": list(spec.properties.keys()) if spec.properties is not None else None,
        "items": encode_field_spec(spec.items) if spec.items is not None else None,
        "allow_additional_fields": spec.allow_additional_fields,
    }


def encode_extraction_schema(schema: ExtractionSchema) -> dict[str, Any]:
    """Encode an `ExtractionSchema` to the `Oracle.lean` JSON representation."""
    return {
        "schema_id": schema.schema_id,
        "schema_version": schema.schema_version,
        "cardinality": schema.cardinality.value,
        "allow_additional_fields": schema.allow_additional_fields,
        "max_depth": schema.max_depth,
        "fields": {k: encode_field_spec(v) for k, v in schema.fields.items()},
        "field_order": list(schema.fields.keys()),
    }


def encode_record_fields(
    fields: dict[str, tuple[FieldPresence, Any]],
) -> dict[str, dict[str, Any]]:
    """Encode `DeterministicValidator.validate_record` input mapping for `Oracle.lean`."""
    return {
        fname: {"presence": presence.value, "value": encode_runtime_value(val)}
        for fname, (presence, val) in fields.items()
    }


def _leaf_spec(
    name: str,
    field_type: str,
    *,
    required: bool = True,
    nullable: bool = False,
    constraints: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
    items: dict[str, Any] | None = None,
    allow_additional_fields: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "field_type": field_type,
        "required": required,
        "nullable": nullable,
        "constraints": constraints or {},
        "properties": properties,
        "items": items,
        "allow_additional_fields": allow_additional_fields,
    }


def build_schema_well_formed_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated valid, invalid, and boundary schema dicts for Layer 1 & Layer 2 conformance."""
    valid_basic = {
        "schema_id": "inv",
        "schema_version": "1.0",
        "cardinality": "single",
        "allow_additional_fields": False,
        "max_depth": 16,
        "fields": {
            "title": _leaf_spec("title", "string", constraints={"min_length": 1, "max_length": 20}),
            "count": _leaf_spec("count", "integer", constraints={"min_value": 0, "max_value": 100}),
            "amount": _leaf_spec("amount", "decimal", constraints={"min_value": 0}),
            "active": _leaf_spec("active", "boolean"),
            "issued": _leaf_spec("issued", "date", required=False, nullable=True),
            "tags": _leaf_spec(
                "tags",
                "array",
                required=False,
                constraints={"min_items": 0, "max_items": 5},
                items=_leaf_spec("tags_item", "string"),
            ),
            "vendor": _leaf_spec(
                "vendor",
                "object",
                required=False,
                properties={"name": _leaf_spec("name", "string")},
            ),
        },
    }
    valid_open_multi = {
        "schema_id": "multi-open",
        "schema_version": "2.0",
        "cardinality": "multiple",
        "allow_additional_fields": True,
        "max_depth": 4,
        "fields": {
            "status": _leaf_spec(
                "status",
                "string",
                constraints={
                    "allowed_values": [
                        {"kind": "string", "value": "open"},
                        {"kind": "string", "value": "closed"},
                    ]
                },
            )
        },
    }
    deep_obj = _leaf_spec(
        "root",
        "object",
        properties={
            "child": _leaf_spec(
                "child",
                "object",
                properties={"leaf": _leaf_spec("leaf", "string")},
            )
        },
    )
    return [
        (valid_basic, True),
        (valid_open_multi, True),
        # Depth 3 with max_depth=3 -> valid
        (
            {
                "schema_id": "d3",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 3,
                "fields": {"root": deep_obj},
            },
            True,
        ),
        # Depth 3 with max_depth=2 -> invalid
        (
            {
                "schema_id": "d3-bad",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 2,
                "fields": {"root": deep_obj},
            },
            False,
        ),
        # Blank schema_id (whitespace-only)
        (
            {
                "schema_id": "   ",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"x": _leaf_spec("x", "string")},
            },
            False,
        ),
        # Blank schema_version
        (
            {
                "schema_id": "s",
                "schema_version": "  ",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"x": _leaf_spec("x", "string")},
            },
            False,
        ),
        # Empty fields
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {},
            },
            False,
        ),
        # max_depth < 1
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 0,
                "fields": {"x": _leaf_spec("x", "string")},
            },
            False,
        ),
        # Key != FieldSpec.name
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"x": _leaf_spec("different", "string")},
            },
            False,
        ),
        # Conflicting min_value > max_value
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {
                    "n": _leaf_spec("n", "integer", constraints={"min_value": 10, "max_value": 5})
                },
            },
            False,
        ),
        # Negative min_length
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"t": _leaf_spec("t", "string", constraints={"min_length": -1})},
            },
            False,
        ),
        # Conflicting min_length > max_length
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {
                    "t": _leaf_spec("t", "string", constraints={"min_length": 5, "max_length": 2})
                },
            },
            False,
        ),
        # Empty allowed_values
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"t": _leaf_spec("t", "string", constraints={"allowed_values": []})},
            },
            False,
        ),
        # Numeric constraint on boolean
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"b": _leaf_spec("b", "boolean", constraints={"min_value": 1})},
            },
            False,
        ),
        # String constraint on integer
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"n": _leaf_spec("n", "integer", constraints={"min_length": 1})},
            },
            False,
        ),
        # Array constraint on string
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"t": _leaf_spec("t", "string", constraints={"min_items": 1})},
            },
            False,
        ),
        # OBJECT without properties
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"o": _leaf_spec("o", "object", properties=None)},
            },
            False,
        ),
        # OBJECT with empty properties
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"o": _leaf_spec("o", "object", properties={})},
            },
            False,
        ),
        # ARRAY without items
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {"a": _leaf_spec("a", "array", items=None)},
            },
            False,
        ),
        # Non-ARRAY with items
        (
            {
                "schema_id": "s",
                "schema_version": "1.0",
                "cardinality": "single",
                "allow_additional_fields": False,
                "max_depth": 16,
                "fields": {
                    "t": _leaf_spec("t", "string", items=_leaf_spec("it", "string")),
                },
            },
            False,
        ),
    ]


def instantiate_python_schema_from_vector(vec: dict[str, Any]) -> ExtractionSchema:
    """Construct a Python `ExtractionSchema` directly from an oracle schema dict."""

    def _decode_runtime(raw: dict[str, Any]) -> Any:
        kind = raw["kind"]
        if kind == "null":
            return None
        if kind == "string":
            return str(raw["value"])
        if kind == "integer":
            return int(raw["value"])
        if kind == "decimal":
            return Decimal(str(raw["value"]))
        if kind == "boolean":
            return bool(raw["value"])
        if kind == "date":
            return date.fromisoformat(str(raw["value"]))
        raise ValueError(f"Unsupported allowed_values kind: {kind}")

    def _build_field(d: dict[str, Any]) -> FieldSpec:
        cdict = d.get("constraints") or {}
        allowed_raw = cdict.get("allowed_values")
        constraints = FieldConstraints(
            min_value=cdict.get("min_value"),
            max_value=cdict.get("max_value"),
            min_length=cdict.get("min_length"),
            max_length=cdict.get("max_length"),
            min_items=cdict.get("min_items"),
            max_items=cdict.get("max_items"),
            pattern=cdict.get("pattern"),
            allowed_values=(
                tuple(_decode_runtime(v) for v in allowed_raw) if allowed_raw is not None else None
            ),
        )
        props_raw = d.get("properties")
        items_raw = d.get("items")
        return FieldSpec(
            name=d["name"],
            field_type=FieldType(d["field_type"]),
            required=d.get("required", True),
            nullable=d.get("nullable", False),
            constraints=constraints,
            properties=(
                {k: _build_field(v) for k, v in props_raw.items()}
                if props_raw is not None
                else None
            ),
            items=_build_field(items_raw) if items_raw is not None else None,
            allow_additional_fields=d.get("allow_additional_fields", False),
        )

    return ExtractionSchema(
        schema_id=vec["schema_id"],
        schema_version=vec["schema_version"],
        fields={k: _build_field(v) for k, v in vec["fields"].items()},
        cardinality=ExtractionCardinality(vec.get("cardinality", "single")),
        allow_additional_fields=vec.get("allow_additional_fields", False),
        max_depth=vec.get("max_depth", 16),
    )


def build_validation_reference_schema(*, allow_additional_fields: bool = False) -> ExtractionSchema:
    """Rich multi-type schema covering scalars, bounds, enums, arrays, and nested objects."""
    return ExtractionSchema.from_dict(
        {
            "schema_id": "conformance-val",
            "schema_version": "1.0",
            "cardinality": "single",
            "allow_additional_fields": allow_additional_fields,
            "fields": {
                "code": {"type": "string", "required": True, "min_length": 3, "max_length": 12},
                "count": {"type": "integer", "required": True, "min_value": 1, "max_value": 100},
                "amount": {"type": "decimal", "required": True, "min_value": "0.00"},
                "active": {"type": "boolean", "required": True},
                "issued": {"type": "date", "required": True},
                "currency": {
                    "type": "string",
                    "required": True,
                    "allowed_values": ["USD", "EUR"],
                },
                "note": {"type": "string", "required": False, "nullable": True},
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
                        "tax_id": {"type": "string", "required": False, "nullable": True},
                    },
                },
            },
        }
    )
