"""Integration tests for stateless batch extraction, concurrency & fail-fast (T005, T007)."""

from __future__ import annotations

import json
import time
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
    ExtractionEngine,
    ExtractionResult,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    SchemaDefinitionError,
    StrategyOutput,
    ValidationStatus,
    extract_batch,
)
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema


def _make_invoice_handler() -> DeterministicTestStrategy:
    def _handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        inv_num = "INV-2026-001"
        for line in doc.full_text.splitlines():
            if line.startswith("Invoice Number:"):
                inv_num = line.split(":", 1)[1].strip()
        if "EXPLODE" in doc.full_text:
            raise RuntimeError("simulated strategy explosion")
        if "INVALID_AMOUNT" in doc.full_text:
            return StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            "invoice_number": ProposedField(value=inv_num),
                            "vendor_name": ProposedField(value="Acme"),
                            "issue_date": ProposedField(value=date(2026, 3, 15)),
                            "total_amount": ProposedField(value=Decimal("-5.00")),
                            "currency": ProposedField(value="USD"),
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

    return DeterministicTestStrategy(_handler, name="batch-invoice-sim")


def test_mixed_format_batch_ordering_contract_v1_jsonl_and_preflight_errors(
    tmp_path: Path,
    fixtures_dir: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
) -> None:
    txt_doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    pdf_doc = create_minimal_pdf(
        tmp_path / "invoice_002.pdf",
        ["Invoice Number: INV-2026-002\nVendor: Acme Industrial Supply Ltd."],
    )
    docx_doc = create_minimal_docx(
        tmp_path / "invoice_003.docx",
        ["Invoice Number: INV-2026-003", "Vendor: Acme Industrial Supply Ltd."],
    )
    strategy = _make_invoice_handler()

    batch = extract_batch(
        [
            BatchDocumentInput(txt_doc, document_id="inv-txt"),
            pdf_doc,
            BatchDocumentInput(docx_doc, document_id="inv-docx"),
        ],
        invoice_schema_dict,
        strategy=strategy,
    )

    assert len(batch.results) == 3
    assert batch.summary.total_documents == 3
    assert batch.summary.processed_documents == 3
    assert batch.summary.skipped_documents == 0
    assert batch.summary.completed_count == 3
    assert batch.summary.partial_count == 0
    assert batch.summary.failed_count == 0
    assert batch.summary.valid_count == 3
    assert batch.summary.invalid_count == 0
    assert batch.summary.not_validated_count == 0
    assert batch.summary.aborted_early is False
    assert batch.summary.elapsed_ms >= 0.0

    # Exact input ordering & caller-supplied vs generated document_ids
    assert batch.results[0].source is not None
    assert batch.results[0].source.document_id == "inv-txt"
    assert batch.results[1].source is not None
    assert batch.results[1].source.document_id not in ("inv-txt", "inv-docx")
    assert batch.results[2].source is not None
    assert batch.results[2].source.document_id == "inv-docx"
    assert len({r.extraction_id for r in batch.results}) == 3

    # Every JSONL line conforms strictly to Contract v1.0 & round-trips losslessly
    jsonl = batch.to_jsonl()
    assert jsonl.endswith("\n") and not jsonl.endswith("\n\n")
    lines = jsonl.splitlines()
    assert len(lines) == 3
    for idx, line in enumerate(lines):
        parsed = json.loads(line)
        jsonschema.validate(instance=parsed, schema=contract_json_schema)
        assert ExtractionResult.from_json(line) == batch.results[idx]

    # Batch envelope JSON serialization is JSON-safe (Decimals/dates already canonical)
    envelope = json.loads(batch.to_json(indent=2))
    assert envelope["batch_id"] == batch.batch_id
    assert envelope["summary"]["total_documents"] == 3
    assert len(envelope["results"]) == 3

    # Eager pre-flight errors raise immediately
    with pytest.raises(SchemaDefinitionError):
        extract_batch([txt_doc], {"schema_id": "bad", "fields": {}}, strategy=strategy)
    with pytest.raises(ConfigurationError):
        extract_batch(
            [txt_doc],
            invoice_schema_dict,
            strategy=strategy,
            options="bad",  # type: ignore[arg-type]
        )


def test_fault_isolation_and_bounded_concurrency_cross_document_isolation(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
) -> None:
    # 1. Mixed batch with valid, missing, exploding strategy, and schema-invalid docs
    ok1 = tmp_path / "ok1.txt"
    ok1.write_text("Invoice Number: INV-2026-101\n", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    boom = tmp_path / "boom.txt"
    boom.write_text("Invoice Number: INV-2026-102\nEXPLODE\n", encoding="utf-8")
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("Invoice Number: INV-2026-103\nINVALID_AMOUNT\n", encoding="utf-8")
    ok2 = tmp_path / "ok2.txt"
    ok2.write_text("Invoice Number: INV-2026-104\n", encoding="utf-8")

    engine = ExtractionEngine(strategy=_make_invoice_handler())
    inputs = [ok1, missing, boom, invalid, ok2]

    seq_res = engine.extract_batch(inputs, invoice_schema_dict, options=BatchOptions(max_workers=1))
    par_res = engine.extract_batch(inputs, invoice_schema_dict, options=BatchOptions(max_workers=4))

    for res in (seq_res, par_res):
        assert [r.status for r in res.results] == [
            ProcessingStatus.COMPLETED,
            ProcessingStatus.FAILED,
            ProcessingStatus.FAILED,
            ProcessingStatus.COMPLETED,
            ProcessingStatus.COMPLETED,
        ]
        assert [r.validation.status for r in res.results] == [
            ValidationStatus.VALID,
            ValidationStatus.NOT_VALIDATED,
            ValidationStatus.NOT_VALIDATED,
            ValidationStatus.INVALID,
            ValidationStatus.VALID,
        ]
        assert res.summary.completed_count == 3
        assert res.summary.failed_count == 2
        assert res.summary.valid_count == 2
        assert res.summary.invalid_count == 1
        assert res.summary.not_validated_count == 2
        assert res.summary.skipped_documents == 0
        assert res.summary.aborted_early is False

    # 2. 12 distinct documents under max_workers=4 -> exact order & zero cross-doc bleed
    many_inputs: list[BatchDocumentInput] = []
    for idx in range(1, 13):
        p = tmp_path / f"doc_{idx:02d}.txt"
        p.write_text(f"Invoice Number: INV-2026-{idx:03d}\n", encoding="utf-8")
        many_inputs.append(BatchDocumentInput(p, document_id=f"batch-doc-{idx:02d}"))

    conc = engine.extract_batch(
        many_inputs,
        invoice_schema_dict,
        options=BatchOptions(max_workers=4),
    )
    assert conc.summary.completed_count == 12
    assert conc.summary.valid_count == 12
    assert len({r.extraction_id for r in conc.results}) == 12
    for idx, item in enumerate(conc.results, start=1):
        expected_inv = f"INV-2026-{idx:03d}"
        expected_id = f"batch-doc-{idx:02d}"
        assert item.records[0].fields["invoice_number"].value == expected_inv
        assert item.records[0].fields["invoice_number"].provenance[0].source_document_id == (
            expected_id
        )


def test_fail_fast_sequential_concurrent_and_non_trigger_on_validation_invalid(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
) -> None:
    d1 = tmp_path / "d1.txt"
    d1.write_text("Invoice Number: INV-2026-201\nINVALID_AMOUNT\n", encoding="utf-8")
    d2 = tmp_path / "d2.txt"
    d2.write_text("Invoice Number: INV-2026-202\nEXPLODE\n", encoding="utf-8")
    d3 = tmp_path / "d3.txt"
    d3.write_text("Invoice Number: INV-2026-203\n", encoding="utf-8")
    d4 = tmp_path / "d4.txt"
    d4.write_text("Invoice Number: INV-2026-204\n", encoding="utf-8")
    d5 = tmp_path / "d5.txt"
    d5.write_text("Invoice Number: INV-2026-205\n", encoding="utf-8")

    # 1. Sequential fail_fast: d1 is COMPLETED+INVALID (does NOT trigger fail_fast!),
    #    d2 is FAILED (triggers fail_fast!), d3..d5 are skipped.
    seq_ff = extract_batch(
        [d1, d2, d3, d4, d5],
        invoice_schema_dict,
        strategy=_make_invoice_handler(),
        options=BatchOptions(max_workers=1, fail_fast=True),
    )
    assert seq_ff.summary.aborted_early is True
    assert seq_ff.summary.processed_documents == 2
    assert seq_ff.summary.skipped_documents == 3
    assert seq_ff.results[0].status == ProcessingStatus.COMPLETED
    assert seq_ff.results[0].validation.status == ValidationStatus.INVALID
    assert seq_ff.results[1].status == ProcessingStatus.FAILED

    # 2. Concurrent fail_fast with sliding window (max_workers=2): pending items skipped cleanly
    def _slow_after_fail(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        if "EXPLODE" in doc.full_text:
            raise RuntimeError("fast failure")
        time.sleep(0.02)
        return _make_invoice_handler().extract_raw(doc, _schema)

    conc_ff = extract_batch(
        [d2, d1, d3, d4, d5],
        invoice_schema_dict,
        strategy=DeterministicTestStrategy(_slow_after_fail),
        options=BatchOptions(max_workers=2, fail_fast=True),
    )
    assert conc_ff.summary.aborted_early is True
    assert conc_ff.summary.processed_documents + conc_ff.summary.skipped_documents == 5
    assert conc_ff.summary.skipped_documents >= 1
    assert conc_ff.results[0].status == ProcessingStatus.FAILED
