# Public API & CLI Reference — structa-maestro

All stable public symbols are exported from the top-level `structa_maestro` package.

## Library Entry Points

- `ExtractionEngine(strategy, *, limits=None)`
  - `.extract(document: str | Path, schema: ExtractionSchema | Mapping[str, Any], *, document_id: str | None = None) -> ExtractionResult`
- `extract(document, schema, *, strategy, limits=None, document_id=None) -> ExtractionResult`

## Schema Definition

- `ExtractionSchema.from_dict(data)` — accepts either `structa-maestro`'s declarative schema format (`schema_id`, `schema_version`, `cardinality`, `fields`) or a JSON Schema subset (`title`, `properties`, `required`, `additionalProperties`).
- `ExtractionSchema.from_json_file(path)` — loads a JSON schema file with duplicate-key rejection.
- Programmatic classes: `ExtractionSchema`, `FieldSpec`, `FieldConstraints`, `FieldType`, `ExtractionCardinality`.
- Default unexpected-field policy: `allow_additional_fields=False` (extra fields flagged as `unexpected_field` validation errors unless explicitly opted in).

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
- `ConfigurationError`: invalid `DocumentLimits`, invalid strategy URL/timeout/temperature, or missing secret.
- `ContractDeserializationError`: malformed JSON, missing canonical keys, or unsupported `contract_version`.

## Thin CLI (`structa-maestro`)

```text
structa-maestro extract <document> --schema <schema.json>
  [--output <result.json>]
  [--document-id <id>]
  [--model <model>]
  [--base-url <url>]
  [--api-key-env STRUCTA_MAESTRO_API_KEY]
  [--max-file-mb 25]
  [--max-decompressed-mb 50]
  [--max-pages 200]
  [--structured-output-mode strict_with_fallback|strict|json_object]
  [--max-retries 2]
  [--max-input-chars 120000|none]
  [--overflow-policy reject|truncate]
  [--pretty]
```

Exit codes: `0` (`completed` + `valid`), `2` (`partial`/`failed`/`invalid` canonical result emitted), `1` (CLI usage, schema, or configuration error).
