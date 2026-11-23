# Changelog

All notable changes to `structa-maestro` and its Canonical Output Record Contract are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
