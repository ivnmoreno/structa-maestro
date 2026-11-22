# Configuration & Security Guidance — structa-maestro

## Document Safety Limits (`DocumentLimits`)

All limits are enforced prior to and during document parsing to guard against memory/CPU exhaustion and archive bombs:

| Option | Default | CLI Flag | Behavior When Exceeded |
| --- | --- | --- | --- |
| `max_file_bytes` | `26_214_400` (25 MB) | `--max-file-mb` | Returns canonical `failed` result (`resource_limit_exceeded`) |
| `max_decompressed_bytes` | `52_428_800` (50 MB) | `--max-decompressed-mb` | Rejects oversized extracted text or DOCX ZIP archives before inflation |
| `max_pages` | `200` | `--max-pages` | Rejects oversized PDFs prior to page iteration |

Passing non-positive values raises `ConfigurationError`.

## External Model Provider Configuration

When using `OpenAICompatibleLLMStrategy`:

- **Endpoint**: Configure `base_url` to any OpenAI-compatible Chat Completions endpoint (e.g., `https://api.openai.com/v1`, Azure OpenAI proxy, or local Ollama/vLLM server).
- **Credentials**: Supply credentials through environment variables (`STRUCTA_MAESTRO_API_KEY` by default in CLI) or `SecretValue`. The CLI intentionally does not accept raw API keys as flags to prevent leakage into shell history or process listings.
- **Data Transmission Notice**: Prepared text extracted from the submitted document is transmitted to the configured endpoint. Review your provider's retention and compliance terms before sending personal, financial, or confidential records.
- **Prompt Injection Defense**: Every call wraps prepared text in a fresh cryptographic nonce tag (`<untrusted_document nonce="...">`) separated from system instructions, and downstream deterministic validation independently rejects schema-violating or extra injected fields.

## Logging & Data Minimization

`structa_maestro` attaches a `NullHandler` by default and never logs raw document contents or API keys. Operational error messages scrub credential patterns and omit local host directory paths from `SourceMetadata.filename`.
