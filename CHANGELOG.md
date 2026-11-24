# Changelog

All notable changes to `structa-maestro` and its Canonical Output Record Contract are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Pydantic v2 Schema Derivation, Declarative JSON Export & Strict Typed Binding (`from_pydantic`, `to_dict`/`to_json`, `bind_record`/`bind_records`)**:
  - Added `ExtractionSchema.from_pydantic(...)` to deterministically derive immutable `ExtractionSchema` instances from Pydantic v2 `BaseModel` subclasses covering all 7 Contract `v1.0` `FieldType`s, `Enum`/`StrEnum`/`IntEnum`/`Literal`, `Optional`/`| None`, nested `BaseModel`, `list[T]`, `Field(...)`/`Annotated` constraints, exact integer `gt`/`lt` bounds, `ConfigDict(extra=...)`, and declarative cross-field `ValidationRule`s while eagerly rejecting unsupported types (`datetime`, `Any`, multi-type `Union`, custom classes, non-int `gt`/`lt`, `multiple_of`, differing aliases, and cyclic models) with `SchemaDefinitionError`.
  - Added lossless `ExtractionSchema.to_dict()` and `ExtractionSchema.to_json(indent=...)` serialization (including `Decimal`/`date` `allowed_values` round-trip support in `load_schema_from_dict`) so Pydantic-derived schemas export cleanly to JSON files for `structa-maestro extract` and `extract-batch` CLI workflows.
  - Added explicit, opt-in strict non-coercing binding helpers (`bind_record`, `bind_records(only_valid=...)`) and privacy-safe `RecordBindingError` (omitting raw extracted values from messages and `issues`), preserving Canonical Output Record Contract `v1.0` and zero new dependencies, plus offline runnable example `examples/pydantic_schema_example.py` and updated user documentation.
- **In-Memory Document Inputs & CLI `stdin` Support (`DocumentInput`, `extract -`)**:
  - Added immutable `DocumentInput` and `DocumentInputKind` (`from_path`, `from_bytes`, `from_text`, `from_stream`) with mutable-buffer snapshotting, payload-redacting `__repr__`, POSIX/Windows basename-only filename sanitization, eager media-type validation, and bounded chunked stream reading (`max_file_bytes + 1` cap) with zero temporary files written to disk.
  - Extended `extract`, `ExtractionEngine.extract`, `extract_batch`, and `ExtractionEngine.extract_batch` to accept `DocumentInput` seamlessly alongside `str | Path | BatchDocumentInput`, including safe concurrent batch stream pre-materialization and full fault isolation.
  - Extended `structa-maestro extract` to accept `-` for binary `stdin` piping with stdin-only `--filename` and byte-exact `--max-bytes` override, plus offline runnable example `examples/in_memory_extraction_example.py`.
- **Stateless Batch Document Extraction (`extract_batch`, `extract-batch`)**:
  - Added stateless in-process batch orchestration (`extract_batch`, `ExtractionEngine.extract_batch`) with immutable models (`BatchDocumentInput`, `BatchOptions`, `BatchSummary`, `BatchExtractionResult`), eager pre-flight validation, exact input-order preservation, and lossless `to_jsonl()`, `to_dict()`, and `to_json()` helpers preserving Canonical Output Record Contract `v1.0` per item.
  - Added bounded in-process concurrency (`max_workers` in `1..32`, default `1`) using a bounded sliding-window worker pool with strict cross-document isolation, default continue-on-error fault isolation, and optional `fail_fast` stopping with honest `skipped_documents` / `aborted_early` accounting.
  - Added thin `structa-maestro extract-batch` CLI subcommand with safe deterministic directory discovery (`--recursive`, hidden/broken-symlink filtering, `followlinks=False`), compact Contract `v1.0` JSONL streaming, optional `--summary-json`, exit codes `0`/`2`/`1`, and runnable example `examples/batch_extraction_example.py`.
- **Cross-Field & Conditional Validation (`ExtractionSchema.rules`, `DeterministicValidator`)**:
  - Declarative, immutable intra-record validation rules (`ComparisonRule`, `ArithmeticRule`, `ArithmeticTerm`, `FieldCondition`, `ConditionalPresenceRule`, `GroupPresenceRule`) supporting field-to-field comparisons (`eq`, `ne`, `lt`, `lte`, `gt`, `gte`), exact `Decimal` arithmetic balances and nested array collection sums with optional tolerance and `treat_missing_as_zero`, conditional presence (`required_if`, `forbidden_if`), and group constraints (`mutually_exclusive`, `at_least_one_of`).
  - Eager schema-load validation rejecting unknown field paths, incompatible types, duplicate rule IDs, negative tolerances, and unknown rule attributes with `SchemaDefinitionError` (zero arbitrary code execution / `eval`).
  - Configurable `RuleSeverity` (`error` vs. `warning`) preserving Canonical Output Record Contract `v1.0` compliance, per-record multi-record isolation, and runnable example `examples/cross_field_validation_example.py`.
- **LLM Strategy Hardening & Reliability (`OpenAICompatibleLLMStrategy`)**:
  - Deterministic closed JSON Schema compilation (`CompiledOutputSchema`, `compile_extraction_output_schema`) with configurable `StructuredOutputMode` (`strict_with_fallback` default, `strict`, `json_object`) and automatic single-step non-oscillating compatibility fallback (`structured_output_fallback` warning; `strict_schema_incompatible` fail-fast on open schemas in `strict` mode).
  - Safe Markdown-fence and surrounding-prose JSON envelope recovery (`model_output_sanitized` warning) and recursive nested `Decimal`/`date` JSON-wire handling without mutating invalid values.
  - Configurable bounded `RetryPolicy` (`max_retries=2` default) with exponential backoff, jitter, RFC 7231 `Retry-After` integer/HTTP-date parsing, optional `max_total_seconds` deadline capping, retryable vs. permanent error classification, and `provider_retry_succeeded` warning emission.
  - Configurable character-based `InputBudgetPolicy` (`max_input_chars=120_000` default, or `None` to disable) and `InputBudgetOverflowPolicy` (`reject` default with pre-network `input_budget_exceeded` error, or clean-boundary `truncate` forcing `partial` status with `input_truncated` warning).
  - Embedded `<untrusted_document`/`</untrusted_document` delimiter neutralization in prompt messages while keeping `PreparedDocument` untouched for exact downstream provenance verification, plus end-to-end credential scrubbing across retry and fallback error paths.
  - Optional reliability CLI flags (`--structured-output-mode`, `--max-retries`, `--max-input-chars`, `--overflow-policy`) on both `structa-maestro extract` and `python -m evals run`, plus runnable example `examples/hardened_llm_strategy_example.py`.
- **Golden Extraction Evaluation Corpus & Regression Suite (`evals/`, repository-internal)**:
  - 50-case curated synthetic golden corpus (`dataset_version: "2.0.0"`) spanning 8 business document categories, 3 formats (`TXT`, `PDF`, `DOCX`), and 10 challenge profiles with deterministic on-disk fixture materialization and committed offline baseline (`evals/baselines/deterministic_v2.0.0.json`).
  - Multi-metric scoring with strict anti-hallucination accounting, provenance verification, repeated-trial (`mean`/`min`/`max`) distributions, zero-regression baseline gating, differential strategy comparison, and `python -m evals` CLI (`validate-dataset`, `run`, `compare`, `diff`), excluded from published wheel and sdist artifacts.

## [0.1.0] - 2026-11-22

### Added
- **Canonical Output Record Contract (`1.0`)**: Immutable, strategy-independent `ExtractionResult`, `ExtractedRecord`, `ExtractedField`, `ConfidenceAssessment`, `ProvenanceEvidence`, `SourceLocation`, `ValidationOutcome`, and `DiagnosticIssue` models with lossless JSON round-trip serialization.
- **Document Input & Parsing**: Safe magic-byte/archive format detection, configurable `DocumentLimits` (default 25 MB raw file, 50 MB decompressed text, 200 pages), and parsers for `TXT`, text-based `PDF`, and `DOCX`.
- **Schema & Deterministic Validation**: Declarative and programmatic `ExtractionSchema` supporting single- and multi-record extraction, scalar/nested/array types, constraints, nullability, and strict default rejection of unexpected extra fields.
- **Provenance & Confidence**: Verbatim/normalized source evidence verification (`ProvenanceAssociator`) and explicit uncalibrated confidence normalization (`ConfidenceNormalizer`, `calibrated=False`).
- **Strategies**: Replaceable `ExtractionStrategy` protocol, `OpenAICompatibleLLMStrategy` (via `httpx` with per-call nonce untrusted-document delimiters), and offline `DeterministicTestStrategy`.
- **Thin CLI**: Single-document `structa-maestro extract` CLI delegating directly to the library contract.
- **Verification & CI**: Comprehensive unit, contract, document-processing, integration, golden-dataset evaluation, security, resilience, property-based, and compatibility suites plus GitHub Actions workflows.
