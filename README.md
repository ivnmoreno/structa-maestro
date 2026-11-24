# structa-maestro

[![CI](https://github.com/ivnmoreno/structa-maestro/actions/workflows/ci.yml/badge.svg)](https://github.com/ivnmoreno/structa-maestro/actions/workflows/ci.yml)
[![Security](https://github.com/ivnmoreno/structa-maestro/actions/workflows/security.yml/badge.svg)](https://github.com/ivnmoreno/structa-maestro/actions/workflows/security.yml)
[![Evaluation](https://github.com/ivnmoreno/structa-maestro/actions/workflows/evaluation.yml/badge.svg)](https://github.com/ivnmoreno/structa-maestro/actions/workflows/evaluation.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checked: mypy](https://img.shields.io/badge/type%20checked-mypy-blue.svg)](https://mypy-lang.org/)
[![Commitizen friendly](https://img.shields.io/badge/commitizen-friendly-brightgreen.svg)](https://commitizen-tools.github.io/commitizen/)

**Structured Data Extraction Engine** — a reusable Python library (`structa_maestro`) with a minimal thin CLI (`structa-maestro`) that transforms unstructured documents (`TXT`, text-based `PDF`, `DOCX`) into validated, schema-conforming, provenance-backed canonical records.

---

## What It Solves (and What It Does Not)

Unstructured business documents (invoices, contracts, employment records, applications, correspondence) frequently need to be converted into reliable structured data. Existing approaches often blur raw extraction output with schema compliance and factual support.

`structa-maestro` treats extraction as a structured data processing operation with **five strictly separated dimensions**:

| Dimension | Question Answered |
| --- | --- |
| **Extracted value** | What value was identified (distinct from `missing`, `null`, and `""`)? |
| **Confidence** | How reliable does the extraction strategy assess this value (uncalibrated in MVP)? |
| **Provenance** | What verified source excerpt and location in the originating document support it? |
| **Validation** | Does the value deterministically satisfy the user's schema and constraints? |
| **Processing status** | Did processing complete (`completed`), partially succeed (`partial`), or fail (`failed`)? |

### Explicit MVP Boundaries (Out of Scope)
- No OCR or scanned/image-based document recognition (image-only PDFs report `no_extractable_text`).
- No RAG pipelines, vector databases, embeddings, semantic search, or document chunk indexing.
- No web UI, database persistence, extraction history, distributed batch orchestration, or multi-provider routing.

---

## Supported Input Formats

- **TXT** (UTF-8, UTF-8 BOM, UTF-16, ASCII, Latin-1 / Windows-1252)
- **Text-based PDF** (multi-page text extraction with 1-indexed `page` locations and page-limit protection)
- **DOCX** (paragraphs, headings, and tables with pre-open ZIP-bomb protection and `paragraph`/`section` locations)

Format detection inspects actual file signatures (magic bytes / archive contents) rather than blindly trusting filename extensions.

---

## Installation

Requires **Python 3.11, 3.12, or 3.13**.

```bash
pip install structa-maestro
```

For local development with testing, linting, and security tooling:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Quick Usage (Python Library)

```python
import os
from structa_maestro import (
    ExtractionSchema,
    OpenAICompatibleLLMStrategy,
    extract,
)

schema = ExtractionSchema.from_dict(
    {
        "schema_id": "invoice-schema",
        "schema_version": "1.0",
        "cardinality": "single",
        "fields": {
            "invoice_number": {"type": "string", "required": True, "pattern": r"^INV-\d{4}-\d+$"},
            "vendor_name": {"type": "string", "required": True},
            "issue_date": {"type": "date", "required": True},
            "total_amount": {"type": "decimal", "required": True, "min_value": 0},
            "currency": {
                "type": "string",
                "required": True,
                "allowed_values": ["USD", "EUR", "GBP"],
            },
            "notes": {"type": "string", "required": False, "nullable": True},
        },
    }
)

strategy = OpenAICompatibleLLMStrategy(
    model="gpt-4o-mini",
    api_key=os.environ["STRUCTA_MAESTRO_API_KEY"],
)

result = extract("invoice.pdf", schema, strategy=strategy, document_id="doc-001")
print(result.status, result.validation.status)
print(result.to_json(indent=2))
```

In-memory inputs (`bytes`/`bytearray`/`memoryview`, plain text, and binary streams) are supported without writing temporary files via `DocumentInput.from_bytes(...)`, `DocumentInput.from_text(...)`, `DocumentInput.from_stream(...)`, and `DocumentInput.from_path(...)` across both `extract(...)` and `extract_batch(...)`; see [examples/in_memory_extraction_example.py](examples/in_memory_extraction_example.py).

### Pydantic v2 Schemas, JSON Export & Strict Typed Binding

Prefer defining your schema once as a Pydantic v2 `BaseModel` instead of a manual dictionary:

```python
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pydantic import BaseModel, Field
from structa_maestro import (
    ExtractionSchema,
    RecordBindingError,
    bind_records,
    extract,
)


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"


class Invoice(BaseModel):
    invoice_number: str = Field(pattern=r"^INV-\d{4}-\d+$")
    vendor_name: str = Field(min_length=1)
    issue_date: date
    total_amount: Decimal = Field(ge=0)
    currency: Currency = Currency.USD
    notes: str | None = None


# 1. Derive an immutable ExtractionSchema (optionally with cross-field ValidationRules)
schema = ExtractionSchema.from_pydantic(Invoice, schema_id="invoice-schema")

# 2. Optional: export to JSON for CLI `structa-maestro extract --schema invoice_schema.json`
# Path("invoice_schema.json").write_text(schema.to_json(indent=2), encoding="utf-8")

# 3. Extract canonical Contract v1.0 result, then opt into strict non-coercing binding
result = extract("invoice.pdf", schema, strategy=strategy)
try:
    invoices: list[Invoice] = bind_records(result, Invoice)
except RecordBindingError as exc:
    # Failed/invalid results are refused safely (no raw values leaked in exc)
    print(result.status, result.validation.status, exc)
```

- `bind_record(record, Model)` and `bind_records(result, Model, only_valid=False)` use strict, non-coercing Pydantic validation (`strict=True`) while keeping full provenance, confidence, warnings, and status intact on `ExtractionResult`.
- For partial multi-record extractions, pass `only_valid=True` to `bind_records(...)` to bind only `completed` + `valid` records.
- See [examples/pydantic_schema_example.py](examples/pydantic_schema_example.py) and [Public API & CLI Reference](docs/api.md) for the complete type/constraint mapping table and unsupported construct rules.

### Native Asyncio Extraction (`aextract`, `aextract_batch`, FastAPI)

Use `AsyncOpenAICompatibleLLMStrategy` (backed by `httpx.AsyncClient` and non-blocking `asyncio.sleep` retries) with `aextract(...)` and `aextract_batch(...)` in async services:

```python
import os
from structa_maestro import (
    AsyncOpenAICompatibleLLMStrategy,
    BatchOptions,
    DocumentInput,
    aextract,
    aextract_batch,
)

# Pooled client via `async with` (or pass a shared FastAPI lifespan `http_client=httpx.AsyncClient(...)`)
async with AsyncOpenAICompatibleLLMStrategy(
    model="gpt-4o-mini",
    api_key=os.environ["STRUCTA_MAESTRO_API_KEY"],
) as strategy:
    result = await aextract(
        DocumentInput.from_bytes(upload_bytes, filename="invoice.pdf"),
        schema,
        strategy=strategy,
    )
    batch = await aextract_batch(
        [doc_a, doc_b, doc_c],
        schema,
        strategy=strategy,
        options=BatchOptions(max_workers=4),
    )
```

- Blocking document parsing and synchronous strategies are automatically offloaded via `asyncio.to_thread`, and external `asyncio.CancelledError` propagates cleanly while cancelling in-flight batch tasks.
- See [examples/async_extraction_example.py](examples/async_extraction_example.py) and [Configuration & Security](docs/configuration.md) for shared `httpx.AsyncClient` / FastAPI lifespan guidance.

### Privacy-Safe Observability, Stage Timings & Token Accounting

Attach zero-dependency observers (`InMemoryTelemetryObserver`, `StructuredLoggingObserver`, or custom `ExtractionObserver` / `AsyncExtractionObserver` adapters) to any sync or async single/batch call to measure per-stage durations, live batch progress, and cumulative LLM token/retry/fallback usage—without modifying Contract `v1.0` or exposing raw text, extracted values, host paths, or secrets:

```python
from structa_maestro import (
    BatchOptions,
    InMemoryTelemetryObserver,
    aextract_batch,
)

telemetry = InMemoryTelemetryObserver()
batch = await aextract_batch(
    docs,
    schema,
    strategy=strategy,
    options=BatchOptions(max_workers=4),
    observers=[telemetry],
)
print(telemetry.summary().to_json(indent=2))
```

- Observer errors are strictly fault-isolated (never failing extractions or triggering `fail_fast`), while `asyncio.CancelledError` always propagates cleanly.
- See [examples/observability_telemetry_example.py](examples/observability_telemetry_example.py), [Public API & CLI Reference](docs/api.md), and [Configuration & Security](docs/configuration.md) for OpenTelemetry and Prometheus adapter patterns.

By default, `OpenAICompatibleLLMStrategy` enables strict schema-guided structured outputs with automatic `json_object` compatibility fallback, bounded exponential-backoff retries (`max_retries=2`, honoring `Retry-After`), a `120,000`-character input budget (`overflow_policy="reject"`), and embedded delimiter neutralization against prompt injection. See [Configuration & Security](docs/configuration.md) and [examples/hardened_llm_strategy_example.py](examples/hardened_llm_strategy_example.py) to customize these policies.

For offline deterministic tests or custom extraction logic without external API keys, see `DeterministicTestStrategy` and [examples/](examples/).

---

## Thin CLI (`extract` & `extract-batch`)

```bash
export STRUCTA_MAESTRO_API_KEY="your-api-key"

# Single document -> Canonical Output Record Contract v1.0 JSON
structa-maestro extract invoice.pdf --schema invoice_schema.json --pretty

# Pipe binary or text document via stdin ('-') without temporary files
cat invoice.pdf | structa-maestro extract - --schema invoice_schema.json --filename invoice.pdf

# Folder / multi-file batch -> Contract v1.0 JSON Lines (JSONL) + optional summary & telemetry
structa-maestro extract-batch ./invoices/ --schema invoice_schema.json \
  --recursive --max-workers 4 --output results.jsonl \
  --summary-json summary.json --telemetry-json telemetry.json
```

- Supports optional reliability controls (`--structured-output-mode`, `--max-retries`, `--max-input-chars`, `--overflow-policy`) and optional `--telemetry-json <path>` (on both `extract` and `extract-batch`) to write a privacy-safe `TelemetrySummary` JSON file while keeping stdout purely Contract `v1.0`.
- Programmatic stateless batch extraction is available via `extract_batch(...)` and `ExtractionEngine.extract_batch(...)` with `BatchOptions(max_workers=..., fail_fast=...)`; see [examples/batch_extraction_example.py](examples/batch_extraction_example.py).
- Emits the exact **Canonical Output Record Contract v1.0** JSON on `stdout` (or `--output <path>`).
- Exit codes: `0` (all `completed` + `valid`), `2` (any `partial`/`failed`/`invalid` canonical result or early `--fail-fast` stop), `1` (invalid CLI args, empty discovery, schema, or configuration error).

---

## Canonical Output Record Contract (`1.0`)

Every extraction returns an immutable `ExtractionResult` supporting lossless round-trip serialization (`ExtractionResult.from_json(result.to_json()) == result`):

- `contract_version`: `"1.0"`
- `extraction_id`: unique UUIDv4 per operation
- `status`: `"completed" | "partial" | "failed"`
- `source`: `{ "document_id", "filename", "media_type" }` (basename only — local host directories are never leaked)
- `schema`: `{ "schema_id", "schema_version" }`
- `records`: ordered list of `ExtractedRecord` (`record_id`, `status`, `fields`, `validation`, `errors`, `warnings`)
- `validation`: top-level deterministic `ValidationOutcome` (`"valid" | "invalid" | "not_validated"`, `errors`)
- `errors` / `warnings`: structured, privacy-safe `DiagnosticIssue` lists

---

## Current Limitations

- **Uncalibrated confidence**: Confidence scores currently report `"calibrated": false` and MUST NOT be interpreted as statistical probabilities.
- **Intra-record validation scope**: Declarative cross-field and conditional validation rules operate within each extracted record (including nested objects and line-item arrays); cross-document validation remains out of scope. See [Public API & CLI Reference](docs/api.md) and [examples/cross_field_validation_example.py](examples/cross_field_validation_example.py).
- **Text-only extraction**: Scanned PDFs and embedded images are not OCR-processed.
- **External model data transmission**: `OpenAICompatibleLLMStrategy` transmits prepared document text to the configured endpoint; review provider data-handling policies before processing confidential documents.

---

## Golden Evaluation & Regression Suite

Contributors can run the repository-internal 50-case golden evaluation harness offline without external API keys (excluded from published wheels and sdists):

```bash
python -m evals validate-dataset
python -m evals run --strategy deterministic --baseline evals/baselines/deterministic_v2.0.0.json
```

See [docs/development.md](docs/development.md) for subset filtering, repeated-trial (`--trials`) distributions, differential strategy comparison (`python -m evals diff`), and on-demand live-model benchmarking.

---

## Further Documentation

- [Architecture](docs/architecture.md)
- [Public API & CLI Reference](docs/api.md)
- [Configuration & Security](docs/configuration.md)
- [Development & Quality Gates](docs/development.md)
- [Testing Strategy & CI Rules](docs/TESTING.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
