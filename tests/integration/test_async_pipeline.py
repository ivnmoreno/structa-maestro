"""Integration tests for aextract, aextract_batch, concurrency, fail_fast & cancellation."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel, Field
from tests.conftest import create_minimal_docx, create_minimal_pdf

from structa_maestro import (
    AsyncDeterministicTestStrategy,
    BatchDocumentInput,
    BatchOptions,
    ConfigurationError,
    DeterministicTestStrategy,
    DocumentInput,
    DocumentLimits,
    ExtractionEngine,
    ExtractionResult,
    ExtractionSchema,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    SchemaDefinitionError,
    StrategyOutput,
    ValidationStatus,
    aextract,
    aextract_batch,
    bind_records,
)
from structa_maestro.documents.models import PreparedDocument


class AsyncInvoiceModel(BaseModel):
    invoice_number: str = Field(pattern=r"^INV-\d{4}-\d+$")
    vendor_name: str
    issue_date: date
    total_amount: Decimal = Field(ge=0)
    currency: str


def _make_sync_invoice_strategy() -> DeterministicTestStrategy:
    def _handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        inv_num = "INV-2026-001"
        for line in doc.full_text.splitlines():
            if line.startswith("Invoice Number:"):
                inv_num = line.split(":", 1)[1].strip()
        if "EXPLODE" in doc.full_text:
            raise RuntimeError("boom")
        return StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value=inv_num,
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote=f"Invoice Number: {inv_num}"),),
                        ),
                        "vendor_name": ProposedField(value="Acme Corp"),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("1250.50")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            )
        )

    return DeterministicTestStrategy(_handler, name="sync-invoice")


def test_async_single_parity_sync_offload_dual_protocol_and_pydantic_binding(
    tmp_path: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    async def _run() -> None:
        schema = ExtractionSchema.from_pydantic(AsyncInvoiceModel, schema_id="async-inv")
        txt_file = tmp_path / "inv1.txt"
        txt_file.write_text("Invoice Number: INV-2026-001\n", encoding="utf-8")
        pdf_file = create_minimal_pdf(tmp_path / "inv2.pdf", ["Invoice Number: INV-2026-002"])
        docx_file = create_minimal_docx(tmp_path / "inv3.docx", ["Invoice Number: INV-2026-003"])

        # 1. Sync strategy offloaded via asyncio.to_thread works across TXT/PDF/DOCX + DocumentInput
        sync_strat = _make_sync_invoice_strategy()
        for src, expected in (
            (txt_file, "INV-2026-001"),
            (pdf_file, "INV-2026-002"),
            (docx_file, "INV-2026-003"),
            (
                DocumentInput.from_text(
                    "Invoice Number: INV-2026-004\n",
                    filename="mem.txt",
                    document_id="inner-4",
                ),
                "INV-2026-004",
            ),
        ):
            res = await aextract(src, schema, strategy=sync_strat)
            assert res.status == ProcessingStatus.COMPLETED
            assert res.validation.status == ValidationStatus.VALID
            jsonschema.validate(instance=json.loads(res.to_json()), schema=contract_json_schema)
            bound = bind_records(res, AsyncInvoiceModel)
            assert bound[0].invoice_number == expected

        # 2. Call-level document_id override & eager blank-ID rejection on DocumentInput
        res_override = await aextract(
            DocumentInput.from_text("Invoice Number: INV-2026-005\n", document_id="inner"),
            schema,
            strategy=sync_strat,
            document_id="override-5",
        )
        assert res_override.source is not None
        assert res_override.source.document_id == "override-5"

        with pytest.raises(ConfigurationError):
            await aextract(
                DocumentInput.from_text("Invoice Number: INV-2026-005\n"),
                schema,
                strategy=sync_strat,
                document_id="   ",
            )

        # 3. Dual-protocol strategy prefers native async aextract_raw over sync extract_raw
        called_paths: list[str] = []

        class _DualStrategy:
            @property
            def name(self) -> str:
                return "dual-proto"

            def extract_raw(
                self, document: PreparedDocument, schema: ExtractionSchema
            ) -> StrategyOutput:
                called_paths.append("sync")
                return sync_strat.extract_raw(document, schema)

            async def aextract_raw(
                self, document: PreparedDocument, schema: ExtractionSchema
            ) -> StrategyOutput:
                called_paths.append("async")
                await asyncio.sleep(0)
                return sync_strat.extract_raw(document, schema)

        res_dual = await aextract(txt_file, schema, strategy=_DualStrategy())
        assert res_dual.status == ProcessingStatus.COMPLETED
        assert called_paths == ["async"]

        # 4. Invalid non-path/non-DocumentInput -> canonical failed result
        bad_input_res = await aextract(123, schema, strategy=sync_strat)  # type: ignore[arg-type]
        assert bad_input_res.status == ProcessingStatus.FAILED
        assert any(e.code == "invalid_input" for e in bad_input_res.errors)

    asyncio.run(_run())


def test_async_batch_bounded_concurrency_ordering_fault_isolation_and_fail_fast(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
) -> None:
    async def _run() -> None:
        in_flight = 0
        peak_in_flight = 0

        async def _async_handler(
            doc: PreparedDocument, _schema: ExtractionSchema
        ) -> StrategyOutput:
            nonlocal in_flight, peak_in_flight
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
            try:
                await asyncio.sleep(0.01)
                if "EXPLODE" in doc.full_text:
                    raise RuntimeError("simulated async strategy failure")
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
                                    evidence=(
                                        ProposedEvidence(quote=f"Invoice Number: {inv_num}"),
                                    ),
                                ),
                                "vendor_name": ProposedField(value="Acme"),
                                "issue_date": ProposedField(value=date(2026, 3, 15)),
                                "total_amount": ProposedField(value=Decimal("100.00")),
                                "currency": ProposedField(value="USD"),
                            }
                        ),
                    )
                )
            finally:
                in_flight -= 1

        strategy = AsyncDeterministicTestStrategy(_async_handler, name="async-batch-sim")
        engine = ExtractionEngine(strategy=strategy, limits=DocumentLimits(max_file_bytes=64_000))

        # 1. Mixed file + BatchDocumentInput + DocumentInput (bytes/text/stream + failing)
        p1 = tmp_path / "f1.txt"
        p1.write_text("Invoice Number: INV-2026-101\n", encoding="utf-8")
        p2 = tmp_path / "f2.txt"
        p2.write_text("Invoice Number: INV-2026-102\n", encoding="utf-8")

        inputs: list[Path | BatchDocumentInput | DocumentInput] = [
            p1,
            BatchDocumentInput(p2, document_id="id-2"),
            DocumentInput.from_bytes(b"Invoice Number: INV-2026-103\n", document_id="id-3"),
            DocumentInput.from_text("Invoice Number: INV-2026-104\nEXPLODE\n", document_id="id-4"),
            DocumentInput.from_stream(
                io.BytesIO(b"Invoice Number: INV-2026-105\n"),
                filename="stream.txt",
                document_id="id-5",
            ),
            DocumentInput.from_stream(
                io.BytesIO(b"A" * 70_000),
                filename="huge.txt",
                document_id="id-6",
            ),
            DocumentInput.from_text("Invoice Number: INV-2026-107\n", document_id="id-7"),
            DocumentInput.from_text("Invoice Number: INV-2026-108\n", document_id="id-8"),
        ]

        batch = await engine.aextract_batch(
            inputs,
            invoice_schema_dict,
            options=BatchOptions(max_workers=3),
        )
        assert peak_in_flight <= 3
        assert peak_in_flight >= 2
        assert batch.summary.total_documents == 8
        assert batch.summary.processed_documents == 8
        assert batch.summary.skipped_documents == 0
        assert batch.summary.completed_count == 6
        assert batch.summary.failed_count == 2
        assert batch.summary.valid_count == 6
        assert batch.summary.not_validated_count == 2
        assert batch.summary.aborted_early is False

        # Verify exact input ordering, provenance binding & Contract v1.0 JSONL round-trip
        assert batch.results[0].records[0].fields["invoice_number"].value == "INV-2026-101"
        assert batch.results[1].source is not None and batch.results[1].source.document_id == "id-2"
        assert batch.results[2].records[0].fields["invoice_number"].value == "INV-2026-103"
        assert batch.results[3].status == ProcessingStatus.FAILED
        assert batch.results[4].records[0].fields["invoice_number"].value == "INV-2026-105"
        assert batch.results[5].status == ProcessingStatus.FAILED
        assert any(e.code == "resource_limit_exceeded" for e in batch.results[5].errors)
        assert batch.results[7].records[0].fields["invoice_number"].value == "INV-2026-108"

        lines = batch.to_jsonl().splitlines()
        assert len(lines) == 8
        for idx, line in enumerate(lines):
            jsonschema.validate(instance=json.loads(line), schema=contract_json_schema)
            assert ExtractionResult.from_json(line) == batch.results[idx]

        # 2. Sequential fail_fast=True (max_workers=1) skips lazily without reading next stream
        unread_stream = io.BytesIO(b"Invoice Number: INV-2026-999\n")
        seq_ff = await engine.aextract_batch(
            [
                DocumentInput.from_text("EXPLODE\n", document_id="ff-1"),
                DocumentInput.from_stream(unread_stream, document_id="ff-2"),
            ],
            invoice_schema_dict,
            options=BatchOptions(max_workers=1, fail_fast=True),
        )
        assert seq_ff.summary.aborted_early is True
        assert seq_ff.summary.processed_documents == 1
        assert seq_ff.summary.skipped_documents == 1
        assert unread_stream.tell() == 0

        # 3. Concurrent fail_fast=True stops scheduling remaining pending items cleanly
        conc_ff = await aextract_batch(
            [
                DocumentInput.from_text("EXPLODE\n", document_id="cf-1"),
                DocumentInput.from_text("Invoice Number: INV-2026-202\n", document_id="cf-2"),
                DocumentInput.from_text("Invoice Number: INV-2026-203\n", document_id="cf-3"),
                DocumentInput.from_text("Invoice Number: INV-2026-204\n", document_id="cf-4"),
                DocumentInput.from_text("Invoice Number: INV-2026-205\n", document_id="cf-5"),
            ],
            invoice_schema_dict,
            strategy=strategy,
            options=BatchOptions(max_workers=2, fail_fast=True),
        )
        assert conc_ff.summary.aborted_early is True
        assert conc_ff.summary.processed_documents + conc_ff.summary.skipped_documents == 5
        assert conc_ff.summary.skipped_documents >= 1
        assert conc_ff.results[0].status == ProcessingStatus.FAILED

        # 4. Eager pre-flight errors raise immediately
        with pytest.raises(SchemaDefinitionError):
            await aextract_batch([p1], {"schema_id": "bad", "fields": {}}, strategy=strategy)
        with pytest.raises(ConfigurationError):
            await aextract_batch([], invoice_schema_dict, strategy=strategy)
        with pytest.raises(ConfigurationError, match="Duplicate"):
            await aextract_batch(
                [
                    DocumentInput.from_text("Invoice Number: INV-2026-1\n", document_id="dup"),
                    DocumentInput.from_text("Invoice Number: INV-2026-2\n", document_id="dup"),
                ],
                invoice_schema_dict,
                strategy=strategy,
            )

    asyncio.run(_run())


def test_async_extract_and_batch_propagate_cancellation_without_dangling_tasks(
    invoice_schema_dict: dict[str, Any],
) -> None:
    async def _run() -> None:
        started_count = 0
        cancelled_count = 0
        two_started = asyncio.Event()

        async def _hanging_handler(
            _doc: PreparedDocument, _schema: ExtractionSchema
        ) -> StrategyOutput:
            nonlocal started_count, cancelled_count
            started_count += 1
            if started_count >= 2:
                two_started.set()
            try:
                await asyncio.sleep(10.0)
                return StrategyOutput(records=())
            except asyncio.CancelledError:
                cancelled_count += 1
                raise

        strategy = AsyncDeterministicTestStrategy(_hanging_handler, name="hanging-async")

        # 1. Single aextract cancellation via asyncio.wait_for
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                aextract(
                    DocumentInput.from_text("Invoice Number: INV-2026-001\n"),
                    invoice_schema_dict,
                    strategy=strategy,
                ),
                timeout=0.02,
            )

        # 2. Concurrent aextract_batch cancellation cleans up all active child tasks
        started_count = 0
        cancelled_count = 0
        two_started.clear()
        batch_task = asyncio.create_task(
            aextract_batch(
                [DocumentInput.from_text(f"Invoice Number: INV-2026-{i:03d}\n") for i in range(6)],
                invoice_schema_dict,
                strategy=strategy,
                options=BatchOptions(max_workers=2),
            )
        )
        await two_started.wait()
        batch_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch_task
        assert cancelled_count == 2

    asyncio.run(_run())
