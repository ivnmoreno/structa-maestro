"""Declarative dict and JSON Schema loader raising SchemaDefinitionError on invalid input."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from structa_maestro.contracts.enums import ExtractionCardinality, FieldType
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.schema.rules import (
    ArithmeticRule,
    ArithmeticTerm,
    ComparisonRule,
    ConditionalPresenceKind,
    ConditionalPresenceRule,
    FieldCondition,
    GroupPresenceKind,
    GroupPresenceRule,
    RuleSeverity,
    ValidationRule,
    resolve_schema_field_spec,
)

_MAX_SCHEMA_JSON_BYTES = 2 * 1024 * 1024  # 2 MB safety limit for schema files

_TYPE_ALIASES: dict[str, FieldType] = {
    "string": FieldType.STRING,
    "str": FieldType.STRING,
    "integer": FieldType.INTEGER,
    "int": FieldType.INTEGER,
    "decimal": FieldType.DECIMAL,
    "number": FieldType.DECIMAL,
    "boolean": FieldType.BOOLEAN,
    "bool": FieldType.BOOLEAN,
    "date": FieldType.DATE,
    "array": FieldType.ARRAY,
    "object": FieldType.OBJECT,
}

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_id",
        "schema_version",
        "cardinality",
        "fields",
        "properties",
        "required",
        "allow_additional_fields",
        "additionalProperties",
        "max_depth",
        "title",
        "type",
        "$schema",
        "$id",
        "description",
        "rules",
    }
)

_ALLOWED_COMPARISON_KEYS = frozenset(
    {"rule_id", "kind", "left_field", "operator", "right_field", "severity", "message"}
)
_ALLOWED_ARITHMETIC_KEYS = frozenset(
    {"rule_id", "kind", "terms", "target_field", "tolerance", "severity", "message"}
)
_ALLOWED_TERM_KEYS = frozenset({"field", "sign", "item_field", "treat_missing_as_zero"})
_ALLOWED_CONDITIONAL_KEYS = frozenset(
    {"rule_id", "kind", "when", "target_fields", "severity", "message"}
)
_ALLOWED_CONDITION_KEYS = frozenset({"field", "operator", "value", "values"})
_ALLOWED_GROUP_KEYS = frozenset({"rule_id", "kind", "fields", "severity", "message"})

_ALLOWED_FIELD_KEYS = frozenset(
    {
        "type",
        "format",
        "required",
        "nullable",
        "description",
        "min_value",
        "max_value",
        "minimum",
        "maximum",
        "min_length",
        "max_length",
        "minLength",
        "maxLength",
        "min_items",
        "max_items",
        "minItems",
        "maxItems",
        "pattern",
        "allowed_values",
        "enum",
        "properties",
        "items",
        "allow_additional_fields",
        "additionalProperties",
        "default",
        "$ref",
    }
)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, val in pairs:
        if key in obj:
            raise SchemaDefinitionError(f"Duplicate key detected in schema definition: {key!r}")
        obj[key] = val
    return obj


def _coerce_numeric_bound(raw: Any, field_type: FieldType, bound_name: str) -> int | Decimal:
    if isinstance(raw, bool):
        raise SchemaDefinitionError(f"{bound_name} cannot be a boolean.")
    if field_type == FieldType.INTEGER:
        if isinstance(raw, int):
            return raw
        raise SchemaDefinitionError(f"{bound_name} for integer field must be an int.")
    if isinstance(raw, (int, Decimal)):
        return raw
    if isinstance(raw, (float, str)):
        try:
            return Decimal(str(raw))
        except InvalidOperation as exc:
            raise SchemaDefinitionError(f"Invalid numeric {bound_name}: {raw!r}") from exc
    raise SchemaDefinitionError(f"Invalid numeric {bound_name}: {raw!r}")


def _parse_field_type(raw_field: Mapping[str, Any], field_name: str) -> tuple[FieldType, bool]:
    raw_type = raw_field.get("type")
    inferred_nullable = False

    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if t != "null"]
        inferred_nullable = "null" in raw_type
        if len(non_null) != 1 or not isinstance(non_null[0], str):
            raise SchemaDefinitionError(
                f"Field {field_name!r} has unsupported union type: {raw_type!r}"
            )
        raw_type = non_null[0]

    if not isinstance(raw_type, str):
        raise SchemaDefinitionError(f"Field {field_name!r} must declare a valid string 'type'.")

    fmt = raw_field.get("format")
    if raw_type == "string" and fmt == "date":
        return FieldType.DATE, inferred_nullable

    mapped = _TYPE_ALIASES.get(raw_type.lower())
    if mapped is None:
        raise SchemaDefinitionError(
            f"Unsupported schema type {raw_type!r} for field {field_name!r}."
        )
    return mapped, inferred_nullable


def _parse_field_spec(
    name: str,
    raw: Any,
    *,
    required_override: bool | None = None,
    depth: int,
    max_depth: int,
) -> FieldSpec:
    if depth > max_depth:
        raise SchemaDefinitionError(
            f"Schema nesting depth at {name!r} exceeds maximum allowed depth ({max_depth})."
        )
    if not isinstance(raw, Mapping):
        raise SchemaDefinitionError(f"Field definition for {name!r} must be an object/mapping.")

    if "$ref" in raw:
        raise SchemaDefinitionError(
            f"Recursive or external '$ref' schema definitions are unsupported (field {name!r})."
        )

    unknown_keys = set(raw.keys()) - _ALLOWED_FIELD_KEYS
    if unknown_keys:
        raise SchemaDefinitionError(
            f"Unknown schema attributes on field {name!r}: {', '.join(sorted(unknown_keys))}"
        )

    field_type, inferred_nullable = _parse_field_type(raw, name)

    raw_required = raw.get("required")
    if field_type == FieldType.OBJECT and isinstance(raw_required, list):
        nested_required_names = set(raw_required)
        is_required = True if required_override is None else required_override
    else:
        nested_required_names = None
        if raw_required is None:
            is_required = True if required_override is None else required_override
        elif isinstance(raw_required, bool):
            is_required = raw_required
        else:
            raise SchemaDefinitionError(f"Field {name!r} 'required' attribute must be a boolean.")

    raw_nullable = raw.get("nullable")
    if raw_nullable is None:
        nullable = inferred_nullable
    elif isinstance(raw_nullable, bool):
        nullable = raw_nullable or inferred_nullable
    else:
        raise SchemaDefinitionError(f"Field {name!r} 'nullable' attribute must be a boolean.")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        raise SchemaDefinitionError(f"Field {name!r} 'description' must be a string.")

    # Constraints
    raw_min_val = raw.get("min_value", raw.get("minimum"))
    raw_max_val = raw.get("max_value", raw.get("maximum"))
    min_value = (
        _coerce_numeric_bound(raw_min_val, field_type, "min_value")
        if raw_min_val is not None
        else None
    )
    max_value = (
        _coerce_numeric_bound(raw_max_val, field_type, "max_value")
        if raw_max_val is not None
        else None
    )
    min_length = raw.get("min_length", raw.get("minLength"))
    max_length = raw.get("max_length", raw.get("maxLength"))
    min_items = raw.get("min_items", raw.get("minItems"))
    max_items = raw.get("max_items", raw.get("maxItems"))
    pattern = raw.get("pattern")
    raw_allowed = raw.get("allowed_values", raw.get("enum"))
    allowed_values: tuple[Any, ...] | None = None
    if raw_allowed is not None:
        if not isinstance(raw_allowed, (list, tuple)):
            raise SchemaDefinitionError(
                f"Field {name!r} 'allowed_values'/'enum' must be a list or tuple."
            )
        allowed_values = tuple(raw_allowed)

    constraints = FieldConstraints(
        min_value=min_value,
        max_value=max_value,
        min_length=min_length,
        max_length=max_length,
        min_items=min_items,
        max_items=max_items,
        pattern=pattern,
        allowed_values=allowed_values,
    )

    # Nested properties (for object)
    parsed_properties: dict[str, FieldSpec] | None = None
    if "properties" in raw and raw["properties"] is not None:
        raw_props = raw["properties"]
        if not isinstance(raw_props, Mapping) or not raw_props:
            raise SchemaDefinitionError(
                f"Object field {name!r} must define a non-empty 'properties' mapping."
            )
        parsed_properties = {}
        for prop_name, prop_def in raw_props.items():
            req_override = (
                prop_name in nested_required_names if nested_required_names is not None else None
            )
            parsed_properties[prop_name] = _parse_field_spec(
                prop_name,
                prop_def,
                required_override=req_override,
                depth=depth + 1,
                max_depth=max_depth,
            )

    # Array items
    parsed_items: FieldSpec | None = None
    if "items" in raw and raw["items"] is not None:
        parsed_items = _parse_field_spec(
            f"{name}[]",
            raw["items"],
            required_override=True,
            depth=depth + 1,
            max_depth=max_depth,
        )

    raw_addl = raw.get("allow_additional_fields", raw.get("additionalProperties", False))
    if not isinstance(raw_addl, bool):
        raise SchemaDefinitionError(
            f"Field {name!r} 'allow_additional_fields'/'additionalProperties' must be a boolean."
        )

    return FieldSpec(
        name=name,
        field_type=field_type,
        required=is_required,
        nullable=nullable,
        description=description,
        constraints=constraints,
        properties=parsed_properties,
        items=parsed_items,
        allow_additional_fields=raw_addl,
    )


def load_schema_from_dict(data: Mapping[str, Any]) -> ExtractionSchema:
    """Parse and validate a declarative dict or JSON Schema subset into ExtractionSchema."""
    if not isinstance(data, Mapping) or not data:
        raise SchemaDefinitionError("Schema definition must be a non-empty mapping.")

    unknown_top = set(data.keys()) - _ALLOWED_TOP_LEVEL_KEYS
    if unknown_top:
        raise SchemaDefinitionError(
            f"Unknown top-level schema attributes: {', '.join(sorted(unknown_top))}"
        )

    schema_id = data.get("schema_id") or data.get("title") or data.get("$id")
    if not isinstance(schema_id, str) or not schema_id.strip():
        raise SchemaDefinitionError("Schema must specify a non-empty 'schema_id' (or 'title').")

    schema_version = data.get("schema_version", "1.0")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise SchemaDefinitionError("Schema 'schema_version' must be a non-empty string.")

    raw_card = data.get("cardinality", ExtractionCardinality.SINGLE.value)
    try:
        cardinality = ExtractionCardinality(raw_card)
    except ValueError as exc:
        raise SchemaDefinitionError(
            f"Unsupported schema cardinality {raw_card!r}; expected 'single' or 'multiple'."
        ) from exc

    raw_max_depth = data.get("max_depth", 16)
    if isinstance(raw_max_depth, bool) or not isinstance(raw_max_depth, int) or raw_max_depth < 1:
        raise SchemaDefinitionError("Schema 'max_depth' must be an integer >= 1.")

    raw_addl = data.get("allow_additional_fields", data.get("additionalProperties", False))
    if not isinstance(raw_addl, bool):
        raise SchemaDefinitionError(
            "Schema 'allow_additional_fields'/'additionalProperties' must be a boolean."
        )

    raw_fields = data.get("fields") if "fields" in data else data.get("properties")
    if not isinstance(raw_fields, Mapping) or not raw_fields:
        raise SchemaDefinitionError(
            "Schema must define a non-empty 'fields' (or 'properties') mapping."
        )

    top_required = data.get("required")
    top_required_set: set[str] | None = None
    if top_required is not None:
        if not isinstance(top_required, list) or not all(isinstance(x, str) for x in top_required):
            raise SchemaDefinitionError("Top-level 'required' must be a list of field names.")
        top_required_set = set(top_required)

    parsed_fields: dict[str, FieldSpec] = {}
    for fname, fdef in raw_fields.items():
        if not isinstance(fname, str) or not fname.strip():
            raise SchemaDefinitionError("Schema field names must be non-empty strings.")
        req_override = fname in top_required_set if top_required_set is not None else None
        parsed_fields[fname] = _parse_field_spec(
            fname,
            fdef,
            required_override=req_override,
            depth=1,
            max_depth=raw_max_depth,
        )

    parsed_rules = _parse_rules(data.get("rules"), parsed_fields)

    return ExtractionSchema(
        schema_id=schema_id.strip(),
        schema_version=schema_version.strip(),
        fields=parsed_fields,
        cardinality=cardinality,
        allow_additional_fields=raw_addl,
        max_depth=raw_max_depth,
        rules=parsed_rules,
    )


def _coerce_condition_literal(raw: Any, field_type: FieldType) -> Any:
    if raw is None:
        return None
    if field_type == FieldType.DATE and isinstance(raw, str):
        try:
            return date.fromisoformat(raw.strip())
        except ValueError as exc:
            raise SchemaDefinitionError(
                f"Invalid ISO date literal in rule condition: {raw!r}"
            ) from exc
    if field_type == FieldType.DECIMAL and not isinstance(raw, bool):
        if isinstance(raw, Decimal):
            return raw
        if isinstance(raw, (int, float, str)):
            try:
                return Decimal(str(raw).strip())
            except InvalidOperation as exc:
                raise SchemaDefinitionError(
                    f"Invalid decimal literal in rule condition: {raw!r}"
                ) from exc
    return raw


def _parse_rules(
    raw_rules: Any,
    parsed_fields: Mapping[str, FieldSpec],
) -> tuple[ValidationRule, ...]:
    if raw_rules is None:
        return ()
    if not isinstance(raw_rules, list):
        raise SchemaDefinitionError("Schema 'rules' attribute must be a list of rule objects.")

    rules: list[ValidationRule] = []
    for idx, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, Mapping):
            raise SchemaDefinitionError(f"Rule at index {idx} must be a mapping/object.")
        raw_kind = raw_rule.get("kind")
        if not isinstance(raw_kind, str) or not raw_kind.strip():
            raise SchemaDefinitionError(f"Rule at index {idx} must specify a string 'kind'.")
        kind = raw_kind.strip().lower()
        severity = raw_rule.get("severity", RuleSeverity.ERROR)
        message = raw_rule.get("message")
        rule_id = raw_rule.get("rule_id")
        if not isinstance(rule_id, str):
            raise SchemaDefinitionError(f"Rule at index {idx} must specify a string 'rule_id'.")

        if kind == "comparison":
            unknown = set(raw_rule.keys()) - _ALLOWED_COMPARISON_KEYS
            if unknown:
                raise SchemaDefinitionError(
                    f"Unknown attributes on comparison rule {rule_id!r}: "
                    f"{', '.join(sorted(unknown))}"
                )
            rules.append(
                ComparisonRule(
                    rule_id=rule_id,
                    left_field=raw_rule.get("left_field", ""),
                    operator=raw_rule.get("operator", ""),
                    right_field=raw_rule.get("right_field", ""),
                    severity=severity,
                    message=message,
                )
            )

        elif kind == "arithmetic":
            unknown = set(raw_rule.keys()) - _ALLOWED_ARITHMETIC_KEYS
            if unknown:
                raise SchemaDefinitionError(
                    f"Unknown attributes on arithmetic rule {rule_id!r}: "
                    f"{', '.join(sorted(unknown))}"
                )
            raw_terms = raw_rule.get("terms")
            if not isinstance(raw_terms, list) or not raw_terms:
                raise SchemaDefinitionError(
                    f"Arithmetic rule {rule_id!r} must define a non-empty 'terms' list."
                )
            parsed_terms: list[ArithmeticTerm] = []
            for t_idx, raw_term in enumerate(raw_terms):
                if not isinstance(raw_term, Mapping):
                    raise SchemaDefinitionError(
                        f"Arithmetic rule {rule_id!r} term #{t_idx} must be an object."
                    )
                t_unknown = set(raw_term.keys()) - _ALLOWED_TERM_KEYS
                if t_unknown:
                    raise SchemaDefinitionError(
                        f"Unknown attributes on arithmetic term in {rule_id!r}: "
                        f"{', '.join(sorted(t_unknown))}"
                    )
                parsed_terms.append(
                    ArithmeticTerm(
                        field=raw_term.get("field", ""),
                        sign=raw_term.get("sign", 1),
                        item_field=raw_term.get("item_field"),
                        treat_missing_as_zero=raw_term.get("treat_missing_as_zero", False),
                    )
                )
            rules.append(
                ArithmeticRule(
                    rule_id=rule_id,
                    terms=tuple(parsed_terms),
                    target_field=raw_rule.get("target_field", ""),
                    tolerance=raw_rule.get("tolerance", Decimal("0")),
                    severity=severity,
                    message=message,
                )
            )

        elif kind in (
            ConditionalPresenceKind.REQUIRED_IF.value,
            ConditionalPresenceKind.FORBIDDEN_IF.value,
        ):
            unknown = set(raw_rule.keys()) - _ALLOWED_CONDITIONAL_KEYS
            if unknown:
                raise SchemaDefinitionError(
                    f"Unknown attributes on conditional rule {rule_id!r}: "
                    f"{', '.join(sorted(unknown))}"
                )
            raw_when = raw_rule.get("when")
            if not isinstance(raw_when, Mapping):
                raise SchemaDefinitionError(
                    f"Conditional rule {rule_id!r} must define a 'when' condition object."
                )
            w_unknown = set(raw_when.keys()) - _ALLOWED_CONDITION_KEYS
            if w_unknown:
                raise SchemaDefinitionError(
                    f"Unknown attributes on condition in rule {rule_id!r}: "
                    f"{', '.join(sorted(w_unknown))}"
                )
            cond_field = raw_when.get("field", "")
            cond_spec = (
                resolve_schema_field_spec(parsed_fields, cond_field.strip())
                if isinstance(cond_field, str) and cond_field.strip()
                else None
            )
            cond_val = raw_when.get("value")
            if cond_val is not None and cond_spec is not None:
                cond_val = _coerce_condition_literal(cond_val, cond_spec.field_type)
            raw_vals = raw_when.get("values")
            cond_vals: tuple[Any, ...] | None = None
            if raw_vals is not None:
                if not isinstance(raw_vals, (list, tuple)):
                    raise SchemaDefinitionError(
                        f"Condition 'values' in rule {rule_id!r} must be a list."
                    )
                cond_vals = tuple(
                    _coerce_condition_literal(v, cond_spec.field_type)
                    if cond_spec is not None
                    else v
                    for v in raw_vals
                )
            when = FieldCondition(
                field=cond_field,
                operator=raw_when.get("operator", ""),
                value=cond_val,
                values=cond_vals,
            )
            raw_targets = raw_rule.get("target_fields")
            if not isinstance(raw_targets, list) or not all(
                isinstance(t, str) for t in raw_targets
            ):
                raise SchemaDefinitionError(
                    f"Conditional rule {rule_id!r} 'target_fields' must be a list of strings."
                )
            rules.append(
                ConditionalPresenceRule(
                    rule_id=rule_id,
                    kind=ConditionalPresenceKind(kind),
                    when=when,
                    target_fields=tuple(raw_targets),
                    severity=severity,
                    message=message,
                )
            )

        elif kind in (
            GroupPresenceKind.MUTUALLY_EXCLUSIVE.value,
            GroupPresenceKind.AT_LEAST_ONE_OF.value,
        ):
            unknown = set(raw_rule.keys()) - _ALLOWED_GROUP_KEYS
            if unknown:
                raise SchemaDefinitionError(
                    f"Unknown attributes on group rule {rule_id!r}: {', '.join(sorted(unknown))}"
                )
            raw_group = raw_rule.get("fields")
            if not isinstance(raw_group, list) or not all(isinstance(f, str) for f in raw_group):
                raise SchemaDefinitionError(
                    f"Group rule {rule_id!r} 'fields' must be a list of strings."
                )
            rules.append(
                GroupPresenceRule(
                    rule_id=rule_id,
                    kind=GroupPresenceKind(kind),
                    fields=tuple(raw_group),
                    severity=severity,
                    message=message,
                )
            )

        else:
            raise SchemaDefinitionError(
                f"Unsupported validation rule kind {raw_kind!r} on rule {rule_id!r}."
            )

    return tuple(rules)


def load_schema_from_json_file(path: str | Path) -> ExtractionSchema:
    """Load and validate an ExtractionSchema from a JSON file with duplicate-key rejection."""
    schema_path = Path(path)
    if not schema_path.exists():
        raise SchemaDefinitionError(f"Schema file not found: {schema_path.name}")
    if not schema_path.is_file():
        raise SchemaDefinitionError(f"Schema path is not a regular file: {schema_path.name}")
    if schema_path.stat().st_size > _MAX_SCHEMA_JSON_BYTES:
        raise SchemaDefinitionError(
            f"Schema file exceeds maximum allowed size ({_MAX_SCHEMA_JSON_BYTES} bytes)."
        )
    try:
        raw_text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaDefinitionError(f"Unable to read schema file: {exc}") from exc

    try:
        parsed = json.loads(raw_text, object_pairs_hook=_reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise SchemaDefinitionError(
            f"Invalid JSON syntax in schema file at line {exc.lineno}, col {exc.colno}."
        ) from exc

    if not isinstance(parsed, Mapping):
        raise SchemaDefinitionError("Schema JSON root must be an object.")
    return load_schema_from_dict(parsed)
