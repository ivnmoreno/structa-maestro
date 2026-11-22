"""Provenance association and integrity tests covering PRO-001 through PRO-015."""

from __future__ import annotations

from pathlib import Path

from conftest import create_minimal_docx, create_minimal_pdf
from structa_maestro.contracts.enums import EvidenceRelationship, FieldPresence
from structa_maestro.documents.loader import load_document
from structa_maestro.provenance.associator import ProvenanceAssociator
from structa_maestro.strategies.base import ProposedEvidence


def test_pro_001_to_005_direct_multi_passage_pdf_page_docx_paragraph_and_multiline(
    tmp_path: Path,
) -> None:
    associator = ProvenanceAssociator()

    # PDF page provenance (PRO-003) & multi-passage (PRO-002)
    pdf_path = create_minimal_pdf(
        tmp_path / "p.pdf",
        ["Invoice Number: INV-2026-001", "Reference copy for INV-2026-001"],
    )
    loaded_pdf = load_document(pdf_path, document_id="pdf-1")
    assert loaded_pdf.document is not None
    res = associator.associate(
        document=loaded_pdf.document,
        proposed_evidence=(
            ProposedEvidence(quote="Invoice Number: INV-2026-001"),
            ProposedEvidence(quote="Reference copy for INV-2026-001"),
        ),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert len(res.evidence) == 2
    assert res.evidence[0].source_document_id == "pdf-1"
    assert res.evidence[0].location is not None and res.evidence[0].location.page == 1
    assert res.evidence[1].location is not None and res.evidence[1].location.page == 2
    assert all(ev.relationship == EvidenceRelationship.DIRECT for ev in res.evidence)

    # DOCX paragraph & heading provenance (PRO-004)
    docx_path = create_minimal_docx(
        tmp_path / "d.docx",
        headings=["Summary"],
        paragraphs=["Supplier: Northwind Traders"],
    )
    loaded_docx = load_document(docx_path, document_id="docx-1")
    assert loaded_docx.document is not None
    d_res = associator.associate(
        document=loaded_docx.document,
        proposed_evidence=(ProposedEvidence(quote="Supplier: Northwind Traders"),),
        field_name="vendor_name",
        presence=FieldPresence.PRESENT,
        value="Northwind Traders",
        record_id="record-001",
    )
    assert len(d_res.evidence) == 1
    assert d_res.evidence[0].location is not None
    assert d_res.evidence[0].location.paragraph == 2
    assert d_res.evidence[0].location.section == "Summary"

    # Multiline normalized match (PRO-005 & PRO-013)
    txt_path = tmp_path / "m.txt"
    txt_path.write_text("Invoice Number:\nINV-2026-001\nDone", encoding="utf-8")
    loaded_txt = load_document(txt_path, document_id="txt-1")
    assert loaded_txt.document is not None
    m_res = associator.associate(
        document=loaded_txt.document,
        proposed_evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert len(m_res.evidence) == 1
    assert m_res.evidence[0].location is not None
    assert m_res.evidence[0].location.line_start == 1
    assert m_res.evidence[0].location.line_end == 2
    assert m_res.evidence[0].evidence == "Invoice Number:\nINV-2026-001"


def test_pro_006_to_012_unavailable_conflict_foreign_document_and_truncation(
    tmp_path: Path,
) -> None:
    txt_path = tmp_path / "inv.txt"
    long_line = "Invoice Number: INV-2026-001 " + ("details " * 80)
    txt_path.write_text(long_line, encoding="utf-8")
    loaded = load_document(txt_path, document_id="doc-A")
    assert loaded.document is not None

    associator = ProvenanceAssociator(max_excerpt_chars=60)

    # PRO-006: Unavailable evidence -> empty list cleanly
    empty_res = associator.associate(
        document=loaded.document,
        proposed_evidence=(),
        field_name="notes",
        presence=FieldPresence.PRESENT,
        value="anything",
        record_id="record-001",
    )
    assert empty_res.evidence == []

    # PRO-008 & Provenance Integrity: Wrong extracted value with cited sentence flagged CONFLICTING
    conflict_res = associator.associate(
        document=loaded.document,
        proposed_evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-002",
        record_id="record-001",
    )
    assert conflict_res.evidence[0].relationship == EvidenceRelationship.CONFLICTING
    assert any(w.code == "evidence_value_conflict" for w in conflict_res.warnings)

    # PRO-010: Cross-document provenance rejected
    foreign_res = associator.associate(
        document=loaded.document,
        proposed_evidence=(
            ProposedEvidence(quote="Invoice Number: INV-2026-001", source_document_id="doc-OTHER"),
        ),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert foreign_res.evidence == []
    assert any(w.code == "cross_document_evidence_rejected" for w in foreign_res.warnings)

    # PRO-011: Quote absent from document -> UNVERIFIED and location=None (never fabricated!)
    unverified_res = associator.associate(
        document=loaded.document,
        proposed_evidence=(ProposedEvidence(quote="Fabricated sentence not in file"),),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert unverified_res.evidence[0].relationship == EvidenceRelationship.UNVERIFIED
    assert unverified_res.evidence[0].location is None
    assert any(w.code == "unverified_evidence" for w in unverified_res.warnings)

    # PRO-012: Truncated evidence honestly flagged
    trunc_res = associator.associate(
        document=loaded.document,
        proposed_evidence=(ProposedEvidence(quote=long_line.strip()),),
        field_name="invoice_number",
        presence=FieldPresence.PRESENT,
        value="INV-2026-001",
        record_id="record-001",
    )
    assert trunc_res.evidence[0].truncated is True
    assert len(trunc_res.evidence[0].evidence) == 60
