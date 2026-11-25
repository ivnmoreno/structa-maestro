"""Layer 1: Always-on pure-Python & Hypothesis conformance tests for Schema & Validator.

Verifies empirical conformance between `schema/models.py` / `validation/validator.py` and the
formal specifications in `formal/StructaFormal/SchemaModels.lean`, `SchemaInvariants.lean`,
and `DeterministicValidation.lean` without requiring Lean 4 installed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.conformance.schema_reference import (
    build_schema_well_formed_vectors,
    build_validation_reference_schema,
    instantiate_python_schema_from_vector,
)

from structa_maestro.contracts.enums import (
    ExtractionCardinality,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ValidationStatus,
)
from structa_maestro.contracts.models import DiagnosticIssue, ValidationOutcome
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.validation.validator import DeterministicValidator


@pytest.mark.parametrize(("schema_vec", "expected_valid"), build_schema_well_formed_vectors())
def test_conformance_schema_well_formedness_vectors(
    schema_vec: dict[str, Any], expected_valid: bool
) -> None:
    if expected_valid:
        schema = instantiate_python_schema_from_vector(schema_vec)
        assert schema.schema_id.strip() != ""
        assert len(schema.fields) >= 1
    else:
        with pytest.raises(SchemaDefinitionError):
            instantiate_python_schema_from_vector(schema_vec)


def test_conformance_validator_strict_non_coercion_and_decimal_int_acceptance() -> None:
    schema = build_validation_reference_schema()
    validator = DeterministicValidator()

    # Non-coercion failures: "42" for int, True for int & decimal, datetime for date, NaN for dec
    bad_fields: dict[str, tuple[FieldPresence, Any]] = {
        "code": (FieldPresence.PRESENT, "INV-1"),
        "count": (FieldPresence.PRESENT, "42"),
        "amount": (FieldPresence.PRESENT, True),
        "active": (FieldPresence.PRESENT, 1),
        "issued": (FieldPresence.PRESENT, datetime(2026, 3, 15, 10, 0)),
        "currency": (FieldPresence.PRESENT, "USD"),
    }
    res = validator.validate_record(bad_fields, schema, record_id="record-001")
    assert res.record_outcome.status == ValidationStatus.INVALID
    pairs = {(e.field_path, e.code) for e in res.record_outcome.errors}
    assert ("count", "type_mismatch") in pairs
    assert ("amount", "type_mismatch") in pairs
    assert ("active", "type_mismatch") in pairs
    assert ("issued", "type_mismatch") in pairs

    # Bool rejected for INTEGER; non-finite Decimal rejected for DECIMAL; int accepted for DECIMAL
    bad_fields_2: dict[str, tuple[FieldPresence, Any]] = {
        "code": (FieldPresence.PRESENT, "INV-1"),
        "count": (FieldPresence.PRESENT, True),
        "amount": (FieldPresence.PRESENT, Decimal("NaN")),
        "active": (FieldPresence.PRESENT, False),
        "issued": (FieldPresence.PRESENT, date(2026, 3, 15)),
        "currency": (FieldPresence.PRESENT, "EUR"),
    }
    res2 = validator.validate_record(bad_fields_2, schema, record_id="record-001")
    pairs2 = {(e.field_path, e.code) for e in res2.record_outcome.errors}
    assert ("count", "type_mismatch") in pairs2
    assert ("amount", "type_mismatch") in pairs2

    # Python int accepted for DECIMAL (matching validator.py:336)
    ok_fields: dict[str, tuple[FieldPresence, Any]] = {
        "code": (FieldPresence.PRESENT, "INV-1"),
        "count": (FieldPresence.PRESENT, 5),
        "amount": (FieldPresence.PRESENT, 25),
        "active": (FieldPresence.PRESENT, True),
        "issued": (FieldPresence.PRESENT, date(2026, 3, 15)),
        "currency": (FieldPresence.PRESENT, "USD"),
    }
    res_ok = validator.validate_record(ok_fields, schema, record_id="record-001")
    assert res_ok.record_outcome.status == ValidationStatus.VALID
    assert res_ok.record_outcome.errors == []


def test_conformance_validator_presence_nullability_and_extra_fields() -> None:
    closed_schema = build_validation_reference_schema(allow_additional_fields=False)
    open_schema = build_validation_reference_schema(allow_additional_fields=True)
    validator = DeterministicValidator()

    base: dict[str, tuple[FieldPresence, Any]] = {
        "code": (FieldPresence.MISSING, None),  # required missing -> missing_required
        "count": (FieldPresence.PRESENT, None),  # non-nullable null -> disallowed_null
        "amount": (FieldPresence.PRESENT, Decimal("10.00")),
        "active": (FieldPresence.PRESENT, True),
        "issued": (FieldPresence.PRESENT, date(2026, 3, 15)),
        "currency": (FieldPresence.PRESENT, "USD"),
        "note": (FieldPresence.PRESENT, None),  # optional nullable -> VALID
        "ghost_missing": (FieldPresence.MISSING, None),  # missing extra -> VALID
        "ghost_present": (FieldPresence.PRESENT, "surprise"),  # present extra -> unexpected_field
    }
    res_closed = validator.validate_record(base, closed_schema, record_id="record-001")
    assert res_closed.field_outcomes["code"].errors[0].code == "missing_required"
    assert res_closed.field_outcomes["count"].errors[0].code == "disallowed_null"
    assert res_closed.field_outcomes["note"].status == ValidationStatus.VALID
    assert res_closed.field_outcomes["ghost_missing"].status == ValidationStatus.VALID
    assert res_closed.field_outcomes["ghost_present"].errors[0].code == "unexpected_field"

    # Under allow_additional_fields=True, ghost_present passes
    res_open = validator.validate_record(base, open_schema, record_id="record-001")
    assert res_open.field_outcomes["ghost_present"].status == ValidationStatus.VALID


def test_conformance_validator_allowed_values_bool_vs_int_and_real_regex() -> None:
    schema = ExtractionSchema(
        schema_id="enum-regex",
        schema_version="1.0",
        fields={
            "code": FieldSpec(
                name="code",
                field_type=FieldType.STRING,
                constraints=FieldConstraints(pattern=r"^INV-\d{4}$"),
            ),
            "tier": FieldSpec(
                name="tier",
                field_type=FieldType.INTEGER,
                constraints=FieldConstraints(allowed_values=(0, 1)),
            ),
            "flag": FieldSpec(
                name="flag",
                field_type=FieldType.BOOLEAN,
                constraints=FieldConstraints(allowed_values=(1,)),  # int 1 must NOT match bool True
            ),
            "price": FieldSpec(
                name="price",
                field_type=FieldType.DECIMAL,
                constraints=FieldConstraints(allowed_values=(Decimal("10.00"),)),
            ),
        },
    )
    validator = DeterministicValidator()
    res = validator.validate_record(
        {
            "code": (FieldPresence.PRESENT, "INV-2026"),
            "tier": (FieldPresence.PRESENT, 1),
            "flag": (FieldPresence.PRESENT, True),
            "price": (FieldPresence.PRESENT, 10),  # int 10 == Decimal("10.00")
        },
        schema,
        record_id="record-001",
    )
    assert res.field_outcomes["code"].status == ValidationStatus.VALID
    assert res.field_outcomes["tier"].status == ValidationStatus.VALID
    assert res.field_outcomes["flag"].status == ValidationStatus.INVALID
    assert res.field_outcomes["flag"].errors[0].code == "constraint_violation"
    assert res.field_outcomes["price"].status == ValidationStatus.VALID


def test_conformance_result_cardinality_single_multiple_and_prevalidation() -> None:
    validator = DeterministicValidator()
    single_schema = build_validation_reference_schema()
    multi_schema = ExtractionSchema(
        schema_id="multi",
        schema_version="1.0",
        cardinality=ExtractionCardinality.MULTIPLE,
        fields=single_schema.fields,
    )
    valid_rec = ValidationOutcome(status=ValidationStatus.VALID)
    invalid_rec = ValidationOutcome(
        status=ValidationStatus.INVALID,
        errors=[
            DiagnosticIssue(
                code="type_mismatch",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.VALIDATION,
                message="Bad type.",
            )
        ],
    )

    # Pre-validation failure with 0 records -> NOT_VALIDATED
    for sch in (single_schema, multi_schema):
        out = validator.validate_result_cardinality(
            [], sch, has_records=False, failed_before_validation=True
        )
        assert out.status == ValidationStatus.NOT_VALIDATED
        assert out.errors == []

    # SINGLE: 0 -> missing_record, 1 valid -> VALID, 2 -> cardinality_violation
    o0 = validator.validate_result_cardinality(
        [], single_schema, has_records=False, failed_before_validation=False
    )
    assert o0.status == ValidationStatus.INVALID
    assert o0.errors[0].code == "missing_record"

    o1 = validator.validate_result_cardinality(
        [valid_rec], single_schema, has_records=True, failed_before_validation=False
    )
    assert o1.status == ValidationStatus.VALID

    o2 = validator.validate_result_cardinality(
        [valid_rec, valid_rec], single_schema, has_records=True, failed_before_validation=False
    )
    assert o2.status == ValidationStatus.INVALID
    assert o2.errors[0].code == "cardinality_violation"

    # MULTIPLE: 0 valid -> VALID, 2 valid -> VALID, 1 valid + 1 invalid -> INVALID
    m0 = validator.validate_result_cardinality(
        [], multi_schema, has_records=False, failed_before_validation=False
    )
    assert m0.status == ValidationStatus.VALID

    m2 = validator.validate_result_cardinality(
        [valid_rec, valid_rec], multi_schema, has_records=True, failed_before_validation=False
    )
    assert m2.status == ValidationStatus.VALID

    m_bad = validator.validate_result_cardinality(
        [valid_rec, invalid_rec], multi_schema, has_records=True, failed_before_validation=False
    )
    assert m_bad.status == ValidationStatus.INVALID


@pytest.mark.property
@settings(max_examples=50)
@given(
    min_len=st.one_of(st.none(), st.integers(min_value=-2, max_value=10)),
    max_len=st.one_of(st.none(), st.integers(min_value=-2, max_value=10)),
    min_val=st.one_of(st.none(), st.integers(min_value=-10, max_value=10)),
    max_val=st.one_of(st.none(), st.integers(min_value=-10, max_value=10)),
    ft=st.sampled_from([FieldType.STRING, FieldType.INTEGER, FieldType.BOOLEAN]),
)
def test_property_conformance_field_spec_constraint_compatibility(
    min_len: int | None,
    max_len: int | None,
    min_val: int | None,
    max_val: int | None,
    ft: FieldType,
) -> None:
    constraints_ok = (
        (min_len is None or min_len >= 0)
        and (max_len is None or max_len >= 0)
        and (min_len is None or max_len is None or min_len <= max_len)
        and (min_val is None or max_val is None or min_val <= max_val)
    )
    type_ok = ((min_val is None and max_val is None) or ft == FieldType.INTEGER) and (
        (min_len is None and max_len is None) or ft == FieldType.STRING
    )
    expected = constraints_ok and type_ok
    if expected:
        spec = FieldSpec(
            name="f",
            field_type=ft,
            constraints=FieldConstraints(
                min_length=min_len,
                max_length=max_len,
                min_value=min_val,
                max_value=max_val,
            ),
        )
        assert spec.field_type == ft
    else:
        with pytest.raises(SchemaDefinitionError):
            FieldSpec(
                name="f",
                field_type=ft,
                constraints=FieldConstraints(
                    min_length=min_len,
                    max_length=max_len,
                    min_value=min_val,
                    max_value=max_val,
                ),
            )
