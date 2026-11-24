"""Self-contained offline example demonstrating native asyncio single & batch extraction."""

from __future__ import annotations

import asyncio
import io
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from structa_maestro import (
    AsyncDeterministicTestStrategy,
    BatchOptions,
    DocumentInput,
    ExtractionSchema,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    aextract,
    aextract_batch,
    bind_records,
)


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"


class Invoice(BaseModel):
    invoice_number: str = Field(pattern=r"^INV-\d{4}-\d+$")
    vendor_name: str = Field(min_length=1)
    issue_date: date
    total_amount: Decimal = Field(ge=0)
    currency: Currency = Currency.USD


async def _async_offline_handler(
    doc: PreparedDocument,
    _schema: ExtractionSchema,
) -> StrategyOutput:
    await asyncio.sleep(0.005)  # Simulate non-blocking async provider latency
    inv_num = "INV-2026-000"
    for line in doc.full_text.splitlines():
        if line.startswith("Invoice Number:"):
            inv_num = line.split(":", 1)[1].strip()
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        value=inv_num,
                        confidence=0.96,
                        evidence=(ProposedEvidence(quote=f"Invoice Number: {inv_num}"),),
                    ),
                    "vendor_name": ProposedField(value="Northwind Logistics Ltd."),
                    "issue_date": ProposedField(value=date(2026, 7, 1)),
                    "total_amount": ProposedField(value=Decimal("640.00")),
                    "currency": ProposedField(value="USD"),
                }
            ),
        )
    )


async def _run_demo() -> None:
    schema = ExtractionSchema.from_pydantic(Invoice, schema_id="async-invoice-demo")
    strategy = AsyncDeterministicTestStrategy(_async_offline_handler, name="offline-async-demo")

    # 1. Single-document non-blocking extraction + strict Pydantic binding
    single = await aextract(
        DocumentInput.from_text("Invoice Number: INV-2026-501\n", filename="upload_501.txt"),
        schema,
        strategy=strategy,
    )
    bound_single = bind_records(single, Invoice)[0]
    print(
        f"Async Single: {bound_single.invoice_number} "
        f"({bound_single.total_amount} {bound_single.currency.value})"
    )

    # 2. Bounded-concurrency async batch across bytes, text, and binary stream inputs
    batch_inputs = [
        DocumentInput.from_text("Invoice Number: INV-2026-502\n", document_id="doc-502"),
        DocumentInput.from_bytes(b"Invoice Number: INV-2026-503\n", document_id="doc-503"),
        DocumentInput.from_stream(
            io.BytesIO(b"Invoice Number: INV-2026-504\n"),
            filename="s3/bucket/inv_504.txt",
            document_id="doc-504",
        ),
    ]
    batch = await aextract_batch(
        batch_inputs,
        schema,
        strategy=strategy,
        options=BatchOptions(max_workers=2),
    )
    print("Async Batch Summary:", batch.summary.to_json(indent=2))
    print("Bound Invoices:", [bind_records(r, Invoice)[0].invoice_number for r in batch.results])


def main() -> None:
    asyncio.run(_run_demo())


if __name__ == "__main__":
    main()
