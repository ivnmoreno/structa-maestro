"""Basic single-record invoice extraction example with provenance and validation inspection."""

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
            "schema_id": "invoice-schema",
            "schema_version": "1.0",
            "cardinality": "single",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "total_amount": {"type": "decimal", "required": True, "min_value": 0},
                "issue_date": {"type": "date", "required": True},
                "notes": {"type": "string", "required": False},
            },
        }
    )

    # Swap DeterministicTestStrategy for OpenAICompatibleLLMStrategy in production
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "total_amount": ProposedField(
                            value=Decimal("1250.50"),
                            confidence=0.94,
                            evidence=(ProposedEvidence(quote="Total Amount: 1250.50"),),
                        ),
                        "issue_date": ProposedField(
                            value=date(2026, 3, 15),
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote="Issue Date: 2026-03-15"),),
                        ),
                    }
                ),
            )
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        doc_path = Path(tmp) / "invoice.txt"
        doc_path.write_text(
            "Invoice Number: INV-2026-001\nIssue Date: 2026-03-15\nTotal Amount: 1250.50\n",
            encoding="utf-8",
        )
        result = extract(doc_path, schema, strategy=strategy, document_id="doc-001")
        print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
