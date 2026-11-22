"""User-defined ExtractionSchema domain models (data-model.md Section 2.4)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from structa_maestro.contracts.enums import ExtractionCardinality, FieldType
from structa_maestro.exceptions import SchemaDefinitionError

_NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.DECIMAL})
_DEFAULT_MAX_DEPTH = 16


@dataclass(frozen=True, slots=True)
class FieldConstraints:
    """Optional single-field validation constraints (data-model.md Section 2.4)."""

    min_value: int | Decimal | None = None
    max_value: int | Decimal | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    pattern: str | None = None
    allowed_values: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        if isinstance(self.min_value, bool) or isinstance(self.max_value, bool):
            raise SchemaDefinitionError("min_value/max_value cannot be booleans.")
        if (
            self.min_value is not None
            and self.max_value is not None
            and self.min_value > self.max_value
        ):
            raise SchemaDefinitionError(
                f"Conflicting numeric bounds: min_value ({self.min_value}) > "
                f"max_value ({self.max_value})."
            )
        if self.min_length is not None and (
            isinstance(self.min_length, bool) or self.min_length < 0
        ):
            raise SchemaDefinitionError("min_length must be an integer >= 0.")
        if self.max_length is not None and (
            isinstance(self.max_length, bool) or self.max_length < 0
        ):
            raise SchemaDefinitionError("max_length must be an integer >= 0.")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise SchemaDefinitionError(
                f"Conflicting length bounds: min_length ({self.min_length}) > "
                f"max_length ({self.max_length})."
            )
        if self.min_items is not None and (isinstance(self.min_items, bool) or self.min_items < 0):
            raise SchemaDefinitionError("min_items must be an integer >= 0.")
        if self.max_items is not None and (isinstance(self.max_items, bool) or self.max_items < 0):
            raise SchemaDefinitionError("max_items must be an integer >= 0.")
        if (
            self.min_items is not None
            and self.max_items is not None
            and self.min_items > self.max_items
        ):
            raise SchemaDefinitionError(
                f"Conflicting array bounds: min_items ({self.min_items}) > "
                f"max_items ({self.max_items})."
            )
        if self.pattern is not None:
            if not isinstance(self.pattern, str):
                raise SchemaDefinitionError("pattern constraint must be a string.")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise SchemaDefinitionError(f"Invalid regex pattern constraint: {exc}") from exc
        if self.allowed_values is not None and len(self.allowed_values) == 0:
            raise SchemaDefinitionError("allowed_values cannot be an empty collection.")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Definition of a single target field in an ExtractionSchema (data-model.md Section 2.4)."""

    name: str
    field_type: FieldType
    required: bool = True
    nullable: bool = False
    description: str | None = None
    constraints: FieldConstraints = field(default_factory=FieldConstraints)
    properties: dict[str, FieldSpec] | None = None
    items: FieldSpec | None = None
    allow_additional_fields: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise SchemaDefinitionError("FieldSpec.name must be a non-empty string.")
        if not isinstance(self.field_type, FieldType):
            raise SchemaDefinitionError(f"Unsupported field_type: {self.field_type!r}")
        if not isinstance(self.required, bool):
            raise SchemaDefinitionError("FieldSpec.required must be a boolean.")
        if not isinstance(self.nullable, bool):
            raise SchemaDefinitionError("FieldSpec.nullable must be a boolean.")
        if not isinstance(self.allow_additional_fields, bool):
            raise SchemaDefinitionError("FieldSpec.allow_additional_fields must be a boolean.")

        c = self.constraints
        if (c.min_value is not None or c.max_value is not None) and (
            self.field_type not in _NUMERIC_TYPES
        ):
            raise SchemaDefinitionError(
                "Numeric min_value/max_value constraints are incompatible with "
                f"{self.field_type.value!r}."
            )
        if (c.min_length is not None or c.max_length is not None or c.pattern is not None) and (
            self.field_type != FieldType.STRING
        ):
            raise SchemaDefinitionError(
                f"String constraints are incompatible with field type {self.field_type.value!r}."
            )
        if (c.min_items is not None or c.max_items is not None) and (
            self.field_type != FieldType.ARRAY
        ):
            raise SchemaDefinitionError(
                f"Array item count constraints are incompatible with {self.field_type.value!r}."
            )

        if self.field_type == FieldType.OBJECT:
            if not self.properties:
                raise SchemaDefinitionError(
                    f"Object field {self.name!r} must define non-empty properties."
                )
            for prop_key, prop_spec in self.properties.items():
                if prop_spec.name != prop_key:
                    raise SchemaDefinitionError(
                        f"Property key {prop_key!r} does not match "
                        f"FieldSpec.name {prop_spec.name!r}."
                    )
        elif self.properties is not None:
            raise SchemaDefinitionError(
                f"Non-object field {self.name!r} cannot define nested properties."
            )

        if self.field_type == FieldType.ARRAY:
            if self.items is None:
                raise SchemaDefinitionError(f"Array field {self.name!r} must define items.")
        elif self.items is not None:
            raise SchemaDefinitionError(f"Non-array field {self.name!r} cannot define items.")


@dataclass(frozen=True, slots=True)
class ExtractionSchema:
    """User-defined structured extraction schema (data-model.md Section 2.4)."""

    schema_id: str
    schema_version: str
    fields: dict[str, FieldSpec]
    cardinality: ExtractionCardinality = ExtractionCardinality.SINGLE
    allow_additional_fields: bool = False
    max_depth: int = _DEFAULT_MAX_DEPTH

    def __post_init__(self) -> None:
        if not isinstance(self.schema_id, str) or not self.schema_id.strip():
            raise SchemaDefinitionError("ExtractionSchema.schema_id must be a non-empty string.")
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise SchemaDefinitionError(
                "ExtractionSchema.schema_version must be a non-empty string."
            )
        if not isinstance(self.cardinality, ExtractionCardinality):
            raise SchemaDefinitionError(f"Unsupported cardinality: {self.cardinality!r}")
        if not isinstance(self.allow_additional_fields, bool):
            raise SchemaDefinitionError("ExtractionSchema.allow_additional_fields must be boolean.")
        if isinstance(self.max_depth, bool) or self.max_depth < 1:
            raise SchemaDefinitionError("ExtractionSchema.max_depth must be an integer >= 1.")
        if not self.fields:
            raise SchemaDefinitionError("ExtractionSchema.fields must define at least one field.")

        for key, spec in self.fields.items():
            if spec.name != key:
                raise SchemaDefinitionError(
                    f"Schema field key {key!r} does not match FieldSpec.name {spec.name!r}."
                )
            _validate_depth(spec, depth=1, max_depth=self.max_depth, seen_ids=set())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExtractionSchema:
        """Parse and validate a declarative dict or JSON Schema subset into ExtractionSchema."""
        from structa_maestro.schema.loader import load_schema_from_dict

        return load_schema_from_dict(data)

    @classmethod
    def from_json_file(cls, path: str | Path) -> ExtractionSchema:
        """Load and validate an ExtractionSchema from a JSON file."""
        from structa_maestro.schema.loader import load_schema_from_json_file

        return load_schema_from_json_file(path)


def _validate_depth(
    spec: FieldSpec,
    *,
    depth: int,
    max_depth: int,
    seen_ids: set[int],
) -> None:
    obj_id = id(spec)
    if obj_id in seen_ids:
        raise SchemaDefinitionError(
            f"Recursive/cyclic schema definition detected at {spec.name!r}."
        )
    if depth > max_depth:
        raise SchemaDefinitionError(
            f"Schema nesting depth at {spec.name!r} exceeds maximum allowed depth ({max_depth})."
        )
    next_seen = seen_ids | {obj_id}
    if spec.properties:
        for child in spec.properties.values():
            _validate_depth(child, depth=depth + 1, max_depth=max_depth, seen_ids=next_seen)
    if spec.items is not None:
        _validate_depth(spec.items, depth=depth + 1, max_depth=max_depth, seen_ids=next_seen)
