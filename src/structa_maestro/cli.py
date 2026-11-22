"""Thin single-document CLI delegating directly to structa_maestro.extract (FR-035)."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from structa_maestro.config import DocumentLimits, sanitize_message
from structa_maestro.contracts.enums import ProcessingStatus, ValidationStatus
from structa_maestro.engine import extract
from structa_maestro.exceptions import StructaMaestroError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import ExtractionStrategy
from structa_maestro.strategies.llm_openai_compat import OpenAICompatibleLLMStrategy

_DEFAULT_API_KEY_ENV = "STRUCTA_MAESTRO_API_KEY"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "gpt-4o-mini"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="structa-maestro",
        description=(
            "Extract validated, provenance-backed canonical structured records from a single "
            "TXT, text-based PDF, or DOCX document."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ext = subparsers.add_parser(
        "extract",
        help="Extract structured records from one document using a JSON schema file.",
    )
    ext.add_argument("document", help="Path to the input document (TXT, PDF, or DOCX).")
    ext.add_argument("--schema", required=True, help="Path to the JSON extraction schema file.")
    ext.add_argument("--output", help="Optional path to write canonical JSON output.")
    ext.add_argument("--document-id", help="Optional caller-supplied source document identifier.")
    ext.add_argument("--model", default=_DEFAULT_MODEL, help="Model identifier for LLM strategy.")
    ext.add_argument(
        "--base-url",
        default=_DEFAULT_BASE_URL,
        help="OpenAI-compatible endpoint base URL.",
    )
    ext.add_argument(
        "--api-key-env",
        default=_DEFAULT_API_KEY_ENV,
        help=f"Environment variable holding the API key (default: {_DEFAULT_API_KEY_ENV}).",
    )
    ext.add_argument(
        "--max-file-mb",
        type=int,
        default=25,
        help="Maximum raw file size in MB (default: 25).",
    )
    ext.add_argument(
        "--max-decompressed-mb",
        type=int,
        default=50,
        help="Maximum decompressed/extracted text size in MB (default: 50).",
    )
    ext.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Maximum PDF page count (default: 200).",
    )
    ext.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print canonical JSON output with 2-space indentation.",
    )
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    strategy_override: ExtractionStrategy | None = None,
) -> int:
    """Execute the thin single-document CLI and return an explicit exit code (0, 1, or 2)."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = _build_parser()

    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 1

    if args.command != "extract":
        err.write(f"Unsupported command: {args.command}\n")
        return 1

    api_key_val = os.environ.get(args.api_key_env, "")
    secrets_to_scrub = (api_key_val,) if api_key_val else ()

    try:
        schema = ExtractionSchema.from_json_file(args.schema)
        limits = DocumentLimits(
            max_file_bytes=args.max_file_mb * 1024 * 1024,
            max_decompressed_bytes=args.max_decompressed_mb * 1024 * 1024,
            max_pages=args.max_pages,
        )
        if strategy_override is not None:
            strategy: ExtractionStrategy = strategy_override
        else:
            if not api_key_val.strip():
                err.write(
                    f"Configuration error: environment variable '{args.api_key_env}' is not set.\n"
                )
                return 1
            strategy = OpenAICompatibleLLMStrategy(
                model=args.model,
                api_key=api_key_val,
                base_url=args.base_url,
            )

        result = extract(
            args.document,
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


def main(argv: Sequence[str] | None = None) -> None:
    """Console script entry point for `structa-maestro`."""
    raise SystemExit(run_cli(argv))
