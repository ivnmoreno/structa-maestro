"""Strict, non-coercing opt-in binding of canonical ExtractedRecords to Pydantic v2 models."""

from __future__ import annotations

import types
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError

from structa_maestro.contracts.enums import FieldPresence, ProcessingStatus, ValidationStatus
from structa_maestro.contracts.models import ExtractedRecord, ExtractionResult
from structa_maestro.exceptions import RecordBindingError

TModel = TypeVar("TModel", bound=BaseModel)
_UNION_ORIGINS = (Union, types.UnionType)


def _unwrap_target_annotation(annotation: Any) -> Any:
    """Unwrap Annotated[...] and Optional[T] / T | None to inspect the concrete target type."""
    current = annotation
    while get_origin(current) is Annotated:
        current = get_args(current)[0]
    origin = get_origin(current)
    if origin in _UNION_ORIGINS:
        non_none = [arg for arg in get_args(current) if arg is not type(None)]
        if len(non_none) == 1:
            current = non_none[0]
            while get_origin(current) is Annotated:
                current = get_args(current)[0]
    return current


def _adapt_value_for_annotation(value: Any, annotation: Any) -> Any:
    """Adapt validated canonical values for strict Pydantic v2 validation without coercion.

    Only performs lossless structural/representation adaptations required by Pydantic's strict mode:
    - Exact-type primitive -> Enum member matching ``member.value == value`` and exact runtime type.
    - Finite ``Decimal`` -> ``float`` for fields explicitly annotated as ``float``.
    - Non-bool ``int`` -> ``Decimal`` for fields explicitly annotated as ``Decimal``.
    - Recursive adaptation across nested ``BaseModel`` dicts and ``list[T]`` items.
    Strings, booleans, and mismatched types are never coerced.
    """
    if value is None:
        return None

    target = _unwrap_target_annotation(annotation)
    origin = get_origin(target)

    if origin is list:
        if isinstance(value, list):
            args = get_args(target)
            if len(args) == 1:
                return [_adapt_value_for_annotation(item, args[0]) for item in value]
        return value

    if isinstance(target, type):
        if issubclass(target, BaseModel):
            if isinstance(value, dict):
                return _adapt_model_dict(value, target)
            return value

        if issubclass(target, Enum):
            if isinstance(value, target):
                return value
            for member in target:
                if type(member.value) is type(value) and member.value == value:
                    return member
            return value

        if target is float:
            if isinstance(value, Decimal) and not value.is_nan() and not value.is_infinite():
                return float(value)
            return value

        if target is Decimal:
            if not isinstance(value, bool) and isinstance(value, int):
                return Decimal(value)
            return value

    return value


def _adapt_model_dict(raw_dict: dict[str, Any], model_cls: type[BaseModel]) -> dict[str, Any]:
    adapted: dict[str, Any] = {}
    model_fields = model_cls.model_fields
    for key, val in raw_dict.items():
        if key in model_fields:
            adapted[key] = _adapt_value_for_annotation(val, model_fields[key].annotation)
        else:
            adapted[key] = val
    return adapted


def _build_record_payload(record: ExtractedRecord, model_cls: type[BaseModel]) -> dict[str, Any]:
    """Extract a binding payload preserving the missing-versus-null distinction."""
    payload: dict[str, Any] = {}
    model_fields = model_cls.model_fields
    for field_name, extracted_field in record.fields.items():
        if extracted_field.presence == FieldPresence.MISSING:
            continue
        if field_name in model_fields:
            payload[field_name] = _adapt_value_for_annotation(
                extracted_field.value,
                model_fields[field_name].annotation,
            )
        else:
            payload[field_name] = extracted_field.value
    return payload


def _sanitize_pydantic_errors(exc: ValidationError) -> tuple[str, ...]:
    """Extract field locations and error types without ever echoing raw input values."""
    issues: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        loc_parts = [str(part) for part in err.get("loc", ())]
        loc = ".".join(loc_parts) if loc_parts else "<root>"
        err_type = str(err.get("type", "validation_error"))
        # Use error type rather than raw msg if msg could echo unexpected data
        issues.append(f"{loc}: {err_type}")
    return tuple(issues)


def _validate_model_cls(model_cls: Any) -> None:
    if not (isinstance(model_cls, type) and issubclass(model_cls, BaseModel)):
        raise RecordBindingError("model_cls must be a Pydantic v2 BaseModel subclass.")


def bind_record(record: ExtractedRecord, model_cls: type[TModel]) -> TModel:
    """Strictly materialize one completed, schema-valid ExtractedRecord into a Pydantic model.

    Raises ``RecordBindingError`` (with privacy-safe field paths and zero raw values) if the
    record is not completed/valid or fails strict Pydantic validation.
    """
    _validate_model_cls(model_cls)
    if not isinstance(record, ExtractedRecord):
        raise RecordBindingError("record must be an ExtractedRecord instance.")

    if record.status != ProcessingStatus.COMPLETED:
        raise RecordBindingError(
            f"Cannot bind record {record.record_id!r}: processing status is "
            f"{record.status.value!r} (expected 'completed').",
            record_id=record.record_id,
        )
    if record.validation.status != ValidationStatus.VALID:
        raise RecordBindingError(
            f"Cannot bind record {record.record_id!r}: validation status is "
            f"{record.validation.status.value!r} (expected 'valid').",
            record_id=record.record_id,
        )

    payload = _build_record_payload(record, model_cls)
    try:
        return model_cls.model_validate(payload, strict=True)
    except ValidationError as exc:
        issues = _sanitize_pydantic_errors(exc)
        summary = "; ".join(issues) if issues else "strict_validation_failed"
        raise RecordBindingError(
            f"Strict binding failed for record {record.record_id!r} against "
            f"{model_cls.__name__}: {summary}.",
            record_id=record.record_id,
            issues=issues,
        ) from None


def bind_records(
    result: ExtractionResult,
    model_cls: type[TModel],
    *,
    only_valid: bool = False,
) -> list[TModel]:
    """Strictly materialize records from an ExtractionResult into typed Pydantic model instances.

    - When ``only_valid=False`` (default): requires ``result.status == COMPLETED`` and
      ``result.validation.status == VALID``, raising ``RecordBindingError`` otherwise.
    - When ``only_valid=True``: binds only records with ``status == COMPLETED`` and
      ``validation.status == VALID`` in exact canonical order, skipping invalid/failed records.
    """
    _validate_model_cls(model_cls)
    if not isinstance(result, ExtractionResult):
        raise RecordBindingError("result must be an ExtractionResult instance.")
    if not isinstance(only_valid, bool):
        raise RecordBindingError("only_valid must be a boolean.")

    if not only_valid:
        if result.status != ProcessingStatus.COMPLETED:
            raise RecordBindingError(
                f"Cannot bind ExtractionResult: processing status is "
                f"{result.status.value!r} (expected 'completed')."
            )
        if result.validation.status != ValidationStatus.VALID:
            raise RecordBindingError(
                f"Cannot bind ExtractionResult: validation status is "
                f"{result.validation.status.value!r} (expected 'valid')."
            )
        return [bind_record(rec, model_cls) for rec in result.records]

    bound: list[TModel] = []
    for rec in result.records:
        if (
            rec.status == ProcessingStatus.COMPLETED
            and rec.validation.status == ValidationStatus.VALID
        ):
            bound.append(bind_record(rec, model_cls))
    return bound
