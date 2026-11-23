"""Deterministic closed JSON Schema compiler for strict OpenAI-compatible structured outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import FieldType
from structa_maestro.schema.models import ExtractionSchema, FieldSpec

_SCHEMA_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class StrictSchemaIncompatibilityError(ValueError):
    """Raised when an ExtractionSchema cannot be compiled into a closed strict JSON Schema."""


@dataclass(frozen=True, slots=True)
class CompiledOutputSchema:
    """Compiled strict OpenAI-compatible json_schema payload metadata and schema body."""

    name: str
    strict: bool
    schema: dict[str, Any]

    def to_response_format(self) -> dict[str, Any]:
        """Return OpenAI-compatible Chat Completions response_format dictionary."""
        return {
            "type": "json_schema",
            "json_schema": {
                "name": self.name,
                "strict": self.strict,
                "schema": self.schema,
            },
        }


def _sanitize_schema_name(schema_id: str) -> str:
    cleaned = _SCHEMA_NAME_RE.sub("_", schema_id.strip()).strip("_")
    if not cleaned:
        cleaned = "extraction_output"
    return cleaned[:64]


def _has_open_additional_fields(schema: ExtractionSchema) -> str | None:
    if schema.allow_additional_fields:
        return schema.schema_id

    def _walk(spec: FieldSpec, path: str) -> str | None:
        if spec.allow_additional_fields:
            return path
        if spec.properties:
            for key, child in spec.properties.items():
                found = _walk(child, f"{path}.{key}")
                if found is not None:
                    return found
        if spec.items is not None:
            return _walk(spec.items, f"{path}[]")
        return None

    for fname, fspec in schema.fields.items():
        found = _walk(fspec, fname)
        if found is not None:
            return found
    return None


def _to_json_enum_value(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, date):
        return val.isoformat()
    return val


def _compile_value_schema(spec: FieldSpec, *, allow_null: bool) -> dict[str, Any]:
    """Compile a FieldSpec into a strict-mode-compatible JSON Schema for its value."""
    allowed = spec.constraints.allowed_values

    if spec.field_type == FieldType.STRING:
        node: dict[str, Any] = {"type": ["string", "null"] if allow_null else "string"}
        if allowed is not None:
            vals = [_to_json_enum_value(v) for v in allowed]
            if allow_null and None not in vals:
                vals.append(None)
            node["enum"] = vals
        return node

    if spec.field_type == FieldType.INTEGER:
        node = {"type": ["integer", "null"] if allow_null else "integer"}
        if allowed is not None:
            vals = [_to_json_enum_value(v) for v in allowed]
            if allow_null and None not in vals:
                vals.append(None)
            node["enum"] = vals
        return node

    if spec.field_type == FieldType.BOOLEAN:
        node = {"type": ["boolean", "null"] if allow_null else "boolean"}
        if allowed is not None:
            vals = [_to_json_enum_value(v) for v in allowed]
            if allow_null and None not in vals:
                vals.append(None)
            node["enum"] = vals
        return node

    if spec.field_type == FieldType.DATE:
        node = {
            "type": ["string", "null"] if allow_null else "string",
            "description": "ISO-8601 date string (YYYY-MM-DD).",
        }
        if allowed is not None:
            vals = [_to_json_enum_value(v) for v in allowed]
            if allow_null and None not in vals:
                vals.append(None)
            node["enum"] = vals
        return node

    if spec.field_type == FieldType.DECIMAL:
        if allowed is not None:
            str_vals = [str(v) for v in allowed]
            num_vals: list[float] = []
            for v in allowed:
                if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
                    num_vals.append(float(v))
            variants: list[dict[str, Any]] = [{"type": "string", "enum": str_vals}]
            if num_vals:
                variants.append({"type": "number", "enum": num_vals})
            if allow_null:
                variants.append({"type": "null"})
            return {"anyOf": variants}
        variants = [{"type": "string"}, {"type": "number"}]
        if allow_null:
            variants.append({"type": "null"})
        return {"anyOf": variants}

    if spec.field_type == FieldType.OBJECT:
        props = spec.properties or {}
        compiled_props: dict[str, Any] = {}
        req_keys: list[str] = []
        for prop_name, prop_spec in props.items():
            req_keys.append(prop_name)
            prop_nullable = prop_spec.nullable or not prop_spec.required
            compiled_props[prop_name] = _compile_value_schema(prop_spec, allow_null=prop_nullable)
        obj_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": req_keys,
            "properties": compiled_props,
        }
        if allow_null:
            return {"anyOf": [obj_schema, {"type": "null"}]}
        return obj_schema

    if spec.field_type == FieldType.ARRAY:
        item_spec = spec.items
        if item_spec is None:
            item_schema: dict[str, Any] = {"type": ["string", "null"]}
        else:
            item_schema = _compile_value_schema(item_spec, allow_null=item_spec.nullable)
        arr_schema: dict[str, Any] = {"type": "array", "items": item_schema}
        if allow_null:
            return {"anyOf": [arr_schema, {"type": "null"}]}
        return arr_schema

    return {"type": ["string", "null"] if allow_null else "string"}


def _compile_field_envelope(spec: FieldSpec) -> dict[str, Any]:
    """Compile one top-level declared field into its canonical extraction envelope schema."""
    evidence_item: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["quote", "relationship"],
        "properties": {
            "quote": {"type": "string"},
            "relationship": {
                "type": "string",
                "enum": ["direct", "inferred", "conflicting"],
            },
        },
    }
    envelope: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["presence", "value", "confidence", "ambiguous", "evidence"],
        "properties": {
            "presence": {"type": "string", "enum": ["present", "missing"]},
            # Always allow null on top-level field value because presence="missing" pairs with null.
            "value": _compile_value_schema(spec, allow_null=True),
            "confidence": {"type": ["number", "null"]},
            "ambiguous": {"type": "boolean"},
            "evidence": {"type": "array", "items": evidence_item},
        },
    }
    if spec.description:
        envelope["description"] = spec.description
    return envelope


def compile_extraction_output_schema(schema: ExtractionSchema) -> CompiledOutputSchema:
    """Compile ExtractionSchema into a deterministic closed strict JSON Schema envelope."""
    open_path = _has_open_additional_fields(schema)
    if open_path is not None:
        raise StrictSchemaIncompatibilityError(
            f"ExtractionSchema allows additional fields at {open_path!r}, which cannot be "
            "represented in closed strict json_schema mode."
        )

    field_names = list(schema.fields.keys())
    field_properties = {
        fname: _compile_field_envelope(fspec) for fname, fspec in schema.fields.items()
    }

    record_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fields"],
        "properties": {
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": field_names,
                "properties": field_properties,
            }
        },
    }

    root_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {
            "records": {
                "type": "array",
                "items": record_schema,
            }
        },
    }

    return CompiledOutputSchema(
        name=_sanitize_schema_name(schema.schema_id),
        strict=True,
        schema=root_schema,
    )
