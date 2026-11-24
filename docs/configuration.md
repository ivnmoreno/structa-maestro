# Configuration & Security Guidance — structa-maestro

## Document Safety Limits (`DocumentLimits`)

All limits are enforced prior to and during document parsing to guard against memory/CPU exhaustion and archive bombs:

| Option | Default | CLI Flag | Behavior When Exceeded |
| --- | --- | --- | --- |
| `max_file_bytes` | `26_214_400` (25 MB) | `--max-file-mb` / `--max-bytes` | Returns canonical `failed` result (`resource_limit_exceeded`); streams cap reads at `max_file_bytes + 1` |
| `max_decompressed_bytes` | `52_428_800` (50 MB) | `--max-decompressed-mb` | Rejects oversized extracted text or DOCX ZIP archives before inflation |
| `max_pages` | `200` | `--max-pages` | Rejects oversized PDFs prior to page iteration |

Passing non-positive values raises `ConfigurationError`.

## External Model Provider Configuration

When using `OpenAICompatibleLLMStrategy`:

- **Endpoint**: Configure `base_url` to any OpenAI-compatible Chat Completions endpoint (e.g., `https://api.openai.com/v1`, Azure OpenAI proxy, or local Ollama/vLLM server).
- **Credentials**: Supply credentials through environment variables (`STRUCTA_MAESTRO_API_KEY` by default in CLI) or `SecretValue`. The CLI intentionally does not accept raw API keys as flags to prevent leakage into shell history or process listings.
- **Data Transmission Notice**: Prepared text extracted from the submitted document is transmitted to the configured endpoint. Review your provider's retention and compliance terms before sending personal, financial, or confidential records.
- **Structured Output Modes (`StructuredOutputMode`)**:
  - `strict_with_fallback` *(default)*: Compiles `ExtractionSchema` into a strict closed `json_schema` response format (`additionalProperties: false`) and automatically falls back once to `json_object` with warning `structured_output_fallback` if the schema sets `allow_additional_fields=True` or the endpoint rejects strict schema mode.
  - `strict`: Enforces strict `json_schema` mode without fallback (fails explicitly with `strict_schema_incompatible` on open schemas or on endpoint rejection).
  - `json_object`: Uses basic JSON-object response mode directly.
- **Bounded Retries (`RetryPolicy`)**: Automatically retries transient failures (`408`, `429`, `5xx`, timeouts, connection errors, and retryable malformed/truncated model JSON by default) up to `max_retries=2` with bounded exponential backoff, jitter, RFC 7231 `Retry-After` honoring, and optional `max_total_seconds` deadline capping. Permanent errors (`400`/`422` non-fallback, `401`, `403`, `404`, policy refusals) fail immediately with zero retries.
- **Document Input Budget (`InputBudgetPolicy`)**: Enforces `max_input_chars=120_000` by default (overrideable to any positive integer or `None` to disable) with `overflow_policy="reject"` (fails pre-network with `input_budget_exceeded`) or `overflow_policy="truncate"` (truncates on a clean line boundary, forces `partial` status, and emits `input_truncated`).
- **Prompt Injection Defense**: Every call wraps prepared text in a fresh cryptographic nonce tag (`<untrusted_document nonce="...">`) separated from system instructions, neutralizes embedded `<untrusted_document`/`</untrusted_document` tags inside prompt text without mutating `PreparedDocument` for provenance verification, and independently rejects schema-violating or extra injected fields during deterministic validation.

### LLM Reliability & Input Budget Defaults

| Control | Default | CLI Flag | Behavior |
| --- | --- | --- | --- |
| `structured_output_mode` | `strict_with_fallback` | `--structured-output-mode` | Sends strict `json_schema`; falls back once to `json_object` if unsupported |
| `RetryPolicy.max_retries` | `2` | `--max-retries` | Bounded retries on `408`/`429`/`5xx`, timeouts, transport errors, and malformed JSON |
| `RetryPolicy.initial_backoff_seconds` | `0.5` | *(Python API)* | Base exponential backoff delay (seconds) |
| `RetryPolicy.max_backoff_seconds` | `8.0` | *(Python API)* | Upper backoff cap (also bounds parsed `Retry-After` delays) |
| `RetryPolicy.jitter` | `True` | *(Python API)* | Equal jitter in `[0.5 * base, base]` to prevent retry storms |
| `RetryPolicy.retry_on_malformed_output` | `True` | *(Python API)* | Retries empty, unparseable, or truncated model JSON within `max_retries` |
| `RetryPolicy.max_total_seconds` | `None` | *(Python API)* | Optional overall deadline across wait + request attempts |
| `InputBudgetPolicy.max_input_chars` | `120_000` | `--max-input-chars` | Maximum prepared document characters (`None` / `"none"` disables) |
| `InputBudgetPolicy.overflow_policy` | `reject` | `--overflow-policy` | `reject` fails pre-network (`input_budget_exceeded`); `truncate` marks `partial` (`input_truncated`) |

### Python Configuration Example

```python
import os
from structa_maestro import (
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    OpenAICompatibleLLMStrategy,
    RetryPolicy,
    StructuredOutputMode,
)

strategy = OpenAICompatibleLLMStrategy(
    model="gpt-4o-mini",
    api_key=os.environ["STRUCTA_MAESTRO_API_KEY"],
    structured_output_mode=StructuredOutputMode.STRICT_WITH_FALLBACK,
    retry_policy=RetryPolicy(
        max_retries=2,
        initial_backoff_seconds=0.5,
        max_backoff_seconds=8.0,
        jitter=True,
        retry_on_malformed_output=True,
        max_total_seconds=30.0,
    ),
    input_budget=InputBudgetPolicy(
        max_input_chars=120_000,
        overflow_policy=InputBudgetOverflowPolicy.REJECT,
    ),
)
```

### Async HTTP Client Lifecycle & FastAPI Lifespan Guidance

When using `AsyncOpenAICompatibleLLMStrategy` in async web services (such as FastAPI or Starlette):

- **Shared Lifespan Client (Recommended for Services)**: Create one `httpx.AsyncClient(timeout=60.0)` during application startup/lifespan, pass it via `http_client=client`, and close it on application shutdown. `AsyncOpenAICompatibleLLMStrategy` will never close a caller-injected client.
- **Scoped Pooled Context (Recommended for Scripts & Batch Jobs)**: Enter `async with AsyncOpenAICompatibleLLMStrategy(...) as strategy:` to pool a single strategy-owned `httpx.AsyncClient` across concurrent `aextract_batch` calls (nested `async with` is reference-counted and `await strategy.aclose()` is idempotent).
- **Standalone One-Off Calls**: Calling `await aextract(...)` outside `async with` without an injected `http_client` safely creates and closes a per-call `httpx.AsyncClient` so sockets never leak.
- **Task Cancellation**: `asyncio.CancelledError` is never swallowed by `aextract`, `aextract_batch`, or `AsyncOpenAICompatibleLLMStrategy`; cancelling an `aextract_batch` task cancels and awaits all active child tasks cleanly before propagating `CancelledError`.

## Logging & Data Minimization

`structa_maestro` attaches a `NullHandler` by default and never logs raw document contents or API keys. Operational error messages scrub credential patterns and omit local host directory paths from `SourceMetadata.filename`.

- **In-Memory Inputs**: `DocumentInput` snapshots mutable byte buffers, redacts raw payloads and host paths from `__repr__`, sanitizes POSIX/Windows filenames to basename-only, and writes zero temporary files to disk.
- **Pydantic Schema Derivation & Strict Binding**: `ExtractionSchema.from_pydantic(...)` inspects Pydantic v2 field metadata directly with zero dynamic code evaluation, and `RecordBindingError` strips Pydantic's `input_value` so raw extracted values and confidential document fields never appear in binding error messages or `issues`.
- **Observability & Telemetry Privacy Minimization**: `ExtractionEvent`, `TelemetrySummary`, `InMemoryTelemetryObserver`, and `StructuredLoggingObserver` structurally exclude raw document text, extracted field values, provenance quotes, host directory paths (filenames are sanitized to basename-only), and credentials. Only opaque IDs, numeric timings/counts, enum statuses, and diagnostic codes are emitted.

## Integrating Observability with OpenTelemetry & Prometheus

`structa-maestro` intentionally avoids third-party APM dependencies. Bridge `ExtractionObserver` / `AsyncExtractionObserver` to your metrics or tracing stack in a few lines:

```python
from structa_maestro import ExtractionEvent, ExtractionEventKind


class PrometheusTelemetryAdapter:
    """Thin adapter forwarding ExtractionEvents to Prometheus counters/histograms."""

    def __init__(self, doc_counter, stage_histogram, token_counter) -> None:
        self._doc_counter = doc_counter
        self._stage_histogram = stage_histogram
        self._token_counter = token_counter

    def on_event(self, event: ExtractionEvent) -> None:
        if event.event_kind == ExtractionEventKind.EXTRACTION_COMPLETED and event.status:
            self._doc_counter.labels(
                schema_id=event.schema_id or "unknown",
                status=event.status.value,
            ).inc()
            for timing in event.stage_timings:
                self._stage_histogram.labels(stage=timing.stage).observe(timing.elapsed_ms)
        elif (
            event.event_kind == ExtractionEventKind.STRATEGY_COMPLETED
            and event.strategy_telemetry
            and event.strategy_telemetry.token_usage
        ):
            usage = event.strategy_telemetry.token_usage
            if usage.total_tokens is not None:
                self._token_counter.labels(strategy=event.strategy_name or "unknown").inc(
                    usage.total_tokens
                )
```

For async I/O-bound telemetry sinks (e.g., flushing spans or webhooks over HTTP), implement `AsyncExtractionObserver` (`async def aon_event(self, event: ExtractionEvent) -> None`) so the event loop is never blocked. For CLI jobs, pass `--telemetry-json telemetry.json` to `structa-maestro extract` or `extract-batch` to export `TelemetrySummary` JSON without altering Contract `v1.0` output on `stdout`.
