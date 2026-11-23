"""Main ExtractionEngine and extract() orchestration pipeline with stage failure isolation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from structa_maestro.batch import (
    BatchDocumentInput,
    BatchExtractionResult,
    BatchOptions,
    normalize_batch_inputs,
    run_batch_extraction,
)
from structa_maestro.builder.result_builder import ResultBuilder
from structa_maestro.config import DocumentLimits, get_logger, sanitize_message
from structa_maestro.contracts.enums import IssueSeverity, PipelineStage
from structa_maestro.contracts.models import DiagnosticIssue, ExtractionResult, SchemaMetadata
from structa_maestro.documents.loader import load_document
from structa_maestro.exceptions import ConfigurationError, SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import ExtractionStrategy

_logger = get_logger(__name__)


def _coerce_schema(schema: ExtractionSchema | Mapping[str, Any]) -> ExtractionSchema:
    if isinstance(schema, ExtractionSchema):
        return schema
    if isinstance(schema, Mapping):
        return ExtractionSchema.from_dict(schema)
    raise SchemaDefinitionError(
        f"Expected ExtractionSchema or mapping schema definition, got {type(schema).__name__}."
    )


class ExtractionEngine:
    """Stateless structured extraction orchestrator isolating all operational stages."""

    def __init__(
        self,
        strategy: ExtractionStrategy,
        *,
        limits: DocumentLimits | None = None,
        result_builder: ResultBuilder | None = None,
    ) -> None:
        if not isinstance(strategy, ExtractionStrategy):
            raise ConfigurationError(
                "ExtractionEngine requires a strategy implementing ExtractionStrategy."
            )
        if limits is not None and not isinstance(limits, DocumentLimits):
            raise ConfigurationError("limits must be a DocumentLimits instance or None.")
        self._strategy = strategy
        self._limits = limits or DocumentLimits()
        self._builder = result_builder or ResultBuilder()

    @property
    def limits(self) -> DocumentLimits:
        """Configured document safety limits."""
        return self._limits

    def extract(
        self,
        document: str | Path,
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        document_id: str | None = None,
    ) -> ExtractionResult:
        """Extract validated canonical structured records from a single document.

        Failure boundary (contracts/python-api-and-cli.md Section 1.2):
        - Invalid schema or engine configuration raises SchemaDefinitionError / ConfigurationError.
        - All document input, parsing, extraction, provenance, and validation failures return
          a canonical ExtractionResult with status='failed' or status='partial'.
        """
        # 1. Pre-flight schema validation (raises SchemaDefinitionError on caller mistake)
        parsed_schema = _coerce_schema(schema)

        if not isinstance(document, (str, Path)):
            return self._builder.build_failed(
                schema=parsed_schema,
                source=None,
                errors=[
                    DiagnosticIssue(
                        code="invalid_input",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.INPUT,
                        message="Document input must be a file path (str or pathlib.Path).",
                    )
                ],
            )

        # 2. Safe document loading & text preparation
        load_result = load_document(document, limits=self._limits, document_id=document_id)
        if load_result.document is None:
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

        # 3. Extraction strategy execution (fully isolated; never crashes consumer)
        try:
            strategy_output = self._strategy.extract_raw(load_result.document, parsed_schema)
        except Exception as exc:
            safe_msg = sanitize_message(
                f"Extraction strategy '{self._strategy.name}' failed: {type(exc).__name__}"
            )
            _logger.debug(
                "Strategy error for document_id=%s: %s", load_result.source.document_id, safe_msg
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

        # 4. Provenance, confidence, deterministic validation & canonical assembly
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

    def extract_batch(
        self,
        documents: Sequence[str | Path | BatchDocumentInput],
        schema: ExtractionSchema | Mapping[str, Any],
        *,
        options: BatchOptions | None = None,
    ) -> BatchExtractionResult:
        """Statelessly extract a collection of documents in deterministic input order."""
        parsed_schema = _coerce_schema(schema)
        resolved_options = options if options is not None else BatchOptions()
        if not isinstance(resolved_options, BatchOptions):
            raise ConfigurationError("options must be a BatchOptions instance or None.")
        normalized_inputs = normalize_batch_inputs(documents)
        schema_meta = SchemaMetadata(
            schema_id=parsed_schema.schema_id,
            schema_version=parsed_schema.schema_version,
        )

        def _extract_one(item: BatchDocumentInput) -> ExtractionResult:
            return self.extract(item.path, parsed_schema, document_id=item.document_id)

        return run_batch_extraction(
            inputs=normalized_inputs,
            schema_metadata=schema_meta,
            options=resolved_options,
            extract_one=_extract_one,
        )


def extract(
    document: str | Path,
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    document_id: str | None = None,
) -> ExtractionResult:
    """Convenience functional entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return engine.extract(document, schema, document_id=document_id)


def extract_batch(
    documents: Sequence[str | Path | BatchDocumentInput],
    schema: ExtractionSchema | Mapping[str, Any],
    *,
    strategy: ExtractionStrategy,
    limits: DocumentLimits | None = None,
    options: BatchOptions | None = None,
) -> BatchExtractionResult:
    """Convenience functional batch entry point delegating directly to ExtractionEngine."""
    engine = ExtractionEngine(strategy=strategy, limits=limits)
    return engine.extract_batch(documents, schema, options=options)
