"""Layer 2: Lean 4 Oracle differential conformance tests (`structa-formal-oracle`).

Feeds Contract v1.0 JSON payloads and pipeline-assembly inputs generated in Python directly into
the compiled Lean 4 executable `formal/.lake/build/bin/structa-formal-oracle` (whose predicates
are machine-checked equivalent to `Valid*` via `isValid*_iff` in `StructaFormal/Models.lean`),
asserting 100% agreement between Lean's compiled specification and Python's authoritative runtime.

- When `STRUCTA_REQUIRE_LEAN_ORACLE=1` (mandatory in `.github/workflows/formal-assurance.yml`),
  any missing Lean binary fails the test suite immediately.
- On Python-only environments without Lean installed (and without that env var), Layer 2 skips
  cleanly while Layer 1 (`test_formal_conformance.py`) still executes in full.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from tests.conformance.cross_field_reference import (
    build_cross_field_schema_well_formed_vectors,
    build_curated_cross_field_eval_cases,
    build_invoice_cross_field_schema,
    encode_schema_with_rules,
    instantiate_python_schema_with_rules_from_vector,
)
from tests.conformance.reference_model import (
    build_confidence_vectors,
    build_extracted_field_vectors,
    build_extraction_result_vectors,
    build_source_location_vectors,
    build_validation_outcome_vectors,
    formal_assemble_overall_status,
    formal_assemble_record_status,
    formal_make_result_cardinality_status,
)
from tests.conformance.schema_reference import (
    build_schema_well_formed_vectors,
    build_validation_reference_schema,
    encode_extraction_schema,
    encode_record_fields,
    instantiate_python_schema_from_vector,
)

from structa_maestro.confidence.normalizer import ConfidenceNormalizer
from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    ExtractionCardinality,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    DiagnosticIssue,
    ExtractedField,
    ExtractionResult,
    SourceLocation,
    ValidationOutcome,
)
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.validation.validator import DeterministicValidator

REPO_ROOT = Path(__file__).resolve().parents[2]
FORMAL_DIR = REPO_ROOT / "formal"
ORACLE_BIN = FORMAL_DIR / ".lake" / "build" / "bin" / "structa-formal-oracle"


def _find_lake_binary() -> str | None:
    found = shutil.which("lake")
    if found:
        return found
    elan_lake = Path.home() / ".elan" / "bin" / "lake"
    if elan_lake.is_file() and os.access(elan_lake, os.X_OK):
        return str(elan_lake)
    return None


def _ensure_oracle_command() -> list[str]:
    require_oracle = os.environ.get("STRUCTA_REQUIRE_LEAN_ORACLE", "").strip() == "1"
    if ORACLE_BIN.is_file() and os.access(ORACLE_BIN, os.X_OK):
        return [str(ORACLE_BIN)]

    lake_bin = _find_lake_binary()
    if lake_bin is None:
        msg = (
            "Lean 4 / lake not found (expected formal/.lake/build/bin/structa-formal-oracle or "
            "lake on PATH). Set STRUCTA_REQUIRE_LEAN_ORACLE=1 in CI to fail on missing oracle."
        )
        if require_oracle:
            pytest.fail(msg)
        pytest.skip(msg)

    env = os.environ.copy()
    elan_dir = str(Path.home() / ".elan" / "bin")
    env["PATH"] = f"{elan_dir}:{env.get('PATH', '')}"
    proc = subprocess.run(
        [lake_bin, "build", "structa-formal-oracle"],
        cwd=FORMAL_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"lake build structa-formal-oracle failed:\n{proc.stdout}\n{proc.stderr}")
    if not ORACLE_BIN.is_file():
        pytest.fail(f"Oracle binary missing after lake build: {ORACLE_BIN}")
    return [str(ORACLE_BIN)]


def _query_lean_oracle(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cmd = _ensure_oracle_command()
    stdin_payload = "\n".join(json.dumps(req) for req in requests) + "\n"
    proc = subprocess.run(
        cmd,
        cwd=FORMAL_DIR,
        input=stdin_payload,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, f"Lean oracle failed (rc={proc.returncode}): {proc.stderr}"
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == len(requests), (
        f"Expected {len(requests)} oracle responses, got {len(lines)}"
    )
    return [json.loads(line) for line in lines]


def _python_accepts(model_cls: type[Any], payload: dict[str, Any]) -> bool:
    try:
        model_cls.model_validate(payload)
        return True
    except ValidationError:
        return False


def test_lean_oracle_matches_python_contract_models() -> None:
    """Differential check of all curated valid/invalid/boundary Contract v1.0 vectors."""
    cases: list[tuple[str, type[Any], dict[str, Any], bool]] = []
    for payload, expected in build_source_location_vectors():
        cases.append(("source_location", SourceLocation, payload, expected))
    for payload, expected in build_confidence_vectors():
        cases.append(("confidence_assessment", ConfidenceAssessment, payload, expected))
    for payload, expected in build_validation_outcome_vectors():
        cases.append(("validation_outcome", ValidationOutcome, payload, expected))
    for payload, expected in build_extracted_field_vectors():
        cases.append(("extracted_field", ExtractedField, payload, expected))
    for payload, expected in build_extraction_result_vectors():
        cases.append(("extraction_result", ExtractionResult, payload, expected))

    requests = [{"kind": kind, "payload": payload} for kind, _, payload, _ in cases]
    responses = _query_lean_oracle(requests)

    for (kind, model_cls, payload, expected), resp in zip(cases, responses, strict=True):
        assert resp["ok"] is True
        lean_valid = resp["valid"]
        python_valid = _python_accepts(model_cls, payload)
        assert lean_valid is expected, f"Lean mismatch for {kind}: {payload}"
        assert python_valid is lean_valid, (
            f"Python/Lean divergence for {kind}: python={python_valid} lean={lean_valid} "
            f"payload={payload}"
        )


def test_lean_oracle_matches_result_builder_and_record_status_matrices() -> None:
    """Exhaustive differential check of all 64 result-status + 16 record-status combinations."""
    requests: list[dict[str, Any]] = []
    expected_results: list[tuple[str, str | None]] = []

    for sf, sp, hre, hr, arnc, hve in itertools.product([False, True], repeat=6):
        requests.append(
            {
                "kind": "result_builder_status",
                "payload": {
                    "strategy_failed": sf,
                    "strategy_partial": sp,
                    "has_result_errors": hre,
                    "has_records": hr,
                    "any_record_not_completed": arnc,
                    "has_validation_errors": hve,
                },
            }
        )
        ps = formal_assemble_overall_status(
            strategy_failed=sf,
            strategy_partial=sp,
            has_result_errors=hre,
            has_records=hr,
            any_record_not_completed=arnc,
        )
        vs = formal_make_result_cardinality_status(
            failed_before_validation=sf,
            has_records=hr,
            has_validation_errors=hve,
        )
        expected_results.append((ps.value, vs.value))

    for rf, rp, hre, hfe in itertools.product([False, True], repeat=4):
        requests.append(
            {
                "kind": "record_builder_status",
                "payload": {
                    "record_failed": rf,
                    "record_partial": rp,
                    "has_record_errors": hre,
                    "has_field_errors": hfe,
                },
            }
        )
        ps = formal_assemble_record_status(
            record_failed=rf,
            record_partial=rp,
            has_record_errors=hre,
            has_field_errors=hfe,
        )
        expected_results.append((ps.value, None))

    responses = _query_lean_oracle(requests)
    for (exp_ps, exp_vs), resp in zip(expected_results, responses, strict=True):
        assert resp["ok"] is True
        assert resp["processing_status"] == exp_ps
        if exp_vs is not None:
            assert resp["validation_status"] == exp_vs


def test_lean_oracle_matches_confidence_normalizer_and_provenance_associator() -> None:
    """Differential check of ConfidenceNormalizer and ProvenanceAssociator rules against Lean."""
    normalizer = ConfidenceNormalizer()
    conf_cases: list[tuple[str, Any, str, str | None]] = [
        ("missing", None, "0-1", None),
        ("non_numeric", "high", "0-1", None),
        ("non_numeric", True, "0-1", "llm"),
        ("non_finite", float("nan"), "0-1", None),
        ("non_finite", float("inf"), "0-1", None),
        ("finite", 0.0, "0-1", None),
        ("finite", 0.75, "0-1", "llm"),
        ("finite", 1.0, "0-1", " "),
        ("finite", -0.1, "0-1", None),
        ("finite", 1.25, "0-1", None),
        ("finite", 85.5, "0-100", "custom"),
    ]
    conf_requests = [
        {
            "kind": "confidence_normalizer",
            "payload": {
                "input_kind": kind,
                "value": val if kind == "finite" else None,
                "scale": scale,
                "method": method,
                "default_method": "extraction-assessment",
            },
        }
        for kind, val, scale, method in conf_cases
    ]

    prov_cases: list[tuple[str, str | None, bool, str, bool, bool, bool, str | None, bool]] = [
        ("doc-1", "doc-2", True, "direct", True, True, False, None, False),
        ("doc-1", None, False, "direct", True, True, True, "unverified", False),
        ("doc-1", "doc-1", False, "inferred", True, True, True, "unverified", False),
        ("doc-1", "doc-1", True, "direct", True, False, True, "conflicting", True),
        ("doc-1", None, True, "direct", True, True, True, "direct", True),
        ("doc-1", None, True, "inferred", True, False, True, "inferred", True),
    ]
    prov_requests = [
        {
            "kind": "provenance_associator",
            "payload": {
                "doc_id": doc_id,
                "proposed_doc_id": p_doc,
                "has_location": has_loc,
                "requested_relationship": rel,
                "present_field": present,
                "scalar_supported": supported,
            },
        }
        for doc_id, p_doc, has_loc, rel, present, supported, _, _, _ in prov_cases
    ]

    responses = _query_lean_oracle(conf_requests + prov_requests)
    conf_resps = responses[: len(conf_requests)]
    prov_resps = responses[len(conf_requests) :]

    for (_, raw_val, scale, method), resp in zip(conf_cases, conf_resps, strict=True):
        py_conf = normalizer.normalize(raw_value=raw_val, scale=scale, method=method).confidence
        lean_conf = resp["confidence"]
        if py_conf is None:
            assert lean_conf is None
        else:
            assert lean_conf is not None
            assert lean_conf["calibrated"] is False
            assert py_conf.calibrated is False
            assert lean_conf["scale"] == py_conf.scale
            assert lean_conf["method"] == py_conf.method
            if py_conf.value is None:
                assert lean_conf["value"] is None
            else:
                assert lean_conf["value"] is not None
                lean_float = lean_conf["value"]["num"] / lean_conf["value"]["den"]
                assert abs(lean_float - py_conf.value) < 1e-6

    for (
        _,
        _,
        _,
        _,
        _,
        _,
        exp_attached,
        exp_rel,
        exp_has_loc,
    ), resp in zip(prov_cases, prov_resps, strict=True):
        assert resp["attached"] is exp_attached
        assert resp["relationship"] == exp_rel
        assert resp["has_location"] is exp_has_loc


@pytest.mark.property
@settings(max_examples=25, deadline=None)
@given(
    presence=st.sampled_from([FieldPresence.PRESENT.value, FieldPresence.MISSING.value]),
    value=st.one_of(st.none(), st.just(""), st.just("INV-1"), st.just(0), st.just(False)),
    page=st.one_of(st.none(), st.integers(min_value=-1, max_value=5)),
    char_start=st.one_of(st.none(), st.integers(min_value=-1, max_value=10)),
    char_end=st.one_of(st.none(), st.integers(min_value=-1, max_value=10)),
    rel=st.sampled_from([e.value for e in EvidenceRelationship]),
)
def test_property_lean_oracle_differential_extracted_field(
    presence: str,
    value: Any,
    page: int | None,
    char_start: int | None,
    char_end: int | None,
    rel: str,
) -> None:
    payload = {
        "presence": presence,
        "value": value,
        "confidence": None,
        "provenance": [
            {
                "source_document_id": "doc-1",
                "location": {"page": page, "char_start": char_start, "char_end": char_end},
                "evidence": "snippet",
                "relationship": rel,
                "truncated": False,
            }
        ],
        "validation": {"status": "valid", "errors": []},
        "errors": [],
        "warnings": [],
    }
    resp = _query_lean_oracle([{"kind": "extracted_field", "payload": payload}])[0]
    assert _python_accepts(ExtractedField, payload) is resp["valid"]


# ---------------------------------------------------------------------------
# Spec 013 — Schema Well-Formedness & Deterministic Validation Differential Checks
# ---------------------------------------------------------------------------


def _python_accepts_schema(vec: dict[str, Any]) -> bool:
    try:
        instantiate_python_schema_from_vector(vec)
        return True
    except (SchemaDefinitionError, ValueError):
        return False


def test_lean_oracle_matches_python_schema_well_formedness() -> None:
    """Differential check of all curated valid/invalid/boundary ExtractionSchema vectors."""
    vectors = build_schema_well_formed_vectors()
    requests = [{"kind": "schema_well_formed", "payload": vec} for vec, _ in vectors]
    responses = _query_lean_oracle(requests)

    for (vec, expected), resp in zip(vectors, responses, strict=True):
        assert resp["ok"] is True
        lean_valid = resp["valid"]
        python_valid = _python_accepts_schema(vec)
        assert lean_valid is expected, f"Lean mismatch for schema_well_formed: {vec}"
        assert python_valid is lean_valid, (
            f"Python/Lean divergence for schema_well_formed: "
            f"python={python_valid} lean={lean_valid} vec={vec}"
        )


def test_lean_oracle_matches_python_deterministic_validator_curated() -> None:
    """Differential check of DeterministicValidator.validate_record against Lean oracle.

    Asserts exact agreement on record status, per-field statuses, AND ordered
    `(field_path, code)` diagnostic error lists across non-coercion, presence/nullability,
    bounds, allowed_values, nested OBJECT/ARRAY recursion, extra fields, and pattern oracle.
    """
    validator = DeterministicValidator()
    closed_schema = build_validation_reference_schema(allow_additional_fields=False)
    open_schema = build_validation_reference_schema(allow_additional_fields=True)

    enum_bool_int_schema = ExtractionSchema(
        schema_id="enum-bool-int",
        schema_version="1.0",
        fields={
            "num_enum": FieldSpec(
                name="num_enum",
                field_type=FieldType.DECIMAL,
                required=True,
                constraints=FieldConstraints(allowed_values=(Decimal("10.00"),)),
            ),
            "int_enum": FieldSpec(
                name="int_enum",
                field_type=FieldType.INTEGER,
                required=True,
                constraints=FieldConstraints(allowed_values=(1,)),
            ),
            "bool_enum": FieldSpec(
                name="bool_enum",
                field_type=FieldType.BOOLEAN,
                required=True,
                constraints=FieldConstraints(allowed_values=(True,)),
            ),
        },
    )

    pattern_schema = ExtractionSchema.from_dict(
        {
            "schema_id": "pattern-check",
            "schema_version": "1.0",
            "fields": {
                "invoice_number": {
                    "type": "string",
                    "required": True,
                    "pattern": r"^INV-\d+$",
                }
            },
        }
    )

    open_nested_schema = ExtractionSchema.from_dict(
        {
            "schema_id": "open-nested",
            "schema_version": "1.0",
            "fields": {
                "vendor": {
                    "type": "object",
                    "required": True,
                    "allow_additional_fields": True,
                    "properties": {"name": {"type": "string", "required": True}},
                }
            },
        }
    )

    base_valid: dict[str, tuple[FieldPresence, Any]] = {
        "code": (FieldPresence.PRESENT, "INV-100"),
        "count": (FieldPresence.PRESENT, 5),
        "amount": (FieldPresence.PRESENT, Decimal("42.50")),
        "active": (FieldPresence.PRESENT, True),
        "issued": (FieldPresence.PRESENT, date(2026, 1, 15)),
        "currency": (FieldPresence.PRESENT, "USD"),
        "note": (FieldPresence.PRESENT, None),
        "tags": (FieldPresence.PRESENT, ["ops", "billing"]),
        "vendor": (FieldPresence.PRESENT, {"name": "Acme", "tax_id": None}),
    }

    cases: list[tuple[ExtractionSchema, dict[str, tuple[FieldPresence, Any]], dict[str, bool]]] = [
        # Valid baseline & int accepted for DECIMAL
        (closed_schema, base_valid, {}),
        (closed_schema, {**base_valid, "amount": (FieldPresence.PRESENT, 100)}, {}),
        # Non-coercion matrix
        (closed_schema, {**base_valid, "count": (FieldPresence.PRESENT, "42")}, {}),
        (closed_schema, {**base_valid, "count": (FieldPresence.PRESENT, True)}, {}),
        (closed_schema, {**base_valid, "amount": (FieldPresence.PRESENT, False)}, {}),
        (closed_schema, {**base_valid, "amount": (FieldPresence.PRESENT, Decimal("NaN"))}, {}),
        (closed_schema, {**base_valid, "amount": (FieldPresence.PRESENT, Decimal("Infinity"))}, {}),
        (closed_schema, {**base_valid, "amount": (FieldPresence.PRESENT, float("nan"))}, {}),
        (
            closed_schema,
            {
                **base_valid,
                "issued": (FieldPresence.PRESENT, datetime(2026, 1, 15, tzinfo=UTC)),
            },
            {},
        ),
        (closed_schema, {**base_valid, "code": (FieldPresence.PRESENT, object())}, {}),
        # Presence & nullability separation
        (closed_schema, {**base_valid, "code": (FieldPresence.MISSING, None)}, {}),
        (closed_schema, {**base_valid, "note": (FieldPresence.MISSING, None)}, {}),
        (closed_schema, {**base_valid, "code": (FieldPresence.PRESENT, None)}, {}),
        # Bounds & allowed_values
        (
            closed_schema,
            {
                **base_valid,
                "code": (FieldPresence.PRESENT, "A"),
                "count": (FieldPresence.PRESENT, 101),
                "amount": (FieldPresence.PRESENT, Decimal("-1.00")),
                "currency": (FieldPresence.PRESENT, "GBP"),
            },
            {},
        ),
        # allowed_values bool vs int & int vs Decimal
        (
            enum_bool_int_schema,
            {
                "num_enum": (FieldPresence.PRESENT, 10),
                "int_enum": (FieldPresence.PRESENT, 1),
                "bool_enum": (FieldPresence.PRESENT, True),
            },
            {},
        ),
        (
            enum_bool_int_schema,
            {
                "num_enum": (FieldPresence.PRESENT, Decimal("10.01")),
                "int_enum": (FieldPresence.PRESENT, True),
                "bool_enum": (FieldPresence.PRESENT, 1),
            },
            {},
        ),
        # Nested ARRAY & OBJECT recursion + sorted nested extras
        (
            closed_schema,
            {
                **base_valid,
                "tags": (FieldPresence.PRESENT, ["ok", "x", 99, "abcd"]),
                "vendor": (
                    FieldPresence.PRESENT,
                    {"z_extra": 1, "a_extra": 2},
                ),
            },
            {},
        ),
        (
            open_nested_schema,
            {"vendor": (FieldPresence.PRESENT, {"name": "Acme", "extra_nested": 123})},
            {},
        ),
        # Top-level extra fields: closed present (sorted), open present, closed missing
        (
            closed_schema,
            {
                **base_valid,
                "z_extra": (FieldPresence.PRESENT, "z"),
                "a_extra": (FieldPresence.PRESENT, "a"),
            },
            {},
        ),
        (open_schema, {**base_valid, "extra": (FieldPresence.PRESENT, "ok")}, {}),
        (closed_schema, {**base_valid, "ghost_extra": (FieldPresence.MISSING, None)}, {}),
        # Pattern oracle pass / fail
        (
            pattern_schema,
            {"invoice_number": (FieldPresence.PRESENT, "INV-42")},
            {"invoice_number": True},
        ),
        (
            pattern_schema,
            {"invoice_number": (FieldPresence.PRESENT, "BAD")},
            {"invoice_number": False},
        ),
    ]

    requests = [
        {
            "kind": "validate_record",
            "payload": {
                "schema": encode_extraction_schema(schema),
                "record_id": "rec-1",
                "fields": encode_record_fields(fields),
                "pattern_results": pat_results,
            },
        }
        for schema, fields, pat_results in cases
    ]
    responses = _query_lean_oracle(requests)

    for (schema, fields, _), resp in zip(cases, responses, strict=True):
        assert resp["ok"] is True
        py_res = validator.validate_record(fields, schema, record_id="rec-1")
        py_rec = py_res.record_outcome
        py_fields = py_res.field_outcomes
        py_errors = [(e.field_path, e.code) for e in py_rec.errors]
        lean_errors = [(e["field_path"], e["code"]) for e in resp["errors"]]
        py_field_statuses = {k: v.status.value for k, v in py_fields.items()}

        assert resp["status"] == py_rec.status.value, (
            f"Record status mismatch: lean={resp['status']} py={py_rec.status.value}"
        )
        assert lean_errors == py_errors, (
            f"Diagnostic error list mismatch: lean={lean_errors} py={py_errors}"
        )
        assert resp["field_statuses"] == py_field_statuses, (
            f"Field statuses mismatch: lean={resp['field_statuses']} py={py_field_statuses}"
        )


def test_lean_oracle_matches_python_result_cardinality_matrix() -> None:
    """Exhaustive differential check of validate_result_cardinality against Lean oracle."""
    validator = DeterministicValidator()
    dummy_err = DiagnosticIssue(
        code="err",
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.VALIDATION,
        message="validation error",
    )

    cases: list[tuple[ExtractionCardinality, list[int], bool, bool]] = []
    for card in (ExtractionCardinality.SINGLE, ExtractionCardinality.MULTIPLE):
        for counts in ([], [0], [1], [0, 0], [1, 0], [0, 2, 1]):
            has_recs = len(counts) > 0
            for failed_before in (False, True):
                cases.append((card, counts, has_recs, failed_before))

    requests = [
        {
            "kind": "validate_result_cardinality",
            "payload": {
                "cardinality": card.value,
                "record_error_counts": counts,
                "has_records": has_recs,
                "failed_before_validation": failed_before,
            },
        }
        for card, counts, has_recs, failed_before in cases
    ]
    responses = _query_lean_oracle(requests)

    for (card, counts, has_recs, failed_before), resp in zip(cases, responses, strict=True):
        assert resp["ok"] is True
        schema = ExtractionSchema(
            schema_id="card",
            schema_version="1.0",
            fields={"x": FieldSpec(name="x", field_type=FieldType.STRING)},
            cardinality=card,
        )
        rec_outcomes = [
            ValidationOutcome(
                status=ValidationStatus.VALID if n == 0 else ValidationStatus.INVALID,
                errors=tuple(dummy_err for _ in range(n)),
            )
            for n in counts
        ]
        py_out = validator.validate_result_cardinality(
            rec_outcomes,
            schema,
            has_records=has_recs,
            failed_before_validation=failed_before,
        )
        assert resp["status"] == py_out.status.value
        assert resp["codes"] == [e.code for e in py_out.errors]


_HYPOTHESIS_VALUE_ST = st.one_of(
    st.none(),
    st.sampled_from(["", "ok", "INV-1", "a" * 20, "USD", "GBP"]),
    st.sampled_from([-5, 0, 1, 50, 101]),
    st.sampled_from([Decimal("-1.5"), Decimal("0.00"), Decimal("10.25"), Decimal("NaN")]),
    st.booleans(),
    st.sampled_from([date(2026, 5, 1), datetime(2026, 5, 1, tzinfo=UTC)]),
    st.sampled_from([[], ["ab"], ["x", 1], ["aa", "bb", "cc", "dd"]]),
    st.sampled_from([{}, {"name": "Acme"}, {"name": "Acme", "extra": 1}]),
)


@pytest.mark.property
@settings(max_examples=30, deadline=None)
@given(
    allow_additional=st.booleans(),
    pres_code=st.sampled_from([FieldPresence.PRESENT, FieldPresence.MISSING]),
    val_code=_HYPOTHESIS_VALUE_ST,
    val_count=_HYPOTHESIS_VALUE_ST,
    val_amount=_HYPOTHESIS_VALUE_ST,
    val_active=_HYPOTHESIS_VALUE_ST,
    val_issued=_HYPOTHESIS_VALUE_ST,
    extra_pres=st.one_of(
        st.none(), st.sampled_from([FieldPresence.PRESENT, FieldPresence.MISSING])
    ),
)
def test_property_lean_oracle_differential_validate_record(
    allow_additional: bool,
    pres_code: FieldPresence,
    val_code: Any,
    val_count: Any,
    val_amount: Any,
    val_active: Any,
    val_issued: Any,
    extra_pres: FieldPresence | None,
) -> None:
    """Hypothesis differential property check: Python DeterministicValidator == Lean oracle."""
    schema = build_validation_reference_schema(allow_additional_fields=allow_additional)
    fields: dict[str, tuple[FieldPresence, Any]] = {
        "code": (pres_code, None if pres_code == FieldPresence.MISSING else val_code),
        "count": (FieldPresence.PRESENT, val_count),
        "amount": (FieldPresence.PRESENT, val_amount),
        "active": (FieldPresence.PRESENT, val_active),
        "issued": (FieldPresence.PRESENT, val_issued),
        "currency": (FieldPresence.PRESENT, "USD"),
    }
    if extra_pres is not None:
        fields["undeclared_x"] = (
            extra_pres,
            None if extra_pres == FieldPresence.MISSING else "extra_val",
        )

    validator = DeterministicValidator()
    py_res = validator.validate_record(fields, schema, record_id="prop-1")
    py_rec = py_res.record_outcome
    py_fields = py_res.field_outcomes
    resp = _query_lean_oracle(
        [
            {
                "kind": "validate_record",
                "payload": {
                    "schema": encode_extraction_schema(schema),
                    "record_id": "prop-1",
                    "fields": encode_record_fields(fields),
                    "pattern_results": {},
                },
            }
        ]
    )[0]

    assert resp["ok"] is True
    assert resp["status"] == py_rec.status.value
    assert [(e["field_path"], e["code"]) for e in resp["errors"]] == [
        (e.field_path, e.code) for e in py_rec.errors
    ]
    assert resp["field_statuses"] == {k: v.status.value for k, v in py_fields.items()}


# ---------------------------------------------------------------------------
# Spec 014 — Cross-Field & Conditional Validation Rules Differential Checks
# ---------------------------------------------------------------------------


def _python_accepts_schema_with_rules(vec: dict[str, Any]) -> bool:
    try:
        instantiate_python_schema_with_rules_from_vector(vec)
        return True
    except (SchemaDefinitionError, ValueError):
        return False


def test_lean_oracle_matches_python_cross_field_rule_well_formedness() -> None:
    """Differential check of all curated valid/invalid cross-field rule-schema vectors."""
    vectors = build_cross_field_schema_well_formed_vectors()
    requests = [{"kind": "schema_well_formed", "payload": vec} for vec, _ in vectors]
    responses = _query_lean_oracle(requests)

    for (vec, expected), resp in zip(vectors, responses, strict=True):
        assert resp["ok"] is True
        lean_valid = resp["valid"]
        python_valid = _python_accepts_schema_with_rules(vec)
        assert lean_valid is expected, f"Lean mismatch for cross-field schema_well_formed: {vec}"
        assert python_valid is lean_valid, (
            f"Python/Lean divergence for cross-field schema_well_formed: "
            f"python={python_valid} lean={lean_valid} vec={vec}"
        )


def test_lean_oracle_matches_python_cross_field_validation_curated() -> None:
    """Differential check of DeterministicValidator.validate_record with declarative rules.

    Asserts 100% agreement on record status, per-field statuses, ordered `(field_path, code)`
    errors, AND ordered `(field_path, code)` warnings.
    """
    validator = DeterministicValidator()
    cases = build_curated_cross_field_eval_cases()
    requests = [
        {
            "kind": "validate_record",
            "payload": {
                "schema": encode_schema_with_rules(schema),
                "record_id": "cf-1",
                "fields": encode_record_fields(fields),
                "pattern_results": {},
            },
        }
        for _, schema, fields in cases
    ]
    responses = _query_lean_oracle(requests)

    for (name, schema, fields), resp in zip(cases, responses, strict=True):
        assert resp["ok"] is True
        py_res = validator.validate_record(fields, schema, record_id="cf-1")
        py_errors = [(e.field_path, e.code) for e in py_res.record_outcome.errors]
        py_warnings = [(w.field_path, w.code) for w in py_res.warnings]
        py_field_statuses = {k: v.status.value for k, v in py_res.field_outcomes.items()}

        lean_errors = [(e["field_path"], e["code"]) for e in resp["errors"]]
        lean_warnings = [(w["field_path"], w["code"]) for w in resp["warnings"]]

        assert resp["status"] == py_res.record_outcome.status.value, (
            f"[{name}] status mismatch: "
            f"lean={resp['status']} py={py_res.record_outcome.status.value}"
        )
        assert lean_errors == py_errors, (
            f"[{name}] errors mismatch: lean={lean_errors} py={py_errors}"
        )
        assert lean_warnings == py_warnings, (
            f"[{name}] warnings mismatch: lean={lean_warnings} py={py_warnings}"
        )
        assert resp["field_statuses"] == py_field_statuses, (
            f"[{name}] field_statuses mismatch: "
            f"lean={resp['field_statuses']} py={py_field_statuses}"
        )


@pytest.mark.property
@settings(max_examples=30, deadline=None)
@given(
    subtotal=st.one_of(
        st.none(),
        st.sampled_from([Decimal("100.00"), Decimal("50.00"), Decimal("NaN"), True, "100"]),
    ),
    tax_pres=st.sampled_from([FieldPresence.PRESENT, FieldPresence.MISSING]),
    tax_val=st.sampled_from([None, Decimal("10.00"), Decimal("0.00")]),
    total=st.sampled_from(
        [Decimal("100.00"), Decimal("110.00"), Decimal("110.01"), Decimal("110.05")]
    ),
    pm_pres=st.sampled_from([FieldPresence.PRESENT, FieldPresence.MISSING]),
    pm_val=st.sampled_from([None, "invoice", "wire", "card"]),
    po_val=st.sampled_from([None, "PO-1"]),
    note_val=st.sampled_from([None, "ok"]),
    ssn_val=st.sampled_from([None, "123"]),
    ein_val=st.sampled_from([None, "99-1"]),
    due_day=st.sampled_from([date(2026, 1, 5), date(2026, 1, 10), date(2026, 2, 1)]),
)
def test_property_lean_oracle_differential_cross_field_rules(
    subtotal: Any,
    tax_pres: FieldPresence,
    tax_val: Any,
    total: Decimal,
    pm_pres: FieldPresence,
    pm_val: Any,
    po_val: Any,
    note_val: Any,
    ssn_val: Any,
    ein_val: Any,
    due_day: date,
) -> None:
    """Hypothesis differential property check: Python cross-field rules == Lean oracle."""
    schema = build_invoice_cross_field_schema()
    fields: dict[str, tuple[FieldPresence, Any]] = {
        "issue_date": (FieldPresence.PRESENT, date(2026, 1, 10)),
        "due_date": (FieldPresence.PRESENT, due_day),
        "subtotal": (
            FieldPresence.MISSING if subtotal is None else FieldPresence.PRESENT,
            subtotal,
        ),
        "tax": (tax_pres, None if tax_pres == FieldPresence.MISSING else tax_val),
        "total": (FieldPresence.PRESENT, total),
        "payment_method": (pm_pres, None if pm_pres == FieldPresence.MISSING else pm_val),
        "po_number": (FieldPresence.MISSING if po_val is None else FieldPresence.PRESENT, po_val),
        "nullable_note": (FieldPresence.PRESENT, note_val),
        "ssn": (FieldPresence.MISSING if ssn_val is None else FieldPresence.PRESENT, ssn_val),
        "ein": (FieldPresence.MISSING if ein_val is None else FieldPresence.PRESENT, ein_val),
    }
    validator = DeterministicValidator()
    py_res = validator.validate_record(fields, schema, record_id="prop-cf")
    resp = _query_lean_oracle(
        [
            {
                "kind": "validate_record",
                "payload": {
                    "schema": encode_schema_with_rules(schema),
                    "record_id": "prop-cf",
                    "fields": encode_record_fields(fields),
                    "pattern_results": {},
                },
            }
        ]
    )[0]

    assert resp["ok"] is True
    assert resp["status"] == py_res.record_outcome.status.value
    assert [(e["field_path"], e["code"]) for e in resp["errors"]] == [
        (e.field_path, e.code) for e in py_res.record_outcome.errors
    ]
    assert [(w["field_path"], w["code"]) for w in resp["warnings"]] == [
        (w.field_path, w.code) for w in py_res.warnings
    ]
    assert resp["field_statuses"] == {k: v.status.value for k, v in py_res.field_outcomes.items()}
