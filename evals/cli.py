"""Repository-internal CLI entry point for golden dataset evaluation (`python -m evals`)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from evals.comparison import compare_against_baseline, diff_strategies
from evals.dataset import DEFAULT_MANIFEST_PATH, load_dataset
from evals.models import (
    ChallengeProfile,
    DocumentCategory,
    EvaluationFilter,
    RegressionThresholds,
)
from evals.reference_strategy import ReferenceDeterministicStrategy
from evals.reporting import (
    load_report,
    render_comparison_markdown,
    render_differential_markdown,
    render_report_markdown,
    save_report,
)
from evals.runner import EvaluationRunner
from structa_maestro.config import sanitize_message
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.strategies.base import ExtractionStrategy
from structa_maestro.strategies.llm_config import (
    DEFAULT_MAX_INPUT_CHARS,
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    RetryPolicy,
    StructuredOutputMode,
    parse_max_input_chars_arg,
)
from structa_maestro.strategies.llm_openai_compat import OpenAICompatibleLLMStrategy

_DEFAULT_API_KEY_ENV = "STRUCTA_MAESTRO_API_KEY"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"


def _add_threshold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-exact-match-drop", type=float, default=0.0)
    parser.add_argument("--max-f1-drop", type=float, default=0.0)
    parser.add_argument("--max-provenance-drop", type=float, default=0.0)
    parser.add_argument("--max-validation-drop", type=float, default=0.0)
    parser.add_argument("--max-hallucination-increase", type=float, default=0.0)
    parser.add_argument("--max-failure-rate-increase", type=float, default=0.0)
    parser.add_argument("--allow-version-mismatch", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Repository-internal golden evaluation corpus runner and regression gate.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    val_p = sub.add_parser("validate-dataset", help="Validate golden dataset manifest integrity.")
    val_p.add_argument("--dataset", default=str(DEFAULT_MANIFEST_PATH))

    run_p = sub.add_parser("run", help="Run golden evaluation suite and optional baseline gate.")
    run_p.add_argument("--dataset", default=str(DEFAULT_MANIFEST_PATH))
    run_p.add_argument("--strategy", choices=["deterministic", "llm"], default="deterministic")
    run_p.add_argument("--model", default=_DEFAULT_MODEL)
    run_p.add_argument("--base-url", default=_DEFAULT_BASE_URL)
    run_p.add_argument("--api-key-env", default=_DEFAULT_API_KEY_ENV)
    run_p.add_argument(
        "--structured-output-mode",
        choices=[m.value for m in StructuredOutputMode],
        default=StructuredOutputMode.STRICT_WITH_FALLBACK.value,
    )
    run_p.add_argument("--max-retries", type=int, default=2)
    run_p.add_argument("--max-input-chars", default=str(DEFAULT_MAX_INPUT_CHARS))
    run_p.add_argument(
        "--overflow-policy",
        choices=[p.value for p in InputBudgetOverflowPolicy],
        default=InputBudgetOverflowPolicy.REJECT.value,
    )
    run_p.add_argument("--config-version", default="default")
    run_p.add_argument("--trials", type=int, default=1)
    run_p.add_argument("--category", action="append", default=[])
    run_p.add_argument("--format", dest="doc_format", action="append", default=[])
    run_p.add_argument("--profile", action="append", default=[])
    run_p.add_argument("--case-id", action="append", default=[])
    run_p.add_argument("--baseline", default=None)
    run_p.add_argument("--output-json", default=None)
    run_p.add_argument("--output-md", default=None)
    _add_threshold_args(run_p)

    cmp_p = sub.add_parser("compare", help="Compare candidate report JSON against baseline JSON.")
    cmp_p.add_argument("candidate")
    cmp_p.add_argument("baseline")
    cmp_p.add_argument("--output-md", default=None)
    _add_threshold_args(cmp_p)

    diff_p = sub.add_parser("diff", help="Differential side-by-side comparison of two reports.")
    diff_p.add_argument("report_a")
    diff_p.add_argument("report_b")
    diff_p.add_argument("--output-md", default=None)
    return parser


def _thresholds_from_args(args: argparse.Namespace) -> RegressionThresholds:
    return RegressionThresholds(
        max_exact_match_drop=args.max_exact_match_drop,
        max_f1_drop=args.max_f1_drop,
        max_provenance_drop=args.max_provenance_drop,
        max_validation_drop=args.max_validation_drop,
        max_hallucination_increase=args.max_hallucination_increase,
        max_failure_rate_increase=args.max_failure_rate_increase,
        allow_dataset_version_mismatch=args.allow_version_mismatch,
    )


def run_eval_cli(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    strategy_override: ExtractionStrategy | None = None,
) -> int:
    """Execute the `python -m evals` CLI and return exit code 0 (pass), 2 (gate fail), 1 (err)."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_parser()

    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    try:
        if args.command == "validate-dataset":
            manifest = load_dataset(args.dataset, validate_full_corpus=True)
            out.write(
                f"Dataset valid: version={manifest.dataset_version}, cases={len(manifest.cases)}\n"
            )
            return 0

        if args.command == "compare":
            cand = load_report(args.candidate)
            base = load_report(args.baseline)
            comparison = compare_against_baseline(cand, base, _thresholds_from_args(args))
            md = render_comparison_markdown(comparison)
            out.write(md)
            if args.output_md:
                p = Path(args.output_md)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(md, encoding="utf-8")
            return 0 if comparison.passed else 2

        if args.command == "diff":
            rep_a = load_report(args.report_a)
            rep_b = load_report(args.report_b)
            diff = diff_strategies(rep_a, rep_b)
            md = render_differential_markdown(diff)
            out.write(md)
            if args.output_md:
                p = Path(args.output_md)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(md, encoding="utf-8")
            return 0

        if args.command == "run":
            api_key = os.environ.get(args.api_key_env, "")
            secrets_to_scrub = (api_key,) if api_key else ()
            manifest = load_dataset(args.dataset, validate_full_corpus=False)
            filters = EvaluationFilter(
                categories=[DocumentCategory(c) for c in args.category],
                formats=[DocumentFormat(f) for f in args.doc_format],
                profiles=[ChallengeProfile(p) for p in args.profile],
                case_ids=list(args.case_id),
            )
            retry_policy = RetryPolicy(max_retries=args.max_retries)
            input_budget = InputBudgetPolicy(
                max_input_chars=parse_max_input_chars_arg(args.max_input_chars),
                overflow_policy=InputBudgetOverflowPolicy(args.overflow_policy),
            )
            if strategy_override is not None:
                strategy: ExtractionStrategy = strategy_override
            elif args.strategy == "deterministic":
                strategy = ReferenceDeterministicStrategy()
            else:
                if not api_key.strip():
                    err.write(
                        f"Configuration error: environment variable '{args.api_key_env}' "
                        "is required for --strategy llm.\n"
                    )
                    return 1
                strategy = OpenAICompatibleLLMStrategy(
                    model=args.model,
                    api_key=api_key,
                    base_url=args.base_url,
                    structured_output_mode=args.structured_output_mode,
                    retry_policy=retry_policy,
                    input_budget=input_budget,
                )

            runner = EvaluationRunner(manifest)
            report = runner.run(
                strategy=strategy,
                config_version=args.config_version,
                trials=args.trials,
                filters=filters,
                secrets_to_scrub=secrets_to_scrub,
            )

            if args.output_json:
                save_report(report, args.output_json, secrets_to_scrub=secrets_to_scrub)

            md_parts = [render_report_markdown(report)]
            exit_code = 0

            if args.baseline:
                baseline_report = load_report(args.baseline)
                comparison = compare_against_baseline(
                    report, baseline_report, _thresholds_from_args(args)
                )
                md_parts.append(render_comparison_markdown(comparison))
                if not comparison.passed:
                    exit_code = 2

            full_md = "\n".join(md_parts)
            out.write(full_md)
            if args.output_md:
                p = Path(args.output_md)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(full_md, encoding="utf-8")
            return exit_code

    except Exception as exc:
        raw_secret = os.environ.get(getattr(args, "api_key_env", _DEFAULT_API_KEY_ENV), "")
        safe = sanitize_message(str(exc), secrets=(raw_secret,) if raw_secret else ())
        err.write(f"Error: {safe}\n")
        return 1

    return 1


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for `python -m evals`."""
    raise SystemExit(run_eval_cli(argv))
