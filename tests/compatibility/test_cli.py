"""Thin single-document CLI compatibility tests (FR-035, contracts/python-api-and-cli.md)."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionResult,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)
from structa_maestro.cli import run_cli


def test_cli_valid_extraction_emits_canonical_json_and_exits_zero(
    fixtures_dir: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    schema_path = fixtures_dir / "schemas" / "invoice_schema.json"
    strat = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "vendor_name": ProposedField(value="Acme Industrial Supply Ltd."),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("1250.50")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            )
        )
    )
    out_buf = io.StringIO()
    err_buf = io.StringIO()

    code = run_cli(
        ["extract", str(doc), "--schema", str(schema_path), "--pretty"],
        stdout=out_buf,
        stderr=err_buf,
        strategy_override=strat,
    )
    assert code == 0
    assert err_buf.getvalue() == ""
    payload = json.loads(out_buf.getvalue())
    jsonschema.validate(instance=payload, schema=contract_json_schema)
    restored = ExtractionResult.from_json(out_buf.getvalue())
    assert restored.records[0].fields["invoice_number"].value == "INV-2026-001"


def test_cli_exit_code_2_on_failed_or_invalid_result_and_1_on_bad_schema_or_missing_key(
    fixtures_dir: Path,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    schema_path = fixtures_dir / "schemas" / "invoice_schema.json"
    out_file = tmp_path / "result.json"

    # Exit code 2: canonical failed result (nonexistent document) written to --output
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    code_2 = run_cli(
        [
            "extract",
            str(tmp_path / "missing.txt"),
            "--schema",
            str(schema_path),
            "--output",
            str(out_file),
        ],
        stdout=out_buf,
        stderr=err_buf,
        strategy_override=DeterministicTestStrategy(StrategyOutput(records=())),
    )
    assert code_2 == 2
    assert out_file.exists()
    parsed = ExtractionResult.from_json(out_file.read_text(encoding="utf-8"))
    assert parsed.status.value == "failed"

    # Exit code 1: missing API key env var (without strategy override)
    monkeypatch.delenv("STRUCTA_MAESTRO_API_KEY", raising=False)
    err_missing_key = io.StringIO()
    code_1 = run_cli(
        ["extract", str(doc), "--schema", str(schema_path)],
        stdout=io.StringIO(),
        stderr=err_missing_key,
    )
    assert code_1 == 1
    assert "STRUCTA_MAESTRO_API_KEY" in err_missing_key.getvalue()

    # Exit code 1: invalid schema file
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text("{not json", encoding="utf-8")
    err_bad_schema = io.StringIO()
    code_bad = run_cli(
        ["extract", str(doc), "--schema", str(bad_schema)],
        stdout=io.StringIO(),
        stderr=err_bad_schema,
        strategy_override=DeterministicTestStrategy(StrategyOutput(records=())),
    )
    assert code_bad == 1
    assert "Error:" in err_bad_schema.getvalue()


def test_cli_llm_hardening_flags_and_validation(
    fixtures_dir: Path,
    monkeypatch: Any,
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    schema_path = fixtures_dir / "schemas" / "invoice_schema.json"
    strat = DeterministicTestStrategy(StrategyOutput(records=()))

    # Valid new flags accepted (including --max-input-chars none)
    code_ok = run_cli(
        [
            "extract",
            str(doc),
            "--schema",
            str(schema_path),
            "--structured-output-mode",
            "strict",
            "--max-retries",
            "0",
            "--max-input-chars",
            "none",
            "--overflow-policy",
            "truncate",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        strategy_override=strat,
    )
    assert code_ok == 2  # empty records -> invalid canonical result, flags parsed cleanly

    # Invalid --max-retries (-1) exits 1
    err_retry = io.StringIO()
    assert (
        run_cli(
            ["extract", str(doc), "--schema", str(schema_path), "--max-retries", "-1"],
            stdout=io.StringIO(),
            stderr=err_retry,
            strategy_override=strat,
        )
        == 1
    )
    assert "Error:" in err_retry.getvalue()

    # Invalid --max-input-chars (0 and non-numeric) exits 1
    for bad_budget in ("0", "-50", "not-a-number"):
        err_budget = io.StringIO()
        assert (
            run_cli(
                [
                    "extract",
                    str(doc),
                    "--schema",
                    str(schema_path),
                    "--max-input-chars",
                    bad_budget,
                ],
                stdout=io.StringIO(),
                stderr=err_budget,
                strategy_override=strat,
            )
            == 1
        )
        assert "Error:" in err_budget.getvalue()

    # Verify flag values forward cleanly to OpenAICompatibleLLMStrategy
    captured: dict[str, Any] = {}
    monkeypatch.setenv("STRUCTA_MAESTRO_API_KEY", "sk-cli-flag-test-123456")

    def _fake_extract(_doc: Any, _schema: Any, **kwargs: Any) -> Any:
        captured["strategy"] = kwargs["strategy"]
        raise RuntimeError("stop-after-capture")

    monkeypatch.setattr("structa_maestro.cli.extract", _fake_extract)
    run_cli(
        [
            "extract",
            str(doc),
            "--schema",
            str(schema_path),
            "--structured-output-mode",
            "json_object",
            "--max-retries",
            "4",
            "--max-input-chars",
            "8000",
            "--overflow-policy",
            "truncate",
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    strat_obj = captured["strategy"]
    assert strat_obj.structured_output_mode.value == "json_object"
    assert strat_obj.retry_policy.max_retries == 4
    assert strat_obj.input_budget.max_input_chars == 8000
    assert strat_obj.input_budget.overflow_policy.value == "truncate"
