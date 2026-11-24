"""Offline runnable example: native Anthropic Messages API sync & async strategies + telemetry."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import httpx
from pydantic import BaseModel

from structa_maestro import (
    AnthropicLLMStrategy,
    AsyncAnthropicLLMStrategy,
    DocumentInput,
    ExtractionSchema,
    InMemoryTelemetryObserver,
    aextract,
    bind_records,
    extract,
)


class Invoice(BaseModel):
    invoice_number: str
    vendor_name: str
    issue_date: date
    total_amount: Decimal
    currency: str


def _mock_anthropic_response() -> dict[str, object]:
    return {
        "id": "msg_offline_demo",
        "type": "message",
        "role": "assistant",
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_offline_1",
                "name": "emit_extraction_result",
                "input": {
                    "records": [
                        {
                            "fields": {
                                "invoice_number": {
                                    "presence": "present",
                                    "value": "INV-2026-990",
                                    "confidence": 0.98,
                                    "ambiguous": False,
                                    "evidence": [
                                        {
                                            "quote": "Invoice Number: INV-2026-990",
                                            "relationship": "direct",
                                        }
                                    ],
                                },
                                "vendor_name": {
                                    "presence": "present",
                                    "value": "Claude Supply Co.",
                                    "confidence": 0.96,
                                    "ambiguous": False,
                                    "evidence": [
                                        {
                                            "quote": "Vendor: Claude Supply Co.",
                                            "relationship": "direct",
                                        }
                                    ],
                                },
                                "issue_date": {
                                    "presence": "present",
                                    "value": "2026-04-20",
                                    "confidence": 0.95,
                                    "ambiguous": False,
                                    "evidence": [],
                                },
                                "total_amount": {
                                    "presence": "present",
                                    "value": "880.00",
                                    "confidence": 0.97,
                                    "ambiguous": False,
                                    "evidence": [],
                                },
                                "currency": {
                                    "presence": "present",
                                    "value": "USD",
                                    "confidence": 0.99,
                                    "ambiguous": False,
                                    "evidence": [],
                                },
                            }
                        }
                    ]
                },
            }
        ],
        "usage": {"input_tokens": 165, "output_tokens": 55},
    }


async def main() -> None:
    schema = ExtractionSchema.from_pydantic(Invoice, schema_id="anthropic-invoice-demo")
    doc = DocumentInput.from_text(
        "Invoice Number: INV-2026-990\nVendor: Claude Supply Co.\nTotal: 880.00 USD\n",
        filename="anthropic_invoice.txt",
    )
    telemetry = InMemoryTelemetryObserver()

    # 1. Synchronous AnthropicLLMStrategy over offline httpx.MockTransport
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json=_mock_anthropic_response())
        )
    ) as sync_client:
        sync_strategy = AnthropicLLMStrategy(
            model="claude-sonnet-4-5",
            api_key="demo-offline-key-not-sent",
            http_client=sync_client,
        )
        sync_result = extract(doc, schema, strategy=sync_strategy, observers=[telemetry])
    sync_invoice = bind_records(sync_result, Invoice)[0]
    print(f"Sync Anthropic:  {sync_invoice.invoice_number} ({sync_invoice.total_amount} USD)")

    # 2. Native asyncio AsyncAnthropicLLMStrategy over offline httpx.MockTransport
    async def _async_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_anthropic_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(_async_handler)) as async_client:
        async_strategy = AsyncAnthropicLLMStrategy(
            model="claude-sonnet-4-5",
            api_key="demo-offline-key-not-sent",
            http_client=async_client,
        )
        async_result = await aextract(doc, schema, strategy=async_strategy, observers=[telemetry])
    async_invoice = bind_records(async_result, Invoice)[0]
    print(f"Async Anthropic: {async_invoice.invoice_number} ({async_invoice.vendor_name})")

    summary = telemetry.summary()
    print(
        f"Telemetry: extractions={summary.total_extractions}, "
        f"input_tokens={summary.prompt_tokens}, output_tokens={summary.completion_tokens}, "
        f"total_tokens={summary.total_tokens}"
    )


if __name__ == "__main__":
    asyncio.run(main())
