"""Offline runnable example: Pydantic v2 schema derivation, JSON export & strict typed binding."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from structa_maestro import (
    ArithmeticRule,
    ArithmeticTerm,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionSchema,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    RecordBindingError,
    StrategyOutput,
    bind_records,
    extract,
)


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


class InvoiceLine(BaseModel):
    description: str = Field(min_length=1)
    amount: Decimal = Field(ge=0)


class Invoice(BaseModel):
    invoice_number: str = Field(pattern=r"^INV-\d{4}-\d+$", description="Unique invoice number")
    vendor_name: str = Field(min_length=1)
    issue_date: date
    currency: Currency = Currency.USD
    total_amount: Decimal = Field(ge=0)
    notes: str | None = None
    lines: list[InvoiceLine] = Field(min_length=1)


def _offline_handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
    inv_num = "INV-2026-901"
    for line in doc.full_text.splitlines():
        if line.startswith("Invoice Number:"):
            inv_num = line.split(":", 1)[1].strip()
    if "MISMATCH" in doc.full_text:
        return StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(value=inv_num),
                        "vendor_name": ProposedField(value="Northwind Supply"),
                        "issue_date": ProposedField(value=date(2026, 6, 1)),
                        "currency": ProposedField(value="USD"),
                        "total_amount": ProposedField(value=Decimal("999.00")),
                        "lines": ProposedField(
                            value=[{"description": "License", "amount": Decimal("250.00")}]
                        ),
                    }
                ),
            )
        )
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        value=inv_num,
                        confidence=0.97,
                        evidence=(ProposedEvidence(quote=f"Invoice Number: {inv_num}"),),
                    ),
                    "vendor_name": ProposedField(value="Northwind Supply"),
                    "issue_date": ProposedField(value=date(2026, 6, 1)),
                    "currency": ProposedField(value="EUR"),
                    "total_amount": ProposedField(value=Decimal("250.00")),
                    "lines": ProposedField(
                        value=[
                            {"description": "Platform License", "amount": Decimal("200.00")},
                            {"description": "Support", "amount": Decimal("50.00")},
                        ]
                    ),
                }
            ),
        )
    )


def main() -> None:
    # 1. Derive ExtractionSchema directly from the Pydantic v2 model + cross-field rule
    sum_rule = ArithmeticRule(
        rule_id="line_items_sum_total",
        terms=(ArithmeticTerm(field="lines", item_field="amount"),),
        target_field="total_amount",
        tolerance=Decimal("0.01"),
    )
    schema = ExtractionSchema.from_pydantic(
        Invoice,
        schema_id="invoice-pydantic-demo",
        rules=(sum_rule,),
    )

    # 2. Export schema to declarative JSON (e.g., for CLI `structa-maestro extract --schema`)
    with tempfile.TemporaryDirectory() as tmp:
        schema_file = Path(tmp) / "invoice_schema.json"
        schema_file.write_text(schema.to_json(indent=2), encoding="utf-8")
        reloaded = ExtractionSchema.from_json_file(schema_file)
        if reloaded != schema:
            raise RuntimeError("Schema JSON round-trip mismatch")

    strategy = DeterministicTestStrategy(_offline_handler, name="pydantic-demo-strategy")

    # 3. Extract canonical result and strictly bind to typed Invoice model
    result = extract(
        DocumentInput.from_text("Invoice Number: INV-2026-901\n", filename="invoice_901.txt"),
        schema,
        strategy=strategy,
    )
    invoices = bind_records(result, Invoice)
    inv = invoices[0]
    print(
        f"Bound Invoice: number={inv.invoice_number}, currency={inv.currency.value}, "
        f"total={inv.total_amount}, lines={len(inv.lines)}"
    )
    print(
        "Provenance preserved on ExtractionResult:",
        result.records[0].fields["invoice_number"].provenance[0].evidence,
    )

    # 4. Invalid canonical results are refused safely with RecordBindingError
    bad_result = extract(
        DocumentInput.from_text("Invoice Number: INV-2026-902\nMISMATCH\n"),
        schema,
        strategy=strategy,
    )
    try:
        bind_records(bad_result, Invoice)
    except RecordBindingError as exc:
        print(f"Refused invalid result cleanly: {exc}")


if __name__ == "__main__":
    main()
