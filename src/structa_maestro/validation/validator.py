"""Dedicated non-coercing deterministic schema validator (FR-024..FR-026, Clarification Q4)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import (
    ExtractionCardinality,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ValidationStatus,
)
from structa_maestro.contracts.models import DiagnosticIssue, ValidationOutcome
from structa_maestro.schema.models import ExtractionSchema, FieldSpec
from structa_maestro.validation.cross_field import evaluate_cross_field_rules


@dataclass(frozen=True, slots=True)
class RecordValidationResult:
    """Deterministic validation outcomes for one record and each of its fields."""

    record_outcome: ValidationOutcome
    field_outcomes: dict[str, ValidationOutcome]
    warnings: tuple[DiagnosticIssue, ...] = ()


def _make_error(
    *,
    code: str,
    message: str,
    record_id: str | None,
    field_path: str | None,
    expected: str | None = None,
    actual: str | None = None,
) -> DiagnosticIssue:
    return DiagnosticIssue(
        code=code,
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.VALIDATION,
        message=message,
        record_id=record_id,
        field_path=field_path,
        expected=expected,
        actual=actual,
    )


def _safe_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


class DeterministicValidator:
    """Evaluates extracted records strictly against ExtractionSchema with zero value coercion."""

    def validate_record(
        self,
        fields: Mapping[str, tuple[FieldPresence, Any]],
        schema: ExtractionSchema,
        *,
        record_id: str,
    ) -> RecordValidationResult:
        """Deterministically validate one record's fields against schema."""
        field_outcomes: dict[str, ValidationOutcome] = {}
        all_errors: list[DiagnosticIssue] = []

        # 1. Validate every declared schema field in deterministic order
        for field_name, spec in schema.fields.items():
            presence, value = fields.get(field_name, (FieldPresence.MISSING, None))
            errs = self._validate_value(
                spec=spec,
                presence=presence,
                value=value,
                record_id=record_id,
                field_path=field_name,
            )
            outcome = ValidationOutcome(
                status=ValidationStatus.INVALID if errs else ValidationStatus.VALID,
                errors=errs,
            )
            field_outcomes[field_name] = outcome
            all_errors.extend(errs)

        # 2. Check unexpected extra fields (rejected by default per Clarification Q4)
        for extra_name in sorted(set(fields.keys()) - set(schema.fields.keys())):
            presence, _ = fields[extra_name]
            if presence == FieldPresence.MISSING:
                field_outcomes[extra_name] = ValidationOutcome(status=ValidationStatus.VALID)
                continue
            if not schema.allow_additional_fields:
                err = _make_error(
                    code="unexpected_field",
                    message=f"Unexpected field '{extra_name}' is not declared in schema.",
                    record_id=record_id,
                    field_path=extra_name,
                    expected="declared schema field only",
                    actual=extra_name,
                )
                field_outcomes[extra_name] = ValidationOutcome(
                    status=ValidationStatus.INVALID,
                    errors=[err],
                )
                all_errors.append(err)
            else:
                field_outcomes[extra_name] = ValidationOutcome(status=ValidationStatus.VALID)

        # 3. Evaluate declarative cross-field & conditional rules (record-level only per Spec 004)
        rule_warnings: list[DiagnosticIssue] = []
        if schema.rules:
            cf_errors, cf_warnings = evaluate_cross_field_rules(
                fields,
                schema,
                record_id=record_id,
            )
            all_errors.extend(cf_errors)
            rule_warnings.extend(cf_warnings)

        record_outcome = ValidationOutcome(
            status=ValidationStatus.INVALID if all_errors else ValidationStatus.VALID,
            errors=all_errors,
        )
        return RecordValidationResult(
            record_outcome=record_outcome,
            field_outcomes=field_outcomes,
            warnings=tuple(rule_warnings),
        )

    def validate_result_cardinality(
        self,
        record_outcomes: list[ValidationOutcome],
        schema: ExtractionSchema,
        *,
        has_records: bool,
        failed_before_validation: bool,
    ) -> ValidationOutcome:
        """Aggregate record-level validation outcomes and check schema cardinality rules."""
        if failed_before_validation and not has_records:
            return ValidationOutcome(status=ValidationStatus.NOT_VALIDATED)

        top_errors: list[DiagnosticIssue] = []
        if schema.cardinality == ExtractionCardinality.SINGLE:
            if len(record_outcomes) == 0:
                top_errors.append(
                    _make_error(
                        code="missing_record",
                        message=(
                            "Single-record schema expected 1 record, but 0 records were extracted."
                        ),
                        record_id=None,
                        field_path=None,
                        expected="1 record",
                        actual="0 records",
                    )
                )
            elif len(record_outcomes) > 1:
                top_errors.append(
                    _make_error(
                        code="cardinality_violation",
                        message=(
                            f"Single-record schema expected 1 record, "
                            f"but {len(record_outcomes)} records were extracted."
                        ),
                        record_id=None,
                        field_path=None,
                        expected="1 record",
                        actual=f"{len(record_outcomes)} records",
                    )
                )

        for rec_outcome in record_outcomes:
            top_errors.extend(rec_outcome.errors)

        return ValidationOutcome(
            status=ValidationStatus.INVALID if top_errors else ValidationStatus.VALID,
            errors=top_errors,
        )

    def _validate_value(
        self,
        *,
        spec: FieldSpec,
        presence: FieldPresence,
        value: Any,
        record_id: str,
        field_path: str,
    ) -> list[DiagnosticIssue]:
        if presence == FieldPresence.MISSING:
            if spec.required:
                return [
                    _make_error(
                        code="missing_required",
                        message=f"Required field '{field_path}' is missing.",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"required {spec.field_type.value}",
                        actual="missing",
                    )
                ]
            return []

        if value is None:
            if spec.nullable:
                return []
            return [
                _make_error(
                    code="disallowed_null",
                    message=f"Field '{field_path}' does not allow null values.",
                    record_id=record_id,
                    field_path=field_path,
                    expected=f"non-null {spec.field_type.value}",
                    actual="null",
                )
            ]

        errors: list[DiagnosticIssue] = []
        ft = spec.field_type
        c = spec.constraints

        # Strict type checks (NO silent coercion!)
        if ft == FieldType.STRING:
            if not isinstance(value, str):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected string, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="string",
                        actual=_safe_type_name(value),
                    )
                ]
            if c.min_length is not None and len(value) < c.min_length:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=(
                            f"Field '{field_path}' length is below min_length ({c.min_length})."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"length >= {c.min_length}",
                        actual=f"length {len(value)}",
                    )
                )
            if c.max_length is not None and len(value) > c.max_length:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=(
                            f"Field '{field_path}' length exceeds max_length ({c.max_length})."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"length <= {c.max_length}",
                        actual=f"length {len(value)}",
                    )
                )
            if c.pattern is not None and re.search(c.pattern, value) is None:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' does not match required pattern.",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"pattern {c.pattern}",
                        actual="pattern mismatch",
                    )
                )

        elif ft == FieldType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected integer, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="integer",
                        actual=_safe_type_name(value),
                    )
                ]
            if c.min_value is not None and value < c.min_value:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' is below min_value ({c.min_value}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f">= {c.min_value}",
                        actual="below minimum",
                    )
                )
            if c.max_value is not None and value > c.max_value:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' exceeds max_value ({c.max_value}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"<= {c.max_value}",
                        actual="above maximum",
                    )
                )

        elif ft == FieldType.DECIMAL:
            if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected decimal, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="decimal",
                        actual=_safe_type_name(value),
                    )
                ]
            dec_val = value if isinstance(value, Decimal) else Decimal(str(value))
            if dec_val.is_nan() or dec_val.is_infinite():
                return [
                    _make_error(
                        code="type_mismatch",
                        message=f"Field '{field_path}' must be a finite decimal value.",
                        record_id=record_id,
                        field_path=field_path,
                        expected="finite decimal",
                        actual="non-finite",
                    )
                ]
            if c.min_value is not None and dec_val < Decimal(str(c.min_value)):
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' is below min_value ({c.min_value}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f">= {c.min_value}",
                        actual="below minimum",
                    )
                )
            if c.max_value is not None and dec_val > Decimal(str(c.max_value)):
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' exceeds max_value ({c.max_value}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"<= {c.max_value}",
                        actual="above maximum",
                    )
                )

        elif ft == FieldType.BOOLEAN:
            if not isinstance(value, bool):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected boolean, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="boolean",
                        actual=_safe_type_name(value),
                    )
                ]

        elif ft == FieldType.DATE:
            if isinstance(value, datetime) or not isinstance(value, date):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected date, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="date",
                        actual=_safe_type_name(value),
                    )
                ]

        elif ft == FieldType.ARRAY:
            if not isinstance(value, list):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected array, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="array",
                        actual=_safe_type_name(value),
                    )
                ]
            if c.min_items is not None and len(value) < c.min_items:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' has fewer than min_items ({c.min_items}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"items >= {c.min_items}",
                        actual=f"{len(value)} items",
                    )
                )
            if c.max_items is not None and len(value) > c.max_items:
                errors.append(
                    _make_error(
                        code="constraint_violation",
                        message=f"Field '{field_path}' exceeds max_items ({c.max_items}).",
                        record_id=record_id,
                        field_path=field_path,
                        expected=f"items <= {c.max_items}",
                        actual=f"{len(value)} items",
                    )
                )
            if spec.items is not None:
                for idx, elem in enumerate(value):
                    errors.extend(
                        self._validate_value(
                            spec=spec.items,
                            presence=FieldPresence.PRESENT,
                            value=elem,
                            record_id=record_id,
                            field_path=f"{field_path}[{idx}]",
                        )
                    )

        elif ft == FieldType.OBJECT:
            if not isinstance(value, dict):
                return [
                    _make_error(
                        code="type_mismatch",
                        message=(
                            f"Field '{field_path}' expected object, got {_safe_type_name(value)}."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                        expected="object",
                        actual=_safe_type_name(value),
                    )
                ]
            props = spec.properties or {}
            for prop_name, prop_spec in props.items():
                if prop_name in value:
                    p_presence = FieldPresence.PRESENT
                    p_val = value[prop_name]
                else:
                    p_presence = FieldPresence.MISSING
                    p_val = None
                errors.extend(
                    self._validate_value(
                        spec=prop_spec,
                        presence=p_presence,
                        value=p_val,
                        record_id=record_id,
                        field_path=f"{field_path}.{prop_name}",
                    )
                )
            if not spec.allow_additional_fields:
                for extra_key in sorted(set(value.keys()) - set(props.keys())):
                    errors.append(
                        _make_error(
                            code="unexpected_field",
                            message=(
                                f"Unexpected nested field '{field_path}.{extra_key}' "
                                "is not declared in schema."
                            ),
                            record_id=record_id,
                            field_path=f"{field_path}.{extra_key}",
                            expected="declared nested property only",
                            actual=str(extra_key),
                        )
                    )

        # Allowed values (enum) check
        if c.allowed_values is not None and not _value_in_allowed(value, c.allowed_values):
            errors.append(
                _make_error(
                    code="constraint_violation",
                    message=f"Field '{field_path}' value is not in allowed_values.",
                    record_id=record_id,
                    field_path=field_path,
                    expected=f"one of {list(c.allowed_values)}",
                    actual="disallowed value",
                )
            )

        return errors


def _value_in_allowed(value: Any, allowed: tuple[Any, ...]) -> bool:
    for candidate in allowed:
        if isinstance(value, bool) != isinstance(candidate, bool):
            continue
        if value == candidate:
            return True
    return False
