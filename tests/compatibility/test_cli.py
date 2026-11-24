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


def test_cli_extract_batch_directory_jsonl_summary_and_exit_codes(
    fixtures_dir: Path,
    tmp_path: Path,
    contract_json_schema: dict[str, Any],
    monkeypatch: Any,
) -> None:
    schema_path = fixtures_dir / "schemas" / "invoice_schema.json"
    batch_dir = tmp_path / "batch_in"
    nested = batch_dir / "nested"
    nested.mkdir(parents=True)
    (batch_dir / "01.txt").write_text("Invoice Number: INV-2026-001\n", encoding="utf-8")
    (batch_dir / "02.txt").write_text("Invoice Number: INV-2026-002\n", encoding="utf-8")
    (nested / "03.txt").write_text("Invoice Number: INV-2026-003\n", encoding="utf-8")
    (batch_dir / "ignore.md").write_text("# ignored\n", encoding="utf-8")

    ok_strat = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(value="INV-2026-001"),
                        "vendor_name": ProposedField(value="Acme"),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("100.00")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            )
        )
    )

    # 1. Non-recursive directory -> 2 lines to --output + --summary-json -> exit 0
    out_jsonl = tmp_path / "out.jsonl"
    sum_json = tmp_path / "summary.json"
    code_0 = run_cli(
        [
            "extract-batch",
            str(batch_dir),
            "--schema",
            str(schema_path),
            "--max-workers",
            "2",
            "--output",
            str(out_jsonl),
            "--summary-json",
            str(sum_json),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        strategy_override=ok_strat,
    )
    assert code_0 == 0
    lines = out_jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        jsonschema.validate(instance=json.loads(line), schema=contract_json_schema)
    summary_data = json.loads(sum_json.read_text(encoding="utf-8"))
    assert summary_data["total_documents"] == 2
    assert summary_data["completed_count"] == 2
    assert summary_data["valid_count"] == 2

    # 2. --recursive includes nested/03.txt -> 3 lines on stdout -> exit 0
    stdout_rec = io.StringIO()
    code_rec = run_cli(
        ["extract-batch", str(batch_dir), "--schema", str(schema_path), "--recursive"],
        stdout=stdout_rec,
        stderr=io.StringIO(),
        strategy_override=ok_strat,
    )
    assert code_rec == 0
    assert len(stdout_rec.getvalue().splitlines()) == 3

    # 3. Explicit nonexistent file alongside valid file -> canonical failed JSONL + exit 2
    stdout_mix = io.StringIO()
    code_2 = run_cli(
        [
            "extract-batch",
            str(batch_dir / "01.txt"),
            str(tmp_path / "missing.txt"),
            "--schema",
            str(schema_path),
            "--fail-fast",
        ],
        stdout=stdout_mix,
        stderr=io.StringIO(),
        strategy_override=ok_strat,
    )
    assert code_2 == 2
    mix_lines = stdout_mix.getvalue().splitlines()
    assert len(mix_lines) == 2
    assert json.loads(mix_lines[0])["status"] == "completed"
    assert json.loads(mix_lines[1])["status"] == "failed"

    # 4. Empty directory -> exit 1
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    err_empty = io.StringIO()
    assert (
        run_cli(
            ["extract-batch", str(empty_dir), "--schema", str(schema_path)],
            stdout=io.StringIO(),
            stderr=err_empty,
            strategy_override=ok_strat,
        )
        == 1
    )
    assert "No supported input documents" in err_empty.getvalue()

    # 5. Identical --output and --summary-json paths -> exit 1
    err_same = io.StringIO()
    assert (
        run_cli(
            [
                "extract-batch",
                str(batch_dir),
                "--schema",
                str(schema_path),
                "--output",
                str(out_jsonl),
                "--summary-json",
                str(out_jsonl),
            ],
            stdout=io.StringIO(),
            stderr=err_same,
            strategy_override=ok_strat,
        )
        == 1
    )
    assert "same file" in err_same.getvalue()

    # 6. Invalid --max-workers & missing API key -> exit 1
    assert (
        run_cli(
            ["extract-batch", str(batch_dir), "--schema", str(schema_path), "--max-workers", "0"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            strategy_override=ok_strat,
        )
        == 1
    )
    monkeypatch.delenv("STRUCTA_MAESTRO_API_KEY", raising=False)
    err_key = io.StringIO()
    assert (
        run_cli(
            ["extract-batch", str(batch_dir), "--schema", str(schema_path)],
            stdout=io.StringIO(),
            stderr=err_key,
        )
        == 1
    )
    assert "STRUCTA_MAESTRO_API_KEY" in err_key.getvalue()


def test_cli_extract_stdin_filename_max_bytes_and_exit_codes(
    fixtures_dir: Path,
    tmp_path: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    from tests.conftest import create_minimal_docx, create_minimal_pdf

    schema_path = fixtures_dir / "schemas" / "invoice_schema.json"
    ok_strat = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(value="INV-2026-001"),
                        "vendor_name": ProposedField(value="Acme"),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("100.00")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            )
        )
    )

    # 1. Piped TXT via '-' with traversal --filename -> sanitized basename & exit 0
    stdout_txt = io.StringIO()
    code_txt = run_cli(
        [
            "extract",
            "-",
            "--schema",
            str(schema_path),
            "--filename",
            "../../private/invoice_001.txt",
            "--document-id",
            "stdin-txt-1",
        ],
        stdout=stdout_txt,
        stderr=io.StringIO(),
        stdin_buffer=io.BytesIO(b"Invoice Number: INV-2026-001\n"),
        strategy_override=ok_strat,
    )
    assert code_txt == 0
    parsed_txt = json.loads(stdout_txt.getvalue())
    jsonschema.validate(instance=parsed_txt, schema=contract_json_schema)
    assert parsed_txt["source"]["filename"] == "invoice_001.txt"
    assert parsed_txt["source"]["document_id"] == "stdin-txt-1"
    assert parsed_txt["source"]["media_type"] == "text/plain"

    # 2. Piped binary PDF & DOCX via '-' without --filename -> source.filename is None & exit 0
    pdf_bytes = create_minimal_pdf(
        tmp_path / "sample.pdf", ["Invoice Number: INV-2026-001"]
    ).read_bytes()
    stdout_pdf = io.StringIO()
    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path)],
            stdout=stdout_pdf,
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(pdf_bytes),
            strategy_override=ok_strat,
        )
        == 0
    )
    parsed_pdf = json.loads(stdout_pdf.getvalue())
    assert parsed_pdf["source"]["filename"] is None
    assert parsed_pdf["source"]["media_type"] == "application/pdf"

    docx_bytes = create_minimal_docx(
        tmp_path / "sample.docx", ["Invoice Number: INV-2026-001"]
    ).read_bytes()
    stdout_docx = io.StringIO()
    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path), "--filename", "doc.docx"],
            stdout=stdout_docx,
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(docx_bytes),
            strategy_override=ok_strat,
        )
        == 0
    )
    parsed_docx = json.loads(stdout_docx.getvalue())
    assert parsed_docx["source"]["filename"] == "doc.docx"
    assert parsed_docx["source"]["media_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    # 3. Empty stdin & oversized stdin via --max-bytes -> canonical failed JSON + exit 2
    stdout_empty = io.StringIO()
    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path)],
            stdout=stdout_empty,
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(b""),
            strategy_override=ok_strat,
        )
        == 2
    )
    assert json.loads(stdout_empty.getvalue())["status"] == "failed"

    stdout_big = io.StringIO()
    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path), "--max-bytes", "32"],
            stdout=stdout_big,
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(b"A" * 1024),
            strategy_override=ok_strat,
        )
        == 2
    )
    parsed_big = json.loads(stdout_big.getvalue())
    assert parsed_big["status"] == "failed"
    assert any(e["code"] == "resource_limit_exceeded" for e in parsed_big["errors"])

    # 4. --filename with regular path, null byte in --filename, or --max-bytes <= 0 -> exit 1
    doc_path = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    err_fn = io.StringIO()
    assert (
        run_cli(
            ["extract", str(doc_path), "--schema", str(schema_path), "--filename", "x.txt"],
            stdout=io.StringIO(),
            stderr=err_fn,
            strategy_override=ok_strat,
        )
        == 1
    )
    assert "--filename" in err_fn.getvalue()

    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path), "--filename", "bad\x00.txt"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(b"hello"),
            strategy_override=ok_strat,
        )
        == 1
    )
    assert (
        run_cli(
            ["extract", "-", "--schema", str(schema_path), "--max-bytes", "0"],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            stdin_buffer=io.BytesIO(b"hello"),
            strategy_override=ok_strat,
        )
        == 1
    )
