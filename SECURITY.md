# Security Policy

## Supported Versions

Security updates are provided for the latest release line and the `main` branch:

| Version | Supported |
| --- | --- |
| `0.1.x` / `main` | Yes |
| `< 0.1.0` | No |

---

## Reporting a Vulnerability

> [!IMPORTANT]
> If you discover a security vulnerability in `structa-maestro` — such as archive decompression / ZIP-bomb bypasses, HTML entity-expansion or XSS/script leakage, MIME-bomb or unsafe attachment handling, unbounded stream memory exhaustion, path-traversal or filename disclosure, prompt-injection delimiter escapes, or credential/sensitive-data leakage in diagnostics, logs, or telemetry — **please report it privately rather than opening a public GitHub issue**.

- **Private Reporting Channel**: Submit a GitHub Security Advisory at [https://github.com/ivnmoreno/structa-maestro/security/advisories/new](https://github.com/ivnmoreno/structa-maestro/security/advisories/new).
- **What to Include**:
  - Affected `structa-maestro` version and Python version
  - Clear description of the vulnerability and potential impact
  - Minimal synthetic proof-of-concept or reproduction steps (please do not attach real personal or confidential documents)
  - Any suggested mitigation or patch, if available
- **Response & Disclosure Targets**:
  - **Initial acknowledgment**: within **3 business days**
  - **Triage & reproduction update**: within **7 business days**
  - **Coordinated disclosure**: We aim to ship a fix and publish a security advisory in coordination with the reporter once verified.

---

## Security Model & Threat Assumptions

1. **Untrusted Documents (All 6 Formats)**: All submitted documents (`TXT`, `PDF`, `DOCX`, `HTML`, `Markdown`, `EML` — whether loaded from files, raw bytes, plain text, binary streams, or CLI `stdin`) are treated strictly as untrusted data, never as privileged instructions. Format detection inspects actual content signatures (magic bytes, binary null-byte/payload checks, and ZIP structure) rather than blindly trusting extensions or media-type hints.
2. **Resource Limits & Parser DoS Defenses**:
   - Configurable `DocumentLimits` default to `25 MB` raw payload size (`max_file_bytes`), `50 MB` decompressed/extracted text (`max_decompressed_bytes`), and `200` PDF pages (`max_pages`).
   - Binary streams and CLI `stdin` are read incrementally in bounded chunks halting at `max_file_bytes + 1` bytes, and DOCX archives are inspected before decompression to prevent ZIP bombs.
   - The stdlib HTML parser strips `<script>`, `<style>`, `<noscript>`, `<template>`, and HTML comments, enforces `max_decompressed_bytes` incrementally against entity-expansion bloat, and never fetches remote `<img src>`, `<link>`, or external DTD URLs.
   - The stdlib RFC 5322 EML parser enforces a `128`-part MIME limit and `16`-level multipart nesting-depth cap (`resource_limit_exceeded`) and skips untrusted binary/file attachments safely without inflating or executing them (`attachments_skipped` count-only warning).
3. **Sensitive Data Minimization & Privacy Hygiene**:
   - Default logs (`NullHandler`), consumer-facing error messages, `DocumentInput.__repr__`, `RecordBindingError` diagnostics, and privacy-safe telemetry (`ExtractionEvent`, `TelemetrySummary`, `InMemoryTelemetryObserver`, `StructuredLoggingObserver`) exclude raw document contents, raw extracted field values (`input_value`), provenance quotes, API keys, and credentials (`SecretValue` redaction plus regex scrubbing for `Bearer`, `sk-...`/`sk-ant-...`, `api_key`, `authorization`, and `x-api-key`).
   - Caller-supplied and filesystem filenames are sanitized to basename-only (stripping POSIX and Windows directory prefixes and rejecting embedded null bytes) so local host directory paths never leak into `SourceMetadata.filename`, JSON/JSONL outputs, or telemetry events.
   - In-memory document extraction writes zero temporary files to disk, and concurrent sync/async batch executions isolate state strictly across documents.
4. **Prompt Injection Defense**: External LLM strategies (`OpenAICompatibleLLMStrategy`, `AsyncOpenAICompatibleLLMStrategy`, `AnthropicLLMStrategy`, `AsyncAnthropicLLMStrategy`) wrap prepared text in per-call cryptographic nonce tags (`<untrusted_document nonce="...">`) separated from system instructions, neutralize embedded delimiter tags in prompt messages without mutating `PreparedDocument` for provenance verification, and enforce deterministic schema validation independently of the model.
5. **External Model Providers & Consumer Responsibilities**: When using OpenAI-compatible or native Anthropic strategies, prepared document text is transmitted to the configured model endpoint. Callers must review their provider's data-retention and compliance policies before submitting confidential documents and supply credentials via environment variables (`STRUCTA_MAESTRO_API_KEY`, `ANTHROPIC_API_KEY`) or `SecretValue` rather than CLI flags.
6. **Output Injection Safety**: Extracted strings containing HTML, JavaScript, SQL, shell commands, or control/bidirectional Unicode characters are serialized as inert JSON data and never executed by `structa-maestro`. Downstream consumers remain responsible for context-appropriate escaping in their own sinks.
7. **Continuous Security & Assurance Verification**: Pull requests and pushes to `main` run automated Ruff Bandit security checks (`ruff check --select S`), dependency vulnerability auditing (`pip-audit`), secret scanning, dedicated adversarial/redaction security suites (`tests/security/`), and independent Lean 4 formal contract verification (`formal-assurance.yml`).
