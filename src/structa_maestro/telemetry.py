"""Privacy-safe observability models, observer protocols, built-in collectors, and dispatchers."""

from __future__ import annotations

import json
import logging
import math
import statistics
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from structa_maestro.config import get_logger, sanitize_message
from structa_maestro.contracts.enums import ProcessingStatus, ValidationStatus
from structa_maestro.documents.inputs import sanitize_filename
from structa_maestro.exceptions import ConfigurationError

_logger = get_logger(__name__)

_STAGE_ORDER: tuple[str, ...] = ("document_load", "strategy", "finalization")


def utc_now_iso() -> str:
    """Return a UTC ISO-8601 timestamp string."""
    return datetime.now(UTC).isoformat()


def _validate_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"{field_name} must be a non-negative integer.")
    return value


def _validate_optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(value, field_name)


def _validate_non_negative_ms(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{field_name} must be a non-negative finite number.")
    fval = float(value)
    if not math.isfinite(fval) or fval < 0.0:
        raise ConfigurationError(f"{field_name} must be a non-negative finite number.")
    return round(fval, 3)


def _validate_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field_name} must be a non-empty string.")
    return value.strip()


class ExtractionEventKind(StrEnum):
    """Closed lifecycle event enumeration for single-document and batch extraction."""

    EXTRACTION_STARTED = "extraction_started"
    DOCUMENT_LOADED = "document_loaded"
    STRATEGY_COMPLETED = "strategy_completed"
    EXTRACTION_COMPLETED = "extraction_completed"
    BATCH_STARTED = "batch_started"
    BATCH_ITEM_COMPLETED = "batch_item_completed"
    BATCH_COMPLETED = "batch_completed"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Immutable, provider-independent LLM token accounting (`None` = unavailable)."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        _validate_optional_non_negative_int(self.prompt_tokens, "TokenUsage.prompt_tokens")
        _validate_optional_non_negative_int(self.completion_tokens, "TokenUsage.completion_tokens")
        _validate_optional_non_negative_int(self.total_tokens, "TokenUsage.total_tokens")

    def to_dict(self) -> dict[str, int | None]:
        """Return a JSON-safe dictionary representation of token counts."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize token usage to JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def parse_openai_token_usage(body: Any) -> TokenUsage | None:
    """Parse an OpenAI-compatible `usage` payload without fabricating missing counts."""
    if not isinstance(body, Mapping):
        return None
    raw_usage = body.get("usage")
    if not isinstance(raw_usage, Mapping):
        return None

    def _safe_count(key: str) -> int | None:
        val = raw_usage.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            return None
        return val

    prompt = _safe_count("prompt_tokens")
    completion = _safe_count("completion_tokens")
    total = _safe_count("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def merge_token_usage(a: TokenUsage | None, b: TokenUsage | None) -> TokenUsage | None:
    """Cumulatively sum observed non-null token counts across strategy attempts."""
    if a is None:
        return b
    if b is None:
        return a

    def _add(x: int | None, y: int | None) -> int | None:
        if x is None:
            return y
        if y is None:
            return x
        return x + y

    return TokenUsage(
        prompt_tokens=_add(a.prompt_tokens, b.prompt_tokens),
        completion_tokens=_add(a.completion_tokens, b.completion_tokens),
        total_tokens=_add(a.total_tokens, b.total_tokens),
    )


@dataclass(frozen=True, slots=True)
class StageTiming:
    """Immutable duration record for a single pipeline stage."""

    stage: str
    elapsed_ms: float
    succeeded: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _validate_non_empty_str(self.stage, "StageTiming.stage"))
        object.__setattr__(
            self,
            "elapsed_ms",
            _validate_non_negative_ms(self.elapsed_ms, "StageTiming.elapsed_ms"),
        )
        if not isinstance(self.succeeded, bool):
            raise ConfigurationError("StageTiming.succeeded must be a boolean.")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of the stage timing."""
        return {
            "stage": self.stage,
            "elapsed_ms": self.elapsed_ms,
            "succeeded": self.succeeded,
        }


@dataclass(frozen=True, slots=True)
class StageTimingSummary:
    """Immutable aggregated timing statistics for a single pipeline stage (`count >= 1`)."""

    stage: str
    count: int
    total_ms: float
    mean_ms: float
    p50_ms: float
    max_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "stage", _validate_non_empty_str(self.stage, "StageTimingSummary.stage")
        )
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ConfigurationError("StageTimingSummary.count must be an integer >= 1.")
        object.__setattr__(
            self,
            "total_ms",
            _validate_non_negative_ms(self.total_ms, "StageTimingSummary.total_ms"),
        )
        object.__setattr__(
            self, "mean_ms", _validate_non_negative_ms(self.mean_ms, "StageTimingSummary.mean_ms")
        )
        object.__setattr__(
            self, "p50_ms", _validate_non_negative_ms(self.p50_ms, "StageTimingSummary.p50_ms")
        )
        object.__setattr__(
            self, "max_ms", _validate_non_negative_ms(self.max_ms, "StageTimingSummary.max_ms")
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of aggregated stage timings."""
        return {
            "stage": self.stage,
            "count": self.count,
            "total_ms": self.total_ms,
            "mean_ms": self.mean_ms,
            "p50_ms": self.p50_ms,
            "max_ms": self.max_ms,
        }


@dataclass(frozen=True, slots=True)
class StrategyTelemetry:
    """Immutable operational metadata emitted by an extraction strategy alongside StrategyOutput."""

    attempts: int
    retry_count: int = 0
    structured_output_mode_used: str | None = None
    fallback_used: bool = False
    truncated: bool = False
    token_usage: TokenUsage | None = None

    def __post_init__(self) -> None:
        _validate_non_negative_int(self.attempts, "StrategyTelemetry.attempts")
        _validate_non_negative_int(self.retry_count, "StrategyTelemetry.retry_count")
        if self.structured_output_mode_used is not None:
            object.__setattr__(
                self,
                "structured_output_mode_used",
                _validate_non_empty_str(
                    self.structured_output_mode_used,
                    "StrategyTelemetry.structured_output_mode_used",
                ),
            )
        if not isinstance(self.fallback_used, bool):
            raise ConfigurationError("StrategyTelemetry.fallback_used must be a boolean.")
        if not isinstance(self.truncated, bool):
            raise ConfigurationError("StrategyTelemetry.truncated must be a boolean.")
        if self.token_usage is not None and not isinstance(self.token_usage, TokenUsage):
            raise ConfigurationError(
                "StrategyTelemetry.token_usage must be a TokenUsage instance or None."
            )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of strategy telemetry."""
        return {
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "structured_output_mode_used": self.structured_output_mode_used,
            "fallback_used": self.fallback_used,
            "truncated": self.truncated,
            "token_usage": self.token_usage.to_dict() if self.token_usage is not None else None,
        }


@dataclass(frozen=True, slots=True)
class BatchProgress:
    """Immutable progress snapshot attached to batch lifecycle events."""

    batch_id: str
    total_documents: int
    processed_documents: int
    skipped_documents: int
    item_index: int | None = None
    completed_count: int = 0
    partial_count: int = 0
    failed_count: int = 0
    aborted_early: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "batch_id", _validate_non_empty_str(self.batch_id, "BatchProgress.batch_id")
        )
        total = _validate_non_negative_int(self.total_documents, "BatchProgress.total_documents")
        processed = _validate_non_negative_int(
            self.processed_documents, "BatchProgress.processed_documents"
        )
        if processed > total:
            raise ConfigurationError(
                "BatchProgress.processed_documents cannot exceed BatchProgress.total_documents."
            )
        _validate_non_negative_int(self.skipped_documents, "BatchProgress.skipped_documents")
        _validate_optional_non_negative_int(self.item_index, "BatchProgress.item_index")
        _validate_non_negative_int(self.completed_count, "BatchProgress.completed_count")
        _validate_non_negative_int(self.partial_count, "BatchProgress.partial_count")
        _validate_non_negative_int(self.failed_count, "BatchProgress.failed_count")
        if not isinstance(self.aborted_early, bool):
            raise ConfigurationError("BatchProgress.aborted_early must be a boolean.")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of batch progress."""
        return {
            "batch_id": self.batch_id,
            "total_documents": self.total_documents,
            "processed_documents": self.processed_documents,
            "skipped_documents": self.skipped_documents,
            "item_index": self.item_index,
            "completed_count": self.completed_count,
            "partial_count": self.partial_count,
            "failed_count": self.failed_count,
            "aborted_early": self.aborted_early,
        }


@dataclass(frozen=True, slots=True)
class ExtractionEvent:
    """Unified immutable, privacy-safe lifecycle event delivered to telemetry observers."""

    event_kind: ExtractionEventKind
    timestamp: str
    extraction_id: str | None = None
    batch_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    media_type: str | None = None
    schema_id: str | None = None
    schema_version: str | None = None
    strategy_name: str | None = None
    elapsed_ms: float | None = None
    status: ProcessingStatus | None = None
    validation_status: ValidationStatus | None = None
    record_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None
    error_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    stage_timing: StageTiming | None = None
    stage_timings: tuple[StageTiming, ...] = ()
    strategy_telemetry: StrategyTelemetry | None = None
    batch_progress: BatchProgress | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_kind, ExtractionEventKind):
            raise ConfigurationError(
                "ExtractionEvent.event_kind must be an ExtractionEventKind value."
            )
        object.__setattr__(
            self,
            "timestamp",
            _validate_non_empty_str(self.timestamp, "ExtractionEvent.timestamp"),
        )
        if self.filename is not None:
            object.__setattr__(self, "filename", sanitize_filename(self.filename))
        if self.elapsed_ms is not None:
            object.__setattr__(
                self,
                "elapsed_ms",
                _validate_non_negative_ms(self.elapsed_ms, "ExtractionEvent.elapsed_ms"),
            )
        _validate_optional_non_negative_int(self.record_count, "ExtractionEvent.record_count")
        _validate_optional_non_negative_int(self.error_count, "ExtractionEvent.error_count")
        _validate_optional_non_negative_int(self.warning_count, "ExtractionEvent.warning_count")
        object.__setattr__(self, "error_codes", tuple(self.error_codes))
        object.__setattr__(self, "warning_codes", tuple(self.warning_codes))
        object.__setattr__(self, "stage_timings", tuple(self.stage_timings))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, privacy-safe dictionary representation of the event."""
        return {
            "event_kind": self.event_kind.value,
            "timestamp": self.timestamp,
            "extraction_id": self.extraction_id,
            "batch_id": self.batch_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "media_type": self.media_type,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "strategy_name": self.strategy_name,
            "elapsed_ms": self.elapsed_ms,
            "status": self.status.value if self.status is not None else None,
            "validation_status": (
                self.validation_status.value if self.validation_status is not None else None
            ),
            "record_count": self.record_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "error_codes": list(self.error_codes),
            "warning_codes": list(self.warning_codes),
            "stage_timing": (
                self.stage_timing.to_dict() if self.stage_timing is not None else None
            ),
            "stage_timings": [st.to_dict() for st in self.stage_timings],
            "strategy_telemetry": (
                self.strategy_telemetry.to_dict() if self.strategy_telemetry is not None else None
            ),
            "batch_progress": (
                self.batch_progress.to_dict() if self.batch_progress is not None else None
            ),
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the telemetry event to JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class TelemetrySummary:
    """Immutable aggregated telemetry summary across observed extraction events."""

    total_events: int
    total_extractions: int
    completed_count: int
    partial_count: int
    failed_count: int
    valid_count: int
    invalid_count: int
    not_validated_count: int
    skipped_documents: int
    aborted_early_count: int
    stage_timings: Mapping[str, StageTimingSummary] = field(
        default_factory=lambda: MappingProxyType({})
    )
    total_attempts: int = 0
    total_retries: int = 0
    fallback_count: int = 0
    truncated_count: int = 0
    usage_reported_count: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    total_elapsed_ms: float = 0.0

    def __post_init__(self) -> None:
        _validate_non_negative_int(self.total_events, "TelemetrySummary.total_events")
        _validate_non_negative_int(self.total_extractions, "TelemetrySummary.total_extractions")
        _validate_non_negative_int(self.completed_count, "TelemetrySummary.completed_count")
        _validate_non_negative_int(self.partial_count, "TelemetrySummary.partial_count")
        _validate_non_negative_int(self.failed_count, "TelemetrySummary.failed_count")
        _validate_non_negative_int(self.valid_count, "TelemetrySummary.valid_count")
        _validate_non_negative_int(self.invalid_count, "TelemetrySummary.invalid_count")
        _validate_non_negative_int(self.not_validated_count, "TelemetrySummary.not_validated_count")
        _validate_non_negative_int(self.skipped_documents, "TelemetrySummary.skipped_documents")
        _validate_non_negative_int(self.aborted_early_count, "TelemetrySummary.aborted_early_count")
        _validate_non_negative_int(self.total_attempts, "TelemetrySummary.total_attempts")
        _validate_non_negative_int(self.total_retries, "TelemetrySummary.total_retries")
        _validate_non_negative_int(self.fallback_count, "TelemetrySummary.fallback_count")
        _validate_non_negative_int(self.truncated_count, "TelemetrySummary.truncated_count")
        _validate_non_negative_int(
            self.usage_reported_count, "TelemetrySummary.usage_reported_count"
        )
        _validate_optional_non_negative_int(self.prompt_tokens, "TelemetrySummary.prompt_tokens")
        _validate_optional_non_negative_int(
            self.completion_tokens, "TelemetrySummary.completion_tokens"
        )
        _validate_optional_non_negative_int(self.total_tokens, "TelemetrySummary.total_tokens")
        object.__setattr__(
            self,
            "total_elapsed_ms",
            _validate_non_negative_ms(self.total_elapsed_ms, "TelemetrySummary.total_elapsed_ms"),
        )
        object.__setattr__(self, "stage_timings", MappingProxyType(dict(self.stage_timings)))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, privacy-safe dictionary representation of the telemetry summary."""
        return {
            "total_events": self.total_events,
            "total_extractions": self.total_extractions,
            "completed_count": self.completed_count,
            "partial_count": self.partial_count,
            "failed_count": self.failed_count,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "not_validated_count": self.not_validated_count,
            "skipped_documents": self.skipped_documents,
            "aborted_early_count": self.aborted_early_count,
            "stage_timings": {k: v.to_dict() for k, v in self.stage_timings.items()},
            "total_attempts": self.total_attempts,
            "total_retries": self.total_retries,
            "fallback_count": self.fallback_count,
            "truncated_count": self.truncated_count,
            "usage_reported_count": self.usage_reported_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_elapsed_ms": self.total_elapsed_ms,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the telemetry summary to JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@runtime_checkable
class ExtractionObserver(Protocol):
    """Narrow synchronous observer protocol receiving privacy-safe ExtractionEvent notifications."""

    def on_event(self, event: ExtractionEvent) -> None:
        """Handle an emitted extraction lifecycle event."""
        ...


@runtime_checkable
class AsyncExtractionObserver(Protocol):
    """Narrow asyncio observer protocol receiving privacy-safe ExtractionEvent notifications."""

    async def aon_event(self, event: ExtractionEvent) -> None:
        """Asynchronously handle an emitted extraction lifecycle event."""
        ...


AnyExtractionObserver = ExtractionObserver | AsyncExtractionObserver


class InMemoryTelemetryObserver:
    """Thread-safe and asyncio-safe in-memory telemetry collector and summary aggregator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[ExtractionEvent] = []

    def on_event(self, event: ExtractionEvent) -> None:
        """Record an immutable ExtractionEvent safely under a lock."""
        with self._lock:
            self._events.append(event)

    def events(self) -> tuple[ExtractionEvent, ...]:
        """Return an immutable snapshot of all events recorded since creation or last reset()."""
        with self._lock:
            return tuple(self._events)

    def reset(self) -> None:
        """Clear all recorded events safely."""
        with self._lock:
            self._events.clear()

    def summary(self) -> TelemetrySummary:
        """Compute a deterministic aggregated TelemetrySummary from recorded events."""
        snapshot = self.events()
        total_extractions = 0
        completed_count = 0
        partial_count = 0
        failed_count = 0
        valid_count = 0
        invalid_count = 0
        not_validated_count = 0
        skipped_documents = 0
        aborted_early_count = 0

        stage_samples: dict[str, list[float]] = {}
        total_attempts = 0
        total_retries = 0
        fallback_count = 0
        truncated_count = 0
        usage_reported_count = 0
        cumulative_usage: TokenUsage | None = None
        total_elapsed_ms = 0.0

        for ev in snapshot:
            if ev.event_kind == ExtractionEventKind.EXTRACTION_COMPLETED:
                total_extractions += 1
                if ev.status == ProcessingStatus.COMPLETED:
                    completed_count += 1
                elif ev.status == ProcessingStatus.PARTIAL:
                    partial_count += 1
                elif ev.status == ProcessingStatus.FAILED:
                    failed_count += 1

                if ev.validation_status == ValidationStatus.VALID:
                    valid_count += 1
                elif ev.validation_status == ValidationStatus.INVALID:
                    invalid_count += 1
                elif ev.validation_status == ValidationStatus.NOT_VALIDATED:
                    not_validated_count += 1

                for st in ev.stage_timings:
                    stage_samples.setdefault(st.stage, []).append(st.elapsed_ms)

                if ev.batch_id is None and ev.elapsed_ms is not None:
                    total_elapsed_ms += ev.elapsed_ms

            elif ev.event_kind == ExtractionEventKind.STRATEGY_COMPLETED:
                st_tel = ev.strategy_telemetry
                if st_tel is not None:
                    total_attempts += st_tel.attempts
                    total_retries += st_tel.retry_count
                    if st_tel.fallback_used:
                        fallback_count += 1
                    if st_tel.truncated:
                        truncated_count += 1
                    if st_tel.token_usage is not None:
                        usage_reported_count += 1
                        cumulative_usage = merge_token_usage(cumulative_usage, st_tel.token_usage)

            elif ev.event_kind == ExtractionEventKind.BATCH_COMPLETED:
                if ev.batch_progress is not None:
                    skipped_documents += ev.batch_progress.skipped_documents
                    if ev.batch_progress.aborted_early:
                        aborted_early_count += 1
                if ev.elapsed_ms is not None:
                    total_elapsed_ms += ev.elapsed_ms

        ordered_stages: list[str] = [s for s in _STAGE_ORDER if s in stage_samples]
        ordered_stages.extend(sorted(s for s in stage_samples if s not in _STAGE_ORDER))

        stage_summaries: dict[str, StageTimingSummary] = {}
        for stage_name in ordered_stages:
            samples = stage_samples[stage_name]
            if not samples:
                continue
            stage_total = sum(samples)
            stage_summaries[stage_name] = StageTimingSummary(
                stage=stage_name,
                count=len(samples),
                total_ms=round(stage_total, 3),
                mean_ms=round(stage_total / len(samples), 3),
                p50_ms=round(float(statistics.median(samples)), 3),
                max_ms=round(max(samples), 3),
            )

        return TelemetrySummary(
            total_events=len(snapshot),
            total_extractions=total_extractions,
            completed_count=completed_count,
            partial_count=partial_count,
            failed_count=failed_count,
            valid_count=valid_count,
            invalid_count=invalid_count,
            not_validated_count=not_validated_count,
            skipped_documents=skipped_documents,
            aborted_early_count=aborted_early_count,
            stage_timings=stage_summaries,
            total_attempts=total_attempts,
            total_retries=total_retries,
            fallback_count=fallback_count,
            truncated_count=truncated_count,
            usage_reported_count=usage_reported_count,
            prompt_tokens=cumulative_usage.prompt_tokens if cumulative_usage is not None else None,
            completion_tokens=(
                cumulative_usage.completion_tokens if cumulative_usage is not None else None
            ),
            total_tokens=cumulative_usage.total_tokens if cumulative_usage is not None else None,
            total_elapsed_ms=round(total_elapsed_ms, 3),
        )


class StructuredLoggingObserver:
    """Zero-dependency observer emitting privacy-safe JSON telemetry records via stdlib logging."""

    def __init__(
        self,
        *,
        logger_name: str = "structa_maestro.telemetry",
        level: int = logging.INFO,
    ) -> None:
        self._logger_name = _validate_non_empty_str(
            logger_name, "StructuredLoggingObserver.logger_name"
        )
        if isinstance(level, bool) or not isinstance(level, int):
            raise ConfigurationError("StructuredLoggingObserver.level must be an integer.")
        self._level = level
        self._log = get_logger(self._logger_name)

    @property
    def logger_name(self) -> str:
        """Configured target logger name."""
        return self._logger_name

    @property
    def level(self) -> int:
        """Configured logging level."""
        return self._level

    def on_event(self, event: ExtractionEvent) -> None:
        """Emit a compact, privacy-safe JSON log record for the event."""
        self._log.log(
            self._level,
            event.to_json(),
            extra={"structa_maestro_event": event.to_dict()},
        )


def validate_observers(
    observers: Sequence[Any] | None,
    *,
    require_sync: bool = False,
) -> tuple[AnyExtractionObserver, ...]:
    """Eagerly validate an optional observer sequence before any document I/O or strategy call."""
    if observers is None:
        return ()
    if isinstance(observers, (str, bytes, bytearray)) or not isinstance(observers, Sequence):
        raise ConfigurationError(
            "observers must be a sequence of ExtractionObserver or AsyncExtractionObserver "
            "instances, or None."
        )

    validated: list[AnyExtractionObserver] = []
    for idx, obs in enumerate(observers):
        is_sync = isinstance(obs, ExtractionObserver)
        is_async = isinstance(obs, AsyncExtractionObserver)
        if not is_sync and not is_async:
            raise ConfigurationError(
                f"Observer at index {idx} ({type(obs).__name__}) must implement "
                "ExtractionObserver (on_event) or AsyncExtractionObserver (aon_event)."
            )
        if require_sync and not is_sync:
            raise ConfigurationError(
                f"Observer at index {idx} ({type(obs).__name__}) implements "
                "AsyncExtractionObserver only; use 'await aextract(...)' / "
                "'await aextract_batch(...)' or supply a synchronous ExtractionObserver."
            )
        validated.append(obs)
    return tuple(validated)


def dispatch_event_sync(
    observers: tuple[AnyExtractionObserver, ...],
    event: ExtractionEvent,
) -> None:
    """Deliver an event to sync observers with strict fault isolation and secret scrubbing."""
    for obs in observers:
        if not isinstance(obs, ExtractionObserver):
            continue
        try:
            obs.on_event(event)
        except Exception as exc:
            safe_detail = sanitize_message(f"{type(exc).__name__}: {exc}")
            _logger.warning(
                "Telemetry observer %s raised an error on %s: %s",
                type(obs).__name__,
                event.event_kind.value,
                safe_detail,
            )


async def dispatch_event_async(
    observers: tuple[AnyExtractionObserver, ...],
    event: ExtractionEvent,
) -> None:
    """Deliver an event to async/sync observers without swallowing BaseException/CancelledError."""
    for obs in observers:
        try:
            if isinstance(obs, AsyncExtractionObserver):
                await obs.aon_event(event)
            elif isinstance(obs, ExtractionObserver):
                obs.on_event(event)
        except Exception as exc:
            # BaseException (including asyncio.CancelledError) intentionally propagates immediately
            safe_detail = sanitize_message(f"{type(exc).__name__}: {exc}")
            _logger.warning(
                "Telemetry observer %s raised an error on %s: %s",
                type(obs).__name__,
                event.event_kind.value,
                safe_detail,
            )
