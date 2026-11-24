"""Offline runnable example: HTML, Markdown, and EML extraction with verified provenance."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from email.message import EmailMessage

from structa_maestro import (
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionSchema,
    FieldPresence,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract,
)


def _extract_invoice(document: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
    text = document.full_text
    inv = re.search(r"Invoice Number:\s*(INV-\d{4}-\d+)", text)
    vendor = re.search(r"Vendor:\s*([^\n|]+)", text)
    total = re.search(r"Total Amount:\s*([0-9]+\.[0-9]+)", text)
    assert inv and vendor and total
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=inv.group(1),
                        confidence=0.98,
                        evidence=(ProposedEvidence(quote=inv.group(0)),),
                    ),
                    "vendor_name": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=vendor.group(1).strip(),
                        confidence=0.96,
                        evidence=(ProposedEvidence(quote=vendor.group(0)),),
                    ),
                    "issue_date": ProposedField(value=date(2026, 4, 15), confidence=0.95),
                    "total_amount": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=Decimal(total.group(1)),
                        confidence=0.97,
                        evidence=(ProposedEvidence(quote=total.group(0)),),
                    ),
                }
            ),
        )
    )


def main() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "multiformat-invoice",
            "schema_version": "1.0",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "vendor_name": {"type": "string", "required": True},
                "issue_date": {"type": "date", "required": True},
                "total_amount": {"type": "decimal", "required": True},
            },
        }
    )
    strategy = DeterministicTestStrategy(_extract_invoice)

    # 1. HTML portal invoice (scripts/styles stripped automatically)
    html_doc = DocumentInput.from_text(
        "<!DOCTYPE html><html><head><script>alert(1)</script></head>"
        "<body><h1>Billing Portal</h1>"
        "<p>Invoice Number: INV-2026-801</p>"
        "<p>Vendor: Acme Robotics</p>"
        "<table><tr><td>Total Amount: 1495.00</td></tr></table>"
        "</body></html>",
        filename="portal_invoice.html",
        media_type="text/html",
    )

    # 2. Markdown report
    md_doc = DocumentInput.from_text(
        "# Billing Summary\n\n"
        "Invoice Number: INV-2026-801\n\n"
        "Vendor: Acme Robotics\n\n"
        "Total Amount: 1495.00\n",
        filename="billing_summary.md",
        media_type="text/markdown",
    )

    # 3. RFC 5322 EML message with a skipped binary attachment
    msg = EmailMessage()
    msg["From"] = "billing@acme.example"
    msg["To"] = "ap@buyer.example"
    msg["Subject"] = "Invoice INV-2026-801"
    msg.set_content("Invoice Number: INV-2026-801\nVendor: Acme Robotics\nTotal Amount: 1495.00\n")
    msg.add_attachment(
        b"\x89PNG\r\n\x1a\nfake", maintype="image", subtype="png", filename="logo.png"
    )
    eml_doc = DocumentInput.from_bytes(msg.as_bytes(), filename="vendor_notice.eml")

    for label, doc in (("HTML", html_doc), ("Markdown", md_doc), ("EML", eml_doc)):
        res = extract(doc, schema, strategy=strategy)
        prov = res.records[0].fields["invoice_number"].provenance[0]
        loc = prov.location
        warn_codes = [w.code for w in res.warnings]
        print(
            f"{label:8s} | media={res.source.media_type if res.source else None} | "
            f"invoice={res.records[0].fields['invoice_number'].value} | "
            f"section={loc.section if loc else None!r} "
            f"para={loc.paragraph if loc else None} line={loc.line_start if loc else None} | "
            f"warnings={warn_codes}"
        )


if __name__ == "__main__":
    main()
