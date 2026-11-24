"""Thin CLI delegating directly to structa_maestro.extract and extract_batch."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, TextIO

from structa_maestro.batch import BatchOptions, discover_batch_documents
from structa_maestro.config import DocumentLimits, sanitize_message
from structa_maestro.contracts.enums import ProcessingStatus, ValidationStatus
from structa_maestro.documents.inputs import DocumentInput
from structa_maestro.engine import extract, extract_batch
from structa_maestro.exceptions import ConfigurationError, StructaMaestroError
from structa_maestro.schema.models import ExtractionSchema
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


def _add_common_extraction_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--schema", required=True, help="Path to the JSON extraction schema file.")
    parser.add_argument(
        "--model", default=_DEFAULT_MODEL, help="Model identifier for LLM strategy."
    )
    parser.add_argument(
        "--base-url",
        default=_DEFAULT_BASE_URL,
        help="OpenAI-compatible endpoint base URL.",
    )
    parser.add_argument(
        "--api-key-env",
        default=_DEFAULT_API_KEY_ENV,
        help=f"Environment variable holding the API key (default: {_DEFAULT_API_KEY_ENV}).",
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="structa-maestro",
        description=(
            "Extract validated, provenance-backed canonical structured records from "
            "TXT, text-based PDF, or DOCX documents."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ext = subparsers.add_parser(
        "extract",
        help="Extract structured records from one document (or '-' for stdin) using a JSON schema.",
    )
    ext.add_argument(
        "document",
        help="Path to the input document (TXT, PDF, or DOCX), or '-' to read from binary stdin.",
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
    return parser


def _build_strategy_and_limits(
    args: argparse.Namespace,
    *,
    api_key_val: str,
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
            f"Configuration error: environment variable '{args.api_key_env}' is not set."
        )
    strategy = OpenAICompatibleLLMStrategy(
        model=args.model,
        api_key=api_key_val,
        base_url=args.base_url,
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

    if args.command not in ("extract", "extract-batch"):
        err.write(f"Unsupported command: {args.command}\n")
        return 1

    api_key_val = os.environ.get(args.api_key_env, "")
    secrets_to_scrub = (api_key_val,) if api_key_val else ()

    if args.command == "extract":
        try:
            if args.filename is not None and args.document != "-":
                raise ConfigurationError(
                    "--filename is only supported when reading from stdin ('-')."
                )
            schema = ExtractionSchema.from_json_file(args.schema)
            strategy, limits = _build_strategy_and_limits(
                args,
                api_key_val=api_key_val,
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
            result = extract(
                doc_target,
                schema,
                strategy=strategy,
                limits=limits,
                document_id=args.document_id,
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

        if (
            result.status == ProcessingStatus.COMPLETED
            and result.validation.status == ValidationStatus.VALID
        ):
            return 0
        return 2

    # extract-batch subcommand
    try:
        if (
            args.output
            and args.summary_json
            and Path(args.output).resolve() == Path(args.summary_json).resolve()
        ):
            raise ConfigurationError("--output and --summary-json cannot point to the same file.")
        schema = ExtractionSchema.from_json_file(args.schema)
        batch_options = BatchOptions(
            max_workers=args.max_workers,
            fail_fast=args.fail_fast,
        )
        strategy, limits = _build_strategy_and_limits(
            args,
            api_key_val=api_key_val,
            strategy_override=strategy_override,
        )
        discovered_paths = discover_batch_documents(args.inputs, recursive=args.recursive)
        if not discovered_paths:
            raise ConfigurationError(
                "No supported input documents (.txt, .pdf, .docx) were discovered."
            )
        batch_result = extract_batch(
            discovered_paths,
            schema,
            strategy=strategy,
            limits=limits,
            options=batch_options,
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
