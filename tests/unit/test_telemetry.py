"""Unit tests for privacy-safe telemetry models, usage parsers, observers, and validation (T006)."""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from structa_maestro import (
    BatchProgress,
    ConfigurationError,
    ExtractionEvent,
    ExtractionEventKind,
    InMemoryTelemetryObserver,
    ProcessingStatus,
    StageTiming,
    StageTimingSummary,
    StrategyTelemetry,
    StructuredLoggingObserver,
    TelemetrySummary,
    TokenUsage,
    ValidationStatus,
)
from structa_maestro.telemetry import (
    merge_token_usage,
    parse_openai_token_usage,
    utc_now_iso,
    validate_observers,
)


def test_token_usage_parsing_merging_and_validation() -> None:
    u1 = TokenUsage(prompt_tokens=100, completion_tokens=25, total_tokens=125)
    assert u1.to_dict() == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
    }
    assert '"total_tokens": 125' in u1.to_json()
    with pytest.raises(FrozenInstanceError):
        u1.total_tokens = 999  # type: ignore[misc]

    # Zero tokens are distinct from None
    u_zero = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    assert u_zero.prompt_tokens == 0

    for bad in (-1, True, False, "10", 1.5):
        with pytest.raises(ConfigurationError):
            TokenUsage(prompt_tokens=bad)  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError):
            TokenUsage(completion_tokens=bad)  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError):
            TokenUsage(total_tokens=bad)  # type: ignore[arg-type]

    # parse_openai_token_usage never synthesizes missing total_tokens and ignores invalid entries
    assert parse_openai_token_usage("not-a-dict") is None
    assert parse_openai_token_usage({}) is None
    assert parse_openai_token_usage({"usage": "bad"}) is None
    assert parse_openai_token_usage({"usage": {"prompt_tokens": -5, "total_tokens": True}}) is None

    partial = parse_openai_token_usage({"usage": {"prompt_tokens": 42, "completion_tokens": 8}})
    assert partial == TokenUsage(prompt_tokens=42, completion_tokens=8, total_tokens=None)

    # merge_token_usage accumulates observed counts without synthesizing missing fields
    assert merge_token_usage(None, None) is None
    assert merge_token_usage(partial, None) == partial
    assert merge_token_usage(None, partial) == partial
    merged = merge_token_usage(
        TokenUsage(prompt_tokens=42, completion_tokens=8, total_tokens=None),
        TokenUsage(prompt_tokens=58, completion_tokens=12, total_tokens=70),
    )
    assert merged == TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=70)


def test_stage_timing_strategy_telemetry_batch_progress_and_event_validation() -> None:
    st = StageTiming(stage="strategy", elapsed_ms=12.3456, succeeded=True)
    assert st.elapsed_ms == 12.346
    assert st.to_dict() == {"stage": "strategy", "elapsed_ms": 12.346, "succeeded": True}

    with pytest.raises(ConfigurationError):
        StageTiming(stage="   ", elapsed_ms=1.0, succeeded=True)
    with pytest.raises(ConfigurationError):
        StageTiming(stage="strategy", elapsed_ms=-0.1, succeeded=True)
    with pytest.raises(ConfigurationError):
        StageTiming(stage="strategy", elapsed_ms=float("nan"), succeeded=True)
    with pytest.raises(ConfigurationError):
        StageTiming(stage="strategy", elapsed_ms=True, succeeded=True)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        StageTiming(stage="strategy", elapsed_ms=1.0, succeeded="yes")  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError):
        StageTimingSummary(
            stage="strategy", count=0, total_ms=0.0, mean_ms=0.0, p50_ms=0.0, max_ms=0.0
        )

    tel = StrategyTelemetry(
        attempts=2,
        retry_count=1,
        structured_output_mode_used="json_object",
        fallback_used=True,
        truncated=True,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    assert tel.to_dict()["fallback_used"] is True
    with pytest.raises(ConfigurationError):
        StrategyTelemetry(attempts=-1)
    with pytest.raises(ConfigurationError):
        StrategyTelemetry(attempts=1, structured_output_mode_used="  ")
    with pytest.raises(ConfigurationError):
        StrategyTelemetry(attempts=1, token_usage="bad")  # type: ignore[arg-type]

    bp = BatchProgress(
        batch_id="batch-1",
        total_documents=3,
        processed_documents=2,
        skipped_documents=0,
        item_index=1,
        completed_count=1,
        failed_count=1,
    )
    assert bp.to_dict()["item_index"] == 1
    with pytest.raises(ConfigurationError):
        BatchProgress(
            batch_id="batch-1",
            total_documents=2,
            processed_documents=3,
            skipped_documents=0,
        )

    # ExtractionEvent sanitizes host paths to basename-only
    ev = ExtractionEvent(
        event_kind=ExtractionEventKind.EXTRACTION_STARTED,
        timestamp=utc_now_iso(),
        extraction_id="ext-1",
        filename=r"C:\confidential\hr\salary.pdf",
    )
    assert ev.filename == "salary.pdf"
    assert "confidential" not in ev.to_json()

    with pytest.raises(ConfigurationError):
        ExtractionEvent(event_kind="bad", timestamp=utc_now_iso())  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ExtractionEvent(event_kind=ExtractionEventKind.EXTRACTION_STARTED, timestamp="  ")


def test_in_memory_observer_median_p50_multi_run_reset_and_immutable_summary() -> None:
    obs = InMemoryTelemetryObserver()
    assert obs.events() == ()
    empty = obs.summary()
    assert empty.total_events == 0
    assert empty.stage_timings == {}
    assert empty.prompt_tokens is None
    assert empty.total_tokens is None

    # Record 4 extractions (even count -> p50_ms is exact arithmetic mean of middle two!)
    # Standalone durations: 10.0, 20.0, 40.0, 50.0 -> median = 30.0, mean = 30.0, max = 50.0
    for idx, strat_ms in enumerate((10.0, 50.0, 20.0, 40.0)):
        obs.on_event(
            ExtractionEvent(
                event_kind=ExtractionEventKind.STRATEGY_COMPLETED,
                timestamp=utc_now_iso(),
                extraction_id=f"ext-{idx}",
                strategy_telemetry=StrategyTelemetry(
                    attempts=1 + (idx % 2),
                    retry_count=idx % 2,
                    fallback_used=(idx == 1),
                    truncated=(idx == 2),
                    token_usage=(
                        TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
                        if idx < 2
                        else None
                    ),
                ),
            )
        )
        obs.on_event(
            ExtractionEvent(
                event_kind=ExtractionEventKind.EXTRACTION_COMPLETED,
                timestamp=utc_now_iso(),
                extraction_id=f"ext-{idx}",
                elapsed_ms=strat_ms + 5.0,
                status=ProcessingStatus.COMPLETED,
                validation_status=ValidationStatus.VALID,
                stage_timings=(
                    StageTiming("document_load", 2.0, True),
                    StageTiming("strategy", strat_ms, True),
                    StageTiming("finalization", 3.0, True),
                ),
            )
        )

    summary = obs.summary()
    assert isinstance(summary, TelemetrySummary)
    assert summary.total_events == 8
    assert summary.total_extractions == 4
    assert summary.completed_count == 4
    assert summary.valid_count == 4
    assert summary.stage_timings["strategy"].count == 4
    assert summary.stage_timings["strategy"].p50_ms == 30.0
    assert summary.stage_timings["strategy"].mean_ms == 30.0
    assert summary.stage_timings["strategy"].max_ms == 50.0
    assert summary.total_attempts == 6
    assert summary.total_retries == 2
    assert summary.fallback_count == 1
    assert summary.truncated_count == 1
    assert summary.usage_reported_count == 2
    assert summary.prompt_tokens == 200
    assert summary.completion_tokens == 100
    assert summary.total_tokens == 300
    assert summary.total_elapsed_ms == 140.0
    # MappingProxyType prevents mutation
    with pytest.raises(TypeError):
        summary.stage_timings["strategy"] = StageTimingSummary(  # type: ignore[index]
            stage="strategy", count=1, total_ms=1.0, mean_ms=1.0, p50_ms=1.0, max_ms=1.0
        )

    # Reset clears retained events
    obs.reset()
    assert obs.events() == ()
    assert obs.summary().total_events == 0


def test_structured_logging_observer_and_observer_validation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(ConfigurationError):
        StructuredLoggingObserver(logger_name="   ")
    with pytest.raises(ConfigurationError):
        StructuredLoggingObserver(level=True)  # type: ignore[arg-type]

    log_obs = StructuredLoggingObserver(logger_name="structa_maestro.test_obs", level=logging.INFO)
    assert log_obs.logger_name == "structa_maestro.test_obs"
    assert log_obs.level == logging.INFO

    ev = ExtractionEvent(
        event_kind=ExtractionEventKind.EXTRACTION_STARTED,
        timestamp=utc_now_iso(),
        extraction_id="ext-log-1",
        schema_id="inv",
        schema_version="1.0",
    )
    with caplog.at_level(logging.INFO, logger="structa_maestro.test_obs"):
        log_obs.on_event(ev)
    assert len(caplog.records) == 1
    assert '"event_kind": "extraction_started"' in caplog.records[0].getMessage()
    assert caplog.records[0].structa_maestro_event["extraction_id"] == "ext-log-1"  # type: ignore[attr-defined]

    class _AsyncOnlyObserver:
        async def aon_event(self, event: ExtractionEvent) -> None:
            _ = event

    class _DualObserver:
        def on_event(self, event: ExtractionEvent) -> None:
            _ = event

        async def aon_event(self, event: ExtractionEvent) -> None:
            _ = event

    assert validate_observers(None, require_sync=True) == ()
    assert len(validate_observers([log_obs, _DualObserver()], require_sync=True)) == 2
    assert len(validate_observers([_AsyncOnlyObserver()], require_sync=False)) == 1

    with pytest.raises(ConfigurationError):
        validate_observers([_AsyncOnlyObserver()], require_sync=True)
    for bad_seq in ("not-a-seq", b"bytes", 42):
        with pytest.raises(ConfigurationError):
            validate_observers(bad_seq)  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        validate_observers([object()])  # type: ignore[list-item]
    bad_any: Any = [123]
    with pytest.raises(ConfigurationError):
        validate_observers(bad_any)
