"""Deterministic, non-coercing cross-field and conditional validation evaluator (Spec 004)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from structa_maestro.contracts.enums import (
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
)
from structa_maestro.contracts.models import DiagnosticIssue
from structa_maestro.schema.models import ExtractionSchema, FieldSpec
from structa_maestro.schema.rules import (
    ArithmeticRule,
    ArithmeticTerm,
    ComparisonOperator,
    ComparisonRule,
    ConditionalPresenceKind,
    ConditionalPresenceRule,
    ConditionOperator,
    FieldCondition,
    GroupPresenceKind,
    GroupPresenceRule,
    RuleSeverity,
    resolve_schema_field_spec,
)

_NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.DECIMAL})


def _to_exact_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return None if (value.is_nan() or value.is_infinite()) else value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        dec = Decimal(str(value))
        return None if (dec.is_nan() or dec.is_infinite()) else dec
    return None


def _is_valid_value_for_spec(value: Any, spec: FieldSpec) -> bool:
    """Strict non-coercing type validity check matching DeterministicValidator."""
    if value is None:
        return False
    ft = spec.field_type
    if ft == FieldType.STRING:
        return isinstance(value, str)
    if ft == FieldType.INTEGER:
        return not isinstance(value, bool) and isinstance(value, int)
    if ft == FieldType.DECIMAL:
        return _to_exact_decimal(value) is not None
    if ft == FieldType.BOOLEAN:
        return isinstance(value, bool)
    if ft == FieldType.DATE:
        return isinstance(value, date) and not isinstance(value, datetime)
    if ft == FieldType.ARRAY:
        return isinstance(value, list)
    if ft == FieldType.OBJECT:
        return isinstance(value, dict)
    return False


def _resolve_record_path(
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    path: str,
) -> tuple[FieldPresence, Any, FieldSpec]:
    """Resolve top-level or dot-separated nested object path from record fields without mutation."""
    spec = resolve_schema_field_spec(schema.fields, path)
    segments = path.split(".")
    top_presence, top_value = fields.get(segments[0], (FieldPresence.MISSING, None))
    if len(segments) == 1:
        return top_presence, top_value, spec

    if top_presence == FieldPresence.MISSING or not isinstance(top_value, dict):
        return FieldPresence.MISSING, None, spec

    current: Any = top_value
    for idx, seg in enumerate(segments[1:], start=1):
        if not isinstance(current, dict) or seg not in current:
            return FieldPresence.MISSING, None, spec
        current = current[seg]
        if idx < len(segments) - 1 and not isinstance(current, dict):
            return FieldPresence.MISSING, None, spec

    return FieldPresence.PRESENT, current, spec


def _resolve_nested_dict_path(
    obj: Mapping[str, Any],
    props: Mapping[str, FieldSpec],
    path: str,
) -> tuple[FieldPresence, Any, FieldSpec]:
    spec = resolve_schema_field_spec(props, path)
    current: Any = obj
    for seg in path.split("."):
        if not isinstance(current, Mapping) or seg not in current:
            return FieldPresence.MISSING, None, spec
        current = current[seg]
    return FieldPresence.PRESENT, current, spec


def _make_rule_issue(
    *,
    code: str,
    severity: RuleSeverity,
    message: str,
    record_id: str,
    field_path: str,
    expected: str | None = None,
    actual: str | None = None,
) -> DiagnosticIssue:
    issue_sev = IssueSeverity.ERROR if severity == RuleSeverity.ERROR else IssueSeverity.WARNING
    return DiagnosticIssue(
        code=code,
        severity=issue_sev,
        stage=PipelineStage.VALIDATION,
        message=message,
        record_id=record_id,
        field_path=field_path,
        expected=expected,
        actual=actual,
    )


def _compare_typed_values(
    left_val: Any,
    left_type: FieldType,
    right_val: Any,
    right_type: FieldType,
    operator: ComparisonOperator,
) -> bool:
    if left_type in _NUMERIC_TYPES and right_type in _NUMERIC_TYPES:
        lv = _to_exact_decimal(left_val)
        rv = _to_exact_decimal(right_val)
        if lv is None or rv is None:
            return True
    else:
        lv = left_val
        rv = right_val

    if operator == ComparisonOperator.EQ:
        return bool(lv == rv)
    if operator == ComparisonOperator.NE:
        return bool(lv != rv)
    if operator == ComparisonOperator.LT:
        return bool(lv < rv)
    if operator == ComparisonOperator.LTE:
        return bool(lv <= rv)
    if operator == ComparisonOperator.GT:
        return bool(lv > rv)
    if operator == ComparisonOperator.GTE:
        return bool(lv >= rv)
    return True


def _eval_comparison(
    rule: ComparisonRule,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    record_id: str,
) -> DiagnosticIssue | None:
    lp, lv, lspec = _resolve_record_path(fields, schema, rule.left_field)
    rp, rv, rspec = _resolve_record_path(fields, schema, rule.right_field)

    if (
        lp == FieldPresence.MISSING
        or rp == FieldPresence.MISSING
        or lv is None
        or rv is None
        or not _is_valid_value_for_spec(lv, lspec)
        or not _is_valid_value_for_spec(rv, rspec)
    ):
        return None

    if _compare_typed_values(lv, lspec.field_type, rv, rspec.field_type, rule.operator):
        return None

    default_msg = (
        f"Cross-field rule '{rule.rule_id}' failed: "
        f"'{rule.left_field}' ({rule.operator.value}) '{rule.right_field}' did not hold."
    )
    return _make_rule_issue(
        code="cross_field_comparison_failed",
        severity=rule.severity,
        message=rule.message or default_msg,
        record_id=record_id,
        field_path=rule.left_field,
        expected=f"{rule.left_field} {rule.operator.value} {rule.right_field}",
        actual=f"{lv} vs {rv}",
    )


def _eval_arithmetic_term(
    term: ArithmeticTerm,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
) -> Decimal | None:
    presence, value, spec = _resolve_record_path(fields, schema, term.field)
    if presence == FieldPresence.MISSING or value is None:
        return Decimal("0") if term.treat_missing_as_zero else None

    if spec.field_type == FieldType.ARRAY:
        if not isinstance(value, list):
            return None
        total = Decimal("0")
        if term.item_field is not None:
            if spec.items is None or not spec.items.properties:
                return None
            for elem in value:
                if not isinstance(elem, Mapping):
                    return None
                ep, ev, espec = _resolve_nested_dict_path(
                    elem,
                    spec.items.properties,
                    term.item_field,
                )
                if (
                    ep == FieldPresence.MISSING
                    or ev is None
                    or not _is_valid_value_for_spec(ev, espec)
                ):
                    return None
                dec_elem = _to_exact_decimal(ev)
                if dec_elem is None:
                    return None
                total += dec_elem
        else:
            if spec.items is None:
                return None
            for elem in value:
                if elem is None or not _is_valid_value_for_spec(elem, spec.items):
                    return None
                dec_elem = _to_exact_decimal(elem)
                if dec_elem is None:
                    return None
                total += dec_elem
        return total * Decimal(term.sign)

    if not _is_valid_value_for_spec(value, spec):
        return None
    dec = _to_exact_decimal(value)
    if dec is None:
        return None
    return dec * Decimal(term.sign)


def _eval_arithmetic(
    rule: ArithmeticRule,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    record_id: str,
) -> DiagnosticIssue | None:
    tp, tv, tspec = _resolve_record_path(fields, schema, rule.target_field)
    if tp == FieldPresence.MISSING or tv is None or not _is_valid_value_for_spec(tv, tspec):
        return None
    target_dec = _to_exact_decimal(tv)
    if target_dec is None:
        return None

    computed = Decimal("0")
    for term in rule.terms:
        val = _eval_arithmetic_term(term, fields, schema)
        if val is None:
            return None
        computed += val

    if abs(computed - target_dec) <= rule.tolerance:
        return None

    default_msg = (
        f"Cross-field arithmetic rule '{rule.rule_id}' failed: "
        f"computed {computed} does not match '{rule.target_field}' ({target_dec}) "
        f"within tolerance {rule.tolerance}."
    )
    return _make_rule_issue(
        code="cross_field_arithmetic_mismatch",
        severity=rule.severity,
        message=rule.message or default_msg,
        record_id=record_id,
        field_path=rule.target_field,
        expected=f"{computed} (+/- {rule.tolerance})",
        actual=str(target_dec),
    )


def _values_equal_strict(actual: Any, expected: Any, field_type: FieldType) -> bool:
    if field_type in _NUMERIC_TYPES:
        a_dec = _to_exact_decimal(actual)
        e_dec = _to_exact_decimal(expected)
        return a_dec is not None and e_dec is not None and a_dec == e_dec
    if isinstance(actual, bool) != isinstance(expected, bool):
        return False
    return bool(actual == expected)


def _eval_condition(
    cond: FieldCondition,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
) -> bool:
    presence, value, spec = _resolve_record_path(fields, schema, cond.field)

    if cond.operator == ConditionOperator.IS_MISSING:
        return presence == FieldPresence.MISSING
    if cond.operator == ConditionOperator.IS_PRESENT:
        return presence == FieldPresence.PRESENT and value is not None

    # Value predicates evaluate to False when controlling field is missing, null, or type-invalid
    if (
        presence == FieldPresence.MISSING
        or value is None
        or not _is_valid_value_for_spec(value, spec)
    ):
        return False

    if cond.operator == ConditionOperator.EQUALS:
        return _values_equal_strict(value, cond.value, spec.field_type)
    if cond.operator == ConditionOperator.NOT_EQUALS:
        return not _values_equal_strict(value, cond.value, spec.field_type)
    if cond.operator == ConditionOperator.IN and cond.values is not None:
        return any(_values_equal_strict(value, item, spec.field_type) for item in cond.values)
    return False


def _eval_conditional_presence(
    rule: ConditionalPresenceRule,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    record_id: str,
) -> list[DiagnosticIssue]:
    if not _eval_condition(rule.when, fields, schema):
        return []

    issues: list[DiagnosticIssue] = []
    for target_path in rule.target_fields:
        tp, tv, tspec = _resolve_record_path(fields, schema, target_path)
        if rule.kind == ConditionalPresenceKind.REQUIRED_IF:
            is_satisfied = tp == FieldPresence.PRESENT and (tv is not None or tspec.nullable)
            if not is_satisfied:
                default_msg = (
                    f"Conditional rule '{rule.rule_id}' failed: field '{target_path}' is "
                    f"required when '{rule.when.field}' satisfies '{rule.when.operator.value}'."
                )
                issues.append(
                    _make_rule_issue(
                        code="conditional_required_missing",
                        severity=rule.severity,
                        message=rule.message or default_msg,
                        record_id=record_id,
                        field_path=target_path,
                        expected=f"present {target_path}",
                        actual="missing" if tp == FieldPresence.MISSING else "null",
                    )
                )
        elif rule.kind == ConditionalPresenceKind.FORBIDDEN_IF and (
            tp == FieldPresence.PRESENT and tv is not None
        ):
            default_msg = (
                f"Conditional rule '{rule.rule_id}' failed: field '{target_path}' is "
                f"forbidden when '{rule.when.field}' satisfies '{rule.when.operator.value}'."
            )
            issues.append(
                _make_rule_issue(
                    code="conditional_forbidden_present",
                    severity=rule.severity,
                    message=rule.message or default_msg,
                    record_id=record_id,
                    field_path=target_path,
                    expected=f"absent {target_path}",
                    actual="present",
                )
            )
    return issues


def _eval_group_presence(
    rule: GroupPresenceRule,
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    record_id: str,
) -> DiagnosticIssue | None:
    present_fields: list[str] = []
    for fpath in rule.fields:
        fp, fv, _ = _resolve_record_path(fields, schema, fpath)
        if fp == FieldPresence.PRESENT and fv is not None:
            present_fields.append(fpath)

    if rule.kind == GroupPresenceKind.MUTUALLY_EXCLUSIVE and len(present_fields) > 1:
        default_msg = (
            f"Group rule '{rule.rule_id}' failed: fields {', '.join(present_fields)} "
            "are mutually exclusive."
        )
        return _make_rule_issue(
            code="mutually_exclusive_fields",
            severity=rule.severity,
            message=rule.message or default_msg,
            record_id=record_id,
            field_path=present_fields[0],
            expected=f"at most one of {', '.join(rule.fields)}",
            actual=f"present: {', '.join(present_fields)}",
        )

    if rule.kind == GroupPresenceKind.AT_LEAST_ONE_OF and not present_fields:
        default_msg = (
            f"Group rule '{rule.rule_id}' failed: at least one of "
            f"{', '.join(rule.fields)} must be present."
        )
        return _make_rule_issue(
            code="missing_required_group",
            severity=rule.severity,
            message=rule.message or default_msg,
            record_id=record_id,
            field_path=rule.fields[0],
            expected=f"at least one of {', '.join(rule.fields)}",
            actual="none present",
        )

    return None


def evaluate_cross_field_rules(
    fields: Mapping[str, tuple[FieldPresence, Any]],
    schema: ExtractionSchema,
    *,
    record_id: str,
) -> tuple[list[DiagnosticIssue], list[DiagnosticIssue]]:
    """Evaluate all declared ExtractionSchema.rules in order, separating errors and warnings."""
    errors: list[DiagnosticIssue] = []
    warnings: list[DiagnosticIssue] = []

    for rule in schema.rules:
        emitted: list[DiagnosticIssue] = []
        if isinstance(rule, ComparisonRule):
            issue = _eval_comparison(rule, fields, schema, record_id)
            if issue is not None:
                emitted.append(issue)
        elif isinstance(rule, ArithmeticRule):
            issue = _eval_arithmetic(rule, fields, schema, record_id)
            if issue is not None:
                emitted.append(issue)
        elif isinstance(rule, ConditionalPresenceRule):
            emitted.extend(_eval_conditional_presence(rule, fields, schema, record_id))
        elif isinstance(rule, GroupPresenceRule):
            issue = _eval_group_presence(rule, fields, schema, record_id)
            if issue is not None:
                emitted.append(issue)

        for diag in emitted:
            if diag.severity == IssueSeverity.ERROR:
                errors.append(diag)
            else:
                warnings.append(diag)

    return errors, warnings
