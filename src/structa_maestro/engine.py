"""Main ExtractionEngine, extract/aextract & batch orchestration with stage failure isolation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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
from structa_maestro.config import DocumentLimits, get_logger, sanitize_message
from structa_maestro.contracts.enums import IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, ExtractionResult, SchemaMetadata
from structa_maestro.documents.inputs import DocumentInput
from structa_maestro.documents.loader import (
    DocumentLoadResult,
    load_document,
    load_document_input,
)
from structa_maestro.exceptions import ConfigurationError, SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import AsyncExtractionStrategy, ExtractionStrategy

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


class ExtractionEngine:
    """Stateless structured extraction orchestrator (sync and asyncio) isolating all stages."""

    def __init__(
        self,
        strategy: AnyExtractionStrategy,
        *,
        limits: DocumentLimits | None = None,
        result_builder: ResultBuilder | None = None,
    ) -> None:
        if not isinstance(strategy, (ExtractionStrategy, AsyncExtractionStrategy)):
            raise ConfigurationError(
                "ExtractionEngine requires a strategy implementing ExtractionStrategy "
                "or AsyncExtractionStrategy."
            )
        if limits is not None and not isinstance(limits, DocumentLimits):
            raise ConfigurationError("limits must be a DocumentLimits instance or None.")
        self._strategy: AnyExtractionStrategy = strategy
        self._limits = limits or DocumentLimits()
        self._builder = result_builder or ResultBuilder()

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

    def _finalize_from_strategy_output(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        strategy_output: Any,
    ) -> ExtractionResult:
        if load_result.document is None:
            return self._build_load_failure(load_result, parsed_schema)
        try:
            return self._builder.build(
                document=load_result.document,
                schema=parsed_schema,
                strategy_output=strategy_output,
                document_warnings=load_result.warnings,
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
            )

    def _build_load_failure(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
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
        )

    def _build_strategy_failure(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        exc: Exception,
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
        )

    def _extract_from_load_result(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
        sync_strategy: ExtractionStrategy,
    ) -> ExtractionResult:
        """Run sync strategy + canonical assembly from an already-loaded DocumentLoadResult."""
        if load_result.document is None:
            return self._build_load_failure(load_result, parsed_schema)

        try:
            strategy_output = sync_strategy.extract_raw(load_result.document, parsed_schema)
        except Exception as exc:
            return self._build_strategy_failure(load_result, parsed_schema, exc)

        return self._finalize_from_strategy_output(load_result, parsed_schema, strategy_output)

    async def _aextract_from_load_result(
        self,
        load_result: DocumentLoadResult,
        parsed_schema: ExtractionSchema,
    ) -> ExtractionResult:
        """Run async (or offloaded sync) strategy + canonical assembly without blocking."""
        if load_result.document is None:
            return self._build_load_failure(load_result, parsed_schema)

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
            # Note: asyncio.CancelledError subclasses BaseException (not Exception) so it propagates
            return self._build_strategy_failure(load_result, parsed_schema, exc)

        return self._finalize_from_strategy_output(load_result, parsed_schema, strategy_output)

    def extract(
        self,
        document: str | Path | DocumentInput,
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        document_id: str | None = None,
    ) -> ExtractionResult:
        """Extract validated canonical structured records synchronously."""
        sync_strategy = self._require_sync_strategy()
        parsed_schema = _coerce_schema(schema)

        if isinstance(document, DocumentInput):
            load_result = load_document_input(
                document,
                limits=self._limits,
                document_id=document_id,
            )
            return self._extract_from_load_result(load_result, parsed_schema, sync_strategy)

        if not isinstance(document, (str, Path)):
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
            )

        load_result = load_document(document, limits=self._limits, document_id=document_id)
        return self._extract_from_load_result(load_result, parsed_schema, sync_strategy)

    async def aextract(
        self,
        document: str | Path | DocumentInput,
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        document_id: str | None = None,
    ) -> ExtractionResult:
        """Asynchronously extract validated canonical structured records without blocking."""
        parsed_schema = _coerce_schema(schema)

        if isinstance(document, DocumentInput):
            _validate_document_id_override(document_id)
            load_result = await asyncio.to_thread(
                load_document_input,
                document,
                limits=self._limits,
                document_id=document_id,
            )
            return await self._aextract_from_load_result(load_result, parsed_schema)

        if not isinstance(document, (str, Path)):
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
            )

        load_result = await asyncio.to_thread(
            load_document,
            document,
            limits=self._limits,
            document_id=document_id,
        )
        return await self._aextract_from_load_result(load_result, parsed_schema)

    def extract_batch(
        self,
        documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        options: BatchOptions | None = None,
    ) -> BatchExtractionResult:
        """Statelessly extract a mixed collection of paths and in-memory documents in order."""
        sync_strategy = self._require_sync_strategy()
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

        def _extract_one(item: _PreparedBatchItem) -> ExtractionResult:
            if item.preloaded_error is not None:
                return self._extract_from_load_result(
                    item.preloaded_error,
                    parsed_schema,
                    sync_strategy,
                )
            return self.extract(item.document_input, parsed_schema)

        return run_batch_extraction(
            inputs=prepared_items,
            schema_metadata=schema_meta,
            options=resolved_options,
            extract_one=_extract_one,
        )

    async def aextract_batch(
        self,
        documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        options: BatchOptions | None = None,
    ) -> BatchExtractionResult:
        """Asynchronously extract a mixed collection of documents with bounded concurrency."""
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

        async def _aextract_one(item: _PreparedBatchItem) -> ExtractionResult:
            if item.preloaded_error is not None:
                return self._build_load_failure(item.preloaded_error, parsed_schema)
            return await self.aextract(item.document_input, parsed_schema)

        return await run_async_batch_extraction(
            inputs=prepared_items,
            schema_metadata=schema_meta,
            options=resolved_options,
            extract_one=_aextract_one,
        )


def extract(
    document: str | Path | DocumentInput,
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
) -> ExtractionResult:
    """Convenience synchronous entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return engine.extract(document, schema, document_id=document_id)


async def aextract(
    document: str | Path | DocumentInput,
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: AnyExtractionStrategy,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
) -> ExtractionResult:
    """Convenience asyncio entry point delegating directly to ExtractionEngine.aextract."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return await engine.aextract(document, schema, document_id=document_id)


def extract_batch(
    documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    options: BatchOptions | None = None,
) -> BatchExtractionResult:
    """Convenience synchronous batch entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return engine.extract_batch(documents, schema, options=options)


async def aextract_batch(
    documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: AnyExtractionStrategy,
    limits: DocumentLimits | None = None,
    options: BatchOptions | None = None,
) -> BatchExtractionResult:
    """Convenience asyncio batch entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return await engine.aextract_batch(documents, schema, options=options)
