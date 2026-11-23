"""CLI, filtering, live-strategy isolation, secret hygiene, and packaging exclusion tests."""

from __future__ import annotations

import io
import tomllib
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.cli import run_eval_cli
from evals.reporting import load_report
from structa_maestro import OpenAICompatibleLLMStrategy

BASELINE_PATH = (
    Path(__file__).parent.parent.parent / "evals" / "baselines" / "deterministic_v2.0.0.json"
)
PYPROJECT_PATH = Path(__file__).parent.parent.parent / "pyproject.toml"


@pytest.mark.evaluation
def test_cli_validate_run_compare_diff_and_filtering(tmp_path: Path) -> None:
    # 1. validate-dataset exits 0
    out_val = io.StringIO()
    err_val = io.StringIO()
    assert run_eval_cli(["validate-dataset"], stdout=out_val, stderr=err_val) == 0
    assert "cases=50" in out_val.getvalue()

    # 2. run filtered subset records is_filtered=True
    filtered_json = tmp_path / "filtered.json"
    filtered_md = tmp_path / "filtered.md"
    out_flt = io.StringIO()
    code_flt = run_eval_cli(
        [
            "run",
            "--category",
            "invoices",
            "--format",
            "txt",
            "--output-json",
            str(filtered_json),
            "--output-md",
            str(filtered_md),
        ],
        stdout=out_flt,
        stderr=io.StringIO(),
    )
    assert code_flt == 0
    rep = load_report(filtered_json)
    assert rep.filters.is_filtered is True
    assert rep.summary.total_cases == 2
    assert filtered_md.exists()

    # 3. empty filter exits 1 with clean error
    err_empty = io.StringIO()
    code_empty = run_eval_cli(
        ["run", "--case-id", "does-not-exist"],
        stdout=io.StringIO(),
        stderr=err_empty,
    )
    assert code_empty == 1
    assert "matched 0 golden cases" in err_empty.getvalue()

    # 4. compare & diff commands work end-to-end
    out_cmp = io.StringIO()
    code_cmp = run_eval_cli(
        ["compare", str(BASELINE_PATH), str(BASELINE_PATH)],
        stdout=out_cmp,
        stderr=io.StringIO(),
    )
    assert code_cmp == 0
    assert "Baseline Regression Gate: PASS" in out_cmp.getvalue()

    out_diff = io.StringIO()
    code_diff = run_eval_cli(
        ["diff", str(BASELINE_PATH), str(filtered_json)],
        stdout=out_diff,
        stderr=io.StringIO(),
    )
    assert code_diff == 0
    assert "## Differential Strategy Comparison" in out_diff.getvalue()


@pytest.mark.evaluation
def test_cli_live_mode_fault_isolation_and_secret_scrubbing(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Missing API key env var exits 1
    monkeypatch.delenv("STRUCTA_MAESTRO_API_KEY", raising=False)
    err_missing = io.StringIO()
    assert (
        run_eval_cli(["run", "--strategy", "llm", "--case-id", "inv-txt-001"], stderr=err_missing)
        == 1
    )
    assert "STRUCTA_MAESTRO_API_KEY" in err_missing.getvalue()

    # Transient failure on one case must still complete remaining cases without leaking API key
    fake_secret = "sk-eval-supersecret-999999999"
    monkeypatch.setenv("STRUCTA_MAESTRO_API_KEY", fake_secret)

    def _failing_http(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"upstream error with {fake_secret}")

    transport = httpx.MockTransport(_failing_http)
    report_out = tmp_path / "live.json"
    out_live = io.StringIO()
    err_live = io.StringIO()
    with httpx.Client(transport=transport) as client:
        live_strat = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key=fake_secret,
            base_url="https://api.example.test/v1",
            http_client=client,
            sleep_fn=lambda _s: None,
        )
        code = run_eval_cli(
            [
                "run",
                "--category",
                "invoices",
                "--format",
                "txt",
                "--output-json",
                str(report_out),
            ],
            stdout=out_live,
            stderr=err_live,
            strategy_override=live_strat,
        )

    assert code == 0
    raw_report = report_out.read_text(encoding="utf-8")
    assert fake_secret not in raw_report
    assert fake_secret not in out_live.getvalue()
    assert fake_secret not in err_live.getvalue()

    # Invalid LLM hardening flags exit 1 cleanly
    err_bad = io.StringIO()
    assert (
        run_eval_cli(
            [
                "run",
                "--strategy",
                "llm",
                "--case-id",
                "inv-txt-001",
                "--max-retries",
                "-1",
            ],
            stderr=err_bad,
        )
        == 1
    )
    assert "Error:" in err_bad.getvalue()

    err_budget = io.StringIO()
    assert (
        run_eval_cli(
            [
                "run",
                "--strategy",
                "llm",
                "--case-id",
                "inv-txt-001",
                "--max-input-chars",
                "0",
            ],
            stderr=err_budget,
        )
        == 1
    )
    assert "Error:" in err_budget.getvalue()

    # Valid --max-input-chars none & --overflow-policy truncate accepted
    assert (
        run_eval_cli(
            [
                "run",
                "--case-id",
                "inv-txt-001",
                "--structured-output-mode",
                "strict",
                "--max-retries",
                "1",
                "--max-input-chars",
                "none",
                "--overflow-policy",
                "truncate",
            ],
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        == 0
    )


@pytest.mark.evaluation
def test_evals_excluded_from_published_package_and_zero_extra_runtime_deps() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    sdist_includes = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    runtime_deps = pyproject["project"]["dependencies"]

    assert wheel_packages == ["src/structa_maestro"]
    assert all("evals" not in inc for inc in sdist_includes)
    assert len(runtime_deps) == 4
