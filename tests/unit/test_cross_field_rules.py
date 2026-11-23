"""Unit tests for declarative cross-field & conditional validation rules (Spec 004)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from structa_maestro import (
    ArithmeticRule,
    ArithmeticTerm,
    ComparisonOperator,
    ComparisonRule,
    ConditionalPresenceKind,
    ConditionalPresenceRule,
    ConditionOperator,
    DeterministicValidator,
    ExtractionSchema,
    FieldCondition,
    FieldPresence,
    GroupPresenceKind,
    GroupPresenceRule,
    IssueSeverity,
    PipelineStage,
    RuleSeverity,
    SchemaDefinitionError,
    ValidationStatus,
)


def test_comparison_rules_all_operators_nested_paths_and_operand_skipping() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "comp-schema",
            "schema_version": "1.0",
            "fields": {
                "issue_date": {"type": "date", "required": False, "nullable": True},
                "due_date": {"type": "date", "required": False, "nullable": True},
                "net_amount": {"type": "decimal", "required": False},
                "gross_amount": {"type": "integer", "required": False},
                "billing_currency": {"type": "string", "required": False},
                "payout_currency": {"type": "string", "required": False},
                "active_a": {"type": "boolean", "required": False},
                "active_b": {"type": "boolean", "required": False},
                "period": {
                    "type": "object",
                    "required": False,
                    "properties": {
                        "start": {"type": "date", "required": False},
                        "end": {"type": "date", "required": False},
                    },
                },
            },
            "rules": [
                {
                    "rule_id": "issue-lte-due",
                    "kind": "comparison",
                    "left_field": "issue_date",
                    "operator": "lte",
                    "right_field": "due_date",
                },
                {
                    "rule_id": "net-lt-gross",
                    "kind": "comparison",
                    "left_field": "net_amount",
                    "operator": "lt",
                    "right_field": "gross_amount",
                },
                {
                    "rule_id": "same-currency",
                    "kind": "comparison",
                    "left_field": "billing_currency",
                    "operator": "eq",
                    "right_field": "payout_currency",
                },
                {
                    "rule_id": "flags-ne",
                    "kind": "comparison",
                    "left_field": "active_a",
                    "operator": "ne",
                    "right_field": "active_b",
                },
                {
                    "rule_id": "period-ordered",
                    "kind": "comparison",
                    "left_field": "period.start",
                    "operator": "lt",
                    "right_field": "period.end",
                },
            ],
        }
    )
    validator = DeterministicValidator()

    # 1. All pass
    ok = validator.validate_record(
        {
            "issue_date": (FieldPresence.PRESENT, date(2026, 3, 15)),
            "due_date": (FieldPresence.PRESENT, date(2026, 3, 15)),
            "net_amount": (FieldPresence.PRESENT, Decimal("99.50")),
            "gross_amount": (FieldPresence.PRESENT, 100),
            "billing_currency": (FieldPresence.PRESENT, "USD"),
            "payout_currency": (FieldPresence.PRESENT, "USD"),
            "active_a": (FieldPresence.PRESENT, True),
            "active_b": (FieldPresence.PRESENT, False),
            "period": (
                FieldPresence.PRESENT,
                {"start": date(2026, 1, 1), "end": date(2026, 12, 31)},
            ),
        },
        schema,
        record_id="rec-1",
    )
    assert ok.record_outcome.status == ValidationStatus.VALID
    assert ok.record_outcome.errors == []

    # 2. Violations detected at record level while per-field outcomes stay VALID (Clarification Q1)
    bad = validator.validate_record(
        {
            "issue_date": (FieldPresence.PRESENT, date(2026, 4, 20)),
            "due_date": (FieldPresence.PRESENT, date(2026, 4, 15)),
            "net_amount": (FieldPresence.PRESENT, Decimal("120.00")),
            "gross_amount": (FieldPresence.PRESENT, 100),
            "billing_currency": (FieldPresence.PRESENT, "USD"),
            "payout_currency": (FieldPresence.PRESENT, "EUR"),
            "active_a": (FieldPresence.PRESENT, True),
            "active_b": (FieldPresence.PRESENT, True),
            "period": (
                FieldPresence.PRESENT,
                {"start": date(2026, 12, 31), "end": date(2026, 1, 1)},
            ),
        },
        schema,
        record_id="rec-2",
    )
    assert bad.record_outcome.status == ValidationStatus.INVALID
    assert len(bad.record_outcome.errors) == 5
    assert all(e.code == "cross_field_comparison_failed" for e in bad.record_outcome.errors)
    assert bad.field_outcomes["issue_date"].status == ValidationStatus.VALID
    assert bad.field_outcomes["due_date"].status == ValidationStatus.VALID

    # 3. Missing, null, and non-coerced type-invalid operands skip cleanly without cascading errors
    skipped = validator.validate_record(
        {
            "issue_date": (FieldPresence.PRESENT, None),
            "due_date": (FieldPresence.PRESENT, date(2026, 4, 15)),
            "net_amount": (FieldPresence.PRESENT, "999"),  # string is NOT coerced to Decimal!
            "gross_amount": (FieldPresence.PRESENT, 100),
        },
        schema,
        record_id="rec-3",
    )
    assert skipped.record_outcome.status == ValidationStatus.INVALID
    # Only the single-field type_mismatch error for net_amount — zero cross-field cascade
    assert [e.code for e in skipped.record_outcome.errors] == ["type_mismatch"]


def test_arithmetic_rules_exact_decimal_tolerance_collection_sums_and_treat_missing_as_zero() -> (
    None
):
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "arith-schema",
            "schema_version": "1.0",
            "fields": {
                "subtotal": {"type": "decimal", "required": True},
                "tax": {"type": "decimal", "required": False},
                "discount": {"type": "decimal", "required": False},
                "total_amount": {"type": "decimal", "required": True},
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
                            "description": {"type": "string", "required": True},
                            "amount": {"type": "decimal", "required": True},
                        },
                    },
                },
            },
            "rules": [
                {
                    "rule_id": "total-balance",
                    "kind": "arithmetic",
                    "terms": [
                        {"field": "subtotal", "sign": 1},
                        {"field": "tax", "sign": 1},
                        {"field": "discount", "sign": -1, "treat_missing_as_zero": True},
                    ],
                    "target_field": "total_amount",
                    "tolerance": "0.01",
                },
                {
                    "rule_id": "items-plus-adj",
                    "kind": "arithmetic",
                    "terms": [
                        {"field": "line_items", "item_field": "amount", "sign": 1},
                        {"field": "adjustments", "sign": 1, "treat_missing_as_zero": True},
                    ],
                    "target_field": "subtotal",
                    "tolerance": "0",
                },
            ],
        }
    )
    validator = DeterministicValidator()

    # Exact decimal precision: 0.10 + 0.20 == 0.30 (no float drift), missing discount -> 0
    exact_ok = validator.validate_record(
        {
            "subtotal": (FieldPresence.PRESENT, Decimal("0.30")),
            "tax": (FieldPresence.PRESENT, Decimal("0.05")),
            "total_amount": (FieldPresence.PRESENT, Decimal("0.36")),  # within 0.01 tol
            "line_items": (
                FieldPresence.PRESENT,
                [
                    {"description": "A", "amount": Decimal("0.10")},
                    {"description": "B", "amount": Decimal("0.20")},
                ],
            ),
        },
        schema,
        record_id="r-1",
    )
    assert exact_ok.record_outcome.status == ValidationStatus.VALID

    # Out of tolerance (0.02 > 0.01) & line-items mismatch
    mismatch = validator.validate_record(
        {
            "subtotal": (FieldPresence.PRESENT, Decimal("100.00")),
            "tax": (FieldPresence.PRESENT, Decimal("19.00")),
            "discount": (FieldPresence.PRESENT, Decimal("5.00")),
            "total_amount": (FieldPresence.PRESENT, Decimal("114.02")),
            "adjustments": (FieldPresence.PRESENT, [Decimal("1.00"), Decimal("-1.00")]),
            "line_items": (
                FieldPresence.PRESENT,
                [{"description": "Only", "amount": Decimal("90.00")}],
            ),
        },
        schema,
        record_id="r-2",
    )
    assert mismatch.record_outcome.status == ValidationStatus.INVALID
    assert [e.code for e in mismatch.record_outcome.errors] == [
        "cross_field_arithmetic_mismatch",
        "cross_field_arithmetic_mismatch",
    ]

    # Empty array sums to Decimal("0") cleanly
    empty_arr_ok = validator.validate_record(
        {
            "subtotal": (FieldPresence.PRESENT, Decimal("0.00")),
            "tax": (FieldPresence.MISSING, None),  # default skip on total-balance
            "total_amount": (FieldPresence.PRESENT, Decimal("999.00")),
            "line_items": (FieldPresence.PRESENT, []),
        },
        schema,
        record_id="r-3",
    )
    assert empty_arr_ok.record_outcome.status == ValidationStatus.VALID


def test_conditional_and_group_presence_rules_and_empty_string_distinction() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "cond-group-schema",
            "schema_version": "1.0",
            "fields": {
                "country": {"type": "string", "required": False},
                "vat_number": {"type": "string", "required": False},
                "nullable_note": {"type": "string", "required": False, "nullable": True},
                "discount_amount": {"type": "decimal", "required": False},
                "discount_reason": {"type": "string", "required": False},
                "passport_number": {"type": "string", "required": False},
                "national_id": {"type": "string", "required": False},
                "contact_email": {"type": "string", "required": False},
                "contact_phone": {"type": "string", "required": False, "nullable": True},
            },
            "rules": [
                {
                    "rule_id": "eu-vat-req",
                    "kind": "required_if",
                    "when": {"field": "country", "operator": "in", "values": ["DE", "FR"]},
                    "target_fields": ["vat_number", "nullable_note"],
                },
                {
                    "rule_id": "no-reason-without-discount",
                    "kind": "forbidden_if",
                    "when": {"field": "discount_amount", "operator": "equals", "value": "0"},
                    "target_fields": ["discount_reason"],
                },
                {
                    "rule_id": "id-xor",
                    "kind": "mutually_exclusive",
                    "fields": ["passport_number", "national_id"],
                },
                {
                    "rule_id": "contact-any",
                    "kind": "at_least_one_of",
                    "fields": ["contact_email", "contact_phone"],
                },
            ],
        }
    )
    validator = DeterministicValidator()

    # Empty string "" counts as PRESENT (distinct from missing/null); nullable_note=None satisfies
    # required_if because nullable_note is explicitly nullable=True!
    ok = validator.validate_record(
        {
            "country": (FieldPresence.PRESENT, "DE"),
            "vat_number": (FieldPresence.PRESENT, ""),
            "nullable_note": (FieldPresence.PRESENT, None),
            "discount_amount": (FieldPresence.PRESENT, Decimal("0.00")),
            "passport_number": (FieldPresence.PRESENT, "P123"),
            "contact_email": (FieldPresence.PRESENT, ""),
        },
        schema,
        record_id="r-ok",
    )
    assert ok.record_outcome.status == ValidationStatus.VALID

    # Violate all 4 rules simultaneously
    bad = validator.validate_record(
        {
            "country": (FieldPresence.PRESENT, "FR"),
            "vat_number": (FieldPresence.MISSING, None),
            "nullable_note": (FieldPresence.MISSING, None),
            "discount_amount": (FieldPresence.PRESENT, Decimal("0")),
            "discount_reason": (FieldPresence.PRESENT, "Promo"),
            "passport_number": (FieldPresence.PRESENT, "P123"),
            "national_id": (FieldPresence.PRESENT, "N456"),
            "contact_email": (FieldPresence.MISSING, None),
            "contact_phone": (FieldPresence.PRESENT, None),  # null counts as unset
        },
        schema,
        record_id="r-bad",
    )
    codes = [e.code for e in bad.record_outcome.errors]
    assert codes == [
        "conditional_required_missing",
        "conditional_required_missing",
        "conditional_forbidden_present",
        "mutually_exclusive_fields",
        "missing_required_group",
    ]


def test_not_equals_does_not_fire_on_missing_or_null_and_warning_severity_stays_valid() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "warn-schema",
            "schema_version": "1.0",
            "fields": {
                "country": {"type": "string", "required": False},
                "customs_code": {"type": "string", "required": False},
                "a": {"type": "integer", "required": False},
                "b": {"type": "integer", "required": False},
            },
            "rules": [
                {
                    "rule_id": "non-us-customs",
                    "kind": "required_if",
                    "when": {"field": "country", "operator": "not_equals", "value": "US"},
                    "target_fields": ["customs_code"],
                },
                {
                    "rule_id": "advisory-a-lte-b",
                    "kind": "comparison",
                    "left_field": "a",
                    "operator": "lte",
                    "right_field": "b",
                    "severity": "warning",
                    "message": "Advisory check: a exceeded b.",
                },
            ],
        }
    )
    validator = DeterministicValidator()

    # country missing -> not_equals does NOT trigger (U2); advisory warning leaves record VALID
    res = validator.validate_record(
        {
            "country": (FieldPresence.MISSING, None),
            "a": (FieldPresence.PRESENT, 10),
            "b": (FieldPresence.PRESENT, 5),
        },
        schema,
        record_id="r-warn",
    )
    assert res.record_outcome.status == ValidationStatus.VALID
    assert res.record_outcome.errors == []
    assert len(res.warnings) == 1
    assert res.warnings[0].code == "cross_field_comparison_failed"
    assert res.warnings[0].severity == IssueSeverity.WARNING
    assert res.warnings[0].stage == PipelineStage.VALIDATION
    assert res.warnings[0].message == "Advisory check: a exceeded b."


def test_eager_schema_validation_rejects_invalid_rules() -> None:
    base_fields: dict[str, Any] = {
        "d": {"type": "date"},
        "s": {"type": "string"},
        "n": {"type": "decimal"},
        "b": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
    }

    # Unknown field path
    with pytest.raises(SchemaDefinitionError, match="unknown field path"):
        ExtractionSchema.from_dict(
            {
                "schema_id": "bad",
                "fields": base_fields,
                "rules": [
                    {
                        "rule_id": "r1",
                        "kind": "comparison",
                        "left_field": "d",
                        "operator": "lt",
                        "right_field": "nonexistent",
                    }
                ],
            }
        )

    # Incompatible types (date vs string) & boolean ordering
    with pytest.raises(SchemaDefinitionError, match="incompatible"):
        ExtractionSchema.from_dict(
            {
                "schema_id": "bad",
                "fields": base_fields,
                "rules": [
                    {
                        "rule_id": "r1",
                        "kind": "comparison",
                        "left_field": "d",
                        "operator": "lte",
                        "right_field": "s",
                    }
                ],
            }
        )
    with pytest.raises(SchemaDefinitionError, match="incompatible"):
        ExtractionSchema.from_dict(
            {
                "schema_id": "bad",
                "fields": {**base_fields, "b2": {"type": "boolean"}},
                "rules": [
                    {
                        "rule_id": "r1",
                        "kind": "comparison",
                        "left_field": "b",
                        "operator": "lt",
                        "right_field": "b2",
                    }
                ],
            }
        )

    # Duplicate rule_id
    with pytest.raises(SchemaDefinitionError, match="Duplicate"):
        ExtractionSchema.from_dict(
            {
                "schema_id": "bad",
                "fields": {**base_fields, "s2": {"type": "string"}},
                "rules": [
                    {
                        "rule_id": "dup",
                        "kind": "comparison",
                        "left_field": "s",
                        "operator": "eq",
                        "right_field": "s2",
                    },
                    {
                        "rule_id": "dup",
                        "kind": "mutually_exclusive",
                        "fields": ["s", "s2"],
                    },
                ],
            }
        )

    # Self-comparison, negative tolerance, non-numeric arithmetic term, <2 group fields, bad cond
    with pytest.raises(SchemaDefinitionError):
        ComparisonRule(rule_id="r", left_field="a", operator=ComparisonOperator.EQ, right_field="a")
    with pytest.raises(SchemaDefinitionError):
        ArithmeticRule(
            rule_id="r",
            terms=(ArithmeticTerm(field="n"),),
            target_field="n",
            tolerance=Decimal("-0.01"),
        )
    with pytest.raises(SchemaDefinitionError, match="must contain numeric items"):
        ExtractionSchema.from_dict(
            {
                "schema_id": "bad",
                "fields": base_fields,
                "rules": [
                    {
                        "rule_id": "bad-arr",
                        "kind": "arithmetic",
                        "terms": [{"field": "tags"}],
                        "target_field": "n",
                    }
                ],
            }
        )
    with pytest.raises(SchemaDefinitionError):
        GroupPresenceRule(rule_id="g", kind=GroupPresenceKind.AT_LEAST_ONE_OF, fields=("s",))
    with pytest.raises(SchemaDefinitionError):
        FieldCondition(field="s", operator=ConditionOperator.EQUALS, value=None)
    with pytest.raises(SchemaDefinitionError):
        ConditionalPresenceRule(
            rule_id="c",
            kind=ConditionalPresenceKind.REQUIRED_IF,
            when=FieldCondition(field="s", operator=ConditionOperator.IS_PRESENT),
            target_fields=("s",),
            severity=RuleSeverity.ERROR,
        )
