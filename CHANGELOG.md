# Changelog

All notable changes to `structa-maestro` and its Canonical Output Record Contract are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-22

### Added
- **Canonical Output Record Contract (`1.0`)**: Immutable, strategy-independent `ExtractionResult`, `ExtractedRecord`, `ExtractedField`, `ConfidenceAssessment`, `ProvenanceEvidence`, `SourceLocation`, `ValidationOutcome`, and `DiagnosticIssue` models with lossless JSON round-trip serialization.
- **Document Input & Parsing**: Safe magic-byte/archive format detection, configurable `DocumentLimits` (default 25 MB raw file, 50 MB decompressed text, 200 pages), and parsers for `TXT`, text-based `PDF`, and `DOCX`.
- **Schema & Deterministic Validation**: Declarative and programmatic `ExtractionSchema` supporting single- and multi-record extraction, scalar/nested/array types, constraints, nullability, and strict default rejection of unexpected extra fields.
- **Provenance & Confidence**: Verbatim/normalized source evidence verification (`ProvenanceAssociator`) and explicit uncalibrated confidence normalization (`ConfidenceNormalizer`, `calibrated=False`).
- **Strategies**: Replaceable `ExtractionStrategy` protocol, `OpenAICompatibleLLMStrategy` (via `httpx` with per-call nonce untrusted-document delimiters), and offline `DeterministicTestStrategy`.
- **Thin CLI**: Single-document `structa-maestro extract` CLI delegating directly to the library contract.
- **Verification & CI**: Comprehensive unit, contract, document-processing, integration, golden-dataset evaluation, security, resilience, property-based, and compatibility suites plus GitHub Actions workflows.
