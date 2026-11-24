"""Integration tests for single & mixed-batch in-memory document inputs (T006, T008)."""

from __future__ import annotations

import io
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from tests.conftest import create_minimal_docx, create_minimal_pdf

from structa_maestro import (
    BatchDocumentInput,
    BatchOptions,
    ConfigurationError,
    DeterministicTestStrategy,
    DocumentInput,
    DocumentLimits,
    ExtractionEngine,
    ExtractionResult,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
    extract,
    extract_batch,
)
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema


def _make_invoice_strategy() -> DeterministicTestStrategy:
    def _handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        inv_num = "INV-2026-001"
        for line in doc.full_text.splitlines():
            if line.startswith("Invoice Number:"):
                inv_num = line.split(":", 1)[1].strip()
        return StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value=inv_num,
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote=f"Invoice Number: {inv_num}"),),
                        ),
                        "vendor_name": ProposedField(value="Acme Industrial Supply Ltd."),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("1250.50")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            )
        )

    return DeterministicTestStrategy(_handler, name="in-memory-invoice-sim")


class _BrokenMidwayStream:
    """Binary stream that raises OSError when read."""

    closed = False

    def readable(self) -> bool:
        return True

    def read(self, _size: int = -1) -> bytes:
        raise OSError("simulated network stream disconnect")


def test_single_document_in_memory_parity_across_txt_pdf_docx_and_zero_tempfiles(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    txt_file = tmp_path / "invoice_001.txt"
    txt_content = "Invoice Number: INV-2026-001\nVendor: Acme Industrial Supply Ltd.\n"
    txt_file.write_text(txt_content, encoding="utf-8")
    pdf_file = create_minimal_pdf(
        tmp_path / "invoice_002.pdf",
        ["Invoice Number: INV-2026-002\nVendor: Acme Industrial Supply Ltd."],
    )
    docx_file = create_minimal_docx(
        tmp_path / "invoice_003.docx",
        ["Invoice Number: INV-2026-003", "Vendor: Acme Industrial Supply Ltd."],
    )

    strategy = _make_invoice_strategy()
    file_txt_res = extract(txt_file, invoice_schema_dict, strategy=strategy, document_id="d-txt")
    file_pdf_res = extract(pdf_file, invoice_schema_dict, strategy=strategy, document_id="d-pdf")
    file_docx_res = extract(docx_file, invoice_schema_dict, strategy=strategy, document_id="d-docx")

    # Guard that zero temporary files are created during in-memory extraction
    def _forbid_tempfile(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("In-memory extraction must not create temporary files on disk")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _forbid_tempfile)
    monkeypatch.setattr(tempfile, "mkstemp", _forbid_tempfile)
    monkeypatch.setattr(tempfile, "TemporaryFile", _forbid_tempfile)

    raw_pdf = pdf_file.read_bytes()
    raw_docx = docx_file.read_bytes()

    in_mem_cases = [
        (
            DocumentInput.from_text(txt_content, filename="invoice_001.txt", document_id="d-txt"),
            file_txt_res,
            "text/plain",
        ),
        (
            DocumentInput.from_bytes(
                txt_content.encode("utf-8"),
                filename="dir/invoice_001.txt",
                document_id="d-txt",
                media_type="text/plain",
            ),
            file_txt_res,
            "text/plain",
        ),
        (
            DocumentInput.from_bytes(
                raw_pdf,
                filename="s3/2026/invoice_002.pdf",
                document_id="d-pdf",
                media_type="application/pdf",
            ),
            file_pdf_res,
            "application/pdf",
        ),
        (
            DocumentInput.from_stream(
                io.BytesIO(raw_docx),
                filename=r"C:\uploads\invoice_003.docx",
                document_id="d-docx",
            ),
            file_docx_res,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]

    for doc_input, expected_file_res, expected_mime in in_mem_cases:
        res = extract(doc_input, invoice_schema_dict, strategy=strategy)
        assert res.status == ProcessingStatus.COMPLETED
        assert res.validation.status == ValidationStatus.VALID
        assert res.source == expected_file_res.source
        assert res.source is not None and res.source.media_type == expected_mime
        assert res.records[0].fields["invoice_number"].value == (
            expected_file_res.records[0].fields["invoice_number"].value
        )
        assert res.records[0].fields["invoice_number"].provenance == (
            expected_file_res.records[0].fields["invoice_number"].provenance
        )
        # Strict Contract v1.0 schema & lossless round-trip
        jsonschema.validate(instance=json.loads(res.to_json()), schema=contract_json_schema)
        assert ExtractionResult.from_json(res.to_json()) == res

    # Call-level document_id override precedence + blank override eager ConfigurationError
    override_res = extract(
        DocumentInput.from_text(txt_content, document_id="inner-id"),
        invoice_schema_dict,
        strategy=strategy,
        document_id="override-id",
    )
    assert override_res.source is not None
    assert override_res.source.document_id == "override-id"

    with pytest.raises(ConfigurationError):
        extract(
            DocumentInput.from_text(txt_content, document_id="inner-id"),
            invoice_schema_dict,
            strategy=strategy,
            document_id="   ",
        )

    # Contradictory media_type hint vs actual content -> canonical failed result
    mismatch = extract(
        DocumentInput.from_bytes(raw_pdf, media_type="text/plain"),
        invoice_schema_dict,
        strategy=strategy,
    )
    assert mismatch.status == ProcessingStatus.FAILED
    assert any(e.code == "unsupported_format" for e in mismatch.errors)

    # Empty bytes and whitespace-only text -> canonical failed result
    empty_b = extract(DocumentInput.from_bytes(b""), invoice_schema_dict, strategy=strategy)
    assert empty_b.status == ProcessingStatus.FAILED
    assert any(e.code == "empty_document" for e in empty_b.errors)

    empty_t = extract(DocumentInput.from_text("   \n"), invoice_schema_dict, strategy=strategy)
    assert empty_t.status == ProcessingStatus.FAILED
    assert any(e.code == "empty_document" for e in empty_t.errors)


def test_mixed_file_and_in_memory_batch_sequential_concurrent_and_fault_isolation(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
) -> None:
    p_txt = tmp_path / "disk_01.txt"
    p_txt.write_text("Invoice Number: INV-2026-101\n", encoding="utf-8")
    p_pdf = create_minimal_pdf(tmp_path / "disk_02.pdf", ["Invoice Number: INV-2026-102"])
    pdf_bytes = p_pdf.read_bytes()
    p_docx = create_minimal_docx(tmp_path / "disk_03.docx", ["Invoice Number: INV-2026-103"])
    docx_bytes = p_docx.read_bytes()

    engine = ExtractionEngine(
        strategy=_make_invoice_strategy(),
        limits=DocumentLimits(max_file_bytes=64 * 1024),
    )

    def _build_mixed_inputs() -> list[Path | BatchDocumentInput | DocumentInput]:
        return [
            p_txt,
            BatchDocumentInput(p_pdf, document_id="batch-path-02"),
            DocumentInput.from_bytes(pdf_bytes, filename="mem_03.pdf", document_id="mem-bytes-03"),
            DocumentInput.from_text(
                "Invoice Number: INV-2026-104\n",
                filename="mem_04.txt",
                document_id="mem-text-04",
            ),
            DocumentInput.from_stream(
                io.BytesIO(docx_bytes),
                filename="mem_05.docx",
                document_id="mem-stream-05",
            ),
            DocumentInput.from_stream(
                _BrokenMidwayStream(),  # type: ignore[arg-type]
                filename="broken.txt",
                document_id="mem-broken-06",
            ),
            DocumentInput.from_stream(
                io.BytesIO(b"A" * (70 * 1024)),
                filename="oversized.txt",
                document_id="mem-oversized-07",
            ),
        ]

    seq_res = engine.extract_batch(
        _build_mixed_inputs(),
        invoice_schema_dict,
        options=BatchOptions(max_workers=1),
    )
    par_res = engine.extract_batch(
        _build_mixed_inputs(),
        invoice_schema_dict,
        options=BatchOptions(max_workers=4),
    )

    for res in (seq_res, par_res):
        assert res.summary.total_documents == 7
        assert res.summary.processed_documents == 7
        assert res.summary.skipped_documents == 0
        assert res.summary.completed_count == 5
        assert res.summary.failed_count == 2
        assert res.summary.valid_count == 5
        assert res.summary.not_validated_count == 2
        assert [r.status for r in res.results] == [
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.FAILED,
        ]
        assert res.results[1].source is not None
        assert res.results[1].source.document_id == "batch-path-02"
        assert res.results[2].records[0].fields["invoice_number"].value == "INV-2026-102"
        assert res.results[3].records[0].fields["invoice_number"].value == "INV-2026-104"
        assert res.results[4].records[0].fields["invoice_number"].value == "INV-2026-103"
        assert any(e.code == "invalid_input" for e in res.results[5].errors)
        assert any(e.code == "resource_limit_exceeded" for e in res.results[6].errors)

        # Contract v1.0 JSONL validation & round-trip
        lines = res.to_jsonl().splitlines()
        assert len(lines) == 7
        for idx, line in enumerate(lines):
            jsonschema.validate(instance=json.loads(line), schema=contract_json_schema)
            assert ExtractionResult.from_json(line) == res.results[idx]

    # Duplicate document_id across BatchDocumentInput and DocumentInput fails eagerly
    with pytest.raises(ConfigurationError, match="Duplicate"):
        extract_batch(
            [
                BatchDocumentInput(p_txt, document_id="dup-id"),
                DocumentInput.from_text("Invoice Number: INV-2026-999", document_id="dup-id"),
            ],
            invoice_schema_dict,
            strategy=_make_invoice_strategy(),
        )

    # Sequential fail_fast stops lazily before consuming subsequent stream
    unread_stream = io.BytesIO(b"Invoice Number: INV-2026-500\n")
    ff = extract_batch(
        [
            DocumentInput.from_bytes(b"", document_id="fail-first"),
            DocumentInput.from_stream(unread_stream, document_id="never-read"),
        ],
        invoice_schema_dict,
        strategy=_make_invoice_strategy(),
        options=BatchOptions(max_workers=1, fail_fast=True),
    )
    assert ff.summary.aborted_early is True
    assert ff.summary.skipped_documents == 1
    assert unread_stream.tell() == 0
