# Public API & CLI Reference — structa-maestro

All stable public symbols are exported from the top-level `structa_maestro` package.

## Library Entry Points

- `ExtractionEngine(strategy: ExtractionStrategy | AsyncExtractionStrategy, *, limits=None, result_builder=None, observers=None)`
  - `.extract(document: str | Path | DocumentInput, schema: ExtractionSchema | Mapping[str, Any], *, document_id: str | None = None, observers: Sequence[ExtractionObserver] | None = None) -> ExtractionResult`
  - `.extract_batch(documents: Sequence[str | Path | BatchDocumentInput | DocumentInput], schema: ExtractionSchema | Mapping[str, Any], *, options: BatchOptions | None = None, observers: Sequence[ExtractionObserver] | None = None) -> BatchExtractionResult`
  - `async .aextract(document: str | Path | DocumentInput, schema: ExtractionSchema | Mapping[str, Any], *, document_id: str | None = None, observers: Sequence[ExtractionObserver | AsyncExtractionObserver] | None = None) -> ExtractionResult`
  - `async .aextract_batch(documents: Sequence[str | Path | BatchDocumentInput | DocumentInput], schema: ExtractionSchema | Mapping[str, Any], *, options: BatchOptions | None = None, observers: Sequence[ExtractionObserver | AsyncExtractionObserver] | None = None) -> BatchExtractionResult`
  - Calling synchronous `.extract(...)` / `.extract_batch(...)` with an async-only strategy or async-only observer raises `ConfigurationError` eagerly. `.aextract(...)` / `.aextract_batch(...)` accept both native `AsyncExtractionStrategy` and synchronous `ExtractionStrategy` (offloaded via `asyncio.to_thread`), prefer native `aextract_raw` / `aon_event` on dual-protocol implementations, and propagate `asyncio.CancelledError` cleanly (cancelling in-flight batch tasks).
- Functional helpers:
  - `extract(document, schema, *, strategy, limits=None, document_id=None, observers=None) -> ExtractionResult`
  - `extract_batch(documents, schema, *, strategy, limits=None, options=None, observers=None) -> BatchExtractionResult`
  - `async aextract(document, schema, *, strategy, limits=None, document_id=None, observers=None) -> ExtractionResult`
  - `async aextract_batch(documents, schema, *, strategy, limits=None, options=None, observers=None) -> BatchExtractionResult`
- See [examples/async_extraction_example.py](../examples/async_extraction_example.py) for an offline runnable async single + bounded-concurrency batch example.
- In-memory input descriptors: `DocumentInput` (`DocumentInputKind`: `path`, `bytes`, `text`, `stream`) constructed via `DocumentInput.from_path(path, *, filename=None, document_id=None, media_type=None)`, `DocumentInput.from_bytes(content, *, filename=None, document_id=None, media_type=None)`, `DocumentInput.from_text(text, *, filename=None, document_id=None, media_type=None)`, and `DocumentInput.from_stream(stream, *, filename=None, document_id=None, media_type=None)`. Supported `media_type` values are `text/plain`, `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `text/html`, `text/markdown`, and `message/rfc822` (`from_text` supports text-based formats `text/plain`, `text/html`, `text/markdown`, and `message/rfc822`). Filenames are sanitized to basename-only (null bytes fail eagerly); binary streams are read incrementally up to `max_file_bytes + 1` bytes without creating temporary files. See [examples/in_memory_extraction_example.py](../examples/in_memory_extraction_example.py) and [examples/multiformat_extraction_example.py](../examples/multiformat_extraction_example.py).
- Batch models: `BatchDocumentInput(path, document_id=None)`, `BatchOptions(max_workers=1, fail_fast=False)` (`1 <= max_workers <= 32`), `BatchSummary` (`to_dict()`, `to_json()`), `BatchExtractionResult` (`to_jsonl()`, `to_dict()`, `to_json()`). See [examples/batch_extraction_example.py](../examples/batch_extraction_example.py).

## Schema Definition

- `ExtractionSchema.from_dict(data)` — accepts either `structa-maestro`'s declarative schema format (`schema_id`, `schema_version`, `cardinality`, `fields`) or a JSON Schema subset (`title`, `properties`, `required`, `additionalProperties`).
- `ExtractionSchema.from_json_file(path)` — loads a JSON schema file with duplicate-key rejection.
- `ExtractionSchema.from_pydantic(model_cls, *, schema_id=None, schema_version="1.0", cardinality=ExtractionCardinality.SINGLE, rules=(), allow_additional_fields=None, max_depth=16)` — deterministically compiles a Pydantic v2 `BaseModel` subclass into an immutable `ExtractionSchema` using pure type/metadata introspection (zero arbitrary code execution).
- `ExtractionSchema.to_dict() -> dict[str, Any]` and `ExtractionSchema.to_json(*, indent: int | None = None) -> str` — export any `ExtractionSchema` (including nested structures, `Decimal`/`date` literals, and all cross-field `rules`) into a JSON-safe declarative schema that round-trips losslessly through `from_dict(...)` / `from_json_file(...)` and works directly with `structa-maestro extract --schema` and `extract-batch --schema`.
- Programmatic classes: `ExtractionSchema`, `FieldSpec`, `FieldConstraints`, `FieldType`, `ExtractionCardinality`.
- Default unexpected-field policy: `allow_additional_fields=False` (extra fields flagged as `unexpected_field` validation errors unless explicitly opted in).

### Pydantic v2 Type & Constraint Mapping (`ExtractionSchema.from_pydantic`)

| Pydantic v2 Construct | Mapped `ExtractionSchema` Representation | Notes |
| --- | --- | --- |
| `str` | `FieldType.STRING` | Maps `min_length`, `max_length`, `pattern` |
| `int` | `FieldType.INTEGER` | Maps `ge`/`le`, plus exact integer `gt=n` → `min_value=n + 1` and `lt=n` → `max_value=n - 1` |
| `Decimal` / `float` | `FieldType.DECIMAL` | Maps `ge`/`le` as exact `Decimal`; non-integer `gt`/`lt` raises `SchemaDefinitionError` |
| `bool` | `FieldType.BOOLEAN` | Strictly distinct from `int` |
| `datetime.date` | `FieldType.DATE` | `datetime.datetime` is eagerly rejected (`SchemaDefinitionError`) to preserve Contract `v1.0` |
| Homogeneous `Enum` / `StrEnum` / `IntEnum` / `Literal[...]` | Matching scalar `FieldType` + `allowed_values` | `Literal[..., None]` sets `nullable=True`; mixed-type or empty definitions fail eagerly |
| `Optional[T]` / `T \| None` | Inner `FieldSpec` with `nullable=True` | Multi-type `Union[A, B]` fails eagerly (`SchemaDefinitionError`) |
| Nested `BaseModel` | `FieldType.OBJECT` + `properties` | Maps child `ConfigDict(extra="allow")` → `allow_additional_fields=True`; cycles fail eagerly |
| `list[T]` | `FieldType.ARRAY` + `items` | Maps `min_length`/`max_length` → `min_items`/`max_items` |
| `Field(default=...)` / `default_factory` | `required=False` | Fields without defaults map to `required=True` |
| Unsupported (`Any`, `dict`, `set`, `tuple`, custom classes, `multiple_of`, differing `alias`/`validation_alias`, empty model, depth > `max_depth`) | Eager `SchemaDefinitionError` | Never silently drops types or constraints |

### Strict Opt-In Typed Record Binding (`bind_record`, `bind_records`)

- `bind_record(record: ExtractedRecord, model_cls: type[T]) -> T`: Materializes one `completed` + `valid` canonical record into a typed Pydantic v2 model instance using `model_validate(..., strict=True)`.
- `bind_records(result: ExtractionResult, model_cls: type[T], *, only_valid: bool = False) -> list[T]`:
  - `only_valid=False` *(default)*: Requires `result.status == COMPLETED` and `result.validation.status == VALID`; raises `RecordBindingError` on `failed`, `partial`, or `invalid` results.
  - `only_valid=True`: Binds only records with `status == COMPLETED` and `validation.status == VALID` in order (useful for multi-record `partial` results).
- **Non-Coercion & Epistemic Honesty**:
  - Strings (`"42"`, `"true"`, `"2026-03-15"`) are **never** coerced to numbers, booleans, or dates.
  - Exact-type `Enum` member values and finite `Decimal`↔`float` / `int`→`Decimal` numeric representations are adapted solely to satisfy the declared target Pydantic field annotation.
  - `FieldPresence.MISSING` fields are omitted (required missing fields fail binding; optional fields with defaults use the model default), whereas `FieldPresence.PRESENT` `None` values are passed explicitly as `None`.
  - Binding is strictly read-only on `ExtractionResult`: confidence, provenance, warnings, errors, and processing status remain fully intact.
  - Note on JSON-deserialized results: `ExtractionResult.from_json(...)` restores top-level `Decimal` and `date` fields automatically under Contract `v1.0`, while `Decimal`/`date` values nested inside `OBJECT`/`ARRAY` remain JSON strings after deserialization; bind directly on in-memory `ExtractionResult` objects when nested models contain `Decimal` or `date` sub-fields.
  - See [examples/pydantic_schema_example.py](../examples/pydantic_schema_example.py).
- **Declarative Cross-Field & Conditional Rules (`ExtractionSchema.rules`)**:
  - Programmatic models: `ComparisonRule` (`ComparisonOperator`), `ArithmeticRule` + `ArithmeticTerm`, `ConditionalPresenceRule` (`ConditionalPresenceKind`) + `FieldCondition` (`ConditionOperator`), `GroupPresenceRule` (`GroupPresenceKind`), `RuleSeverity` (`error` / `warning`), `ValidationRule`.
  - Declarative JSON `"rules"` array supporting `kind` values `"comparison"`, `"arithmetic"` (including nested array collection sums and optional `treat_missing_as_zero`), `"required_if"`, `"forbidden_if"`, `"mutually_exclusive"`, and `"at_least_one_of"`.
  - Error-severity violations fail record-level and top-level `ValidationOutcome` (keeping per-field `validation` strictly single-field); warning-severity violations emit validation-stage warnings at record and top-level without invalidating `ValidationOutcome`. See [examples/cross_field_validation_example.py](../examples/cross_field_validation_example.py).

## Strategies & Reliability Policies

- `ExtractionStrategy` (Protocol): implement `name` property and `extract_raw(document: PreparedDocument, schema: ExtractionSchema) -> StrategyOutput`.
- `AsyncExtractionStrategy` (Protocol): implement `name` property and `async def aextract_raw(document: PreparedDocument, schema: ExtractionSchema) -> StrategyOutput`.
- `OpenAICompatibleLLMStrategy` (sync, `httpx.Client`) and `AsyncOpenAICompatibleLLMStrategy` (async, `httpx.AsyncClient` + `asyncio.sleep`, supporting reference-counted `async with strategy:`, idempotent `await strategy.aclose()`, caller-owned injected `http_client: httpx.AsyncClient`, and safe per-call scoped fallback client):
  ```python
  AsyncOpenAICompatibleLLMStrategy(
      model=...,
      api_key=...,
      base_url="https://api.openai.com/v1",
      timeout_seconds=60.0,
      temperature=0.0,
      http_client=None,  # optional caller-owned httpx.AsyncClient (never closed by strategy)
      structured_output_mode=StructuredOutputMode.STRICT_WITH_FALLBACK,
      retry_policy=RetryPolicy(),
      input_budget=InputBudgetPolicy(),
  )
  ```
- `StructuredOutputMode`: `STRICT_WITH_FALLBACK` (default), `STRICT`, `JSON_OBJECT`.
- `RetryPolicy(max_retries=2, initial_backoff_seconds=0.5, max_backoff_seconds=8.0, jitter=True, retry_on_malformed_output=True, max_total_seconds=None)`.
- `InputBudgetPolicy(max_input_chars=120_000, overflow_policy=InputBudgetOverflowPolicy.REJECT)` (`max_input_chars=None` disables budgeting; `InputBudgetOverflowPolicy` is `REJECT` or `TRUNCATE`).
- `CompiledOutputSchema` & `compile_extraction_output_schema(schema)`: deterministic closed JSON Schema compiler used by strict structured-output mode.
- `DeterministicTestStrategy(handler_or_output, name="deterministic-test")` and `AsyncDeterministicTestStrategy(async_handler_or_output, name="async-deterministic-test")`.

## Observability & Privacy-Safe Telemetry

All telemetry models and observer hooks are strictly decoupled from Canonical Output Record Contract `v1.0` and carry zero raw document text, extracted field values, provenance excerpts, host directory paths, or credentials:

- **Observer Protocols**:
  - `ExtractionObserver` (Protocol): `on_event(self, event: ExtractionEvent) -> None`
  - `AsyncExtractionObserver` (Protocol): `async def aon_event(self, event: ExtractionEvent) -> None`
  - Engine-level and call-level observers compose deterministically (`(*engine_observers, *call_observers)`) without mutating engine state. Observer `Exception`s are caught and logged safely (scrubbed) without affecting extraction results or `fail_fast`; `asyncio.CancelledError` propagates immediately.
- **Built-In Zero-Dependency Observers**:
  - `InMemoryTelemetryObserver`: thread-safe & asyncio-safe collector exposing `.events() -> tuple[ExtractionEvent, ...]`, `.summary() -> TelemetrySummary`, and `.reset()`.
  - `StructuredLoggingObserver(logger_name="structa_maestro.telemetry", level=logging.INFO)`: emits compact privacy-safe JSON log records via stdlib `logging`.
- **Immutable Telemetry Models**:
  - `ExtractionEventKind`: `extraction_started`, `document_loaded`, `strategy_completed`, `extraction_completed`, `batch_started`, `batch_item_completed`, `batch_completed`.
  - `ExtractionEvent` (`to_dict()`, `to_json()`): unified correlated event envelope (`extraction_id`, optional `batch_id`, `document_id`, basename-only `filename`, `media_type`, schema/strategy IDs, `elapsed_ms`, statuses, counts, sanitized diagnostic `code` tuples, `stage_timing`, `stage_timings`, `strategy_telemetry`, `batch_progress`).
  - `StageTiming(stage, elapsed_ms, succeeded)` & `StageTimingSummary(stage, count, total_ms, mean_ms, p50_ms, max_ms)` (`p50_ms` uses exact `statistics.median`).
  - `TokenUsage(prompt_tokens, completion_tokens, total_tokens)` & `StrategyTelemetry(attempts, retry_count, structured_output_mode_used, fallback_used, truncated, token_usage)` (populated cumulatively across attempts by `OpenAICompatibleLLMStrategy` and `AsyncOpenAICompatibleLLMStrategy`, leaving unavailable counts as `None`).
  - `BatchProgress` & `TelemetrySummary` (`to_dict()`, `to_json()`).
- See [examples/observability_telemetry_example.py](../examples/observability_telemetry_example.py) for an offline runnable walkthrough.

## Canonical Output & Serialization

- `ExtractionResult.to_dict() -> dict[str, Any]`
- `ExtractionResult.to_json(*, indent: int | None = None) -> str`
- `ExtractionResult.from_dict(data) -> ExtractionResult`
- `ExtractionResult.from_json(raw_json) -> ExtractionResult`

## Exceptions

- `SchemaDefinitionError`: invalid, empty, duplicate-key, conflicting, cyclic, unsupported Pydantic, or overly deep schema.
- `ConfigurationError`: invalid `DocumentLimits`, `DocumentInput`, `BatchOptions`, `RetryPolicy`, `InputBudgetPolicy`, `StructuredOutputMode`, strategy URL/timeout/temperature, or missing secret.
- `ContractDeserializationError`: malformed JSON, missing canonical keys, or unsupported `contract_version`.
- `RecordBindingError`: failed/invalid record/result passed to `bind_record`/`bind_records` or strict Pydantic validation failure; carries `record_id` and privacy-safe `issues` (field locations + error types, never raw extracted values).

## Extraction Strategy Diagnostic Codes (`PipelineStage.EXTRACTION`)

| Code | Severity | Meaning |
| --- | --- | --- |
| `structured_output_fallback` | `warning` | Strategy fell back from strict `json_schema` to `json_object` (open schema or unsupported endpoint). |
| `strict_schema_incompatible` | `error` | `StructuredOutputMode.STRICT` was requested for a schema with `allow_additional_fields=True`. |
| `model_output_sanitized` | `warning` | Valid top-level JSON envelope was recovered after stripping outer Markdown fences or surrounding text. |
| `provider_retry_succeeded` | `warning` | Extraction succeeded after one or more bounded retries following transient failures. |
| `input_budget_exceeded` | `error` | Prepared document length exceeded `max_input_chars` under `overflow_policy="reject"` (zero outbound requests). |
| `input_truncated` | `warning` | Prepared document was truncated on a clean boundary under `overflow_policy="truncate"` (`status="partial"`). |
| `attachments_skipped` | `warning` | (`PipelineStage.DOCUMENT_PROCESSING`) One or more binary/file attachments in an `.eml` message were skipped safely without inflating or executing them. |

## Cross-Field Validation Diagnostic Codes (`PipelineStage.VALIDATION`)

| Code | Rule Kind | Severity |
| --- | --- | --- |
| `cross_field_comparison_failed` | `ComparisonRule` | `error` or `warning` |
| `cross_field_arithmetic_mismatch` | `ArithmeticRule` | `error` or `warning` |
| `conditional_required_missing` | `ConditionalPresenceRule` (`required_if`) | `error` or `warning` |
| `conditional_forbidden_present` | `ConditionalPresenceRule` (`forbidden_if`) | `error` or `warning` |
| `mutually_exclusive_fields` | `GroupPresenceRule` (`mutually_exclusive`) | `error` or `warning` |
| `missing_required_group` | `GroupPresenceRule` (`at_least_one_of`) | `error` or `warning` |

## Thin CLI (`structa-maestro`)

```text
structa-maestro extract <document|-> --schema <schema.json>
  [--filename <name>]        # stdin ('-') only; sanitized to basename
  [--output <result.json>]
  [--document-id <id>]
  [--model <model>]
  [--base-url <url>]
  [--api-key-env STRUCTA_MAESTRO_API_KEY]
  [--max-file-mb 25]
  [--max-bytes <int>]        # optional byte-exact override for --max-file-mb
  [--max-decompressed-mb 50]
  [--max-pages 200]
  [--structured-output-mode strict_with_fallback|strict|json_object]
  [--max-retries 2]
  [--max-input-chars 120000|none]
  [--overflow-policy reject|truncate]
  [--telemetry-json <telemetry.json>]
  [--pretty]

structa-maestro extract-batch <path_or_dir> [<path_or_dir> ...] --schema <schema.json>
  [--recursive]
  [--max-workers 1]
  [--fail-fast]
  [--output <results.jsonl>]
  [--summary-json <summary.json>]
  [--telemetry-json <telemetry.json>]
  [all common --model / --base-url / --api-key-env / limit / LLM reliability flags]
```

Exit codes: `0` (all `completed` + `valid`), `2` (any `partial`/`failed`/`invalid` canonical result emitted or `--fail-fast` stopped early), `1` (CLI usage, empty discovery, schema, configuration, or file write error). `--telemetry-json <path>` is written on both exit `0` and exit `2` (skipped on eager exit `1` errors) while keeping `stdout` / `--output` strictly Contract `v1.0`.
