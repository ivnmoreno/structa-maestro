"""Pure-introspection Pydantic v2 BaseModel to ExtractionSchema compiler (Spec 007)."""

from __future__ import annotations

import types
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from structa_maestro.contracts.enums import ExtractionCardinality, FieldType
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.schema.rules import ValidationRule

_UNION_ORIGINS = (Union, types.UnionType)


def _unwrap_annotated(annotation: Any) -> tuple[Any, list[Any]]:
    """Unwrap nested typing.Annotated wrappers, collecting all attached metadata."""
    meta: list[Any] = []
    current = annotation
    while get_origin(current) is Annotated:
        args = get_args(current)
        current = args[0]
        meta.extend(args[1:])
    return current, meta


def _unwrap_optional(annotation: Any) -> tuple[Any, bool, list[Any]]:
    """Unwrap Optional[T] / T | None and return (inner_type, nullable, extra_metadata)."""
    current, meta = _unwrap_annotated(annotation)
    origin = get_origin(current)
    if origin in _UNION_ORIGINS:
        args = get_args(current)
        non_none: list[Any] = []
        has_none = False
        for arg in args:
            if arg is type(None):
                has_none = True
            else:
                non_none.append(arg)
        if not has_none or len(non_none) != 1:
            raise SchemaDefinitionError(
                f"Unsupported Union type {annotation!r}: only Optional[T] (T | None) is supported."
            )
        inner, inner_meta = _unwrap_annotated(non_none[0])
        return inner, True, meta + inner_meta
    return current, False, meta


def _scalar_type_for_literal(value: Any, field_name: str) -> tuple[FieldType, Any]:
    if isinstance(value, bool):
        return FieldType.BOOLEAN, value
    if isinstance(value, int):
        return FieldType.INTEGER, value
    if isinstance(value, Decimal):
        return FieldType.DECIMAL, value
    if isinstance(value, float):
        try:
            dec = Decimal(str(value))
        except InvalidOperation as exc:
            raise SchemaDefinitionError(
                f"Non-finite float literal in field {field_name!r} is unsupported."
            ) from exc
        if dec.is_nan() or dec.is_infinite():
            raise SchemaDefinitionError(
                f"Non-finite float literal in field {field_name!r} is unsupported."
            )
        return FieldType.DECIMAL, dec
    if isinstance(value, str):
        return FieldType.STRING, value
    if isinstance(value, date) and not isinstance(value, datetime):
        return FieldType.DATE, value
    raise SchemaDefinitionError(
        f"Unsupported Literal/Enum member value type {type(value).__name__!r} "
        f"on field {field_name!r}."
    )


def _to_decimal_bound(raw: Any, field_name: str, label: str) -> Decimal:
    if isinstance(raw, bool):
        raise SchemaDefinitionError(f"Boolean {label} bound on field {field_name!r} is invalid.")
    if isinstance(raw, (int, Decimal)):
        dec = Decimal(raw) if isinstance(raw, int) else raw
    elif isinstance(raw, float):
        try:
            dec = Decimal(str(raw))
        except InvalidOperation as exc:
            raise SchemaDefinitionError(f"Invalid {label} bound on field {field_name!r}.") from exc
    else:
        raise SchemaDefinitionError(f"Invalid {label} bound on field {field_name!r}.")
    if dec.is_nan() or dec.is_infinite():
        raise SchemaDefinitionError(f"Non-finite {label} bound on field {field_name!r}.")
    return dec


def _extract_constraints(
    *,
    field_name: str,
    field_type: FieldType,
    metadata: Sequence[Any],
    allowed_values: tuple[Any, ...] | None,
) -> FieldConstraints:
    min_value: int | Decimal | None = None
    max_value: int | Decimal | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    pattern: str | None = None

    def _merge_min(cur: int | Decimal | None, nxt: int | Decimal) -> int | Decimal:
        return nxt if cur is None else max(cur, nxt)

    def _merge_max(cur: int | Decimal | None, nxt: int | Decimal) -> int | Decimal:
        return nxt if cur is None else min(cur, nxt)

    for item in metadata:
        if item is None:
            continue
        # Reject unsupported constraints that would otherwise be silently dropped
        if getattr(item, "multiple_of", None) is not None:
            raise SchemaDefinitionError(
                f"Constraint 'multiple_of' on field {field_name!r} is unsupported in "
                "ExtractionSchema."
            )

        ge = getattr(item, "ge", None)
        if ge is not None:
            if field_type == FieldType.INTEGER:
                if isinstance(ge, bool) or not isinstance(ge, int):
                    raise SchemaDefinitionError(
                        f"Integer field {field_name!r} requires an integer 'ge' bound."
                    )
                min_value = _merge_min(min_value, ge)
            elif field_type == FieldType.DECIMAL:
                min_value = _merge_min(min_value, _to_decimal_bound(ge, field_name, "ge"))
            else:
                raise SchemaDefinitionError(
                    f"Numeric 'ge' constraint is incompatible with field {field_name!r} "
                    f"({field_type.value})."
                )

        le = getattr(item, "le", None)
        if le is not None:
            if field_type == FieldType.INTEGER:
                if isinstance(le, bool) or not isinstance(le, int):
                    raise SchemaDefinitionError(
                        f"Integer field {field_name!r} requires an integer 'le' bound."
                    )
                max_value = _merge_max(max_value, le)
            elif field_type == FieldType.DECIMAL:
                max_value = _merge_max(max_value, _to_decimal_bound(le, field_name, "le"))
            else:
                raise SchemaDefinitionError(
                    f"Numeric 'le' constraint is incompatible with field {field_name!r} "
                    f"({field_type.value})."
                )

        gt = getattr(item, "gt", None)
        if gt is not None:
            if field_type == FieldType.INTEGER and not isinstance(gt, bool) and isinstance(gt, int):
                min_value = _merge_min(min_value, gt + 1)
            else:
                raise SchemaDefinitionError(
                    f"Strict exclusive 'gt' constraint on non-integer field {field_name!r} "
                    f"({field_type.value}) cannot be represented losslessly; use 'ge' instead."
                )

        lt = getattr(item, "lt", None)
        if lt is not None:
            if field_type == FieldType.INTEGER and not isinstance(lt, bool) and isinstance(lt, int):
                max_value = _merge_max(max_value, lt - 1)
            else:
                raise SchemaDefinitionError(
                    f"Strict exclusive 'lt' constraint on non-integer field {field_name!r} "
                    f"({field_type.value}) cannot be represented losslessly; use 'le' instead."
                )

        mlen = getattr(item, "min_length", None)
        if mlen is not None:
            if field_type == FieldType.STRING:
                min_length = mlen if min_length is None else max(min_length, mlen)
            elif field_type == FieldType.ARRAY:
                min_items = mlen if min_items is None else max(min_items, mlen)
            else:
                raise SchemaDefinitionError(
                    f"'min_length' constraint is incompatible with field {field_name!r} "
                    f"({field_type.value})."
                )

        xlen = getattr(item, "max_length", None)
        if xlen is not None:
            if field_type == FieldType.STRING:
                max_length = xlen if max_length is None else min(max_length, xlen)
            elif field_type == FieldType.ARRAY:
                max_items = xlen if max_items is None else min(max_items, xlen)
            else:
                raise SchemaDefinitionError(
                    f"'max_length' constraint is incompatible with field {field_name!r} "
                    f"({field_type.value})."
                )

        pat = getattr(item, "pattern", None)
        if pat is not None:
            if field_type != FieldType.STRING:
                raise SchemaDefinitionError(
                    f"'pattern' constraint is incompatible with field {field_name!r} "
                    f"({field_type.value})."
                )
            if pattern is not None and pattern != pat:
                raise SchemaDefinitionError(
                    f"Conflicting 'pattern' constraints defined on field {field_name!r}."
                )
            pattern = pat

    return FieldConstraints(
        min_value=min_value,
        max_value=max_value,
        min_length=min_length,
        max_length=max_length,
        min_items=min_items,
        max_items=max_items,
        pattern=pattern,
        allowed_values=allowed_values,
    )


def _compile_annotation(
    *,
    field_name: str,
    annotation: Any,
    required: bool,
    description: str | None,
    field_metadata: Sequence[Any],
    depth: int,
    max_depth: int,
    active_models: frozenset[type[BaseModel]],
) -> FieldSpec:
    if depth > max_depth:
        raise SchemaDefinitionError(
            f"Schema nesting depth at {field_name!r} exceeds maximum allowed depth ({max_depth})."
        )
    if annotation is None or annotation is Any:
        raise SchemaDefinitionError(
            f"Unsupported type annotation {annotation!r} on field {field_name!r}."
        )

    inner, nullable, ann_meta = _unwrap_optional(annotation)
    combined_meta = list(field_metadata) + ann_meta

    if inner is Any:
        raise SchemaDefinitionError(f"Unsupported 'Any' type annotation on field {field_name!r}.")

    origin = get_origin(inner)

    # Literal[...]
    if origin is Literal:
        lit_args = get_args(inner)
        if not lit_args:
            raise SchemaDefinitionError(f"Empty Literal annotation on field {field_name!r}.")
        non_null_vals: list[Any] = []
        for val in lit_args:
            if val is None:
                nullable = True
            else:
                non_null_vals.append(val)
        if not non_null_vals:
            raise SchemaDefinitionError(
                f"Literal[None] alone is not a valid field type on field {field_name!r}."
            )
        inferred_type: FieldType | None = None
        normalized_vals: list[Any] = []
        for val in non_null_vals:
            ft, norm_val = _scalar_type_for_literal(val, field_name)
            if inferred_type is None:
                inferred_type = ft
            elif inferred_type != ft:
                raise SchemaDefinitionError(
                    f"Mixed-type Literal on field {field_name!r} is unsupported "
                    f"({inferred_type.value} vs {ft.value})."
                )
            normalized_vals.append(norm_val)
        if inferred_type is None:
            raise SchemaDefinitionError(f"Unable to infer Literal type on field {field_name!r}.")
        constraints = _extract_constraints(
            field_name=field_name,
            field_type=inferred_type,
            metadata=combined_meta,
            allowed_values=tuple(normalized_vals),
        )
        return FieldSpec(
            name=field_name,
            field_type=inferred_type,
            required=required,
            nullable=nullable,
            description=description,
            constraints=constraints,
        )

    # list[T]
    if origin is list:
        args = get_args(inner)
        if len(args) != 1:
            raise SchemaDefinitionError(
                f"Unparameterized or invalid list type on field {field_name!r}; use list[T]."
            )
        item_spec = _compile_annotation(
            field_name=f"{field_name}[]",
            annotation=args[0],
            required=True,
            description=None,
            field_metadata=(),
            depth=depth + 1,
            max_depth=max_depth,
            active_models=active_models,
        )
        constraints = _extract_constraints(
            field_name=field_name,
            field_type=FieldType.ARRAY,
            metadata=combined_meta,
            allowed_values=None,
        )
        return FieldSpec(
            name=field_name,
            field_type=FieldType.ARRAY,
            required=required,
            nullable=nullable,
            description=description,
            constraints=constraints,
            items=item_spec,
        )

    # Reject other generic containers (dict, set, tuple, etc.)
    if origin is not None:
        raise SchemaDefinitionError(
            f"Unsupported generic container type {annotation!r} on field {field_name!r}."
        )

    if not isinstance(inner, type):
        raise SchemaDefinitionError(
            f"Unsupported type annotation {annotation!r} on field {field_name!r}."
        )

    # Nested Pydantic BaseModel
    if issubclass(inner, BaseModel):
        if inner in active_models:
            raise SchemaDefinitionError(
                f"Recursive/cyclic Pydantic model reference detected at field {field_name!r} "
                f"({inner.__name__})."
            )
        nested_fields = _compile_model_fields(
            inner,
            depth=depth + 1,
            max_depth=max_depth,
            active_models=active_models | {inner},
        )
        nested_extra = inner.model_config.get("extra") == "allow"
        constraints = _extract_constraints(
            field_name=field_name,
            field_type=FieldType.OBJECT,
            metadata=combined_meta,
            allowed_values=None,
        )
        return FieldSpec(
            name=field_name,
            field_type=FieldType.OBJECT,
            required=required,
            nullable=nullable,
            description=description,
            constraints=constraints,
            properties=nested_fields,
            allow_additional_fields=nested_extra,
        )

    # Enum subclass (checked before str/int because StrEnum/IntEnum subclass str/int)
    if issubclass(inner, Enum):
        members = list(inner)
        if not members:
            raise SchemaDefinitionError(
                f"Empty Enum {inner.__name__!r} on field {field_name!r} is unsupported."
            )
        enum_type: FieldType | None = None
        enum_values: list[Any] = []
        for member in members:
            ft, norm_val = _scalar_type_for_literal(member.value, field_name)
            if enum_type is None:
                enum_type = ft
            elif enum_type != ft:
                raise SchemaDefinitionError(
                    f"Mixed-type Enum {inner.__name__!r} on field {field_name!r} is unsupported."
                )
            enum_values.append(norm_val)
        if enum_type is None:
            raise SchemaDefinitionError(f"Unable to infer Enum type on field {field_name!r}.")
        constraints = _extract_constraints(
            field_name=field_name,
            field_type=enum_type,
            metadata=combined_meta,
            allowed_values=tuple(enum_values),
        )
        return FieldSpec(
            name=field_name,
            field_type=enum_type,
            required=required,
            nullable=nullable,
            description=description,
            constraints=constraints,
        )

    # Scalar types (order matters: bool before int; datetime rejected before date)
    if inner is bool:
        scalar_type = FieldType.BOOLEAN
    elif inner is int:
        scalar_type = FieldType.INTEGER
    elif inner is Decimal or inner is float:
        scalar_type = FieldType.DECIMAL
    elif inner is str:
        scalar_type = FieldType.STRING
    elif issubclass(inner, datetime):
        raise SchemaDefinitionError(
            f"Unsupported type 'datetime' on field {field_name!r}: Canonical Output Record "
            "Contract v1.0 supports 'date' only."
        )
    elif inner is date:
        scalar_type = FieldType.DATE
    else:
        raise SchemaDefinitionError(
            f"Unsupported Pydantic field type {inner.__name__!r} on field {field_name!r}."
        )

    constraints = _extract_constraints(
        field_name=field_name,
        field_type=scalar_type,
        metadata=combined_meta,
        allowed_values=None,
    )
    return FieldSpec(
        name=field_name,
        field_type=scalar_type,
        required=required,
        nullable=nullable,
        description=description,
        constraints=constraints,
    )


def _ensure_model_complete(model_cls: type[BaseModel]) -> None:
    if not getattr(model_cls, "__pydantic_complete__", True):
        try:
            rebuilt = model_cls.model_rebuild()
        except Exception as exc:
            raise SchemaDefinitionError(
                f"Pydantic model {model_cls.__name__!r} has unresolved forward references."
            ) from exc
        if rebuilt is False or not getattr(model_cls, "__pydantic_complete__", False):
            raise SchemaDefinitionError(
                f"Pydantic model {model_cls.__name__!r} is incomplete (unresolved forward refs)."
            )


def _compile_model_fields(
    model_cls: type[BaseModel],
    *,
    depth: int,
    max_depth: int,
    active_models: frozenset[type[BaseModel]],
) -> dict[str, FieldSpec]:
    _ensure_model_complete(model_cls)
    model_fields = model_cls.model_fields
    if not model_fields:
        raise SchemaDefinitionError(
            f"Pydantic model {model_cls.__name__!r} must define at least one field."
        )

    compiled: dict[str, FieldSpec] = {}
    for field_name, field_info in model_fields.items():
        if field_info.alias is not None and field_info.alias != field_name:
            raise SchemaDefinitionError(
                f"Custom field alias {field_info.alias!r} on field {field_name!r} is unsupported "
                "in ExtractionSchema.from_pydantic."
            )
        if field_info.validation_alias is not None and field_info.validation_alias != field_name:
            raise SchemaDefinitionError(
                f"Custom validation_alias on field {field_name!r} is unsupported in "
                "ExtractionSchema.from_pydantic."
            )

        compiled[field_name] = _compile_annotation(
            field_name=field_name,
            annotation=field_info.annotation,
            required=field_info.is_required(),
            description=field_info.description,
            field_metadata=field_info.metadata,
            depth=depth,
            max_depth=max_depth,
            active_models=active_models,
        )
    return compiled


def load_schema_from_pydantic(
    model_cls: type[BaseModel],
    *,
    schema_id: str | None = None,
    schema_version: str = "1.0",
    cardinality: ExtractionCardinality | str = ExtractionCardinality.SINGLE,
    rules: Sequence[ValidationRule] = (),
    allow_additional_fields: bool | None = None,
    max_depth: int = 16,
) -> ExtractionSchema:
    """Deterministically derive an immutable ExtractionSchema from a Pydantic v2 BaseModel."""
    if not (isinstance(model_cls, type) and issubclass(model_cls, BaseModel)):
        raise SchemaDefinitionError(
            "ExtractionSchema.from_pydantic requires a Pydantic v2 BaseModel subclass."
        )
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
        raise SchemaDefinitionError("ExtractionSchema.max_depth must be an integer >= 1.")

    resolved_schema_id = model_cls.__name__ if schema_id is None else schema_id
    if not isinstance(resolved_schema_id, str) or not resolved_schema_id.strip():
        raise SchemaDefinitionError("ExtractionSchema.schema_id must be a non-empty string.")

    if isinstance(cardinality, ExtractionCardinality):
        resolved_cardinality = cardinality
    elif isinstance(cardinality, str):
        try:
            resolved_cardinality = ExtractionCardinality(cardinality.strip().lower())
        except ValueError as exc:
            raise SchemaDefinitionError(f"Unsupported cardinality: {cardinality!r}") from exc
    else:
        raise SchemaDefinitionError(f"Unsupported cardinality: {cardinality!r}")

    if allow_additional_fields is None:
        resolved_extra = model_cls.model_config.get("extra") == "allow"
    elif isinstance(allow_additional_fields, bool):
        resolved_extra = allow_additional_fields
    else:
        raise SchemaDefinitionError("allow_additional_fields must be a boolean or None.")

    if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
        raise SchemaDefinitionError("rules must be a sequence of ValidationRule instances.")

    fields = _compile_model_fields(
        model_cls,
        depth=1,
        max_depth=max_depth,
        active_models=frozenset({model_cls}),
    )

    return ExtractionSchema(
        schema_id=resolved_schema_id.strip(),
        schema_version=schema_version,
        fields=fields,
        cardinality=resolved_cardinality,
        allow_additional_fields=resolved_extra,
        max_depth=max_depth,
        rules=tuple(rules),
    )


__all__ = ["load_schema_from_pydantic"]
