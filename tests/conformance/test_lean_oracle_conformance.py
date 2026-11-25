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
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
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

from structa_maestro.confidence.normalizer import ConfidenceNormalizer
from structa_maestro.contracts.enums import EvidenceRelationship, FieldPresence
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    ExtractedField,
    ExtractionResult,
    SourceLocation,
    ValidationOutcome,
)

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
