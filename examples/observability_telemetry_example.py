"""Offline runnable example: privacy-safe observability hooks, stage timings & token accounting."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from structa_maestro import (
    AsyncDeterministicTestStrategy,
    BatchOptions,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionEvent,
    ExtractionEventKind,
    ExtractionSchema,
    InMemoryTelemetryObserver,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    StrategyTelemetry,
    TokenUsage,
    aextract_batch,
    extract,
)


class LiveBatchMetricsObserver:
    """Example custom observer (adaptable to Prometheus counters or OpenTelemetry spans)."""

    def __init__(self) -> None:
        self.progress_lines: list[str] = []

    async def aon_event(self, event: ExtractionEvent) -> None:
        if event.event_kind == ExtractionEventKind.BATCH_ITEM_COMPLETED and event.batch_progress:
            bp = event.batch_progress
            self.progress_lines.append(
                f"[{bp.processed_documents}/{bp.total_documents}] "
                f"item={bp.item_index} file={event.filename} "
                f"status={event.status.value if event.status else 'unknown'} "
                f"elapsed_ms={event.elapsed_ms}"
            )


def _mock_output(document: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
    invoice_no = "INV-2026-901"
    for line in document.full_text.splitlines():
        if line.startswith("Invoice Number:"):
            invoice_no = line.split(":", 1)[1].strip()
    return StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "invoice_number": ProposedField(
                        value=invoice_no,
                        confidence=0.98,
                        evidence=(ProposedEvidence(quote=f"Invoice Number: {invoice_no}"),),
                    ),
                    "issue_date": ProposedField(value=date(2026, 4, 10), confidence=0.95),
                    "total_amount": ProposedField(value=Decimal("420.00"), confidence=0.97),
                }
            ),
        ),
        telemetry=StrategyTelemetry(
            attempts=1,
            retry_count=0,
            structured_output_mode_used="json_schema",
            token_usage=TokenUsage(prompt_tokens=140, completion_tokens=35, total_tokens=175),
        ),
    )


async def _async_mock_output(
    document: PreparedDocument,
    schema: ExtractionSchema,
) -> StrategyOutput:
    await asyncio.sleep(0.005)
    return _mock_output(document, schema)


async def main() -> None:
    schema = ExtractionSchema.from_dict(
        {
            "schema_id": "invoice-telemetry-demo",
            "schema_version": "1.0",
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "issue_date": {"type": "date", "required": True},
                "total_amount": {"type": "decimal", "required": True},
            },
        }
    )

    # 1. Single-document synchronous extraction with InMemoryTelemetryObserver
    single_obs = InMemoryTelemetryObserver()
    single_doc = DocumentInput.from_text(
        "Invoice Number: INV-2026-901\nIssue Date: 2026-04-10\nTotal: 420.00\n",
        filename="/private/ignored/invoice_901.txt",
    )
    single_res = extract(
        single_doc,
        schema,
        strategy=DeterministicTestStrategy(_mock_output),
        observers=[single_obs],
    )
    print(f"Single extraction: status={single_res.status.value} id={single_res.extraction_id}")
    print(f"Single stages: {list(single_obs.summary().stage_timings.keys())}")

    # 2. Multi-worker async batch with live progress observer + aggregated telemetry collector
    batch_obs = InMemoryTelemetryObserver()
    live_obs = LiveBatchMetricsObserver()
    docs = [
        DocumentInput.from_text(
            f"Invoice Number: INV-2026-90{i}\nIssue Date: 2026-04-10\nTotal: 420.00\n",
            filename=f"batch_invoice_{i}.txt",
        )
        for i in (2, 3, 4)
    ]
    batch_res = await aextract_batch(
        docs,
        schema,
        strategy=AsyncDeterministicTestStrategy(_async_mock_output),
        options=BatchOptions(max_workers=2),
        observers=[batch_obs, live_obs],
    )
    print(f"Batch {batch_res.batch_id}: {batch_res.summary.completed_count}/3 completed")
    for line in live_obs.progress_lines:
        print(f"  progress -> {line}")
    print("Privacy-Safe Telemetry Summary:")
    print(batch_obs.summary().to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
