"""Cross-format consistency tests across semantically equivalent TXT, PDF, and DOCX files."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from conftest import create_minimal_docx, create_minimal_pdf
from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionSchema,
    FieldPresence,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
    extract,
)
from structa_maestro.documents.models import PreparedDocument


def _regex_invoice_handler(doc: PreparedDocument, schema: ExtractionSchema) -> StrategyOutput:
    text = doc.full_text
    inv = re.search(r"Invoice Number:\s*(INV-\d{4}-\d+)", text)
    vendor = re.search(r"Vendor:\s*([^\n]+)", text)
    dt = re.search(r"Issue Date:\s*(\d{4}-\d{2}-\d{2})", text)
    amt = re.search(r"Total Amount:\s*([0-9]+\.[0-9]+)", text)
    cur = re.search(r"Currency:\s*([A-Z]{3})", text)
    assert inv and vendor and dt and amt and cur
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=inv.group(1),
                        confidence=0.95,
                        evidence=(ProposedEvidence(quote=inv.group(0)),),
                    ),
                    "vendor_name": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=vendor.group(1).strip(),
                        confidence=0.93,
                        evidence=(ProposedEvidence(quote=vendor.group(0)),),
                    ),
                    "issue_date": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=date.fromisoformat(dt.group(1)),
                        confidence=0.94,
                        evidence=(ProposedEvidence(quote=dt.group(0)),),
                    ),
                    "total_amount": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=Decimal(amt.group(1)),
                        confidence=0.96,
                        evidence=(ProposedEvidence(quote=amt.group(0)),),
                    ),
                    "currency": ProposedField(
                        presence=FieldPresence.PRESENT,
                        value=cur.group(1),
                        confidence=0.99,
                        evidence=(ProposedEvidence(quote=cur.group(0)),),
                    ),
                }
            ),
        )
    )


def test_equivalent_six_formats_produce_identical_field_values_and_provenance(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    from email.message import EmailMessage

    from structa_maestro import DocumentInput

    lines = [
        "Invoice Number: INV-2026-001",
        "Vendor: Acme Industrial Supply Ltd.",
        "Issue Date: 2026-03-15",
        "Total Amount: 1250.50",
        "Currency: USD",
    ]
    txt_path = tmp_path / "inv.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    pdf_path = create_minimal_pdf(tmp_path / "inv.pdf", ["\n".join(lines)])
    docx_path = create_minimal_docx(tmp_path / "inv.docx", paragraphs=lines)

    html_path = tmp_path / "inv.html"
    html_body = "<h1>Invoice Details</h1>\n" + "\n".join(f"<p>{ln}</p>" for ln in lines)
    html_path.write_text(f"<!DOCTYPE html><html><body>{html_body}</body></html>", encoding="utf-8")

    md_path = tmp_path / "inv.md"
    md_path.write_text("# Invoice Details\n\n" + "\n\n".join(lines) + "\n", encoding="utf-8")

    eml_msg = EmailMessage()
    eml_msg["From"] = "billing@acme.example"
    eml_msg["Subject"] = "Invoice INV-2026-001"
    eml_msg.set_content("\n".join(lines) + "\n")
    eml_path = tmp_path / "inv.eml"
    eml_path.write_bytes(eml_msg.as_bytes())

    strategy = DeterministicTestStrategy(_regex_invoice_handler)
    r_txt = extract(txt_path, invoice_schema_dict, strategy=strategy, document_id="doc-txt")
    r_pdf = extract(pdf_path, invoice_schema_dict, strategy=strategy, document_id="doc-pdf")
    r_docx = extract(docx_path, invoice_schema_dict, strategy=strategy, document_id="doc-docx")
    r_html = extract(html_path, invoice_schema_dict, strategy=strategy, document_id="doc-html")
    r_md = extract(md_path, invoice_schema_dict, strategy=strategy, document_id="doc-md")
    r_eml = extract(eml_path, invoice_schema_dict, strategy=strategy, document_id="doc-eml")
    r_html_mem = extract(
        DocumentInput.from_text(
            f"<!DOCTYPE html><html><body>{html_body}</body></html>",
            filename="mem.html",
            media_type="text/html",
        ),
        invoice_schema_dict,
        strategy=strategy,
        document_id="doc-html-mem",
    )

    all_results = (r_txt, r_pdf, r_docx, r_html, r_md, r_eml, r_html_mem)
    for res in all_results:
        assert res.status == ProcessingStatus.COMPLETED
        assert res.validation.status == ValidationStatus.VALID

    for field_name in ("invoice_number", "vendor_name", "issue_date", "total_amount", "currency"):
        expected = r_txt.records[0].fields[field_name].value
        for res in all_results[1:]:
            assert res.records[0].fields[field_name].value == expected

    # Format-specific location precision preserved honestly across all 6 formats
    assert r_txt.records[0].fields["invoice_number"].provenance[0].location is not None
    assert r_txt.records[0].fields["invoice_number"].provenance[0].location.line_start == 1
    assert r_pdf.records[0].fields["invoice_number"].provenance[0].location is not None
    assert r_pdf.records[0].fields["invoice_number"].provenance[0].location.page == 1
    assert r_docx.records[0].fields["invoice_number"].provenance[0].location is not None
    assert r_docx.records[0].fields["invoice_number"].provenance[0].location.paragraph == 1
    html_loc = r_html.records[0].fields["invoice_number"].provenance[0].location
    assert (
        html_loc is not None and html_loc.section == "Invoice Details" and html_loc.paragraph == 2
    )
    md_loc = r_md.records[0].fields["invoice_number"].provenance[0].location
    assert md_loc is not None and md_loc.section == "Invoice Details" and md_loc.line_start == 3
    eml_loc = r_eml.records[0].fields["invoice_number"].provenance[0].location
    assert (
        eml_loc is not None
        and eml_loc.section in ("Headers", "Body")
        and eml_loc.paragraph is not None
    )
