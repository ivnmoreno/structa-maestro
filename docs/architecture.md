# Architecture — structa-maestro

`structa-maestro` (`structa_maestro`) is structured as a modular, stateless, staged extraction pipeline with explicit internal contracts and only four pure-Python runtime dependencies (`pydantic`, `pypdf`, `python-docx`, `httpx`).

## Processing Lifecycle

```text
Document Path + ExtractionSchema
        │
        ▼  (Pre-flight: raises SchemaDefinitionError / ConfigurationError)
1. Schema Interpretation (`structa_maestro.schema`)
        │
        ▼  (Operational failures isolated into canonical `failed` / `partial` results)
2. Document Input & Parsing (`structa_maestro.documents`)
   - Path/permission checks & configurable DocumentLimits (25MB / 50MB / 200 pages)
   - Magic-byte / ZIP-container format detection (TXT, PDF, DOCX)
   - Lossless text normalization & DocumentSegment location map
        │
        ▼
3. Extraction Strategy (`structa_maestro.strategies`)
   - Narrow `ExtractionStrategy` protocol returning provider-independent `StrategyOutput`
   - Built-in: `OpenAICompatibleLLMStrategy` (httpx + strict `json_schema` compilation with
     single-step `json_object` fallback, bounded retries/`Retry-After`/deadlines,
     `InputBudgetPolicy` enforcement, nonce + delimiter-neutralized untrusted framing)
   - Built-in: `DeterministicTestStrategy` (offline testing & evaluation)
        │
        ▼
4. Quality Verification & Assembly (`provenance`, `confidence`, `validation`, `builder`)
   - `ProvenanceAssociator`: verifies quotes against `PreparedDocument`, maps real locations,
     rejects cross-document links, detects direct value-evidence conflicts
   - `ConfidenceNormalizer`: enforces scale bounds and `calibrated=False`
   - `DeterministicValidator`: strict non-coercing structural & constraint validation
     (rejects unexpected extra fields by default)
   - `ResultBuilder`: assembles immutable `ExtractionResult` v1.0 with honest status rules
        │
        ▼
Canonical Output Record Contract v1.0 (`structa_maestro.contracts`)
```

## Key Architectural Decisions

1. **Strategy-to-Contract Firewall**: Extraction strategies return internal `StrategyOutput` DTOs and never construct `ExtractionResult` directly. Provider-specific response fields (`choices`, `usage`, `id`, etc.) cannot leak into public outputs.
2. **Non-Coercing Deterministic Validation**: Schema validation is implemented independently of Pydantic coercion so extracted values are never silently altered (e.g., `"42"` is not silently coerced to `42`; `""`, `None`, and `FieldPresence.MISSING` remain strictly distinct).
3. **Failure Reporting Boundary**: Caller definition errors (`SchemaDefinitionError`, `ConfigurationError`) raise immediately before work starts; all document, parsing, extraction, provenance, and validation failures return canonical `ExtractionResult` objects with `status="failed"` or `status="partial"`.
4. **Proportional Dependencies**: Direct `httpx` usage avoids vendor LLM SDK lock-in; strict schema compilation, bounded retries, `Retry-After` parsing, input budgeting, and delimiter neutralization are implemented purely with Python's standard library; `pypdf` and `python-docx` avoid native binary and AGPL licensing overhead; the thin CLI uses standard-library `argparse`.
