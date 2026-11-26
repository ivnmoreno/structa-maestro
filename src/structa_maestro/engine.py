"""Main ExtractionEngine, extract/aextract & batch orchestration with stage failure isolation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from structa_maestro.batch import (
    BatchDocumentInput,
    BatchExtractionResult,
    BatchOptions,
    _PreparedBatchItem,
    normalize_batch_inputs,
    prepare_batch_items,
    run_async_batch_extraction,
    run_batch_extraction,
)
from structa_maestro.builder.result_builder import ResultBuilder
from structa_maestro.confidence.calibration import ConfidenceCalibrator, validate_calibrator
from structa_maestro.config import DocumentLimits, get_logger, sanitize_message
from structa_maestro.contracts.enums import IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, ExtractionResult, SchemaMetadata
from structa_maestro.documents.inputs import DocumentInput, sanitize_filename
from structa_maestro.documents.loader import (
    DocumentLoadResult,
    load_document,
    load_document_input,
)
from structa_maestro.exceptions import ConfigurationError, SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import (
    AsyncExtractionStrategy,
    ExtractionStrategy,
    StrategyOutput,
)
from structa_maestro.telemetry import (
    AnyExtractionObserver,
    BatchProgress,
    ExtractionEvent,
    ExtractionEventKind,
    ExtractionObserver,
    StageTiming,
    StrategyTelemetry,
    dispatch_event_async,
    dispatch_event_sync,
    utc_now_iso,
    validate_observers,
)

_logger = get_logger(__name__)

AnyExtractionStrategy = ExtractionStrategy | AsyncExtractionStrategy


def _coerce_schema(schema: ExtractionSchema | Mapping[str, Any]) -> ExtractionSchema:
    if isinstance(schema, ExtractionSchema):
        return schema
    if isinstance(schema, Mapping):
        return ExtractionSchema.from_dict(schema)
    raise SchemaDefinitionError(
        f"Expected ExtractionSchema or mapping schema definition, got {type(schema).__name__}."
    )


def _validate_document_id_override(document_id: str | None) -> None:
    if document_id is not None and (not isinstance(document_id, str) or not document_id.strip()):
        raise ConfigurationError("document_id must be a non-empty string or None.")


def _initial_doc_meta(
    document: Any,
    document_id: str | None,
) -> tuple[str | None, str | None]:
    clean_id = document_id.strip() if isinstance(document_id, str) and document_id.strip() else None
    if isinstance(document, DocumentInput):
        return clean_id or document.document_id, document.filename
    if isinstance(document, (str, Path)):
        raw_name = Path(document).name if str(document).strip() else None
        safe_name = sanitize_filename(raw_name) if raw_name is not None else None
        return clean_id, safe_name
    return clean_id, None


class ExtractionEngine:
    """Stateless structured extraction orchestrator (sync and asyncio) isolating all stages."""

    def __init__(
        self,
        strategy: AnyExtractionStrategy,
        *,
        limits: DocumentLimits | None = None,
        result_builder: ResultBuilder | None = None,
        observers: Sequence[AnyExtractionObserver] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> None:
        if not isinstance(strategy, (ExtractionStrategy, AsyncExtractionStrategy)):
            raise ConfigurationError(
                "ExtractionEngine requires a strategy implementing ExtractionStrategy "
                "or AsyncExtractionStrategy."
            )
        if limits is not None and not isinstance(limits, DocumentLimits):
            raise ConfigurationError("limits must be a DocumentLimits instance or None.")
        if result_builder is not None and calibrator is not None:
            raise ConfigurationError(
                "Cannot pass both result_builder and calibrator to ExtractionEngine."
            )
        validated_calibrator = validate_calibrator(calibrator)
        self._strategy: AnyExtractionStrategy = strategy
        self._limits = limits or DocumentLimits()
        self._builder = result_builder or ResultBuilder(calibrator=validated_calibrator)
        self._observers: tuple[AnyExtractionObserver, ...] = validate_observers(
            observers,
            require_sync=False,
        )

    @property
    def limits(self) -> DocumentLimits:
        """Configured document safety limits."""
        return self._limits

    def _require_sync_strategy(self) -> ExtractionStrategy:
        if not isinstance(self._strategy, ExtractionStrategy):
            raise ConfigurationError(
                f"Strategy {self._strategy.name!r} implements AsyncExtractionStrategy only; "
                "use 'await engine.aextract(...)' or 'await engine.aextract_batch(...)' instead."
            )
        return self._strategy

    def _compose_observers(
        self,
        call_observers: Sequence[Any] | None,
        *,
        require_sync: bool,
    ) -> tuple[AnyExtractionObserver, ...]:
        if require_sync and self._observers:
            validate_observers(self._observers, require_sync=True)
        validated_call = validate_observers(call_observers, require_sync=require_sync)
        if not validated_call:
            return self._observers
        return self._observers + validated_call

    def _finalize_from_strategy_output(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        strategy_output: StrategyOutput,
        *,
        extraction_id: str | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        if load_result.document is None:
            return self._build_load_failure(
                load_result,
                parsed_schema,
                extraction_id=extraction_id,
            )
        try:
            return self._builder.build(
                document=load_result.document,
                schema=parsed_schema,
                strategy_output=strategy_output,
                document_warnings=load_result.warnings,
                extraction_id=extraction_id,
                calibrator=calibrator,
            )
        except Exception as exc:
            safe_msg = sanitize_message(
                f"Canonical result assembly failed unexpectedly: {type(exc).__name__}"
            )
            return self._builder.build_failed(
                schema=parsed_schema,
                source=load_result.source,
                errors=[
                    DiagnosticIssue(
                        code="result_assembly_error",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.VALIDATION,
                        message=safe_msg,
                    )
                ],
                warnings=load_result.warnings,
                extraction_id=extraction_id,
            )

    def _build_load_failure(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        *,
        extraction_id: str | None = None,
    ) -> ExtractionResult:
        _logger.debug(
            "Document loading failed for document_id=%s with %d error(s)",
            load_result.source.document_id,
            len(load_result.errors),
        )
        return self._builder.build_failed(
            schema=parsed_schema,
            source=load_result.source,
            errors=load_result.errors
            or [
                DiagnosticIssue(
                    code="document_processing_failed",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.DOCUMENT_PROCESSING,
                    message="Document processing failed without producing prepared text.",
                )
            ],
            warnings=load_result.warnings,
            extraction_id=extraction_id,
        )

    def _build_invalid_input_failure(
        self,
        parsed_schema: ExtractionSchema,
        *,
        extraction_id: str | None = None,
    ) -> ExtractionResult:
        return self._builder.build_failed(
            schema=parsed_schema,
            source=None,
            errors=[
                DiagnosticIssue(
                    code="invalid_input",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.INPUT,
                    message=(
                        "Document input must be a file path (str or pathlib.Path) "
                        "or a DocumentInput instance."
                    ),
                )
            ],
            extraction_id=extraction_id,
        )

    def _build_strategy_failure(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        exc: Exception,
        *,
        extraction_id: str | None = None,
    ) -> ExtractionResult:
        safe_msg = sanitize_message(
            f"Extraction strategy '{self._strategy.name}' failed: {type(exc).__name__}"
        )
        _logger.debug(
            "Strategy error for document_id=%s: %s",
            load_result.source.document_id,
            safe_msg,
        )
        return self._builder.build_failed(
            schema=parsed_schema,
            source=load_result.source,
            errors=[
                DiagnosticIssue(
                    code="extraction_strategy_error",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.EXTRACTION,
                    message=safe_msg,
                )
            ],
            warnings=load_result.warnings,
            extraction_id=extraction_id,
        )

    def _make_started_event(
        self,
        *,
        extraction_id: str,
        batch_id: str | None,
        document_id: str | None,
        filename: str | None,
        schema: ExtractionSchema,
    ) -> ExtractionEvent:
        return ExtractionEvent(
            event_kind=ExtractionEventKind.EXTRACTION_STARTED,
            timestamp=utc_now_iso(),
            extraction_id=extraction_id,
            batch_id=batch_id,
            document_id=document_id,
            filename=filename,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            strategy_name=self._strategy.name,
        )

    def _make_document_loaded_event(
        self,
        *,
        extraction_id: str,
        batch_id: str | None,
        load_result: DocumentLoadResult | None,
        schema: ExtractionSchema,
        stage_timing: StageTiming,
        error_codes: tuple[str, ...],
        warning_codes: tuple[str, ...],
    ) -> ExtractionEvent:
        src = load_result.source if load_result is not None else None
        return ExtractionEvent(
            event_kind=ExtractionEventKind.DOCUMENT_LOADED,
            timestamp=utc_now_iso(),
            extraction_id=extraction_id,
            batch_id=batch_id,
            document_id=src.document_id if src is not None else None,
            filename=src.filename if src is not None else None,
            media_type=src.media_type if src is not None else None,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            strategy_name=self._strategy.name,
            elapsed_ms=stage_timing.elapsed_ms,
            error_count=len(error_codes),
            warning_count=len(warning_codes),
            error_codes=error_codes,
            warning_codes=warning_codes,
            stage_timing=stage_timing,
        )

    def _make_strategy_completed_event(
        self,
        *,
        extraction_id: str,
        batch_id: str | None,
        load_result: DocumentLoadResult,
        schema: ExtractionSchema,
        stage_timing: StageTiming,
        strategy_telemetry: StrategyTelemetry | None,
        error_codes: tuple[str, ...],
        warning_codes: tuple[str, ...],
    ) -> ExtractionEvent:
        src = load_result.source
        return ExtractionEvent(
            event_kind=ExtractionEventKind.STRATEGY_COMPLETED,
            timestamp=utc_now_iso(),
            extraction_id=extraction_id,
            batch_id=batch_id,
            document_id=src.document_id,
            filename=src.filename,
            media_type=src.media_type,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            strategy_name=self._strategy.name,
            elapsed_ms=stage_timing.elapsed_ms,
            error_count=len(error_codes),
            warning_count=len(warning_codes),
            error_codes=error_codes,
            warning_codes=warning_codes,
            stage_timing=stage_timing,
            strategy_telemetry=strategy_telemetry,
        )

    def _make_extraction_completed_event(
        self,
        *,
        result: ExtractionResult,
        batch_id: str | None,
        schema: ExtractionSchema,
        elapsed_ms: float,
        stage_timings: tuple[StageTiming, ...],
        strategy_telemetry: StrategyTelemetry | None,
    ) -> ExtractionEvent:
        src = result.source
        return ExtractionEvent(
            event_kind=ExtractionEventKind.EXTRACTION_COMPLETED,
            timestamp=utc_now_iso(),
            extraction_id=result.extraction_id,
            batch_id=batch_id,
            document_id=src.document_id if src is not None else None,
            filename=src.filename if src is not None else None,
            media_type=src.media_type if src is not None else None,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            strategy_name=self._strategy.name,
            elapsed_ms=elapsed_ms,
            status=result.status,
            validation_status=result.validation.status,
            record_count=len(result.records),
            error_count=len(result.errors),
            warning_count=len(result.warnings),
            error_codes=tuple(e.code for e in result.errors),
            warning_codes=tuple(w.code for w in result.warnings),
            stage_timings=stage_timings,
            strategy_telemetry=strategy_telemetry,
        )

    def _make_batch_item_completed_event(
        self,
        *,
        result: ExtractionResult,
        batch_id: str,
        schema: ExtractionSchema,
        elapsed_ms: float,
        progress: BatchProgress,
    ) -> ExtractionEvent:
        src = result.source
        return ExtractionEvent(
            event_kind=ExtractionEventKind.BATCH_ITEM_COMPLETED,
            timestamp=utc_now_iso(),
            extraction_id=result.extraction_id,
            batch_id=batch_id,
            document_id=src.document_id if src is not None else None,
            filename=src.filename if src is not None else None,
            media_type=src.media_type if src is not None else None,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            strategy_name=self._strategy.name,
            elapsed_ms=elapsed_ms,
            status=result.status,
            validation_status=result.validation.status,
            record_count=len(result.records),
            error_count=len(result.errors),
            warning_count=len(result.warnings),
            error_codes=tuple(e.code for e in result.errors),
            warning_codes=tuple(w.code for w in result.warnings),
            batch_progress=progress,
        )

    def _extract_single_sync(
        self,
        document: str | Path | DocumentInput,
        parsed_schema: ExtractionSchema,
        sync_strategy: ExtractionStrategy,
        *,
        document_id: str | None,
        observers: tuple[AnyExtractionObserver, ...],
        batch_id: str | None = None,
        preloaded_error: DocumentLoadResult | None = None,
        item_timings: dict[str, float] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        emit = bool(observers)
        extraction_id = str(uuid4()) if emit else None
        op_started = time.perf_counter()

        if emit and extraction_id is not None:
            init_doc_id, init_filename = _initial_doc_meta(document, document_id)
            dispatch_event_sync(
                observers,
                self._make_started_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    document_id=init_doc_id,
                    filename=init_filename,
                    schema=parsed_schema,
                ),
            )

        load_started = time.perf_counter()
        load_result: DocumentLoadResult | None
        invalid_type = False
        if preloaded_error is not None:
            load_result = preloaded_error
        elif isinstance(document, DocumentInput):
            load_result = load_document_input(
                document,
                limits=self._limits,
                document_id=document_id,
            )
        elif isinstance(document, (str, Path)):
            load_result = load_document(document, limits=self._limits, document_id=document_id)
        else:
            load_result = None
            invalid_type = True
        load_ms = (time.perf_counter() - load_started) * 1000.0

        load_ok = load_result is not None and load_result.document is not None
        doc_timing = StageTiming(stage="document_load", elapsed_ms=load_ms, succeeded=load_ok)

        if emit and extraction_id is not None:
            if invalid_type:
                load_err_codes: tuple[str, ...] = ("invalid_input",)
                load_warn_codes: tuple[str, ...] = ()
            elif load_result is not None:
                load_err_codes = (
                    tuple(e.code for e in load_result.errors)
                    if load_result.errors
                    else (() if load_ok else ("document_processing_failed",))
                )
                load_warn_codes = tuple(w.code for w in load_result.warnings)
            else:
                load_err_codes = ("document_processing_failed",)
                load_warn_codes = ()
            dispatch_event_sync(
                observers,
                self._make_document_loaded_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    load_result=load_result,
                    schema=parsed_schema,
                    stage_timing=doc_timing,
                    error_codes=load_err_codes,
                    warning_codes=load_warn_codes,
                ),
            )

        if invalid_type or load_result is None or load_result.document is None:
            fin_started = time.perf_counter()
            if invalid_type or load_result is None:
                result = self._build_invalid_input_failure(
                    parsed_schema,
                    extraction_id=extraction_id,
                )
            else:
                result = self._build_load_failure(
                    load_result,
                    parsed_schema,
                    extraction_id=extraction_id,
                )
            fin_ms = (time.perf_counter() - fin_started) * 1000.0
            op_ms = (time.perf_counter() - op_started) * 1000.0
            if item_timings is not None:
                item_timings[result.extraction_id] = op_ms
            if emit:
                fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=True)
                dispatch_event_sync(
                    observers,
                    self._make_extraction_completed_event(
                        result=result,
                        batch_id=batch_id,
                        schema=parsed_schema,
                        elapsed_ms=op_ms,
                        stage_timings=(doc_timing, fin_timing),
                        strategy_telemetry=None,
                    ),
                )
            return result

        strat_started = time.perf_counter()
        strategy_output: StrategyOutput | None = None
        strategy_exc: Exception | None = None
        try:
            strategy_output = sync_strategy.extract_raw(load_result.document, parsed_schema)
        except Exception as exc:
            strategy_exc = exc
        strat_ms = (time.perf_counter() - strat_started) * 1000.0

        if strategy_exc is not None or strategy_output is None:
            strat_timing = StageTiming(stage="strategy", elapsed_ms=strat_ms, succeeded=False)
            if emit and extraction_id is not None:
                dispatch_event_sync(
                    observers,
                    self._make_strategy_completed_event(
                        extraction_id=extraction_id,
                        batch_id=batch_id,
                        load_result=load_result,
                        schema=parsed_schema,
                        stage_timing=strat_timing,
                        strategy_telemetry=None,
                        error_codes=("extraction_strategy_error",),
                        warning_codes=(),
                    ),
                )
            fin_started = time.perf_counter()
            err_exc = strategy_exc if strategy_exc is not None else RuntimeError("Strategy failed")
            result = self._build_strategy_failure(
                load_result,
                parsed_schema,
                err_exc,
                extraction_id=extraction_id,
            )
            fin_ms = (time.perf_counter() - fin_started) * 1000.0
            op_ms = (time.perf_counter() - op_started) * 1000.0
            if item_timings is not None:
                item_timings[result.extraction_id] = op_ms
            if emit:
                fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=True)
                dispatch_event_sync(
                    observers,
                    self._make_extraction_completed_event(
                        result=result,
                        batch_id=batch_id,
                        schema=parsed_schema,
                        elapsed_ms=op_ms,
                        stage_timings=(doc_timing, strat_timing, fin_timing),
                        strategy_telemetry=None,
                    ),
                )
            return result

        strat_timing = StageTiming(
            stage="strategy",
            elapsed_ms=strat_ms,
            succeeded=not strategy_output.failed,
        )
        if emit and extraction_id is not None:
            dispatch_event_sync(
                observers,
                self._make_strategy_completed_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    load_result=load_result,
                    schema=parsed_schema,
                    stage_timing=strat_timing,
                    strategy_telemetry=strategy_output.telemetry,
                    error_codes=tuple(e.code for e in strategy_output.errors),
                    warning_codes=tuple(w.code for w in strategy_output.warnings),
                ),
            )

        fin_started = time.perf_counter()
        result = self._finalize_from_strategy_output(
            load_result,
            parsed_schema,
            strategy_output,
            extraction_id=extraction_id,
            calibrator=calibrator,
        )
        fin_ms = (time.perf_counter() - fin_started) * 1000.0
        op_ms = (time.perf_counter() - op_started) * 1000.0
        if item_timings is not None:
            item_timings[result.extraction_id] = op_ms
        if emit:
            fin_ok = not any(e.code == "result_assembly_error" for e in result.errors)
            fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=fin_ok)
            dispatch_event_sync(
                observers,
                self._make_extraction_completed_event(
                    result=result,
                    batch_id=batch_id,
                    schema=parsed_schema,
                    elapsed_ms=op_ms,
                    stage_timings=(doc_timing, strat_timing, fin_timing),
                    strategy_telemetry=strategy_output.telemetry,
                ),
            )
        return result

    async def _extract_single_async(
        self,
        document: str | Path | DocumentInput,
        parsed_schema: ExtractionSchema,
        *,
        document_id: str | None,
        observers: tuple[AnyExtractionObserver, ...],
        batch_id: str | None = None,
        preloaded_error: DocumentLoadResult | None = None,
        item_timings: dict[str, float] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        if preloaded_error is None and isinstance(document, DocumentInput):
            _validate_document_id_override(document_id)

        emit = bool(observers)
        extraction_id = str(uuid4()) if emit else None
        op_started = time.perf_counter()

        if emit and extraction_id is not None:
            init_doc_id, init_filename = _initial_doc_meta(document, document_id)
            await dispatch_event_async(
                observers,
                self._make_started_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    document_id=init_doc_id,
                    filename=init_filename,
                    schema=parsed_schema,
                ),
            )

        load_started = time.perf_counter()
        load_result: DocumentLoadResult | None
        invalid_type = False
        if preloaded_error is not None:
            load_result = preloaded_error
        elif isinstance(document, DocumentInput):
            load_result = await asyncio.to_thread(
                load_document_input,
                document,
                limits=self._limits,
                document_id=document_id,
            )
        elif isinstance(document, (str, Path)):
            load_result = await asyncio.to_thread(
                load_document,
                document,
                limits=self._limits,
                document_id=document_id,
            )
        else:
            load_result = None
            invalid_type = True
        load_ms = (time.perf_counter() - load_started) * 1000.0

        load_ok = load_result is not None and load_result.document is not None
        doc_timing = StageTiming(stage="document_load", elapsed_ms=load_ms, succeeded=load_ok)

        if emit and extraction_id is not None:
            if invalid_type:
                load_err_codes: tuple[str, ...] = ("invalid_input",)
                load_warn_codes: tuple[str, ...] = ()
            elif load_result is not None:
                load_err_codes = (
                    tuple(e.code for e in load_result.errors)
                    if load_result.errors
                    else (() if load_ok else ("document_processing_failed",))
                )
                load_warn_codes = tuple(w.code for w in load_result.warnings)
            else:
                load_err_codes = ("document_processing_failed",)
                load_warn_codes = ()
            await dispatch_event_async(
                observers,
                self._make_document_loaded_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    load_result=load_result,
                    schema=parsed_schema,
                    stage_timing=doc_timing,
                    error_codes=load_err_codes,
                    warning_codes=load_warn_codes,
                ),
            )

        if invalid_type or load_result is None or load_result.document is None:
            fin_started = time.perf_counter()
            if invalid_type or load_result is None:
                result = self._build_invalid_input_failure(
                    parsed_schema,
                    extraction_id=extraction_id,
                )
            else:
                result = self._build_load_failure(
                    load_result,
                    parsed_schema,
                    extraction_id=extraction_id,
                )
            fin_ms = (time.perf_counter() - fin_started) * 1000.0
            op_ms = (time.perf_counter() - op_started) * 1000.0
            if item_timings is not None:
                item_timings[result.extraction_id] = op_ms
            if emit:
                fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=True)
                await dispatch_event_async(
                    observers,
                    self._make_extraction_completed_event(
                        result=result,
                        batch_id=batch_id,
                        schema=parsed_schema,
                        elapsed_ms=op_ms,
                        stage_timings=(doc_timing, fin_timing),
                        strategy_telemetry=None,
                    ),
                )
            return result

        strat_started = time.perf_counter()
        strategy_output: StrategyOutput | None = None
        strategy_exc: Exception | None = None
        try:
            if isinstance(self._strategy, AsyncExtractionStrategy):
                strategy_output = await self._strategy.aextract_raw(
                    load_result.document,
                    parsed_schema,
                )
            else:
                strategy_output = await asyncio.to_thread(
                    self._strategy.extract_raw,
                    load_result.document,
                    parsed_schema,
                )
        except Exception as exc:
            # asyncio.CancelledError subclasses BaseException so it propagates cleanly
            strategy_exc = exc
        strat_ms = (time.perf_counter() - strat_started) * 1000.0

        if strategy_exc is not None or strategy_output is None:
            strat_timing = StageTiming(stage="strategy", elapsed_ms=strat_ms, succeeded=False)
            if emit and extraction_id is not None:
                await dispatch_event_async(
                    observers,
                    self._make_strategy_completed_event(
                        extraction_id=extraction_id,
                        batch_id=batch_id,
                        load_result=load_result,
                        schema=parsed_schema,
                        stage_timing=strat_timing,
                        strategy_telemetry=None,
                        error_codes=("extraction_strategy_error",),
                        warning_codes=(),
                    ),
                )
            fin_started = time.perf_counter()
            err_exc = strategy_exc if strategy_exc is not None else RuntimeError("Strategy failed")
            result = self._build_strategy_failure(
                load_result,
                parsed_schema,
                err_exc,
                extraction_id=extraction_id,
            )
            fin_ms = (time.perf_counter() - fin_started) * 1000.0
            op_ms = (time.perf_counter() - op_started) * 1000.0
            if item_timings is not None:
                item_timings[result.extraction_id] = op_ms
            if emit:
                fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=True)
                await dispatch_event_async(
                    observers,
                    self._make_extraction_completed_event(
                        result=result,
                        batch_id=batch_id,
                        schema=parsed_schema,
                        elapsed_ms=op_ms,
                        stage_timings=(doc_timing, strat_timing, fin_timing),
                        strategy_telemetry=None,
                    ),
                )
            return result

        strat_timing = StageTiming(
            stage="strategy",
            elapsed_ms=strat_ms,
            succeeded=not strategy_output.failed,
        )
        if emit and extraction_id is not None:
            await dispatch_event_async(
                observers,
                self._make_strategy_completed_event(
                    extraction_id=extraction_id,
                    batch_id=batch_id,
                    load_result=load_result,
                    schema=parsed_schema,
                    stage_timing=strat_timing,
                    strategy_telemetry=strategy_output.telemetry,
                    error_codes=tuple(e.code for e in strategy_output.errors),
                    warning_codes=tuple(w.code for w in strategy_output.warnings),
                ),
            )

        fin_started = time.perf_counter()
        result = self._finalize_from_strategy_output(
            load_result,
            parsed_schema,
            strategy_output,
            extraction_id=extraction_id,
            calibrator=calibrator,
        )
        fin_ms = (time.perf_counter() - fin_started) * 1000.0
        op_ms = (time.perf_counter() - op_started) * 1000.0
        if item_timings is not None:
            item_timings[result.extraction_id] = op_ms
        if emit:
            fin_ok = not any(e.code == "result_assembly_error" for e in result.errors)
            fin_timing = StageTiming(stage="finalization", elapsed_ms=fin_ms, succeeded=fin_ok)
            await dispatch_event_async(
                observers,
                self._make_extraction_completed_event(
                    result=result,
                    batch_id=batch_id,
                    schema=parsed_schema,
                    elapsed_ms=op_ms,
                    stage_timings=(doc_timing, strat_timing, fin_timing),
                    strategy_telemetry=strategy_output.telemetry,
                ),
            )
        return result

    def extract(
        self,
        document: str | Path | DocumentInput,
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        document_id: str | None = None,
        observers: Sequence[ExtractionObserver] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        """Extract validated canonical structured records synchronously."""
        sync_strategy = self._require_sync_strategy()
        effective_observers = self._compose_observers(observers, require_sync=True)
        validated_calibrator = validate_calibrator(calibrator)
        parsed_schema = _coerce_schema(schema)
        return self._extract_single_sync(
            document,
            parsed_schema,
            sync_strategy,
            document_id=document_id,
            observers=effective_observers,
            calibrator=validated_calibrator,
        )

    async def aextract(
        self,
        document: str | Path | DocumentInput,
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        document_id: str | None = None,
        observers: Sequence[AnyExtractionObserver] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        """Asynchronously extract validated canonical structured records without blocking."""
        effective_observers = self._compose_observers(observers, require_sync=False)
        validated_calibrator = validate_calibrator(calibrator)
        parsed_schema = _coerce_schema(schema)
        return await self._extract_single_async(
            document,
            parsed_schema,
            document_id=document_id,
            observers=effective_observers,
            calibrator=validated_calibrator,
        )

    def extract_batch(
        self,
        documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        options: BatchOptions | None = None,
        observers: Sequence[ExtractionObserver] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> BatchExtractionResult:
        """Statelessly extract a mixed collection of paths and in-memory documents in order."""
        sync_strategy = self._require_sync_strategy()
        effective_observers = self._compose_observers(observers, require_sync=True)
        validated_calibrator = validate_calibrator(calibrator)
        parsed_schema = _coerce_schema(schema)
        resolved_options = options if options is not None else BatchOptions()
        if not isinstance(resolved_options, BatchOptions):
            raise ConfigurationError("options must be a BatchOptions instance or None.")
        normalized_inputs = normalize_batch_inputs(documents)
        prepared_items = prepare_batch_items(
            normalized_inputs,
            limits=self._limits,
            max_workers=resolved_options.max_workers,
        )
        schema_meta = SchemaMetadata(
            schema_id=parsed_schema.schema_id,
            schema_version=parsed_schema.schema_version,
        )

        batch_id = str(uuid4())
        emit = bool(effective_observers)
        item_timings: dict[str, float] | None = {} if emit else None

        if emit:
            dispatch_event_sync(
                effective_observers,
                ExtractionEvent(
                    event_kind=ExtractionEventKind.BATCH_STARTED,
                    timestamp=utc_now_iso(),
                    batch_id=batch_id,
                    schema_id=parsed_schema.schema_id,
                    schema_version=parsed_schema.schema_version,
                    strategy_name=self._strategy.name,
                    batch_progress=BatchProgress(
                        batch_id=batch_id,
                        total_documents=len(prepared_items),
                        processed_documents=0,
                        skipped_documents=0,
                    ),
                ),
            )

        def _extract_one(item: _PreparedBatchItem) -> ExtractionResult:
            return self._extract_single_sync(
                item.document_input,
                parsed_schema,
                sync_strategy,
                document_id=None,
                observers=effective_observers,
                batch_id=batch_id,
                preloaded_error=item.preloaded_error,
                item_timings=item_timings,
                calibrator=validated_calibrator,
            )

        def _on_item_complete(
            _idx: int,
            res: ExtractionResult,
            progress: BatchProgress,
        ) -> None:
            elapsed = item_timings.get(res.extraction_id, 0.0) if item_timings is not None else 0.0
            dispatch_event_sync(
                effective_observers,
                self._make_batch_item_completed_event(
                    result=res,
                    batch_id=batch_id,
                    schema=parsed_schema,
                    elapsed_ms=elapsed,
                    progress=progress,
                ),
            )

        batch_res = run_batch_extraction(
            inputs=prepared_items,
            schema_metadata=schema_meta,
            options=resolved_options,
            extract_one=_extract_one,
            batch_id=batch_id,
            on_item_complete=_on_item_complete if emit else None,
        )

        if emit:
            summary = batch_res.summary
            dispatch_event_sync(
                effective_observers,
                ExtractionEvent(
                    event_kind=ExtractionEventKind.BATCH_COMPLETED,
                    timestamp=utc_now_iso(),
                    batch_id=batch_id,
                    schema_id=parsed_schema.schema_id,
                    schema_version=parsed_schema.schema_version,
                    strategy_name=self._strategy.name,
                    elapsed_ms=summary.elapsed_ms,
                    batch_progress=BatchProgress(
                        batch_id=batch_id,
                        total_documents=summary.total_documents,
                        processed_documents=summary.processed_documents,
                        skipped_documents=summary.skipped_documents,
                        completed_count=summary.completed_count,
                        partial_count=summary.partial_count,
                        failed_count=summary.failed_count,
                        aborted_early=summary.aborted_early,
                    ),
                ),
            )

        return batch_res

    async def aextract_batch(
        self,
        documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        options: BatchOptions | None = None,
        observers: Sequence[AnyExtractionObserver] | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> BatchExtractionResult:
        """Asynchronously extract a mixed collection of documents with bounded concurrency."""
        effective_observers = self._compose_observers(observers, require_sync=False)
        validated_calibrator = validate_calibrator(calibrator)
        parsed_schema = _coerce_schema(schema)
        resolved_options = options if options is not None else BatchOptions()
        if not isinstance(resolved_options, BatchOptions):
            raise ConfigurationError("options must be a BatchOptions instance or None.")
        normalized_inputs = normalize_batch_inputs(documents)
        if resolved_options.max_workers > 1:
            prepared_items = await asyncio.to_thread(
                prepare_batch_items,
                normalized_inputs,
                limits=self._limits,
                max_workers=resolved_options.max_workers,
            )
        else:
            prepared_items = tuple(
                _PreparedBatchItem(document_input=item) for item in normalized_inputs
            )
        schema_meta = SchemaMetadata(
            schema_id=parsed_schema.schema_id,
            schema_version=parsed_schema.schema_version,
        )

        batch_id = str(uuid4())
        emit = bool(effective_observers)
        item_timings: dict[str, float] | None = {} if emit else None

        if emit:
            await dispatch_event_async(
                effective_observers,
                ExtractionEvent(
                    event_kind=ExtractionEventKind.BATCH_STARTED,
                    timestamp=utc_now_iso(),
                    batch_id=batch_id,
                    schema_id=parsed_schema.schema_id,
                    schema_version=parsed_schema.schema_version,
                    strategy_name=self._strategy.name,
                    batch_progress=BatchProgress(
                        batch_id=batch_id,
                        total_documents=len(prepared_items),
                        processed_documents=0,
                        skipped_documents=0,
                    ),
                ),
            )

        async def _aextract_one(item: _PreparedBatchItem) -> ExtractionResult:
            return await self._extract_single_async(
                item.document_input,
                parsed_schema,
                document_id=None,
                observers=effective_observers,
                batch_id=batch_id,
                preloaded_error=item.preloaded_error,
                item_timings=item_timings,
                calibrator=validated_calibrator,
            )

        async def _aon_item_complete(
            _idx: int,
            res: ExtractionResult,
            progress: BatchProgress,
        ) -> None:
            elapsed = item_timings.get(res.extraction_id, 0.0) if item_timings is not None else 0.0
            await dispatch_event_async(
                effective_observers,
                self._make_batch_item_completed_event(
                    result=res,
                    batch_id=batch_id,
                    schema=parsed_schema,
                    elapsed_ms=elapsed,
                    progress=progress,
                ),
            )

        batch_res = await run_async_batch_extraction(
            inputs=prepared_items,
            schema_metadata=schema_meta,
            options=resolved_options,
            extract_one=_aextract_one,
            batch_id=batch_id,
            aon_item_complete=_aon_item_complete if emit else None,
        )

        if emit:
            summary = batch_res.summary
            await dispatch_event_async(
                effective_observers,
                ExtractionEvent(
                    event_kind=ExtractionEventKind.BATCH_COMPLETED,
                    timestamp=utc_now_iso(),
                    batch_id=batch_id,
                    schema_id=parsed_schema.schema_id,
                    schema_version=parsed_schema.schema_version,
                    strategy_name=self._strategy.name,
                    elapsed_ms=summary.elapsed_ms,
                    batch_progress=BatchProgress(
                        batch_id=batch_id,
                        total_documents=summary.total_documents,
                        processed_documents=summary.processed_documents,
                        skipped_documents=summary.skipped_documents,
                        completed_count=summary.completed_count,
                        partial_count=summary.partial_count,
                        failed_count=summary.failed_count,
                        aborted_early=summary.aborted_early,
                    ),
                ),
            )

        return batch_res


def extract(
    document: str | Path | DocumentInput,
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
    observers: Sequence[ExtractionObserver] | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> ExtractionResult:
    """Convenience synchronous entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits, calibrator=calibrator)
    return engine.extract(document, schema, document_id=document_id, observers=observers)


async def aextract(
    document: str | Path | DocumentInput,
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: AnyExtractionStrategy,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
    observers: Sequence[AnyExtractionObserver] | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> ExtractionResult:
    """Convenience asyncio entry point delegating directly to ExtractionEngine.aextract."""
    engine = ExtractionEngine(strategy=strategy, limits=limits, calibrator=calibrator)
    return await engine.aextract(document, schema, document_id=document_id, observers=observers)


def extract_batch(
    documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    options: BatchOptions | None = None,
    observers: Sequence[ExtractionObserver] | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> BatchExtractionResult:
    """Convenience synchronous batch entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits, calibrator=calibrator)
    return engine.extract_batch(documents, schema, options=options, observers=observers)


async def aextract_batch(
    documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: AnyExtractionStrategy,
    limits: DocumentLimits | None = None,
    options: BatchOptions | None = None,
    observers: Sequence[AnyExtractionObserver] | None = None,
    calibrator: ConfidenceCalibrator | None = None,
) -> BatchExtractionResult:
    """Convenience asyncio batch entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits, calibrator=calibrator)
    return await engine.aextract_batch(documents, schema, options=options, observers=observers)
