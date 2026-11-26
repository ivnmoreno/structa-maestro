"""Layer 1: Always-on pure-Python & Hypothesis conformance tests for Cross-Field Rules (Spec 014).

Verifies empirical conformance between `schema/rules.py` / `validation/cross_field.py` and the
formal specifications in `formal/StructaFormal/CrossFieldModels.lean`, `CrossFieldInvariants.lean`,
and `CrossFieldValidation.lean` without requiring Lean 4 installed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conformance.cross_field_reference import (
    build_cross_field_schema_well_formed_vectors,
    build_curated_cross_field_eval_cases,
    build_invoice_cross_field_schema,
    instantiate_python_schema_with_rules_from_vector,
)

from structa_maestro.contracts.enums import (
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ValidationStatus,
)
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.validation.validator import DeterministicValidator


@pytest.mark.parametrize(
    ("schema_vec", "expected_valid"),
    build_cross_field_schema_well_formed_vectors(),
)
def test_conformance_cross_field_rule_well_formedness_vectors(
    schema_vec: dict[str, Any],
    expected_valid: bool,
) -> None:
    if expected_valid:
        schema = instantiate_python_schema_with_rules_from_vector(schema_vec)
        rule_ids = [r.rule_id for r in schema.rules]
        assert len(rule_ids) == len(set(rule_ids))
    else:
        with pytest.raises((SchemaDefinitionError, ValueError)):
            instantiate_python_schema_with_rules_from_vector(schema_vec)


def test_conformance_cross_field_determinism_and_severity_separation() -> None:
    validator = DeterministicValidator()
    for name, schema, fields in build_curated_cross_field_eval_cases():
        res1 = validator.validate_record(fields, schema, record_id="rec-1")
        res2 = validator.validate_record(fields, schema, record_id="rec-1")
        assert res1 == res2, f"Non-deterministic cross-field result for {name}"

        for err in res1.record_outcome.errors:
            assert err.severity == IssueSeverity.ERROR
            assert err.stage == PipelineStage.VALIDATION
        for warn in res1.warnings:
            assert warn.severity == IssueSeverity.WARNING
            assert warn.stage == PipelineStage.VALIDATION

        if res1.record_outcome.errors:
            assert res1.record_outcome.status == ValidationStatus.INVALID
        else:
            assert res1.record_outcome.status == ValidationStatus.VALID


def test_conformance_error_rule_invalidates_valid_fields_and_warnings_never_invalidate() -> None:
    validator = DeterministicValidator()
    cases = dict(
        (name, (schema, fields)) for name, schema, fields in build_curated_cross_field_eval_cases()
    )

    # Error rule forces INVALID even when every individual field is VALID
    schema, err_fields = cases["tolerance_exceeded_error_all_fields_valid"]
    res_err = validator.validate_record(err_fields, schema, record_id="rec-err")
    assert all(o.status == ValidationStatus.VALID for o in res_err.field_outcomes.values())
    assert res_err.record_outcome.status == ValidationStatus.INVALID
    assert [(e.field_path, e.code) for e in res_err.record_outcome.errors] == [
        ("total", "cross_field_arithmetic_mismatch")
    ]

    # Warning-only rule failures never enter errors and leave record VALID
    schema, warn_fields = cases["warning_only_failures_keep_record_valid"]
    res_warn = validator.validate_record(warn_fields, schema, record_id="rec-warn")
    assert res_warn.record_outcome.status == ValidationStatus.VALID
    assert res_warn.record_outcome.errors == []
    assert [(w.field_path, w.code) for w in res_warn.warnings] == [
        ("totals.gross", "cross_field_comparison_failed"),
        ("cash_receipt", "conditional_forbidden_present"),
        ("ssn", "missing_required_group"),
    ]


def test_conformance_conditional_is_missing_vs_present_null_and_nullable_required() -> None:
    validator = DeterministicValidator()
    cases = dict(
        (name, (schema, fields)) for name, schema, fields in build_curated_cross_field_eval_cases()
    )

    # PRESENT + None does NOT trigger `is_missing` warning
    schema, null_pm = cases["is_missing_vs_present_null_on_controlling_field"]
    res_null = validator.validate_record(null_pm, schema, record_id="rec-null")
    assert not any(w.code == "conditional_required_missing" for w in res_null.warnings)

    # MISSING DOES trigger `is_missing` warning
    schema, miss_pm = cases["is_missing_true_triggers_warning_when_po_missing"]
    res_miss = validator.validate_record(miss_pm, schema, record_id="rec-miss")
    assert ("po_number", "conditional_required_missing") in [
        (w.field_path, w.code) for w in res_miss.warnings
    ]


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(
    subtotal=st.decimals(
        min_value=-1000, max_value=1000, places=2, allow_nan=False, allow_infinity=False
    ),
    tax=st.decimals(min_value=0, max_value=200, places=2, allow_nan=False, allow_infinity=False),
    discount=st.decimals(
        min_value=0, max_value=200, places=2, allow_nan=False, allow_infinity=False
    ),
    delta=st.sampled_from(
        [Decimal("-0.02"), Decimal("-0.01"), Decimal("0.00"), Decimal("0.01"), Decimal("0.02")]
    ),
)
def test_property_conformance_exact_arithmetic_balance_tolerance(
    subtotal: Decimal,
    tax: Decimal,
    discount: Decimal,
    delta: Decimal,
) -> None:
    schema = build_invoice_cross_field_schema()
    total = subtotal + tax - discount + delta
    fields: dict[str, tuple[FieldPresence, Any]] = {
        "subtotal": (FieldPresence.PRESENT, subtotal),
        "tax": (FieldPresence.PRESENT, tax),
        "discount": (FieldPresence.PRESENT, discount),
        "total": (FieldPresence.PRESENT, total),
        "ssn": (FieldPresence.PRESENT, "123"),
        "payment_method": (FieldPresence.PRESENT, "card"),
    }
    validator = DeterministicValidator()
    res = validator.validate_record(fields, schema, record_id="prop-arith")
    has_arith_err = any(
        e.code == "cross_field_arithmetic_mismatch" and e.field_path == "total"
        for e in res.record_outcome.errors
    )
    assert has_arith_err is (abs(delta) > Decimal("0.01"))


@pytest.mark.property
@settings(max_examples=30, deadline=None)
@given(
    bad_val=st.one_of(
        st.booleans(),
        st.just("42"),
        st.just(Decimal("NaN")),
        st.just(Decimal("Infinity")),
        st.just(datetime(2026, 1, 1)),
    )
)
def test_property_conformance_cross_field_rules_never_coerce_malformed_operands(
    bad_val: Any,
) -> None:
    schema = build_invoice_cross_field_schema()
    fields: dict[str, tuple[FieldPresence, Any]] = {
        "subtotal": (FieldPresence.PRESENT, bad_val),
        "total": (FieldPresence.PRESENT, Decimal("10.00")),
        "issue_date": (FieldPresence.PRESENT, date(2026, 1, 1)),
        "due_date": (FieldPresence.PRESENT, bad_val),
        "ssn": (FieldPresence.PRESENT, "123"),
        "payment_method": (FieldPresence.PRESENT, "card"),
    }
    validator = DeterministicValidator()
    res = validator.validate_record(fields, schema, record_id="prop-noncoerce")
    cf_codes = {
        e.code
        for e in res.record_outcome.errors
        if e.code in {"cross_field_comparison_failed", "cross_field_arithmetic_mismatch"}
    }
    assert cf_codes == set()
