"""Thin CLI delegating directly to structa_maestro.extract and extract_batch."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, TextIO

from structa_maestro.batch import BatchOptions, discover_batch_documents
from structa_maestro.confidence.calibration import CalibrationModel
from structa_maestro.config import DocumentLimits, sanitize_message
from structa_maestro.contracts.enums import ProcessingStatus, ValidationStatus
from structa_maestro.contracts.models import ExtractionResult
from structa_maestro.documents.inputs import DocumentInput
from structa_maestro.engine import extract, extract_batch
from structa_maestro.exceptions import ConfigurationError, StructaMaestroError
from structa_maestro.review import (
    MAX_REVIEW_PAYLOAD_BYTES,
    ReviewPolicy,
    ReviewSubmission,
    apply_review,
    plan_review,
)
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import ExtractionStrategy
from structa_maestro.strategies.llm_anthropic import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    AnthropicLLMStrategy,
)
from structa_maestro.strategies.llm_config import (
    DEFAULT_MAX_INPUT_CHARS,
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    RetryPolicy,
    StructuredOutputMode,
    parse_max_input_chars_arg,
)
from structa_maestro.strategies.llm_gemini import (
    DEFAULT_GEMINI_BASE_URL,
    GeminiLLMStrategy,
)
from structa_maestro.strategies.llm_openai_compat import OpenAICompatibleLLMStrategy
from structa_maestro.telemetry import InMemoryTelemetryObserver

_DEFAULT_API_KEY_ENV = "STRUCTA_MAESTRO_API_KEY"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
_DEFAULT_GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _resolve_provider_defaults(args: argparse.Namespace) -> tuple[str, str, str]:
    if args.provider == "anthropic":
        model = args.model if args.model is not None else _DEFAULT_ANTHROPIC_MODEL
        base_url = args.base_url if args.base_url is not None else DEFAULT_ANTHROPIC_BASE_URL
        api_key_env = (
            args.api_key_env if args.api_key_env is not None else _DEFAULT_ANTHROPIC_API_KEY_ENV
        )
        return model, base_url, api_key_env
    if args.provider == "gemini":
        model = args.model if args.model is not None else _DEFAULT_GEMINI_MODEL
        base_url = args.base_url if args.base_url is not None else DEFAULT_GEMINI_BASE_URL
        api_key_env = (
            args.api_key_env if args.api_key_env is not None else _DEFAULT_GEMINI_API_KEY_ENV
        )
        return model, base_url, api_key_env
    model = args.model if args.model is not None else _DEFAULT_MODEL
    base_url = args.base_url if args.base_url is not None else _DEFAULT_BASE_URL
    api_key_env = args.api_key_env if args.api_key_env is not None else _DEFAULT_API_KEY_ENV
    return model, base_url, api_key_env


def _add_common_extraction_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--schema", required=True, help="Path to the JSON extraction schema file.")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "gemini"],
        default="openai",
        help="LLM provider strategy backend (default: openai).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model identifier (default: gpt-4o-mini for openai, claude-sonnet-4-5 for anthropic, "
            "gemini-2.5-flash for gemini)."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Provider endpoint base URL (default: https://api.openai.com/v1 for openai, "
            "https://api.anthropic.com for anthropic, "
            "https://generativelanguage.googleapis.com for gemini)."
        ),
    )
    parser.add_argument(
        "--api-key-env",
        default=None,
        help=(
            "Environment variable holding the API key "
            "(default: STRUCTA_MAESTRO_API_KEY for openai, ANTHROPIC_API_KEY for anthropic, "
            "GEMINI_API_KEY for gemini)."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_ANTHROPIC_MAX_TOKENS,
        help=(
            f"Maximum output tokens for Anthropic Messages and Gemini generateContent APIs "
            f"(default: {DEFAULT_ANTHROPIC_MAX_TOKENS})."
        ),
    )
    parser.add_argument(
        "--max-file-mb",
        type=int,
        default=25,
        help="Maximum raw file size in MB (default: 25).",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help=(
            "Optional byte-exact override for maximum input payload size (overrides --max-file-mb)."
        ),
    )
    parser.add_argument(
        "--max-decompressed-mb",
        type=int,
        default=50,
        help="Maximum decompressed/extracted text size in MB (default: 50).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Maximum PDF page count (default: 200).",
    )
    parser.add_argument(
        "--structured-output-mode",
        choices=[m.value for m in StructuredOutputMode],
        default=StructuredOutputMode.STRICT_WITH_FALLBACK.value,
        help="Structured output enforcement mode (default: strict_with_fallback).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Maximum bounded retries on transient provider/output failures (default: 2).",
    )
    parser.add_argument(
        "--max-input-chars",
        default=str(DEFAULT_MAX_INPUT_CHARS),
        help=(
            "Maximum prepared document characters sent to the model, or 'none' to disable "
            f"(default: {DEFAULT_MAX_INPUT_CHARS})."
        ),
    )
    parser.add_argument(
        "--overflow-policy",
        choices=[p.value for p in InputBudgetOverflowPolicy],
        default=InputBudgetOverflowPolicy.REJECT.value,
        help="Overflow policy when input exceeds --max-input-chars (default: reject).",
    )
    parser.add_argument(
        "--telemetry-json",
        default=None,
        help="Optional path to write privacy-safe TelemetrySummary JSON.",
    )
    parser.add_argument(
        "--calibrator",
        default=None,
        help="Optional path to a saved Confidence CalibrationModel JSON artifact.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="structa-maestro",
        description=(
            "Extract validated, provenance-backed canonical structured records from "
            "TXT, text-based PDF, DOCX, HTML, Markdown, or EML documents."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ext = subparsers.add_parser(
        "extract",
        help="Extract structured records from one document (or '-' for stdin) using a JSON schema.",
    )
    ext.add_argument(
        "document",
        help=(
            "Path to the input document (TXT, PDF, DOCX, HTML, Markdown, or EML), "
            "or '-' to read from binary stdin."
        ),
    )
    _add_common_extraction_args(ext)
    ext.add_argument(
        "--filename",
        default=None,
        help="Optional filename hint for stdin ('-') input (sanitized to basename only).",
    )
    ext.add_argument("--output", help="Optional path to write canonical JSON output.")
    ext.add_argument("--document-id", help="Optional caller-supplied source document identifier.")
    ext.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print canonical JSON output with 2-space indentation.",
    )

    batch = subparsers.add_parser(
        "extract-batch",
        help="Extract structured records from multiple files or directories as JSON Lines (JSONL).",
    )
    batch.add_argument(
        "inputs",
        nargs="+",
        help="One or more input document file paths and/or directories.",
    )
    _add_common_extraction_args(batch)
    batch.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan subdirectories when directory inputs are provided.",
    )
    batch.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Bounded in-process concurrency worker count (1..32, default: 1).",
    )
    batch.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop scheduling remaining documents after the first failed extraction.",
    )
    batch.add_argument("--output", help="Optional path to write Contract v1.0 JSONL output.")
    batch.add_argument(
        "--summary-json", help="Optional path to write aggregate batch summary JSON."
    )

    rev_plan = subparsers.add_parser(
        "review-plan",
        help="Deterministically triage a Contract v1.0 ExtractionResult JSON for human review.",
    )
    rev_plan.add_argument("result_file", help="Path to a Contract v1.0 ExtractionResult JSON file.")
    rev_plan.add_argument(
        "--min-confidence",
        default="0.85",
        help=(
            "Minimum '0-1' confidence threshold in [0.0, 1.0], or 'none' to disable "
            "(default: 0.85)."
        ),
    )
    rev_plan.add_argument(
        "--require-calibrated",
        action="store_true",
        help="Flag present fields whose confidence is not empirically calibrated.",
    )
    rev_plan.add_argument(
        "--flag-missing-confidence",
        action="store_true",
        help="Flag present fields that have no confidence score.",
    )
    rev_plan.add_argument(
        "--flag-missing-provenance",
        action="store_true",
        help="Flag present fields that have no provenance entries.",
    )
    rev_plan.add_argument("--output", help="Optional path to write ReviewPlan JSON output.")
    rev_plan.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print ReviewPlan JSON output with 2-space indentation.",
    )

    rev_apply = subparsers.add_parser(
        "apply-review",
        help=(
            "Immutably apply ReviewSubmission decisions, re-validate, and emit Contract v1.0 JSON."
        ),
    )
    rev_apply.add_argument(
        "result_file",
        help="Path to a Contract v1.0 ExtractionResult JSON file.",
    )
    rev_apply.add_argument("--schema", required=True, help="Path to the JSON extraction schema.")
    rev_apply.add_argument(
        "--decisions",
        required=True,
        help="Path to a ReviewSubmission JSON file.",
    )
    rev_apply.add_argument("--output", help="Optional path to write updated Contract v1.0 JSON.")
    rev_apply.add_argument(
        "--audit-json",
        default=None,
        help="Optional path to write privacy-safe ReviewAudit JSON.",
    )
    rev_apply.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print canonical JSON output with 2-space indentation.",
    )
    return parser


def _load_result_json_file(path_str: str) -> ExtractionResult:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ConfigurationError("ExtractionResult file path must be non-empty.")
    target = Path(path_str)
    try:
        if not target.exists() or not target.is_file():
            raise ConfigurationError(f"ExtractionResult file not found: '{target.name}'.")
        if target.stat().st_size > MAX_REVIEW_PAYLOAD_BYTES:
            raise ConfigurationError(
                f"ExtractionResult file exceeds maximum size limit "
                f"({MAX_REVIEW_PAYLOAD_BYTES} bytes)."
            )
        raw_text = target.read_text(encoding="utf-8")
    except ConfigurationError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(
            f"Unable to read ExtractionResult file ({type(exc).__name__})."
        ) from exc
    return ExtractionResult.from_json(raw_text)


def _parse_min_confidence_arg(raw: str) -> float | None:
    cleaned = raw.strip()
    if cleaned.lower() == "none":
        return None
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ConfigurationError(
            "--min-confidence must be a float in [0.0, 1.0] or 'none'."
        ) from exc


def _build_strategy_and_limits(
    args: argparse.Namespace,
    *,
    api_key_val: str,
    api_key_env: str,
    model: str,
    base_url: str,
    strategy_override: ExtractionStrategy | None,
) -> tuple[ExtractionStrategy, DocumentLimits]:
    max_file_bytes = (
        args.max_bytes if args.max_bytes is not None else args.max_file_mb * 1024 * 1024
    )
    limits = DocumentLimits(
        max_file_bytes=max_file_bytes,
        max_decompressed_bytes=args.max_decompressed_mb * 1024 * 1024,
        max_pages=args.max_pages,
    )
    retry_policy = RetryPolicy(max_retries=args.max_retries)
    input_budget = InputBudgetPolicy(
        max_input_chars=parse_max_input_chars_arg(args.max_input_chars),
        overflow_policy=InputBudgetOverflowPolicy(args.overflow_policy),
    )
    if strategy_override is not None:
        return strategy_override, limits
    if not api_key_val.strip():
        raise ConfigurationError(
            f"Configuration error: environment variable '{api_key_env}' is not set."
        )
    if args.provider == "anthropic":
        strategy: ExtractionStrategy = AnthropicLLMStrategy(
            model=model,
            api_key=api_key_val,
            base_url=base_url,
            max_tokens=args.max_tokens,
            structured_output_mode=args.structured_output_mode,
            retry_policy=retry_policy,
            input_budget=input_budget,
        )
        return strategy, limits
    if args.provider == "gemini":
        strategy = GeminiLLMStrategy(
            model=model,
            api_key=api_key_val,
            base_url=base_url,
            max_output_tokens=args.max_tokens,
            structured_output_mode=args.structured_output_mode,
            retry_policy=retry_policy,
            input_budget=input_budget,
        )
        return strategy, limits
    strategy = OpenAICompatibleLLMStrategy(
        model=model,
        api_key=api_key_val,
        base_url=base_url,
        structured_output_mode=args.structured_output_mode,
        retry_policy=retry_policy,
        input_budget=input_budget,
    )
    return strategy, limits


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin_buffer: BinaryIO | None = None,
    strategy_override: ExtractionStrategy | None = None,
) -> int:
    """Execute the thin CLI (`extract` or `extract-batch`) and return exit code 0, 1, or 2."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_parser()

    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1

    if args.command == "review-plan":
        try:
            loaded_result = _load_result_json_file(args.result_file)
            min_conf = _parse_min_confidence_arg(args.min_confidence)
            policy = ReviewPolicy(
                min_confidence=min_conf,
                require_calibrated_confidence=args.require_calibrated,
                flag_missing_confidence=args.flag_missing_confidence,
                flag_missing_provenance=args.flag_missing_provenance,
            )
            plan = plan_review(loaded_result, policy=policy)
        except StructaMaestroError as exc:
            err.write(f"Error: {sanitize_message(str(exc))}\n")
            return 1
        except Exception as exc:
            err.write(f"Fatal error: {sanitize_message(type(exc).__name__)}\n")
            return 1

        plan_json = plan.to_json(indent=2 if args.pretty else None)
        if args.output:
            try:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(plan_json + "\n", encoding="utf-8")
            except OSError as exc:
                err.write(f"Output write error: {sanitize_message(type(exc).__name__)}\n")
                return 1
        else:
            out.write(plan_json + "\n")
        return 2 if plan.needs_review else 0

    if args.command == "apply-review":
        try:
            if (
                args.output
                and args.audit_json
                and Path(args.output).resolve() == Path(args.audit_json).resolve()
            ):
                raise ConfigurationError("--output and --audit-json cannot point to the same file.")
            if args.audit_json is not None and not args.audit_json.strip():
                raise ConfigurationError("--audit-json must be a non-empty file path.")
            loaded_result = _load_result_json_file(args.result_file)
            schema = ExtractionSchema.from_json_file(args.schema)
            submission = ReviewSubmission.from_file(args.decisions)
            applied = apply_review(loaded_result, schema, submission, redact_values=True)
        except StructaMaestroError as exc:
            err.write(f"Error: {sanitize_message(str(exc))}\n")
            return 1
        except Exception as exc:
            err.write(f"Fatal error: {sanitize_message(type(exc).__name__)}\n")
            return 1

        updated_json = applied.result.to_json(indent=2 if args.pretty else None)
        if args.output:
            try:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(updated_json + "\n", encoding="utf-8")
            except OSError as exc:
                err.write(f"Output write error: {sanitize_message(type(exc).__name__)}\n")
                return 1
        else:
            out.write(updated_json + "\n")

        if args.audit_json:
            try:
                audit_path = Path(args.audit_json)
                audit_path.parent.mkdir(parents=True, exist_ok=True)
                audit_path.write_text(applied.audit.to_json(indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                err.write(f"Audit write error: {sanitize_message(type(exc).__name__)}\n")
                return 1

        if (
            applied.result.status == ProcessingStatus.COMPLETED
            and applied.result.validation.status == ValidationStatus.VALID
        ):
            return 0
        return 2

    if args.command not in ("extract", "extract-batch"):
        err.write(f"Unsupported command: {args.command}\n")
        return 1

    resolved_model, resolved_base_url, resolved_api_key_env = _resolve_provider_defaults(args)
    api_key_val = os.environ.get(resolved_api_key_env, "")
    secrets_to_scrub = (api_key_val,) if api_key_val else ()

    if args.command == "extract":
        telemetry_observer: InMemoryTelemetryObserver | None = None
        try:
            if args.telemetry_json is not None:
                if not args.telemetry_json.strip():
                    raise ConfigurationError("--telemetry-json must be a non-empty file path.")
                if (
                    args.output
                    and Path(args.output).resolve() == Path(args.telemetry_json).resolve()
                ):
                    raise ConfigurationError(
                        "--output and --telemetry-json cannot point to the same file."
                    )
                telemetry_observer = InMemoryTelemetryObserver()
            if args.filename is not None and args.document != "-":
                raise ConfigurationError(
                    "--filename is only supported when reading from stdin ('-')."
                )
            schema = ExtractionSchema.from_json_file(args.schema)
            calibrator = (
                CalibrationModel.from_file(args.calibrator) if args.calibrator is not None else None
            )
            strategy, limits = _build_strategy_and_limits(
                args,
                api_key_val=api_key_val,
                api_key_env=resolved_api_key_env,
                model=resolved_model,
                base_url=resolved_base_url,
                strategy_override=strategy_override,
            )
            doc_target: str | DocumentInput
            if args.document == "-":
                in_stream = stdin_buffer if stdin_buffer is not None else sys.stdin.buffer
                doc_target = DocumentInput.from_stream(
                    in_stream,
                    filename=args.filename,
                    document_id=args.document_id,
                )
            else:
                doc_target = args.document
            extract_kwargs: dict[str, object] = {
                "strategy": strategy,
                "limits": limits,
                "document_id": args.document_id,
                "calibrator": calibrator,
            }
            if telemetry_observer is not None:
                extract_kwargs["observers"] = (telemetry_observer,)
            result = extract(
                doc_target,
                schema,
                **extract_kwargs,  # type: ignore[arg-type]
            )
        except StructaMaestroError as exc:
            err.write(f"Error: {sanitize_message(str(exc), secrets=secrets_to_scrub)}\n")
            return 1
        except Exception as exc:
            err.write(
                f"Fatal error: {sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)}\n"
            )
            return 1

        indent = 2 if args.pretty else None
        serialized = result.to_json(indent=indent)

        if args.output:
            try:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(serialized + "\n", encoding="utf-8")
            except OSError as exc:
                safe_err = sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)
                err.write(f"Output write error: {safe_err}\n")
                return 1
        else:
            out.write(serialized + "\n")

        if telemetry_observer is not None and args.telemetry_json:
            try:
                tel_path = Path(args.telemetry_json)
                tel_path.parent.mkdir(parents=True, exist_ok=True)
                tel_path.write_text(
                    telemetry_observer.summary().to_json(indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                safe_err = sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)
                err.write(f"Telemetry write error: {safe_err}\n")
                return 1

        if (
            result.status == ProcessingStatus.COMPLETED
            and result.validation.status == ValidationStatus.VALID
        ):
            return 0
        return 2

    # extract-batch subcommand
    batch_telemetry_observer: InMemoryTelemetryObserver | None = None
    try:
        if (
            args.output
            and args.summary_json
            and Path(args.output).resolve() == Path(args.summary_json).resolve()
        ):
            raise ConfigurationError("--output and --summary-json cannot point to the same file.")
        if args.telemetry_json is not None:
            if not args.telemetry_json.strip():
                raise ConfigurationError("--telemetry-json must be a non-empty file path.")
            tel_resolved = Path(args.telemetry_json).resolve()
            if args.output and Path(args.output).resolve() == tel_resolved:
                raise ConfigurationError(
                    "--output and --telemetry-json cannot point to the same file."
                )
            if args.summary_json and Path(args.summary_json).resolve() == tel_resolved:
                raise ConfigurationError(
                    "--summary-json and --telemetry-json cannot point to the same file."
                )
            batch_telemetry_observer = InMemoryTelemetryObserver()
        schema = ExtractionSchema.from_json_file(args.schema)
        calibrator = (
            CalibrationModel.from_file(args.calibrator) if args.calibrator is not None else None
        )
        batch_options = BatchOptions(
            max_workers=args.max_workers,
            fail_fast=args.fail_fast,
        )
        strategy, limits = _build_strategy_and_limits(
            args,
            api_key_val=api_key_val,
            api_key_env=resolved_api_key_env,
            model=resolved_model,
            base_url=resolved_base_url,
            strategy_override=strategy_override,
        )
        discovered_paths = discover_batch_documents(args.inputs, recursive=args.recursive)
        if not discovered_paths:
            raise ConfigurationError(
                "No supported input documents "
                "(.txt, .pdf, .docx, .html, .htm, .md, .markdown, .eml) were discovered."
            )
        batch_kwargs: dict[str, object] = {
            "strategy": strategy,
            "limits": limits,
            "options": batch_options,
            "calibrator": calibrator,
        }
        if batch_telemetry_observer is not None:
            batch_kwargs["observers"] = (batch_telemetry_observer,)
        batch_result = extract_batch(
            discovered_paths,
            schema,
            **batch_kwargs,  # type: ignore[arg-type]
        )
    except StructaMaestroError as exc:
        err.write(f"Error: {sanitize_message(str(exc), secrets=secrets_to_scrub)}\n")
        return 1
    except Exception as exc:
        err.write(
            f"Fatal error: {sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)}\n"
        )
        return 1

    jsonl_payload = batch_result.to_jsonl()
    if args.output:
        try:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(jsonl_payload, encoding="utf-8")
        except OSError as exc:
            safe_err = sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)
            err.write(f"Output write error: {safe_err}\n")
            return 1
    else:
        out.write(jsonl_payload)

    if args.summary_json:
        try:
            sum_path = Path(args.summary_json)
            sum_path.parent.mkdir(parents=True, exist_ok=True)
            sum_path.write_text(batch_result.summary.to_json(indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            safe_err = sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)
            err.write(f"Summary write error: {safe_err}\n")
            return 1

    if batch_telemetry_observer is not None and args.telemetry_json:
        try:
            tel_path = Path(args.telemetry_json)
            tel_path.parent.mkdir(parents=True, exist_ok=True)
            tel_path.write_text(
                batch_telemetry_observer.summary().to_json(indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            safe_err = sanitize_message(type(exc).__name__, secrets=secrets_to_scrub)
            err.write(f"Telemetry write error: {safe_err}\n")
            return 1

    summary = batch_result.summary
    if (
        not summary.aborted_early
        and summary.completed_count == summary.total_documents
        and summary.valid_count == summary.total_documents
    ):
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> None:
    """Console script entry point for `structa-maestro`."""
    raise SystemExit(run_cli(argv))
