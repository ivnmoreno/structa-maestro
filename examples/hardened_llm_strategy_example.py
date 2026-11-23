"""Offline runnable example demonstrating hardened OpenAICompatibleLLMStrategy policies."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from structa_maestro import (
    ExtractionSchema,
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    OpenAICompatibleLLMStrategy,
    RetryPolicy,
    StructuredOutputMode,
    extract,
)


def main() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "invoice-hardened-demo",
            "schema_version": "1.0",
            "cardinality": "single",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "total_amount": {"type": "decimal", "required": True, "min_value": 0},
                "currency": {
                    "type": "string",
                    "required": True,
                    "allowed_values": ["USD", "EUR", "GBP"],
                },
            },
        }
    )

    # Simulate an endpoint that:
    #   1) Rejects strict json_schema (triggers automatic fallback to json_object)
    #   2) Returns transient HTTP 503 (triggers bounded retry)
    #   3) Succeeds with a valid canonical extraction payload
    attempt = 0

    def _simulated_endpoint(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        body = json.loads(request.content.decode("utf-8"))
        if attempt == 1 and body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="json_schema response_format is not supported")
        if attempt == 2:
            return httpx.Response(503, text="temporary backend unavailable")
        payload = {
            "records": [
                {
                    "fields": {
                        "invoice_number": {
                            "presence": "present",
                            "value": "INV-2026-042",
                            "confidence": 0.97,
                            "ambiguous": False,
                            "evidence": [
                                {"quote": "Invoice Number: INV-2026-042", "relationship": "direct"}
                            ],
                        },
                        "total_amount": {
                            "presence": "present",
                            "value": "499.00",
                            "confidence": 0.96,
                            "ambiguous": False,
                            "evidence": [
                                {"quote": "Total Amount: 499.00", "relationship": "direct"}
                            ],
                        },
                        "currency": {
                            "presence": "present",
                            "value": "EUR",
                            "confidence": 0.99,
                            "ambiguous": False,
                            "evidence": [{"quote": "Currency: EUR", "relationship": "direct"}],
                        },
                    }
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]
            },
        )

    transport = httpx.MockTransport(_simulated_endpoint)
    with (
        httpx.Client(transport=transport) as client,
        tempfile.TemporaryDirectory() as tmp,
    ):
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key="demo-local-key",
            base_url="https://api.example.test/v1",
            http_client=client,
            structured_output_mode=StructuredOutputMode.STRICT_WITH_FALLBACK,
            retry_policy=RetryPolicy(
                max_retries=2,
                initial_backoff_seconds=0.1,
                max_backoff_seconds=2.0,
                jitter=True,
                max_total_seconds=15.0,
            ),
            input_budget=InputBudgetPolicy(
                max_input_chars=120_000,
                overflow_policy=InputBudgetOverflowPolicy.REJECT,
            ),
            sleep_fn=lambda _seconds: None,  # Remove in production for real backoff delays
        )

        doc_path = Path(tmp) / "invoice.txt"
        doc_path.write_text(
            "Invoice Number: INV-2026-042\nTotal Amount: 499.00\nCurrency: EUR\n",
            encoding="utf-8",
        )
        result = extract(doc_path, schema, strategy=strategy, document_id="doc-hardened-001")
        print(f"Status: {result.status.value} | Validation: {result.validation.status.value}")
        print("Warnings:", [w.code for w in result.warnings])
        print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
