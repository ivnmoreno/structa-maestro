"""Pure-Python cross-field & conditional rule conformance helpers (Spec 014).

Mirrors `formal/StructaFormal/CrossFieldModels.lean` and `CrossFieldValidation.lean`, providing:
- Lossless Python `ValidationRule` -> JSON encoding for `structa-formal-oracle`
- Curated valid and invalid cross-field rule-definition vectors (schema well-formedness)
- Curated evaluation schemas and records spanning ComparisonRule, ArithmeticRule (scalar,
  numeric array, array-of-objects `item_field`, `treat_missing_as_zero`, tolerance boundaries),
  ConditionalPresenceRule (`required_if`/`forbidden_if`, `nullable`, `is_missing` vs `null`),
  GroupPresenceRule (`mutually_exclusive`/`at_least_one_of`), non-coercion skips, and
  mixed `ERROR` / `WARNING` severities.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tests.conformance.schema_reference import (
    encode_extraction_schema,
    encode_runtime_value,
    instantiate_python_schema_from_vector,
)

from structa_maestro.contracts.enums import FieldPresence
from structa_maestro.schema.models import ExtractionSchema
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
    ValidationRule,
)


def encode_validation_rule(rule: ValidationRule) -> dict[str, Any]:
    """Encode a Python `ValidationRule` into the lossless JSON format for `Oracle.lean`."""
    if isinstance(rule, ComparisonRule):
        return {
            "rule_type": "comparison",
            "rule_id": rule.rule_id,
            "left_field": rule.left_field,
            "operator": rule.operator.value,
            "right_field": rule.right_field,
            "severity": rule.severity.value,
            "message": rule.message,
        }
    if isinstance(rule, ArithmeticRule):
        return {
            "rule_type": "arithmetic",
            "rule_id": rule.rule_id,
            "terms": [
                {
                    "field": t.field,
                    "sign": t.sign,
                    "item_field": t.item_field,
                    "treat_missing_as_zero": t.treat_missing_as_zero,
                }
                for t in rule.terms
            ],
            "target_field": rule.target_field,
            "tolerance": str(rule.tolerance),
            "severity": rule.severity.value,
            "message": rule.message,
        }
    if isinstance(rule, ConditionalPresenceRule):
        return {
            "rule_type": "conditional_presence",
            "rule_id": rule.rule_id,
            "kind": rule.kind.value,
            "when": {
                "field": rule.when.field,
                "operator": rule.when.operator.value,
                "value": (
                    encode_runtime_value(rule.when.value) if rule.when.value is not None else None
                ),
                "values": (
                    [encode_runtime_value(v) for v in rule.when.values]
                    if rule.when.values is not None
                    else None
                ),
            },
            "target_fields": list(rule.target_fields),
            "severity": rule.severity.value,
            "message": rule.message,
        }
    return {
        "rule_type": "group_presence",
        "rule_id": rule.rule_id,
        "kind": rule.kind.value,
        "fields": list(rule.fields),
        "severity": rule.severity.value,
        "message": rule.message,
    }


def encode_schema_with_rules(schema: ExtractionSchema) -> dict[str, Any]:
    """Encode an `ExtractionSchema` including its declarative `rules` for `Oracle.lean`."""
    payload = encode_extraction_schema(schema)
    payload["rules"] = [encode_validation_rule(r) for r in schema.rules]
    return payload


def _decode_tagged_literal(raw: dict[str, Any]) -> Any:
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
    if kind == "datetime":
        return datetime(2026, 1, 1)
    if kind == "non_finite_decimal":
        return Decimal("NaN")
    raise ValueError(f"Unsupported tagged literal kind: {kind}")


def instantiate_python_schema_with_rules_from_vector(vec: dict[str, Any]) -> ExtractionSchema:
    """Construct a Python `ExtractionSchema` (with `rules`) from an oracle vector dict."""
    base = instantiate_python_schema_from_vector(vec)
    raw_rules = vec.get("rules") or []
    built_rules: list[ValidationRule] = []
    for r in raw_rules:
        rtype = r["rule_type"]
        if rtype == "comparison":
            built_rules.append(
                ComparisonRule(
                    rule_id=r["rule_id"],
                    left_field=r["left_field"],
                    operator=ComparisonOperator(r["operator"]),
                    right_field=r["right_field"],
                    severity=RuleSeverity(r.get("severity", "error")),
                    message=r.get("message"),
                )
            )
        elif rtype == "arithmetic":
            terms = tuple(
                ArithmeticTerm(
                    field=t["field"],
                    sign=t["sign"],
                    item_field=t.get("item_field"),
                    treat_missing_as_zero=t.get("treat_missing_as_zero", False),
                )
                for t in r["terms"]
            )
            built_rules.append(
                ArithmeticRule(
                    rule_id=r["rule_id"],
                    terms=terms,
                    target_field=r["target_field"],
                    tolerance=Decimal(str(r.get("tolerance", "0"))),
                    severity=RuleSeverity(r.get("severity", "error")),
                    message=r.get("message"),
                )
            )
        elif rtype == "conditional_presence":
            w = r["when"]
            val = _decode_tagged_literal(w["value"]) if w.get("value") is not None else None
            vals = (
                tuple(_decode_tagged_literal(v) for v in w["values"])
                if w.get("values") is not None
                else None
            )
            built_rules.append(
                ConditionalPresenceRule(
                    rule_id=r["rule_id"],
                    kind=ConditionalPresenceKind(r["kind"]),
                    when=FieldCondition(
                        field=w["field"],
                        operator=ConditionOperator(w["operator"]),
                        value=val,
                        values=vals,
                    ),
                    target_fields=tuple(r["target_fields"]),
                    severity=RuleSeverity(r.get("severity", "error")),
                    message=r.get("message"),
                )
            )
        elif rtype == "group_presence":
            built_rules.append(
                GroupPresenceRule(
                    rule_id=r["rule_id"],
                    kind=GroupPresenceKind(r["kind"]),
                    fields=tuple(r["fields"]),
                    severity=RuleSeverity(r.get("severity", "error")),
                    message=r.get("message"),
                )
            )
        else:
            raise ValueError(f"Unknown rule_type: {rtype}")

    return ExtractionSchema(
        schema_id=base.schema_id,
        schema_version=base.schema_version,
        fields=dict(base.fields),
        cardinality=base.cardinality,
        allow_additional_fields=base.allow_additional_fields,
        max_depth=base.max_depth,
        rules=tuple(built_rules),
    )


def _base_fields_dict() -> dict[str, Any]:
    def leaf(
        name: str, ft: str, *, required: bool = False, nullable: bool = False
    ) -> dict[str, Any]:
        return {
            "name": name,
            "field_type": ft,
            "required": required,
            "nullable": nullable,
            "constraints": {},
            "properties": None,
            "items": None,
            "allow_additional_fields": False,
        }

    return {
        "issue_date": leaf("issue_date", "date"),
        "due_date": leaf("due_date", "date"),
        "count": leaf("count", "integer"),
        "subtotal": leaf("subtotal", "decimal"),
        "tax": leaf("tax", "decimal"),
        "discount": leaf("discount", "decimal"),
        "total": leaf("total", "decimal"),
        "flag_a": leaf("flag_a", "boolean"),
        "flag_b": leaf("flag_b", "boolean"),
        "status": leaf("status", "string"),
        "po_number": leaf("po_number", "string"),
        "ssn": leaf("ssn", "string"),
        "ein": leaf("ein", "string"),
        "amounts": {
            "name": "amounts",
            "field_type": "array",
            "required": False,
            "nullable": False,
            "constraints": {},
            "properties": None,
            "items": leaf("amounts_item", "decimal", required=True),
            "allow_additional_fields": False,
        },
        "tags": {
            "name": "tags",
            "field_type": "array",
            "required": False,
            "nullable": False,
            "constraints": {},
            "properties": None,
            "items": leaf("tags_item", "string", required=True),
            "allow_additional_fields": False,
        },
        "line_items": {
            "name": "line_items",
            "field_type": "array",
            "required": False,
            "nullable": False,
            "constraints": {},
            "properties": None,
            "items": {
                "name": "line_items_item",
                "field_type": "object",
                "required": True,
                "nullable": False,
                "constraints": {},
                "properties": {
                    "label": leaf("label", "string", required=True),
                    "amount": leaf("amount", "decimal", required=True),
                },
                "items": None,
                "allow_additional_fields": False,
            },
            "allow_additional_fields": False,
        },
        "totals": {
            "name": "totals",
            "field_type": "object",
            "required": False,
            "nullable": False,
            "constraints": {},
            "properties": {
                "net": leaf("net", "decimal", required=True),
                "gross": leaf("gross", "decimal", required=True),
            },
            "items": None,
            "allow_additional_fields": False,
        },
    }


def _schema_vec(rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_id": "cf-rules-wf",
        "schema_version": "1.0",
        "cardinality": "single",
        "allow_additional_fields": False,
        "max_depth": 16,
        "fields": _base_fields_dict(),
        "rules": rules,
    }


def build_cross_field_schema_well_formed_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated valid and invalid cross-field rule-schema vectors for Layer 1 & Layer 2."""
    valid_all_four = _schema_vec(
        [
            {
                "rule_type": "comparison",
                "rule_id": "cmp_dates",
                "left_field": "due_date",
                "operator": "gte",
                "right_field": "issue_date",
                "severity": "error",
                "message": None,
            },
            {
                "rule_type": "comparison",
                "rule_id": "cmp_int_dec",
                "left_field": "count",
                "operator": "lte",
                "right_field": "totals.gross",
                "severity": "warning",
                "message": "Count should not exceed gross.",
            },
            {
                "rule_type": "comparison",
                "rule_id": "cmp_bool_eq",
                "left_field": "flag_a",
                "operator": "ne",
                "right_field": "flag_b",
                "severity": "error",
                "message": None,
            },
            {
                "rule_type": "arithmetic",
                "rule_id": "arith_total",
                "terms": [
                    {
                        "field": "line_items",
                        "sign": 1,
                        "item_field": "amount",
                        "treat_missing_as_zero": False,
                    },
                    {
                        "field": "amounts",
                        "sign": 1,
                        "item_field": None,
                        "treat_missing_as_zero": True,
                    },
                    {
                        "field": "discount",
                        "sign": -1,
                        "item_field": None,
                        "treat_missing_as_zero": True,
                    },
                ],
                "target_field": "total",
                "tolerance": "0.01",
                "severity": "error",
                "message": None,
            },
            {
                "rule_type": "conditional_presence",
                "rule_id": "cond_in",
                "kind": "required_if",
                "when": {
                    "field": "status",
                    "operator": "in",
                    "value": None,
                    "values": [
                        {"kind": "string", "value": "approved"},
                        {"kind": "string", "value": "paid"},
                    ],
                },
                "target_fields": ["po_number", "totals.net"],
                "severity": "error",
                "message": None,
            },
            {
                "rule_type": "group_presence",
                "rule_id": "grp_excl",
                "kind": "mutually_exclusive",
                "fields": ["ssn", "ein"],
                "severity": "warning",
                "message": None,
            },
        ]
    )

    return [
        (valid_all_four, True),
        # Empty rules list is valid
        (_schema_vec([]), True),
        # Duplicate rule_id
        (
            _schema_vec(
                [
                    {
                        "rule_type": "group_presence",
                        "rule_id": "dup",
                        "kind": "at_least_one_of",
                        "fields": ["ssn", "ein"],
                    },
                    {
                        "rule_type": "group_presence",
                        "rule_id": "dup",
                        "kind": "mutually_exclusive",
                        "fields": ["ssn", "ein"],
                    },
                ]
            ),
            False,
        ),
        # Blank rule_id
        (
            _schema_vec(
                [
                    {
                        "rule_type": "group_presence",
                        "rule_id": "   ",
                        "kind": "at_least_one_of",
                        "fields": ["ssn", "ein"],
                    }
                ]
            ),
            False,
        ),
        # Blank message
        (
            _schema_vec(
                [
                    {
                        "rule_type": "group_presence",
                        "rule_id": "blank_msg",
                        "kind": "at_least_one_of",
                        "fields": ["ssn", "ein"],
                        "message": "   ",
                    }
                ]
            ),
            False,
        ),
        # Unknown field path
        (
            _schema_vec(
                [
                    {
                        "rule_type": "comparison",
                        "rule_id": "unk",
                        "left_field": "missing_field",
                        "operator": "eq",
                        "right_field": "status",
                    }
                ]
            ),
            False,
        ),
        # Traversing non-object field
        (
            _schema_vec(
                [
                    {
                        "rule_type": "comparison",
                        "rule_id": "non_obj",
                        "left_field": "status.sub",
                        "operator": "eq",
                        "right_field": "po_number",
                    }
                ]
            ),
            False,
        ),
        # Self-comparison
        (
            _schema_vec(
                [
                    {
                        "rule_type": "comparison",
                        "rule_id": "self_cmp",
                        "left_field": "subtotal",
                        "operator": "eq",
                        "right_field": "subtotal",
                    }
                ]
            ),
            False,
        ),
        # Ordered comparison on BOOLEAN
        (
            _schema_vec(
                [
                    {
                        "rule_type": "comparison",
                        "rule_id": "bool_lt",
                        "left_field": "flag_a",
                        "operator": "lt",
                        "right_field": "flag_b",
                    }
                ]
            ),
            False,
        ),
        # Incompatible scalar types (string vs integer)
        (
            _schema_vec(
                [
                    {
                        "rule_type": "comparison",
                        "rule_id": "str_int",
                        "left_field": "status",
                        "operator": "eq",
                        "right_field": "count",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic empty terms
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "empty_terms",
                        "terms": [],
                        "target_field": "total",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic invalid sign
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "bad_sign",
                        "terms": [{"field": "subtotal", "sign": 2}],
                        "target_field": "total",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic negative tolerance
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "neg_tol",
                        "terms": [{"field": "subtotal", "sign": 1}],
                        "target_field": "total",
                        "tolerance": "-0.01",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic non-numeric target
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "bad_target",
                        "terms": [{"field": "subtotal", "sign": 1}],
                        "target_field": "status",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic non-numeric array-of-objects item_field
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "bad_item_field",
                        "terms": [{"field": "line_items", "sign": 1, "item_field": "label"}],
                        "target_field": "total",
                    }
                ]
            ),
            False,
        ),
        # Arithmetic non-numeric primitive array term
        (
            _schema_vec(
                [
                    {
                        "rule_type": "arithmetic",
                        "rule_id": "bad_prim_arr",
                        "terms": [{"field": "tags", "sign": 1}],
                        "target_field": "total",
                    }
                ]
            ),
            False,
        ),
        # Conditional is_present with value set
        (
            _schema_vec(
                [
                    {
                        "rule_type": "conditional_presence",
                        "rule_id": "bad_isp",
                        "kind": "required_if",
                        "when": {
                            "field": "status",
                            "operator": "is_present",
                            "value": {"kind": "string", "value": "x"},
                        },
                        "target_fields": ["po_number"],
                    }
                ]
            ),
            False,
        ),
        # Conditional equals with incompatible literal type
        (
            _schema_vec(
                [
                    {
                        "rule_type": "conditional_presence",
                        "rule_id": "bad_lit",
                        "kind": "required_if",
                        "when": {
                            "field": "count",
                            "operator": "equals",
                            "value": {"kind": "boolean", "value": True},
                        },
                        "target_fields": ["po_number"],
                    }
                ]
            ),
            False,
        ),
        # Conditional in with empty values
        (
            _schema_vec(
                [
                    {
                        "rule_type": "conditional_presence",
                        "rule_id": "empty_in",
                        "kind": "required_if",
                        "when": {"field": "status", "operator": "in", "values": []},
                        "target_fields": ["po_number"],
                    }
                ]
            ),
            False,
        ),
        # Conditional controlling field in target_fields
        (
            _schema_vec(
                [
                    {
                        "rule_type": "conditional_presence",
                        "rule_id": "self_target",
                        "kind": "required_if",
                        "when": {"field": "status", "operator": "is_present"},
                        "target_fields": ["status"],
                    }
                ]
            ),
            False,
        ),
        # Group < 2 fields
        (
            _schema_vec(
                [
                    {
                        "rule_type": "group_presence",
                        "rule_id": "grp_one",
                        "kind": "mutually_exclusive",
                        "fields": ["ssn"],
                    }
                ]
            ),
            False,
        ),
        # Group duplicate fields
        (
            _schema_vec(
                [
                    {
                        "rule_type": "group_presence",
                        "rule_id": "grp_dup",
                        "kind": "at_least_one_of",
                        "fields": ["ssn", "ssn"],
                    }
                ]
            ),
            False,
        ),
    ]


def build_invoice_cross_field_schema() -> ExtractionSchema:
    """Rich multi-rule schema exercising all 4 rule families and both ERROR/WARNING severities."""
    base = ExtractionSchema.from_dict(
        {
            "schema_id": "invoice-cross-field",
            "schema_version": "1.0",
            "fields": {
                "issue_date": {"type": "date", "required": False},
                "due_date": {"type": "date", "required": False},
                "subtotal": {"type": "decimal", "required": False},
                "tax": {"type": "decimal", "required": False},
                "discount": {"type": "decimal", "required": False},
                "total": {"type": "decimal", "required": False},
                "payment_method": {"type": "string", "required": False},
                "po_number": {"type": "string", "required": False},
                "nullable_note": {"type": "string", "required": False, "nullable": True},
                "cash_receipt": {"type": "string", "required": False},
                "ssn": {"type": "string", "required": False},
                "ein": {"type": "string", "required": False},
                "adjustments": {
                    "type": "array",
                    "required": False,
                    "items": {"type": "decimal"},
                },
                "line_items": {
                    "type": "array",
                    "required": False,
                    "items": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "decimal", "required": True},
                        },
                    },
                },
                "totals": {
                    "type": "object",
                    "required": False,
                    "properties": {
                        "net": {"type": "decimal", "required": False},
                        "gross": {"type": "decimal", "required": False},
                    },
                },
            },
        }
    )
    return ExtractionSchema(
        schema_id=base.schema_id,
        schema_version=base.schema_version,
        fields=dict(base.fields),
        cardinality=base.cardinality,
        allow_additional_fields=base.allow_additional_fields,
        max_depth=base.max_depth,
        rules=(
            ComparisonRule(
                rule_id="due_gte_issue",
                left_field="due_date",
                operator=ComparisonOperator.GTE,
                right_field="issue_date",
                severity=RuleSeverity.ERROR,
            ),
            ComparisonRule(
                rule_id="nested_gross_gte_net",
                left_field="totals.gross",
                operator=ComparisonOperator.GTE,
                right_field="totals.net",
                severity=RuleSeverity.WARNING,
            ),
            ArithmeticRule(
                rule_id="balance_total",
                terms=(
                    ArithmeticTerm(field="subtotal", sign=1),
                    ArithmeticTerm(field="tax", sign=1, treat_missing_as_zero=True),
                    ArithmeticTerm(field="discount", sign=-1, treat_missing_as_zero=True),
                ),
                target_field="total",
                tolerance=Decimal("0.01"),
                severity=RuleSeverity.ERROR,
            ),
            ArithmeticRule(
                rule_id="line_items_sum_subtotal",
                terms=(
                    ArithmeticTerm(field="line_items", sign=1, item_field="amount"),
                    ArithmeticTerm(field="adjustments", sign=1, treat_missing_as_zero=True),
                ),
                target_field="subtotal",
                tolerance=Decimal("0"),
                severity=RuleSeverity.ERROR,
            ),
            ConditionalPresenceRule(
                rule_id="invoice_requires_po_and_note",
                kind=ConditionalPresenceKind.REQUIRED_IF,
                when=FieldCondition(
                    field="payment_method",
                    operator=ConditionOperator.EQUALS,
                    value="invoice",
                ),
                target_fields=("po_number", "nullable_note"),
                severity=RuleSeverity.ERROR,
            ),
            ConditionalPresenceRule(
                rule_id="invoice_forbids_cash_receipt",
                kind=ConditionalPresenceKind.FORBIDDEN_IF,
                when=FieldCondition(
                    field="payment_method",
                    operator=ConditionOperator.IN,
                    values=("invoice", "wire"),
                ),
                target_fields=("cash_receipt",),
                severity=RuleSeverity.WARNING,
            ),
            ConditionalPresenceRule(
                rule_id="missing_pm_warns_po",
                kind=ConditionalPresenceKind.REQUIRED_IF,
                when=FieldCondition(
                    field="payment_method",
                    operator=ConditionOperator.IS_MISSING,
                ),
                target_fields=("po_number",),
                severity=RuleSeverity.WARNING,
            ),
            GroupPresenceRule(
                rule_id="tax_id_exclusive",
                kind=GroupPresenceKind.MUTUALLY_EXCLUSIVE,
                fields=("ssn", "ein"),
                severity=RuleSeverity.ERROR,
            ),
            GroupPresenceRule(
                rule_id="tax_id_at_least_one",
                kind=GroupPresenceKind.AT_LEAST_ONE_OF,
                fields=("ssn", "ein"),
                severity=RuleSeverity.WARNING,
            ),
        ),
    )


def build_curated_cross_field_eval_cases() -> list[
    tuple[str, ExtractionSchema, dict[str, tuple[FieldPresence, Any]]]
]:
    """Curated evaluation records exercising all 4 rule families, edge cases, and severities."""
    schema = build_invoice_cross_field_schema()
    clean_valid: dict[str, tuple[FieldPresence, Any]] = {
        "issue_date": (FieldPresence.PRESENT, date(2026, 1, 10)),
        "due_date": (FieldPresence.PRESENT, date(2026, 2, 10)),
        "subtotal": (FieldPresence.PRESENT, Decimal("100.00")),
        "tax": (FieldPresence.PRESENT, Decimal("10.00")),
        "discount": (FieldPresence.PRESENT, Decimal("5.00")),
        "total": (FieldPresence.PRESENT, Decimal("105.00")),
        "payment_method": (FieldPresence.PRESENT, "invoice"),
        "po_number": (FieldPresence.PRESENT, "PO-42"),
        "nullable_note": (FieldPresence.PRESENT, None),
        "ssn": (FieldPresence.PRESENT, "123-45-6789"),
        "line_items": (
            FieldPresence.PRESENT,
            [{"amount": Decimal("60.00")}, {"amount": Decimal("40.00")}],
        ),
        "totals": (FieldPresence.PRESENT, {"net": Decimal("90.00"), "gross": Decimal("105.00")}),
    }
    return [
        ("clean_all_pass_with_nullable_required_if", schema, clean_valid),
        (
            "tolerance_exact_boundary_pass",
            schema,
            {**clean_valid, "total": (FieldPresence.PRESENT, Decimal("105.01"))},
        ),
        (
            "tolerance_exceeded_error_all_fields_valid",
            schema,
            {**clean_valid, "total": (FieldPresence.PRESENT, Decimal("105.02"))},
        ),
        (
            "treat_missing_as_zero_tax_and_discount_missing",
            schema,
            {
                **clean_valid,
                "tax": (FieldPresence.MISSING, None),
                "discount": (FieldPresence.MISSING, None),
                "total": (FieldPresence.PRESENT, Decimal("100.00")),
            },
        ),
        (
            "warning_only_failures_keep_record_valid",
            schema,
            {
                **clean_valid,
                "cash_receipt": (FieldPresence.PRESENT, "RC-1"),
                "ssn": (FieldPresence.MISSING, None),
                "ein": (FieldPresence.MISSING, None),
                "totals": (
                    FieldPresence.PRESENT,
                    {"net": Decimal("120.00"), "gross": Decimal("100.00")},
                ),
            },
        ),
        (
            "mixed_error_and_warning_violations",
            schema,
            {
                **clean_valid,
                "due_date": (FieldPresence.PRESENT, date(2026, 1, 1)),
                "po_number": (FieldPresence.MISSING, None),
                "cash_receipt": (FieldPresence.PRESENT, "RC-2"),
                "ein": (FieldPresence.PRESENT, "99-1234567"),
            },
        ),
        (
            "is_missing_vs_present_null_on_controlling_field",
            schema,
            {
                **clean_valid,
                "payment_method": (FieldPresence.PRESENT, None),
                "po_number": (FieldPresence.MISSING, None),
            },
        ),
        (
            "is_missing_true_triggers_warning_when_po_missing",
            schema,
            {
                **clean_valid,
                "payment_method": (FieldPresence.MISSING, None),
                "po_number": (FieldPresence.MISSING, None),
            },
        ),
        (
            "forbidden_if_ignores_explicit_null",
            schema,
            {
                **clean_valid,
                "cash_receipt": (FieldPresence.PRESENT, None),
            },
        ),
        (
            "primitive_numeric_array_and_empty_line_items",
            schema,
            {
                **clean_valid,
                "line_items": (FieldPresence.PRESENT, []),
                "adjustments": (FieldPresence.PRESENT, [Decimal("75.00"), Decimal("25.00")]),
            },
        ),
        (
            "malformed_array_element_skips_arithmetic_cleanly",
            schema,
            {
                **clean_valid,
                "line_items": (FieldPresence.PRESENT, [{"amount": "bad"}]),
            },
        ),
        (
            "non_coercion_bool_and_nan_skip_cross_field_rules",
            schema,
            {
                **clean_valid,
                "subtotal": (FieldPresence.PRESENT, True),
                "total": (FieldPresence.PRESENT, Decimal("NaN")),
                "due_date": (FieldPresence.PRESENT, datetime(2026, 1, 1)),
            },
        ),
    ]
