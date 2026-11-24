"""End-to-end integration tests for Pydantic schema derivation, export, CLI & binding (T010)."""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, Field

from structa_maestro import (
    ArithmeticRule,
    ArithmeticTerm,
    BatchOptions,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionCardinality,
    ExtractionResult,
    ExtractionSchema,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    RecordBindingError,
    StrategyOutput,
    ValidationStatus,
    bind_records,
    extract,
    extract_batch,
)
from structa_maestro.cli import run_cli
from structa_maestro.documents.models import PreparedDocument


class InvoiceCurrency(StrEnum):
    USD = "USD"
    EUR = "EUR"


class InvoiceLine(BaseModel):
    description: str
    amount: Decimal = Field(ge=0)


class TypedInvoice(BaseModel):
    invoice_number: str = Field(pattern=r"^INV-\d{4}-\d+$")
    vendor_name: str
    issue_date: date
    currency: InvoiceCurrency
    total_amount: Decimal = Field(ge=0)
    notes: str | None = None
    lines: list[InvoiceLine] = Field(min_length=1)


def _invoice_strategy() -> DeterministicTestStrategy:
    def _handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        inv_num = "INV-2026-001"
        for line in doc.full_text.splitlines():
            if line.startswith("Invoice Number:"):
                inv_num = line.split(":", 1)[1].strip()
        if "BAD_SUM" in doc.full_text:
            return StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            "invoice_number": ProposedField(value=inv_num),
                            "vendor_name": ProposedField(value="Acme"),
                            "issue_date": ProposedField(value=date(2026, 4, 15)),
                            "currency": ProposedField(value="USD"),
                            "total_amount": ProposedField(value=Decimal("999.00")),
                            "lines": ProposedField(
                                value=[{"description": "Widget", "amount": Decimal("100.00")}]
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
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote=f"Invoice Number: {inv_num}"),),
                        ),
                        "vendor_name": ProposedField(value="Acme Industrial"),
                        "issue_date": ProposedField(value=date(2026, 4, 15)),
                        "currency": ProposedField(value="USD"),
                        "total_amount": ProposedField(value=Decimal("150.00")),
                        "lines": ProposedField(
                            value=[
                                {"description": "Consulting", "amount": Decimal("100.00")},
                                {"description": "Hosting", "amount": Decimal("50.00")},
                            ]
                        ),
                    }
                ),
            )
        )

    return DeterministicTestStrategy(_handler, name="pydantic-e2e-sim")


def test_pydantic_schema_extraction_cross_field_batch_cli_export_and_strict_binding(
    tmp_path: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    sum_rule = ArithmeticRule(
        rule_id="lines_sum_to_total",
        terms=(ArithmeticTerm(field="lines", item_field="amount"),),
        target_field="total_amount",
        tolerance=Decimal("0.01"),
    )
    schema = ExtractionSchema.from_pydantic(
        TypedInvoice,
        schema_id="typed-invoice-v1",
        rules=(sum_rule,),
    )

    # 1. Single in-memory extraction + Contract v1.0 validation + strict binding
    res = extract(
        DocumentInput.from_text("Invoice Number: INV-2026-701\n", filename="inv_701.txt"),
        schema,
        strategy=_invoice_strategy(),
    )
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    jsonschema.validate(instance=json.loads(res.to_json()), schema=contract_json_schema)
    assert ExtractionResult.from_json(res.to_json()).to_dict() == res.to_dict()

    bound = bind_records(res, TypedInvoice)
    assert len(bound) == 1
    inv = bound[0]
    assert inv.invoice_number == "INV-2026-701"
    assert inv.currency is InvoiceCurrency.USD
    assert inv.total_amount == Decimal("150.00")
    assert len(inv.lines) == 2
    assert inv.notes is None
    # Canonical metadata remains untouched on ExtractionResult
    assert res.records[0].fields["invoice_number"].provenance[0].evidence == (
        "Invoice Number: INV-2026-701"
    )

    # 2. Cross-field failure prevents default binding
    bad_res = extract(
        DocumentInput.from_text("Invoice Number: INV-2026-702\nBAD_SUM\n"),
        schema,
        strategy=_invoice_strategy(),
    )
    assert bad_res.status == ProcessingStatus.COMPLETED
    assert bad_res.validation.status == ValidationStatus.INVALID
    try:
        bind_records(bad_res, TypedInvoice)
        raise AssertionError("Expected RecordBindingError on cross-field invalid result")
    except RecordBindingError:
        pass

    # 3. Export Pydantic-derived schema to JSON and execute thin CLI extract against it
    exported_schema_path = tmp_path / "typed_invoice_schema.json"
    exported_schema_path.write_text(schema.to_json(indent=2), encoding="utf-8")
    stdout_buf = io.StringIO()
    exit_code = run_cli(
        [
            "extract",
            "-",
            "--schema",
            str(exported_schema_path),
            "--filename",
            "cli_inv.txt",
        ],
        stdout=stdout_buf,
        stderr=io.StringIO(),
        stdin_buffer=io.BytesIO(b"Invoice Number: INV-2026-703\n"),
        strategy_override=_invoice_strategy(),
    )
    assert exit_code == 0
    cli_result = ExtractionResult.from_json(stdout_buf.getvalue())
    assert cli_result.status == ProcessingStatus.COMPLETED
    assert cli_result.validation.status == ValidationStatus.VALID
    assert cli_result.records[0].fields["invoice_number"].value == "INV-2026-703"
    assert cli_result.records[0].fields["total_amount"].value == Decimal("150.00")

    # 4. Batch extraction + binding across batch results
    batch = extract_batch(
        [
            DocumentInput.from_text("Invoice Number: INV-2026-704\n", document_id="b1"),
            DocumentInput.from_text("Invoice Number: INV-2026-705\nBAD_SUM\n", document_id="b2"),
        ],
        schema,
        strategy=_invoice_strategy(),
        options=BatchOptions(max_workers=2),
    )
    assert batch.summary.valid_count == 1
    assert batch.summary.invalid_count == 1
    assert bind_records(batch.results[0], TypedInvoice)[0].invoice_number == "INV-2026-704"
    assert bind_records(batch.results[1], TypedInvoice, only_valid=True) == []

    # 5. Multi-record schema with partial valid/invalid records
    multi_schema = ExtractionSchema.from_pydantic(
        TypedInvoice,
        schema_id="multi-typed-invoice",
        cardinality=ExtractionCardinality.MULTIPLE,
        rules=(sum_rule,),
    )
    multi_strat = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(value="INV-2026-801"),
                        "vendor_name": ProposedField(value="Valid Co"),
                        "issue_date": ProposedField(value=date(2026, 4, 15)),
                        "currency": ProposedField(value="EUR"),
                        "total_amount": ProposedField(value=Decimal("50.00")),
                        "lines": ProposedField(
                            value=[{"description": "Item", "amount": Decimal("50.00")}]
                        ),
                    }
                ),
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(value="NOT-AN-INVOICE"),
                        "vendor_name": ProposedField(value="Bad Co"),
                        "issue_date": ProposedField(value=date(2026, 4, 15)),
                        "currency": ProposedField(value="EUR"),
                        "total_amount": ProposedField(value=Decimal("50.00")),
                        "lines": ProposedField(
                            value=[{"description": "Item", "amount": Decimal("50.00")}]
                        ),
                    }
                ),
            )
        )
    )
    multi_res = extract(
        DocumentInput.from_text("multi invoice bundle\n"),
        multi_schema,
        strategy=multi_strat,
    )
    assert len(multi_res.records) == 2
    valid_bound = bind_records(multi_res, TypedInvoice, only_valid=True)
    assert len(valid_bound) == 1
    assert valid_bound[0].invoice_number == "INV-2026-801"
    assert valid_bound[0].currency is InvoiceCurrency.EUR
