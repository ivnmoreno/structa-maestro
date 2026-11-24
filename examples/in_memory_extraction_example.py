"""Self-contained offline example demonstrating in-memory single and mixed-batch extraction."""

from __future__ import annotations

import io
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from structa_maestro import (
    BatchOptions,
    DeterministicTestStrategy,
    DocumentInput,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract,
    extract_batch,
)
from structa_maestro.schema.models import ExtractionSchema

INVOICE_SCHEMA = {
    "schema_id": "invoice-in-memory-demo",
    "schema_version": "1.0",
    "cardinality": "single",
    "fields": {
        "invoice_number": {"type": "string", "required": True, "pattern": r"^INV-\d{4}-\d+$"},
        "vendor_name": {"type": "string", "required": True},
        "issue_date": {"type": "date", "required": True},
        "total_amount": {"type": "decimal", "required": True, "min_value": 0},
        "currency": {"type": "string", "required": True, "allowed_values": ["USD", "EUR", "GBP"]},
    },
}


def _offline_handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
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
                    "issue_date": ProposedField(value=date(2026, 5, 10)),
                    "total_amount": ProposedField(value=Decimal("480.00")),
                    "currency": ProposedField(value="USD"),
                }
            ),
        )
    )


def main() -> None:
    strategy = DeterministicTestStrategy(_offline_handler, name="offline-in-memory-demo")

    # 1. Single-document extraction directly from plain text (no temporary files on disk)
    text_input = DocumentInput.from_text(
        "Invoice Number: INV-2026-301\nVendor: Northwind Logistics Ltd.\n",
        filename="uploads/2026/email_body.txt",
        document_id="email-301",
    )
    single_res = extract(text_input, INVOICE_SCHEMA, strategy=strategy)
    print(
        f"Single In-Memory Result: status={single_res.status.value}, "
        f"validation={single_res.validation.status.value}, "
        f"filename={single_res.source.filename if single_res.source else None}"
    )

    # 2. Mixed batch combining a disk path, raw bytes, plain text, and a binary stream
    with tempfile.TemporaryDirectory() as tmp:
        disk_path = Path(tmp) / "disk_invoice.txt"
        disk_path.write_text("Invoice Number: INV-2026-302\n", encoding="utf-8")

        batch_inputs = [
            disk_path,
            DocumentInput.from_bytes(
                b"Invoice Number: INV-2026-303\n",
                filename="s3/bucket/inv_303.txt",
                document_id="bytes-303",
            ),
            DocumentInput.from_stream(
                io.BytesIO(b"Invoice Number: INV-2026-304\n"),
                filename=r"C:\inbox\inv_304.txt",
                document_id="stream-304",
            ),
        ]

        batch = extract_batch(
            batch_inputs,
            INVOICE_SCHEMA,
            strategy=strategy,
            options=BatchOptions(max_workers=2),
        )

    print("Mixed Batch Summary:", batch.summary.to_json(indent=2))
    print("JSONL Lines:", len(batch.to_jsonl().splitlines()))


if __name__ == "__main__":
    main()
