"""Declarative, immutable cross-field and conditional validation rule models (Spec 004)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from structa_maestro.contracts.enums import FieldType
from structa_maestro.exceptions import SchemaDefinitionError

if TYPE_CHECKING:
    from structa_maestro.schema.models import FieldSpec

_NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.DECIMAL})
_ORDERED_OPERATORS = frozenset({"lt", "lte", "gt", "gte"})
_SCALAR_COMPARABLE_TYPES = frozenset(
    {FieldType.STRING, FieldType.INTEGER, FieldType.DECIMAL, FieldType.BOOLEAN, FieldType.DATE}
)


class ComparisonOperator(StrEnum):
    """Comparison operators for field-to-field rules."""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class ConditionOperator(StrEnum):
    """Predicate operators for controlling fields in conditional presence rules."""

    IS_PRESENT = "is_present"
    IS_MISSING = "is_missing"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"


class RuleSeverity(StrEnum):
    """Diagnostic severity emitted when a cross-field rule is violated."""

    ERROR = "error"
    WARNING = "warning"


class ConditionalPresenceKind(StrEnum):
    """Kinds of conditional presence rules."""

    REQUIRED_IF = "required_if"
    FORBIDDEN_IF = "forbidden_if"


class GroupPresenceKind(StrEnum):
    """Kinds of multi-field group presence rules."""

    MUTUALLY_EXCLUSIVE = "mutually_exclusive"
    AT_LEAST_ONE_OF = "at_least_one_of"


def _normalize_rule_id(rule_id: str) -> str:
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise SchemaDefinitionError("ValidationRule.rule_id must be a non-empty string.")
    return rule_id.strip()


def _normalize_severity(value: RuleSeverity | str) -> RuleSeverity:
    if isinstance(value, RuleSeverity):
        return value
    if isinstance(value, str):
        try:
            return RuleSeverity(value.strip().lower())
        except ValueError as exc:
            raise SchemaDefinitionError(
                f"Invalid rule severity {value!r}; expected 'error' or 'warning'."
            ) from exc
    raise SchemaDefinitionError("ValidationRule.severity must be a RuleSeverity or string.")


def _normalize_message(message: str | None) -> str | None:
    if message is None:
        return None
    if not isinstance(message, str) or not message.strip():
        raise SchemaDefinitionError("ValidationRule.message must be a non-empty string or None.")
    return message.strip()


def _normalize_path(path: str, label: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise SchemaDefinitionError(f"{label} must be a non-empty field path.")
    cleaned = path.strip()
    parts = cleaned.split(".")
    if any(not part.strip() for part in parts):
        raise SchemaDefinitionError(f"Invalid field path {path!r} for {label}.")
    return cleaned


@dataclass(frozen=True, slots=True)
class ComparisonRule:
    """Declarative comparison rule asserting left_field <op> right_field within a record."""

    rule_id: str
    left_field: str
    operator: ComparisonOperator
    right_field: str
    severity: RuleSeverity = RuleSeverity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _normalize_rule_id(self.rule_id))
        left = _normalize_path(self.left_field, "ComparisonRule.left_field")
        right = _normalize_path(self.right_field, "ComparisonRule.right_field")
        if left == right:
            raise SchemaDefinitionError(
                f"ComparisonRule {self.rule_id!r} cannot compare a field to itself ({left!r})."
            )
        object.__setattr__(self, "left_field", left)
        object.__setattr__(self, "right_field", right)

        if isinstance(self.operator, ComparisonOperator):
            op = self.operator
        elif isinstance(self.operator, str):
            try:
                op = ComparisonOperator(self.operator.strip().lower())
            except ValueError as exc:
                valid = ", ".join(o.value for o in ComparisonOperator)
                raise SchemaDefinitionError(
                    f"Invalid ComparisonRule.operator {self.operator!r}; expected one of: {valid}."
                ) from exc
        else:
            raise SchemaDefinitionError("ComparisonRule.operator must be a ComparisonOperator.")
        object.__setattr__(self, "operator", op)
        object.__setattr__(self, "severity", _normalize_severity(self.severity))
        object.__setattr__(self, "message", _normalize_message(self.message))


@dataclass(frozen=True, slots=True)
class ArithmeticTerm:
    """Signed numeric scalar or collection-sum operand in an ArithmeticRule."""

    field: str
    sign: int = 1
    item_field: str | None = None
    treat_missing_as_zero: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _normalize_path(self.field, "ArithmeticTerm.field"))
        if isinstance(self.sign, bool) or self.sign not in (-1, 1):
            raise SchemaDefinitionError("ArithmeticTerm.sign must be 1 or -1.")
        if self.item_field is not None:
            object.__setattr__(
                self,
                "item_field",
                _normalize_path(self.item_field, "ArithmeticTerm.item_field"),
            )
        if not isinstance(self.treat_missing_as_zero, bool):
            raise SchemaDefinitionError("ArithmeticTerm.treat_missing_as_zero must be a boolean.")


@dataclass(frozen=True, slots=True)
class ArithmeticRule:
    """Declarative exact-decimal arithmetic balance rule: sum(terms) == target_field (+/- tol)."""

    rule_id: str
    terms: tuple[ArithmeticTerm, ...]
    target_field: str
    tolerance: Decimal = Decimal("0")
    severity: RuleSeverity = RuleSeverity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _normalize_rule_id(self.rule_id))
        if not isinstance(self.terms, tuple) or not self.terms:
            raise SchemaDefinitionError(
                f"ArithmeticRule {self.rule_id!r} must define a non-empty tuple of ArithmeticTerm."
            )
        if not all(isinstance(t, ArithmeticTerm) for t in self.terms):
            raise SchemaDefinitionError(
                f"ArithmeticRule {self.rule_id!r} terms must all be ArithmeticTerm instances."
            )
        object.__setattr__(
            self,
            "target_field",
            _normalize_path(self.target_field, "ArithmeticRule.target_field"),
        )

        raw_tol: Any = self.tolerance
        if isinstance(raw_tol, bool):
            raise SchemaDefinitionError("ArithmeticRule.tolerance cannot be a boolean.")
        if isinstance(raw_tol, Decimal):
            tol = raw_tol
        elif isinstance(raw_tol, (int, float, str)):
            try:
                tol = Decimal(str(raw_tol).strip())
            except InvalidOperation as exc:
                raise SchemaDefinitionError(
                    f"Invalid ArithmeticRule.tolerance: {raw_tol!r}"
                ) from exc
        else:
            raise SchemaDefinitionError(f"Invalid ArithmeticRule.tolerance: {raw_tol!r}")
        if tol.is_nan() or tol.is_infinite() or tol < Decimal("0"):
            raise SchemaDefinitionError("ArithmeticRule.tolerance must be a finite Decimal >= 0.")
        object.__setattr__(self, "tolerance", tol)
        object.__setattr__(self, "severity", _normalize_severity(self.severity))
        object.__setattr__(self, "message", _normalize_message(self.message))


@dataclass(frozen=True, slots=True)
class FieldCondition:
    """Declarative predicate evaluated against a single controlling field."""

    field: str
    operator: ConditionOperator
    value: Any = None
    values: tuple[Any, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _normalize_path(self.field, "FieldCondition.field"))
        if isinstance(self.operator, ConditionOperator):
            op = self.operator
        elif isinstance(self.operator, str):
            try:
                op = ConditionOperator(self.operator.strip().lower())
            except ValueError as exc:
                valid = ", ".join(o.value for o in ConditionOperator)
                raise SchemaDefinitionError(
                    f"Invalid FieldCondition.operator {self.operator!r}; expected one of: {valid}."
                ) from exc
        else:
            raise SchemaDefinitionError("FieldCondition.operator must be a ConditionOperator.")
        object.__setattr__(self, "operator", op)

        if op in (ConditionOperator.IS_PRESENT, ConditionOperator.IS_MISSING):
            if self.value is not None or self.values is not None:
                raise SchemaDefinitionError(
                    f"FieldCondition with operator {op.value!r} must not set 'value' or 'values'."
                )
        elif op in (ConditionOperator.EQUALS, ConditionOperator.NOT_EQUALS):
            if self.value is None:
                raise SchemaDefinitionError(
                    f"FieldCondition with operator {op.value!r} requires a non-None 'value'."
                )
            if self.values is not None:
                raise SchemaDefinitionError(
                    f"FieldCondition with operator {op.value!r} must not set 'values'."
                )
        elif op == ConditionOperator.IN:
            if self.value is not None:
                raise SchemaDefinitionError(
                    "FieldCondition with operator 'in' must not set 'value'."
                )
            if not isinstance(self.values, tuple) or not self.values:
                raise SchemaDefinitionError(
                    "FieldCondition with operator 'in' requires a non-empty 'values' tuple."
                )
            if any(v is None for v in self.values):
                raise SchemaDefinitionError(
                    "FieldCondition.values for 'in' cannot contain None entries."
                )


@dataclass(frozen=True, slots=True)
class ConditionalPresenceRule:
    """Declarative required_if or forbidden_if rule gated by a FieldCondition."""

    rule_id: str
    kind: ConditionalPresenceKind
    when: FieldCondition
    target_fields: tuple[str, ...]
    severity: RuleSeverity = RuleSeverity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _normalize_rule_id(self.rule_id))
        if isinstance(self.kind, ConditionalPresenceKind):
            kind = self.kind
        elif isinstance(self.kind, str):
            try:
                kind = ConditionalPresenceKind(self.kind.strip().lower())
            except ValueError as exc:
                raise SchemaDefinitionError(
                    f"Invalid ConditionalPresenceRule.kind {self.kind!r}."
                ) from exc
        else:
            raise SchemaDefinitionError(
                "ConditionalPresenceRule.kind must be ConditionalPresenceKind."
            )
        object.__setattr__(self, "kind", kind)

        if not isinstance(self.when, FieldCondition):
            raise SchemaDefinitionError(
                "ConditionalPresenceRule.when must be a FieldCondition instance."
            )
        if not isinstance(self.target_fields, tuple) or not self.target_fields:
            raise SchemaDefinitionError(
                f"ConditionalPresenceRule {self.rule_id!r} must specify non-empty target_fields."
            )
        normalized_targets = tuple(
            _normalize_path(t, "ConditionalPresenceRule.target_fields") for t in self.target_fields
        )
        if len(set(normalized_targets)) != len(normalized_targets):
            raise SchemaDefinitionError(
                f"ConditionalPresenceRule {self.rule_id!r} target_fields must be unique."
            )
        if self.when.field in normalized_targets:
            raise SchemaDefinitionError(
                f"ConditionalPresenceRule {self.rule_id!r} controlling field "
                f"{self.when.field!r} cannot also appear in target_fields."
            )
        object.__setattr__(self, "target_fields", normalized_targets)
        object.__setattr__(self, "severity", _normalize_severity(self.severity))
        object.__setattr__(self, "message", _normalize_message(self.message))


@dataclass(frozen=True, slots=True)
class GroupPresenceRule:
    """Declarative mutually_exclusive or at_least_one_of rule across >= 2 distinct fields."""

    rule_id: str
    kind: GroupPresenceKind
    fields: tuple[str, ...]
    severity: RuleSeverity = RuleSeverity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _normalize_rule_id(self.rule_id))
        if isinstance(self.kind, GroupPresenceKind):
            kind = self.kind
        elif isinstance(self.kind, str):
            try:
                kind = GroupPresenceKind(self.kind.strip().lower())
            except ValueError as exc:
                raise SchemaDefinitionError(
                    f"Invalid GroupPresenceRule.kind {self.kind!r}."
                ) from exc
        else:
            raise SchemaDefinitionError("GroupPresenceRule.kind must be GroupPresenceKind.")
        object.__setattr__(self, "kind", kind)

        if not isinstance(self.fields, tuple) or len(self.fields) < 2:
            raise SchemaDefinitionError(
                f"GroupPresenceRule {self.rule_id!r} requires at least 2 distinct fields."
            )
        normalized = tuple(_normalize_path(f, "GroupPresenceRule.fields") for f in self.fields)
        if len(set(normalized)) != len(normalized):
            raise SchemaDefinitionError(
                f"GroupPresenceRule {self.rule_id!r} fields must all be distinct."
            )
        object.__setattr__(self, "fields", normalized)
        object.__setattr__(self, "severity", _normalize_severity(self.severity))
        object.__setattr__(self, "message", _normalize_message(self.message))


ValidationRule = ComparisonRule | ArithmeticRule | ConditionalPresenceRule | GroupPresenceRule


def resolve_schema_field_spec(
    fields: Mapping[str, FieldSpec],
    path: str,
) -> FieldSpec:
    """Resolve a top-level or dot-separated nested object path against declared FieldSpecs."""
    segments = path.split(".")
    current_map: Mapping[str, FieldSpec] = fields
    current_spec: FieldSpec | None = None

    for idx, seg in enumerate(segments):
        spec = current_map.get(seg)
        if spec is None:
            raise SchemaDefinitionError(
                f"Validation rule references unknown field path {path!r} (missing {seg!r})."
            )
        current_spec = spec
        if idx < len(segments) - 1:
            if spec.field_type != FieldType.OBJECT or not spec.properties:
                raise SchemaDefinitionError(
                    f"Validation rule path {path!r} traverses non-object field {seg!r}."
                )
            current_map = spec.properties

    if current_spec is None:
        raise SchemaDefinitionError(f"Validation rule references invalid field path {path!r}.")
    return current_spec


def _types_compatible_for_comparison(
    left_type: FieldType,
    right_type: FieldType,
    operator: ComparisonOperator,
) -> bool:
    if left_type not in _SCALAR_COMPARABLE_TYPES or right_type not in _SCALAR_COMPARABLE_TYPES:
        return False
    if operator.value in _ORDERED_OPERATORS:
        if left_type == FieldType.BOOLEAN or right_type == FieldType.BOOLEAN:
            return False
        if left_type in _NUMERIC_TYPES and right_type in _NUMERIC_TYPES:
            return True
        return left_type == right_type
    # Equality / inequality
    if left_type in _NUMERIC_TYPES and right_type in _NUMERIC_TYPES:
        return True
    return left_type == right_type


def _is_literal_compatible(val: Any, field_type: FieldType) -> bool:
    if field_type == FieldType.STRING:
        return isinstance(val, str)
    if field_type == FieldType.BOOLEAN:
        return isinstance(val, bool)
    if field_type == FieldType.INTEGER:
        return not isinstance(val, bool) and isinstance(val, int)
    if field_type == FieldType.DECIMAL:
        if isinstance(val, bool) or not isinstance(val, (Decimal, int, float)):
            return False
        dec = val if isinstance(val, Decimal) else Decimal(str(val))
        return not (dec.is_nan() or dec.is_infinite())
    if field_type == FieldType.DATE:
        return isinstance(val, date) and not isinstance(val, datetime)
    return False


def validate_schema_rules(
    fields: Mapping[str, FieldSpec],
    rules: tuple[ValidationRule, ...],
) -> None:
    """Eagerly validate every declared cross-field rule against ExtractionSchema fields."""
    if not isinstance(rules, tuple):
        raise SchemaDefinitionError("ExtractionSchema.rules must be a tuple of ValidationRule.")

    seen_ids: set[str] = set()
    for rule in rules:
        if not isinstance(
            rule,
            (ComparisonRule, ArithmeticRule, ConditionalPresenceRule, GroupPresenceRule),
        ):
            raise SchemaDefinitionError(f"Unsupported validation rule object: {rule!r}")
        if rule.rule_id in seen_ids:
            raise SchemaDefinitionError(
                f"Duplicate cross-field validation rule_id: {rule.rule_id!r}"
            )
        seen_ids.add(rule.rule_id)

        if isinstance(rule, ComparisonRule):
            left_spec = resolve_schema_field_spec(fields, rule.left_field)
            right_spec = resolve_schema_field_spec(fields, rule.right_field)
            if not _types_compatible_for_comparison(
                left_spec.field_type,
                right_spec.field_type,
                rule.operator,
            ):
                raise SchemaDefinitionError(
                    f"ComparisonRule {rule.rule_id!r}: operator {rule.operator.value!r} "
                    f"is incompatible with types {left_spec.field_type.value!r} and "
                    f"{right_spec.field_type.value!r}."
                )

        elif isinstance(rule, ArithmeticRule):
            target_spec = resolve_schema_field_spec(fields, rule.target_field)
            if target_spec.field_type not in _NUMERIC_TYPES:
                raise SchemaDefinitionError(
                    f"ArithmeticRule {rule.rule_id!r}: target_field {rule.target_field!r} "
                    f"must be integer or decimal, got {target_spec.field_type.value!r}."
                )
            for term in rule.terms:
                term_spec = resolve_schema_field_spec(fields, term.field)
                if term.item_field is not None:
                    if (
                        term_spec.field_type != FieldType.ARRAY
                        or term_spec.items is None
                        or term_spec.items.field_type != FieldType.OBJECT
                        or not term_spec.items.properties
                    ):
                        raise SchemaDefinitionError(
                            f"ArithmeticRule {rule.rule_id!r}: term {term.field!r} with "
                            f"item_field {term.item_field!r} requires an array of objects."
                        )
                    item_spec = resolve_schema_field_spec(
                        term_spec.items.properties,
                        term.item_field,
                    )
                    if item_spec.field_type not in _NUMERIC_TYPES:
                        raise SchemaDefinitionError(
                            f"ArithmeticRule {rule.rule_id!r}: item_field "
                            f"{term.field}[].{term.item_field} must be numeric, got "
                            f"{item_spec.field_type.value!r}."
                        )
                elif term_spec.field_type == FieldType.ARRAY:
                    if term_spec.items is None or term_spec.items.field_type not in _NUMERIC_TYPES:
                        raise SchemaDefinitionError(
                            f"ArithmeticRule {rule.rule_id!r}: array term {term.field!r} without "
                            "item_field must contain numeric items."
                        )
                elif term_spec.field_type not in _NUMERIC_TYPES:
                    raise SchemaDefinitionError(
                        f"ArithmeticRule {rule.rule_id!r}: term {term.field!r} must be numeric, "
                        f"got {term_spec.field_type.value!r}."
                    )

        elif isinstance(rule, ConditionalPresenceRule):
            cond_spec = resolve_schema_field_spec(fields, rule.when.field)
            if rule.when.operator in (ConditionOperator.EQUALS, ConditionOperator.NOT_EQUALS):
                if not _is_literal_compatible(rule.when.value, cond_spec.field_type):
                    raise SchemaDefinitionError(
                        f"ConditionalPresenceRule {rule.rule_id!r}: condition value "
                        f"{rule.when.value!r} is incompatible with field "
                        f"{rule.when.field!r} ({cond_spec.field_type.value})."
                    )
            elif rule.when.operator == ConditionOperator.IN and rule.when.values is not None:
                for val in rule.when.values:
                    if not _is_literal_compatible(val, cond_spec.field_type):
                        raise SchemaDefinitionError(
                            f"ConditionalPresenceRule {rule.rule_id!r}: condition 'in' value "
                            f"{val!r} is incompatible with field {rule.when.field!r} "
                            f"({cond_spec.field_type.value})."
                        )
            for tf in rule.target_fields:
                resolve_schema_field_spec(fields, tf)

        elif isinstance(rule, GroupPresenceRule):
            for fname in rule.fields:
                resolve_schema_field_spec(fields, fname)
