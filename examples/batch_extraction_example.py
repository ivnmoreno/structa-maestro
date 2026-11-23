"""Offline runnable example demonstrating stateless batch extraction with fault isolation."""

from __future__ import annotations

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from structa_maestro import (
    BatchDocumentInput,
    BatchOptions,
    DeterministicTestStrategy,
    ExtractionSchema,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract_batch,
)


def main() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "invoice-batch-demo",
            "schema_version": "1.0",
            "cardinality": "single",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "issue_date": {"type": "date", "required": True},
                "total_amount": {"type": "decimal", "required": True, "min_value": 0},
            },
        }
    )

    def _extract_doc(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        inv_num = doc.full_text.split("Invoice:", 1)[1].splitlines()[0].strip()
        return StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value=inv_num,
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote=f"Invoice: {inv_num}"),),
                        ),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("250.00")),
                    }
                ),
            )
        )

    strategy = DeterministicTestStrategy(_extract_doc, name="offline-batch-demo")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        doc_a = tmp_dir / "inv_a.txt"
        doc_b = tmp_dir / "inv_b.txt"
        missing = tmp_dir / "missing.txt"
        doc_a.write_text("Invoice: INV-2026-001\n", encoding="utf-8")
        doc_b.write_text("Invoice: INV-2026-002\n", encoding="utf-8")

        batch_result = extract_batch(
            [
                BatchDocumentInput(doc_a, document_id="batch-001"),
                missing,  # Missing file is isolated into a canonical failed result
                BatchDocumentInput(doc_b, document_id="batch-002"),
            ],
            schema,
            strategy=strategy,
            options=BatchOptions(max_workers=2, fail_fast=False),
        )
        print("Batch Summary:", batch_result.summary.to_json(indent=2))
        print("JSONL Lines:", len(batch_result.to_jsonl().splitlines()))


if __name__ == "__main__":
    main()
