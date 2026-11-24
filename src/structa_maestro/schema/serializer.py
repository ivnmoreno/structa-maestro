"""Lossless declarative dictionary and JSON serialization for ExtractionSchema (Spec 007)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import FieldType
from structa_maestro.schema.models import ExtractionSchema, FieldSpec
from structa_maestro.schema.rules import (
    ArithmeticRule,
    ComparisonRule,
    ConditionalPresenceRule,
    ValidationRule,
)


def _encode_json_scalar(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _serialize_field_spec(spec: FieldSpec) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": spec.field_type.value,
        "required": spec.required,
        "nullable": spec.nullable,
    }
    if spec.description is not None:
        out["description"] = spec.description

    c = spec.constraints
    if c.min_value is not None:
        out["min_value"] = _encode_json_scalar(c.min_value)
    if c.max_value is not None:
        out["max_value"] = _encode_json_scalar(c.max_value)
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
        out["allowed_values"] = [_encode_json_scalar(v) for v in c.allowed_values]

    if spec.field_type == FieldType.OBJECT and spec.properties is not None:
        out["properties"] = {
            prop_name: _serialize_field_spec(prop_spec)
            for prop_name, prop_spec in spec.properties.items()
        }
        out["allow_additional_fields"] = spec.allow_additional_fields

    if spec.field_type == FieldType.ARRAY and spec.items is not None:
        out["items"] = _serialize_field_spec(spec.items)

    return out


def _serialize_rule(rule: ValidationRule) -> dict[str, Any]:
    if isinstance(rule, ComparisonRule):
        out: dict[str, Any] = {
            "rule_id": rule.rule_id,
            "kind": "comparison",
            "left_field": rule.left_field,
            "operator": rule.operator.value,
            "right_field": rule.right_field,
            "severity": rule.severity.value,
        }
        if rule.message is not None:
            out["message"] = rule.message
        return out

    if isinstance(rule, ArithmeticRule):
        terms: list[dict[str, Any]] = []
        for term in rule.terms:
            t_dict: dict[str, Any] = {
                "field": term.field,
                "sign": term.sign,
                "treat_missing_as_zero": term.treat_missing_as_zero,
            }
            if term.item_field is not None:
                t_dict["item_field"] = term.item_field
            terms.append(t_dict)
        out = {
            "rule_id": rule.rule_id,
            "kind": "arithmetic",
            "terms": terms,
            "target_field": rule.target_field,
            "tolerance": str(rule.tolerance),
            "severity": rule.severity.value,
        }
        if rule.message is not None:
            out["message"] = rule.message
        return out

    if isinstance(rule, ConditionalPresenceRule):
        when_dict: dict[str, Any] = {
            "field": rule.when.field,
            "operator": rule.when.operator.value,
        }
        if rule.when.value is not None:
            when_dict["value"] = _encode_json_scalar(rule.when.value)
        if rule.when.values is not None:
            when_dict["values"] = [_encode_json_scalar(v) for v in rule.when.values]
        out = {
            "rule_id": rule.rule_id,
            "kind": rule.kind.value,
            "when": when_dict,
            "target_fields": list(rule.target_fields),
            "severity": rule.severity.value,
        }
        if rule.message is not None:
            out["message"] = rule.message
        return out

    # GroupPresenceRule
    out = {
        "rule_id": rule.rule_id,
        "kind": rule.kind.value,
        "fields": list(rule.fields),
        "severity": rule.severity.value,
    }
    if rule.message is not None:
        out["message"] = rule.message
    return out


def serialize_schema_to_dict(schema: ExtractionSchema) -> dict[str, Any]:
    """Serialize an ExtractionSchema into a JSON-safe declarative dictionary."""
    out: dict[str, Any] = {
        "schema_id": schema.schema_id,
        "schema_version": schema.schema_version,
        "cardinality": schema.cardinality.value,
        "allow_additional_fields": schema.allow_additional_fields,
        "max_depth": schema.max_depth,
        "fields": {
            field_name: _serialize_field_spec(spec) for field_name, spec in schema.fields.items()
        },
    }
    if schema.rules:
        out["rules"] = [_serialize_rule(rule) for rule in schema.rules]
    return out


def serialize_schema_to_json(schema: ExtractionSchema, *, indent: int | None = None) -> str:
    """Serialize an ExtractionSchema into a declarative JSON string."""
    return json.dumps(serialize_schema_to_dict(schema), indent=indent, ensure_ascii=False)
