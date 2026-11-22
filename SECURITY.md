# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in `structa-maestro` (such as archive decompression bypasses, parser denial-of-service vectors, prompt-injection delimiter escapes, or credential/data leakage in diagnostics), please report it privately rather than opening a public issue.

- **Contact**: Open a GitHub Security Advisory on the repository or contact the maintainers privately.
- **Response target**: Initial acknowledgment within 3 business days and a remediation plan once reproduced.

## Security Model & Threat Assumptions

1. **Untrusted Documents**: All submitted documents (`TXT`, `PDF`, `DOCX`) are treated strictly as untrusted data, never as privileged instructions.
2. **Resource Limits**: Configurable `DocumentLimits` default to `25 MB` raw file size, `50 MB` decompressed/extracted text, and `200` pages to protect against archive bombs and memory/CPU exhaustion.
3. **Sensitive Data Minimization**: Default logs, consumer-facing error messages, and exception tracebacks exclude raw document source text, API keys, and credentials (`SecretValue` redaction).
4. **External Model Providers**: When using `OpenAICompatibleLLMStrategy`, document text is transmitted to the configured model endpoint. Callers must review their provider's data-retention and privacy policies before submitting confidential documents and supply credentials via environment variables rather than command-line flags.
5. **Output Injection Safety**: Extracted strings containing HTML, JavaScript, SQL, shell commands, or control/bidirectional Unicode characters are serialized as inert JSON data and never executed by `structa-maestro`. Downstream consumers remain responsible for context-appropriate escaping in their own sinks.
