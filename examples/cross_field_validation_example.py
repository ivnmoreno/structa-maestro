"""Runnable offline example demonstrating declarative cross-field & conditional validation rules."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionSchema,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract,
)


def main() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "invoice-cross-field-demo",
            "schema_version": "1.0",
            "cardinality": "single",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "issue_date": {"type": "date", "required": True},
                "due_date": {"type": "date", "required": True},
                "subtotal": {"type": "decimal", "required": True, "min_value": 0},
                "tax": {"type": "decimal", "required": True, "min_value": 0},
                "discount": {"type": "decimal", "required": False, "min_value": 0},
                "total_amount": {"type": "decimal", "required": True, "min_value": 0},
                "country": {"type": "string", "required": True},
                "vat_number": {"type": "string", "required": False},
                "line_items": {
                    "type": "array",
                    "required": True,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "required": True},
                            "amount": {"type": "decimal", "required": True},
                        },
                    },
                },
            },
            "rules": [
                {
                    "rule_id": "issue-before-due",
                    "kind": "comparison",
                    "left_field": "issue_date",
                    "operator": "lte",
                    "right_field": "due_date",
                },
                {
                    "rule_id": "invoice-balance",
                    "kind": "arithmetic",
                    "terms": [
                        {"field": "subtotal", "sign": 1},
                        {"field": "tax", "sign": 1},
                        {"field": "discount", "sign": -1, "treat_missing_as_zero": True},
                    ],
                    "target_field": "total_amount",
                    "tolerance": "0.01",
                },
                {
                    "rule_id": "line-item-reconciliation",
                    "kind": "arithmetic",
                    "terms": [{"field": "line_items", "item_field": "amount", "sign": 1}],
                    "target_field": "subtotal",
                    "severity": "warning",
                },
                {
                    "rule_id": "eu-vat-required",
                    "kind": "required_if",
                    "when": {"field": "country", "operator": "in", "values": ["DE", "FR", "ES"]},
                    "target_fields": ["vat_number"],
                },
            ],
        }
    )

    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-888",
                            evidence=(ProposedEvidence(quote="Invoice: INV-2026-888"),),
                        ),
                        "issue_date": ProposedField(value=date(2026, 3, 1)),
                        "due_date": ProposedField(value=date(2026, 3, 31)),
                        "subtotal": ProposedField(value=Decimal("200.00")),
                        "tax": ProposedField(value=Decimal("38.00")),
                        "total_amount": ProposedField(value=Decimal("238.00")),
                        "country": ProposedField(value="DE"),
                        "vat_number": ProposedField(value="DE123456789"),
                        "line_items": ProposedField(
                            value=[
                                {"description": "Consulting", "amount": Decimal("150.00")},
                                {"description": "Support", "amount": Decimal("50.00")},
                            ]
                        ),
                    }
                ),
            )
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        doc_path = Path(tmp) / "invoice.txt"
        doc_path.write_text("Invoice: INV-2026-888\nTotal: 238.00 EUR\n", encoding="utf-8")
        result = extract(doc_path, schema, strategy=strategy, document_id="doc-cf-demo")
        print(f"Status: {result.status.value} | Validation: {result.validation.status.value}")
        print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
