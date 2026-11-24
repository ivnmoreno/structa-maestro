"""Integration tests for sync/async observability, batch telemetry, fault isolation & cancel."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from structa_maestro import (
    AsyncDeterministicTestStrategy,
    BatchOptions,
    ConfigurationError,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionEngine,
    ExtractionEvent,
    ExtractionEventKind,
    ExtractionResult,
    InMemoryTelemetryObserver,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    StrategyTelemetry,
    TokenUsage,
    ValidationStatus,
    aextract,
    aextract_batch,
    extract,
    extract_batch,
)


def _ok_output(*, tokens: int = 100) -> StrategyOutput:
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        value="INV-2026-001",
                        confidence=0.95,
                        evidence=(ProposedEvidence(quote="INV-2026-001"),),
                    ),
                    "vendor_name": ProposedField(
                        value="Acme",
                        confidence=0.95,
                        evidence=(ProposedEvidence(quote="Acme"),),
                    ),
                    "issue_date": ProposedField(value=date(2026, 3, 15), confidence=0.95),
                    "total_amount": ProposedField(value=Decimal("100.00"), confidence=0.95),
                    "currency": ProposedField(value="USD", confidence=0.95),
                }
            ),
        ),
        telemetry=StrategyTelemetry(
            attempts=1,
            retry_count=0,
            structured_output_mode_used="json_schema",
            token_usage=TokenUsage(
                prompt_tokens=tokens,
                completion_tokens=20,
                total_tokens=tokens + 20,
            ),
        ),
    )


def test_single_sync_and_async_lifecycle_correlation_load_failure_and_composition(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
) -> None:
    doc = tmp_path / "inv.txt"
    doc.write_text("Invoice: INV-2026-001\nVendor: Acme\n", encoding="utf-8")

    order_log: list[tuple[str, ExtractionEventKind]] = []

    class _NamedSyncObserver:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def on_event(self, event: ExtractionEvent) -> None:
            order_log.append((self.tag, event.event_kind))

    class _DualObserver:
        def __init__(self) -> None:
            self.sync_hits = 0
            self.async_hits = 0

        def on_event(self, _event: ExtractionEvent) -> None:
            self.sync_hits += 1

        async def aon_event(self, _event: ExtractionEvent) -> None:
            self.async_hits += 1

    class _AsyncOnlyObserver:
        async def aon_event(self, _event: ExtractionEvent) -> None:
            return None

    strat = DeterministicTestStrategy(_ok_output(tokens=50))
    engine_obs = InMemoryTelemetryObserver()
    named_eng = _NamedSyncObserver("engine")
    named_call = _NamedSyncObserver("call")
    dual = _DualObserver()

    engine = ExtractionEngine(strat, observers=[engine_obs, named_eng])

    # 1. Sync single extraction composes engine + call observers without mutating engine
    res = engine.extract(doc, invoice_schema_dict, document_id="doc-1", observers=[named_call])
    assert res.status == ProcessingStatus.COMPLETED
    assert ExtractionResult.from_json(res.to_json()) == res

    kinds = [e.event_kind for e in engine_obs.events()]
    assert kinds == [
        ExtractionEventKind.EXTRACTION_STARTED,
        ExtractionEventKind.DOCUMENT_LOADED,
        ExtractionEventKind.STRATEGY_COMPLETED,
        ExtractionEventKind.EXTRACTION_COMPLETED,
    ]
    # Exact 1-to-1 extraction_id correlation
    assert all(e.extraction_id == res.extraction_id for e in engine_obs.events())
    completed_ev = engine_obs.events()[-1]
    assert [st.stage for st in completed_ev.stage_timings] == [
        "document_load",
        "strategy",
        "finalization",
    ]
    assert completed_ev.strategy_telemetry is not None
    assert completed_ev.strategy_telemetry.token_usage == TokenUsage(50, 20, 70)

    # Engine observer fired before call observer on every event
    assert order_log[:2] == [
        ("engine", ExtractionEventKind.EXTRACTION_STARTED),
        ("call", ExtractionEventKind.EXTRACTION_STARTED),
    ]

    # Subsequent call without call observers proves engine observer list was not mutated
    order_log.clear()
    engine.extract(doc, invoice_schema_dict)
    assert all(tag == "engine" for tag, _ in order_log)

    # 2. Load failure & invalid-type input skip strategy_completed
    fail_obs = InMemoryTelemetryObserver()
    res_missing = extract(
        tmp_path / "nope.txt", invoice_schema_dict, strategy=strat, observers=[fail_obs]
    )
    assert res_missing.status == ProcessingStatus.FAILED
    assert [e.event_kind for e in fail_obs.events()] == [
        ExtractionEventKind.EXTRACTION_STARTED,
        ExtractionEventKind.DOCUMENT_LOADED,
        ExtractionEventKind.EXTRACTION_COMPLETED,
    ]
    assert [st.stage for st in fail_obs.events()[-1].stage_timings] == [
        "document_load",
        "finalization",
    ]

    inv_obs = InMemoryTelemetryObserver()
    res_bad = extract(123, invoice_schema_dict, strategy=strat, observers=[inv_obs])  # type: ignore[arg-type]
    assert res_bad.status == ProcessingStatus.FAILED
    assert [e.event_kind for e in inv_obs.events()] == [
        ExtractionEventKind.EXTRACTION_STARTED,
        ExtractionEventKind.DOCUMENT_LOADED,
        ExtractionEventKind.EXTRACTION_COMPLETED,
    ]
    assert "invalid_input" in inv_obs.events()[1].error_codes

    # 3. Unexpected strategy exception emits strategy_completed(succeeded=False)
    def _boom(_doc: Any, _schema: Any) -> StrategyOutput:
        raise RuntimeError("boom")

    boom_obs = InMemoryTelemetryObserver()
    res_boom = extract(
        doc,
        invoice_schema_dict,
        strategy=DeterministicTestStrategy(_boom),
        observers=[boom_obs],
    )
    assert res_boom.status == ProcessingStatus.FAILED
    strat_ev = next(
        e for e in boom_obs.events() if e.event_kind == ExtractionEventKind.STRATEGY_COMPLETED
    )
    assert strat_ev.stage_timing is not None and strat_ev.stage_timing.succeeded is False
    assert strat_ev.error_codes == ("extraction_strategy_error",)
    assert strat_ev.strategy_telemetry is None

    # 4. Sync call eagerly rejects async-only observer (both engine-level and call-level)
    with pytest.raises(ConfigurationError):
        engine.extract(doc, invoice_schema_dict, observers=[_AsyncOnlyObserver()])  # type: ignore[list-item]
    async_obs_engine = ExtractionEngine(strat, observers=[_AsyncOnlyObserver()])
    with pytest.raises(ConfigurationError):
        async_obs_engine.extract(doc, invoice_schema_dict)

    # 5. Async single prefers aon_event on dual-protocol observer
    async def _run_async() -> None:
        a_obs = InMemoryTelemetryObserver()
        res_a = await aextract(doc, invoice_schema_dict, strategy=strat, observers=[a_obs, dual])
        assert res_a.status == ProcessingStatus.COMPLETED
        assert [e.event_kind for e in a_obs.events()] == [
            ExtractionEventKind.EXTRACTION_STARTED,
            ExtractionEventKind.DOCUMENT_LOADED,
            ExtractionEventKind.STRATEGY_COMPLETED,
            ExtractionEventKind.EXTRACTION_COMPLETED,
        ]
        assert all(e.extraction_id == res_a.extraction_id for e in a_obs.events())
        assert dual.async_hits == 4
        assert dual.sync_hits == 0

    asyncio.run(_run_async())


def test_sync_and_async_batch_telemetry_concurrency_fail_fast_and_empty_rejection(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
) -> None:
    f0 = tmp_path / "0.txt"
    f0.write_text("Invoice: INV-2026-001\nVendor: Acme\n", encoding="utf-8")
    docs = [
        f0,
        DocumentInput.from_text("Invoice: INV-2026-001\nVendor: Acme\n", filename="1.txt"),
        DocumentInput.from_stream(
            io.BytesIO(b"Invoice: INV-2026-001\nVendor: Acme\n"), filename="2.txt"
        ),
        tmp_path / "missing_3.txt",
    ]
    strat = DeterministicTestStrategy(_ok_output(tokens=40))

    # Empty batch raises ConfigurationError eagerly with 0 emitted events
    empty_obs = InMemoryTelemetryObserver()
    with pytest.raises(ConfigurationError):
        extract_batch([], invoice_schema_dict, strategy=strat, observers=[empty_obs])
    assert empty_obs.events() == ()

    # Sync concurrent batch (max_workers=4, fail_fast=False)
    sync_obs = InMemoryTelemetryObserver()
    sync_batch = extract_batch(
        docs,
        invoice_schema_dict,
        strategy=strat,
        options=BatchOptions(max_workers=4, fail_fast=False),
        observers=[sync_obs],
    )
    events = sync_obs.events()
    assert events[0].event_kind == ExtractionEventKind.BATCH_STARTED
    assert events[-1].event_kind == ExtractionEventKind.BATCH_COMPLETED
    assert all(e.batch_id == sync_batch.batch_id for e in events)
    item_done = [e for e in events if e.event_kind == ExtractionEventKind.BATCH_ITEM_COMPLETED]
    assert len(item_done) == 4
    assert {e.batch_progress.item_index for e in item_done if e.batch_progress} == {0, 1, 2, 3}
    s = sync_obs.summary()
    assert s.total_extractions == 4
    assert s.completed_count == 3
    assert s.failed_count == 1
    assert s.valid_count == 3
    assert s.not_validated_count == 1
    assert s.skipped_documents == 0
    assert s.prompt_tokens == 120
    assert s.total_tokens == 180

    # Sync fail_fast=True (max_workers=1) skips remaining items cleanly
    ff_obs = InMemoryTelemetryObserver()
    ff_docs = [tmp_path / "missing_first.txt", f0, f0]
    ff_batch = extract_batch(
        ff_docs,
        invoice_schema_dict,
        strategy=strat,
        options=BatchOptions(max_workers=1, fail_fast=True),
        observers=[ff_obs],
    )
    assert ff_batch.summary.aborted_early is True
    assert ff_batch.summary.skipped_documents == 2
    ff_summary = ff_obs.summary()
    assert ff_summary.total_extractions == 1
    assert ff_summary.skipped_documents == 2
    assert ff_summary.aborted_early_count == 1

    # Async concurrent batch (max_workers=4) with native async strategy + async observer
    async def _run_async_batch() -> None:
        async def _async_handler(_doc: Any, _schema: Any) -> StrategyOutput:
            await asyncio.sleep(0.005)
            return _ok_output(tokens=30)

        a_strat = AsyncDeterministicTestStrategy(_async_handler)
        a_obs = InMemoryTelemetryObserver()
        async_seen: list[ExtractionEventKind] = []

        class _AsyncRecorder:
            async def aon_event(self, event: ExtractionEvent) -> None:
                await asyncio.sleep(0)
                async_seen.append(event.event_kind)

        a_docs = [
            f0,
            DocumentInput.from_text("Invoice: INV-2026-001\nVendor: Acme\n", filename="a1.txt"),
            DocumentInput.from_stream(
                io.BytesIO(b"Invoice: INV-2026-001\nVendor: Acme\n"),
                filename="a2.txt",
            ),
            tmp_path / "missing_a3.txt",
        ]
        a_batch = await aextract_batch(
            a_docs,
            invoice_schema_dict,
            strategy=a_strat,
            options=BatchOptions(max_workers=4, fail_fast=False),
            observers=[a_obs, _AsyncRecorder()],
        )
        assert a_obs.events()[0].event_kind == ExtractionEventKind.BATCH_STARTED
        assert a_obs.events()[-1].event_kind == ExtractionEventKind.BATCH_COMPLETED
        assert all(e.batch_id == a_batch.batch_id for e in a_obs.events())
        assert len(async_seen) == len(a_obs.events())
        a_sum = a_obs.summary()
        assert a_sum.total_extractions == 4
        assert a_sum.completed_count == 3
        assert a_sum.failed_count == 1
        assert a_sum.prompt_tokens == 90

    asyncio.run(_run_async_batch())


def test_observer_fault_isolation_secret_scrubbing_and_async_cancellation(
    tmp_path: Path,
    invoice_schema_dict: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    doc = tmp_path / "ok.txt"
    doc.write_text("Invoice: INV-2026-001\nVendor: Acme\n", encoding="utf-8")
    strat = DeterministicTestStrategy(_ok_output())
    leaked_secret = "sk-observer-fault-secret-987654"

    class _BrokenSyncObserver:
        def on_event(self, _event: ExtractionEvent) -> None:
            raise RuntimeError(f"Broken observer leaked Bearer {leaked_secret}")

    class _BrokenAsyncObserver:
        async def aon_event(self, _event: ExtractionEvent) -> None:
            raise ValueError(f"Async observer boom {leaked_secret}")

    # 1. Sync batch with fail_fast=True: broken observer NEVER fails extraction or triggers abort
    healthy = InMemoryTelemetryObserver()
    with caplog.at_level(logging.WARNING, logger="structa_maestro"):
        batch = extract_batch(
            [doc, doc],
            invoice_schema_dict,
            strategy=strat,
            options=BatchOptions(max_workers=1, fail_fast=True),
            observers=[_BrokenSyncObserver(), healthy],
        )
    assert batch.summary.completed_count == 2
    assert batch.summary.valid_count == 2
    assert batch.summary.aborted_early is False
    assert healthy.summary().total_extractions == 2
    assert leaked_secret not in caplog.text
    assert "[REDACTED]" in caplog.text

    # 2. Async fault isolation + external asyncio.CancelledError propagation
    async def _run_async() -> None:
        healthy_async = InMemoryTelemetryObserver()
        res = await aextract(
            doc,
            invoice_schema_dict,
            strategy=strat,
            observers=[_BrokenAsyncObserver(), healthy_async],
        )
        assert res.status == ProcessingStatus.COMPLETED
        assert res.validation.status == ValidationStatus.VALID
        assert healthy_async.summary().total_extractions == 1

        # Cancellation inside AsyncExtractionObserver.aon_event MUST propagate (never swallowed)
        entered = asyncio.Event()

        class _BlockingAsyncObserver:
            async def aon_event(self, _event: ExtractionEvent) -> None:
                entered.set()
                await asyncio.sleep(10.0)

        task = asyncio.create_task(
            aextract(
                doc,
                invoice_schema_dict,
                strategy=strat,
                observers=[_BlockingAsyncObserver()],
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run_async())
