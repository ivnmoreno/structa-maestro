"""Offline runnable example: native Google Gemini generateContent sync & async strategies."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

import httpx
from pydantic import BaseModel

from structa_maestro import (
    AsyncGeminiLLMStrategy,
    DocumentInput,
    ExtractionSchema,
    GeminiLLMStrategy,
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


def _mock_gemini_response() -> dict[str, object]:
    payload = {
        "records": [
            {
                "fields": {
                    "invoice_number": {
                        "presence": "present",
                        "value": "INV-2026-775",
                        "confidence": 0.98,
                        "ambiguous": False,
                        "evidence": [
                            {
                                "quote": "Invoice Number: INV-2026-775",
                                "relationship": "direct",
                            }
                        ],
                    },
                    "vendor_name": {
                        "presence": "present",
                        "value": "Gemini Precision Labs",
                        "confidence": 0.96,
                        "ambiguous": False,
                        "evidence": [
                            {
                                "quote": "Vendor: Gemini Precision Labs",
                                "relationship": "direct",
                            }
                        ],
                    },
                    "issue_date": {
                        "presence": "present",
                        "value": "2026-05-12",
                        "confidence": 0.95,
                        "ambiguous": False,
                        "evidence": [],
                    },
                    "total_amount": {
                        "presence": "present",
                        "value": "1420.75",
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
    }
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": json.dumps(payload)}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 195,
            "candidatesTokenCount": 65,
            "totalTokenCount": 260,
        },
    }


async def main() -> None:
    schema = ExtractionSchema.from_pydantic(Invoice, schema_id="gemini-invoice-demo")
    doc = DocumentInput.from_text(
        "Invoice Number: INV-2026-775\nVendor: Gemini Precision Labs\nTotal: 1420.75 USD\n",
        filename="gemini_invoice.txt",
    )
    telemetry = InMemoryTelemetryObserver()

    # 1. Synchronous GeminiLLMStrategy over offline httpx.MockTransport
    with httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=_mock_gemini_response()))
    ) as sync_client:
        sync_strategy = GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="demo-offline-key-not-sent",
            http_client=sync_client,
        )
        sync_result = extract(doc, schema, strategy=sync_strategy, observers=[telemetry])
    sync_invoice = bind_records(sync_result, Invoice)[0]
    print(f"Sync Gemini:  {sync_invoice.invoice_number} ({sync_invoice.total_amount} USD)")

    # 2. Native asyncio AsyncGeminiLLMStrategy over offline httpx.MockTransport
    async def _async_handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mock_gemini_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(_async_handler)) as async_client:
        async_strategy = AsyncGeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="demo-offline-key-not-sent",
            http_client=async_client,
        )
        async_result = await aextract(doc, schema, strategy=async_strategy, observers=[telemetry])
    async_invoice = bind_records(async_result, Invoice)[0]
    print(f"Async Gemini: {async_invoice.invoice_number} ({async_invoice.vendor_name})")

    summary = telemetry.summary()
    print(
        f"Telemetry: extractions={summary.total_extractions}, "
        f"prompt_tokens={summary.prompt_tokens}, completion_tokens={summary.completion_tokens}, "
        f"total_tokens={summary.total_tokens}"
    )


if __name__ == "__main__":
    asyncio.run(main())
