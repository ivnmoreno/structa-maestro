# Architecture — structa-maestro

`structa-maestro` (`structa_maestro`) is structured as a modular, stateless, staged extraction pipeline with explicit internal contracts and only four pure-Python runtime dependencies (`pydantic`, `pypdf`, `python-docx`, `httpx`).

## Processing Lifecycle

```text
Document Path / DocumentInput (bytes, text, stream) + ExtractionSchema (dict, JSON, or Pydantic v2 BaseModel)
        │
        ▼  (Pre-flight: raises SchemaDefinitionError / ConfigurationError)
1. Schema Interpretation (`structa_maestro.schema`)
   - Declarative dict / JSON Schema subset / pure-introspection Pydantic v2 derivation (`from_pydantic`)
   - Lossless declarative export (`to_dict` / `to_json`) for CLI & versioned schema snapshots
        │
        ▼  (Operational failures isolated into canonical `failed` / `partial` results)
2. Document Input & Parsing (`structa_maestro.documents`)
   - Path/permission checks, basename-only filename sanitization, & bounded chunked stream reading
   - Configurable DocumentLimits (25MB / 50MB / 200 pages) with zero temporary files written to disk
   - Magic-byte / ZIP-container format detection (TXT, PDF, DOCX, HTML, Markdown, EML) + media_type reconciliation
   - Hardened stdlib HTML (`html.parser`), Markdown, and RFC 5322 EML (`email.parser`) parsers (zero network I/O,
     script/style stripping, MIME-bomb limits, safe attachment skipping)
   - Lossless text normalization & DocumentSegment location map
        │
        ▼
3. Extraction Strategy (`structa_maestro.strategies`)
   - Narrow `ExtractionStrategy` (sync) & `AsyncExtractionStrategy` (asyncio) protocols returning `StrategyOutput`
   - Built-in: `OpenAICompatibleLLMStrategy` & `AsyncOpenAICompatibleLLMStrategy` plus native
     `AnthropicLLMStrategy` & `AsyncAnthropicLLMStrategy` (direct httpx `Client` / `AsyncClient`,
     strict `json_schema` / forced `tool_use` compilation + single-step JSON fallback,
     bounded retries/`Retry-After`/deadlines, `InputBudgetPolicy`, nonce + delimiter-neutralized framing)
   - Built-in: `DeterministicTestStrategy` & `AsyncDeterministicTestStrategy` (offline testing & evaluation)
        │
        ▼
4. Quality Verification & Assembly (`provenance`, `confidence`, `validation`, `builder`)
   - `ProvenanceAssociator`: verifies quotes against `PreparedDocument`, maps real locations,
     rejects cross-document links, detects direct value-evidence conflicts
   - `ConfidenceNormalizer`: enforces scale bounds and `calibrated=False`
   - `DeterministicValidator`: strict non-coercing single-field, cross-field, and conditional
     validation (rejects unexpected extra fields by default; exact decimal arithmetic &
     declarative intra-record rules with configurable error/warning severity)
   - `ResultBuilder`: assembles immutable `ExtractionResult` v1.0 with honest status rules
        │
        ▼
Canonical Output Record Contract v1.0 (`structa_maestro.contracts`)
        │
        ▼  (Optional, opt-in post-extraction projection; never mutates Contract v1.0)
5. Strict Typed Record Binding (`structa_maestro.binding`)
   - `bind_record` / `bind_records(only_valid=...)` via strict non-coercing Pydantic v2 validation
   - Refuses failed/invalid results by default with privacy-safe `RecordBindingError`

Orthogonal Cross-Cutting Layer: Privacy-Safe Observability & Telemetry (`structa_maestro.telemetry`)
   - Fault-isolated `ExtractionObserver` / `AsyncExtractionObserver` hooks across stages 2–4 & batch lifecycles
   - Emits correlated, basename-sanitized, zero-PII `ExtractionEvent`s (stage timings, live `BatchProgress`,
     and cumulative `StrategyTelemetry` token/retry/fallback usage) strictly outside Contract v1.0
```

## Key Architectural Decisions

1. **Strategy-to-Contract Firewall**: Extraction strategies return internal `StrategyOutput` DTOs (with optional `StrategyTelemetry`) and never construct `ExtractionResult` directly. Provider-specific response fields (`choices`, `usage`, `id`, etc.) cannot leak into public Contract `v1.0` outputs; normalized token/attempt counts flow exclusively to opt-in telemetry observers.
2. **Non-Coercing Deterministic Validation & Opt-In Binding**: Schema validation is implemented independently of Pydantic coercion so extracted values are never silently altered (e.g., `"42"` is not silently coerced to `42`; `""`, `None`, and `FieldPresence.MISSING` remain strictly distinct). Optional downstream typed binding (`bind_record`/`bind_records`) runs strictly *after* canonical assembly using `strict=True`, leaving provenance, confidence, and validation intact on `ExtractionResult`.
3. **Failure Reporting Boundary**: Caller definition errors (`SchemaDefinitionError`, `ConfigurationError`) raise immediately before work starts; all document, parsing, extraction, provenance, and validation failures return canonical `ExtractionResult` objects with `status="failed"` or `status="partial"`; observer callback `Exception`s are isolated and scrubbed so observability never degrades extraction reliability; opt-in typed binding rejects invalid/mismatched records with privacy-safe `RecordBindingError`.
4. **Proportional Dependencies**: Direct `httpx` (`Client` and `AsyncClient`) usage avoids vendor LLM SDK lock-in; strict schema compilation, bounded retries, `Retry-After` parsing, input budgeting, delimiter neutralization, cross-field validation, stateless in-process sync (`concurrent.futures`) and async (`asyncio` + `to_thread`) batch scheduling, in-memory bounded streaming, and privacy-safe telemetry/observers (`telemetry.py`) use Python's standard library; `ExtractionSchema.from_pydantic` and strict record binding reuse the existing `pydantic` dependency with zero new packages; `pypdf` and `python-docx` avoid native binary and AGPL licensing overhead; the thin CLI (`extract` and `extract-batch`) uses standard-library `argparse`.
