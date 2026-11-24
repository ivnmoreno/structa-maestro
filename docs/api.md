# Public API & CLI Reference — structa-maestro

All stable public symbols are exported from the top-level `structa_maestro` package.

## Library Entry Points

- `ExtractionEngine(strategy, *, limits=None)`
  - `.extract(document: str | Path | DocumentInput, schema: ExtractionSchema | Mapping[str, Any], *, document_id: str | None = None) -> ExtractionResult`
  - `.extract_batch(documents: Sequence[str | Path | BatchDocumentInput | DocumentInput], schema: ExtractionSchema | Mapping[str, Any], *, options: BatchOptions | None = None) -> BatchExtractionResult`
- `extract(document, schema, *, strategy, limits=None, document_id=None) -> ExtractionResult`
- `extract_batch(documents, schema, *, strategy, limits=None, options=None) -> BatchExtractionResult`
- In-memory input descriptors: `DocumentInput` (`DocumentInputKind`: `path`, `bytes`, `text`, `stream`) constructed via `DocumentInput.from_path(path, *, filename=None, document_id=None, media_type=None)`, `DocumentInput.from_bytes(content, *, filename=None, document_id=None, media_type=None)`, `DocumentInput.from_text(text, *, filename=None, document_id=None, media_type=None)`, and `DocumentInput.from_stream(stream, *, filename=None, document_id=None, media_type=None)`. Filenames are sanitized to basename-only (null bytes fail eagerly); binary streams are read incrementally up to `max_file_bytes + 1` bytes without creating temporary files. See [examples/in_memory_extraction_example.py](../examples/in_memory_extraction_example.py).
- Batch models: `BatchDocumentInput(path, document_id=None)`, `BatchOptions(max_workers=1, fail_fast=False)` (`1 <= max_workers <= 32`), `BatchSummary` (`to_dict()`, `to_json()`), `BatchExtractionResult` (`to_jsonl()`, `to_dict()`, `to_json()`). See [examples/batch_extraction_example.py](../examples/batch_extraction_example.py).

## Schema Definition

- `ExtractionSchema.from_dict(data)` — accepts either `structa-maestro`'s declarative schema format (`schema_id`, `schema_version`, `cardinality`, `fields`) or a JSON Schema subset (`title`, `properties`, `required`, `additionalProperties`).
- `ExtractionSchema.from_json_file(path)` — loads a JSON schema file with duplicate-key rejection.
- Programmatic classes: `ExtractionSchema`, `FieldSpec`, `FieldConstraints`, `FieldType`, `ExtractionCardinality`.
- Default unexpected-field policy: `allow_additional_fields=False` (extra fields flagged as `unexpected_field` validation errors unless explicitly opted in).
- **Declarative Cross-Field & Conditional Rules (`ExtractionSchema.rules`)**:
  - Programmatic models: `ComparisonRule` (`ComparisonOperator`), `ArithmeticRule` + `ArithmeticTerm`, `ConditionalPresenceRule` (`ConditionalPresenceKind`) + `FieldCondition` (`ConditionOperator`), `GroupPresenceRule` (`GroupPresenceKind`), `RuleSeverity` (`error` / `warning`), `ValidationRule`.
  - Declarative JSON `"rules"` array supporting `kind` values `"comparison"`, `"arithmetic"` (including nested array collection sums and optional `treat_missing_as_zero`), `"required_if"`, `"forbidden_if"`, `"mutually_exclusive"`, and `"at_least_one_of"`.
  - Error-severity violations fail record-level and top-level `ValidationOutcome` (keeping per-field `validation` strictly single-field); warning-severity violations emit validation-stage warnings at record and top-level without invalidating `ValidationOutcome`. See [examples/cross_field_validation_example.py](../examples/cross_field_validation_example.py).

## Strategies & Reliability Policies

- `ExtractionStrategy` (Protocol): implement `name` property and `extract_raw(document: PreparedDocument, schema: ExtractionSchema) -> StrategyOutput`.
- `OpenAICompatibleLLMStrategy`:
  ```python
  OpenAICompatibleLLMStrategy(
      model=...,
      api_key=...,
      base_url="https://api.openai.com/v1",
      timeout_seconds=60.0,
      temperature=0.0,
      http_client=None,
      structured_output_mode=StructuredOutputMode.STRICT_WITH_FALLBACK,
      retry_policy=RetryPolicy(),
      input_budget=InputBudgetPolicy(),
  )
  ```
- `StructuredOutputMode`: `STRICT_WITH_FALLBACK` (default), `STRICT`, `JSON_OBJECT`.
- `RetryPolicy(max_retries=2, initial_backoff_seconds=0.5, max_backoff_seconds=8.0, jitter=True, retry_on_malformed_output=True, max_total_seconds=None)`.
- `InputBudgetPolicy(max_input_chars=120_000, overflow_policy=InputBudgetOverflowPolicy.REJECT)` (`max_input_chars=None` disables budgeting; `InputBudgetOverflowPolicy` is `REJECT` or `TRUNCATE`).
- `CompiledOutputSchema` & `compile_extraction_output_schema(schema)`: deterministic closed JSON Schema compiler used by strict structured-output mode.
- `DeterministicTestStrategy(handler_or_output, name="deterministic-test")`.

## Canonical Output & Serialization

- `ExtractionResult.to_dict() -> dict[str, Any]`
- `ExtractionResult.to_json(*, indent: int | None = None) -> str`
- `ExtractionResult.from_dict(data) -> ExtractionResult`
- `ExtractionResult.from_json(raw_json) -> ExtractionResult`

## Exceptions

- `SchemaDefinitionError`: invalid, empty, duplicate-key, conflicting, or overly deep schema.
- `ConfigurationError`: invalid `DocumentLimits`, `RetryPolicy`, `InputBudgetPolicy`, `StructuredOutputMode`, strategy URL/timeout/temperature, or missing secret.
- `ContractDeserializationError`: malformed JSON, missing canonical keys, or unsupported `contract_version`.

## Extraction Strategy Diagnostic Codes (`PipelineStage.EXTRACTION`)

| Code | Severity | Meaning |
| --- | --- | --- |
| `structured_output_fallback` | `warning` | Strategy fell back from strict `json_schema` to `json_object` (open schema or unsupported endpoint). |
| `strict_schema_incompatible` | `error` | `StructuredOutputMode.STRICT` was requested for a schema with `allow_additional_fields=True`. |
| `model_output_sanitized` | `warning` | Valid top-level JSON envelope was recovered after stripping outer Markdown fences or surrounding text. |
| `provider_retry_succeeded` | `warning` | Extraction succeeded after one or more bounded retries following transient failures. |
| `input_budget_exceeded` | `error` | Prepared document length exceeded `max_input_chars` under `overflow_policy="reject"` (zero outbound requests). |
| `input_truncated` | `warning` | Prepared document was truncated on a clean boundary under `overflow_policy="truncate"` (`status="partial"`). |

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
  [--pretty]

structa-maestro extract-batch <path_or_dir> [<path_or_dir> ...] --schema <schema.json>
  [--recursive]
  [--max-workers 1]
  [--fail-fast]
  [--output <results.jsonl>]
  [--summary-json <summary.json>]
  [all common --model / --base-url / --api-key-env / limit / LLM reliability flags]
```

Exit codes: `0` (all `completed` + `valid`), `2` (any `partial`/`failed`/`invalid` canonical result emitted or `--fail-fast` stopped early), `1` (CLI usage, empty discovery, schema, or configuration error).
