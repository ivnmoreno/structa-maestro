# Security Policy

## Supported Versions

Security updates are provided for the latest release line and the `main` branch:

| Version | Supported |
| --- | --- |
| `0.1.x` / `main` | Yes |
| `< 0.1.0` | No |

---

## Reporting a Vulnerability

If you discover a security vulnerability in `structa-maestro` — such as archive decompression / ZIP-bomb bypasses, unbounded stream memory exhaustion, parser denial-of-service vectors, path-traversal or filename disclosure, prompt-injection delimiter escapes, or credential/sensitive-data leakage in diagnostics or logs — **please report it privately rather than opening a public GitHub issue**.

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

1. **Untrusted Documents**: All submitted documents (`TXT`, `PDF`, `DOCX` — whether loaded from files, raw bytes, plain text, binary streams, or CLI `stdin`) are treated strictly as untrusted data, never as privileged instructions. Format detection inspects actual content signatures (magic bytes / ZIP structure) rather than blindly trusting extensions or media-type hints.
2. **Resource Limits & DoS Protection**: Configurable `DocumentLimits` default to `25 MB` raw payload size (`max_file_bytes`), `50 MB` decompressed/extracted text (`max_decompressed_bytes`), and `200` PDF pages (`max_pages`). Binary streams and CLI `stdin` are read incrementally in bounded chunks halting at `max_file_bytes + 1` bytes, and DOCX archives are inspected before decompression to prevent ZIP bombs.
3. **Sensitive Data Minimization & Privacy Hygiene**:
   - Default logs (`NullHandler`), consumer-facing error messages, `DocumentInput.__repr__`, and `RecordBindingError` diagnostics exclude raw document contents, raw extracted field values (`input_value`), API keys, and credentials (`SecretValue` redaction).
   - Caller-supplied and filesystem filenames are sanitized to basename-only (stripping POSIX and Windows directory prefixes and rejecting embedded null bytes) so local host directory paths never leak into `SourceMetadata.filename` or JSON/JSONL outputs.
   - In-memory document extraction writes zero temporary files to disk, and concurrent batch executions isolate state strictly across documents.
4. **Prompt Injection Defense**: External LLM strategies wrap prepared text in per-call cryptographic nonce tags (`<untrusted_document nonce="...">`) separated from system instructions, neutralize embedded delimiter tags in prompt messages without mutating `PreparedDocument` for provenance verification, and enforce deterministic schema validation independently of the model.
5. **External Model Providers & Consumer Responsibilities**: When using `OpenAICompatibleLLMStrategy` (or async LLM strategies), prepared document text is transmitted to the configured model endpoint. Callers must review their provider's data-retention and compliance policies before submitting confidential documents and supply credentials via environment variables or `SecretValue` rather than CLI flags.
6. **Output Injection Safety**: Extracted strings containing HTML, JavaScript, SQL, shell commands, or control/bidirectional Unicode characters are serialized as inert JSON data and never executed by `structa-maestro`. Downstream consumers remain responsible for context-appropriate escaping in their own sinks.
7. **Continuous Security Verification**: Pull requests and pushes to `main` run automated Ruff Bandit security checks (`ruff check --select S`), dependency vulnerability auditing (`pip-audit`), secret scanning, and dedicated adversarial/redaction security test suites (`tests/security/`).
