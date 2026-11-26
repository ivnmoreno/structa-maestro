"""Canonical ExtractionResult builder assembling records, provenance, confidence, and validation."""

from __future__ import annotations

from typing import Any

from structa_maestro.confidence.calibration import ConfidenceCalibrator, validate_calibrator
from structa_maestro.confidence.normalizer import ConfidenceNormalizer
from structa_maestro.contracts.enums import (
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    DiagnosticIssue,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    SchemaMetadata,
    SourceMetadata,
    ValidationOutcome,
)
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.exceptions import ConfigurationError
from structa_maestro.provenance.associator import ProvenanceAssociator
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import ProposedRecord, StrategyOutput
from structa_maestro.validation.validator import DeterministicValidator


class ResultBuilder:
    """Assembles Canonical Output Record Contract v1.0 results with strict status honesty."""

    def __init__(
        self,
        *,
        validator: DeterministicValidator | None = None,
        provenance_associator: ProvenanceAssociator | None = None,
        confidence_normalizer: ConfidenceNormalizer | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> None:
        if confidence_normalizer is not None and calibrator is not None:
            raise ConfigurationError(
                "Cannot pass both confidence_normalizer and calibrator to ResultBuilder."
            )
        validated_calibrator = validate_calibrator(calibrator)
        self._validator = validator or DeterministicValidator()
        self._provenance = provenance_associator or ProvenanceAssociator()
        self._confidence = confidence_normalizer or ConfidenceNormalizer(
            calibrator=validated_calibrator
        )

    def build_failed(
        self,
        *,
        schema: ExtractionSchema,
        source: SourceMetadata | None,
        errors: list[DiagnosticIssue],
        warnings: list[DiagnosticIssue] | None = None,
        extraction_id: str | None = None,
    ) -> ExtractionResult:
        """Build a canonical failed result when processing fails prior to producing records."""
        extra_kwargs: dict[str, Any] = {}
        if extraction_id is not None:
            extra_kwargs["extraction_id"] = extraction_id
        return ExtractionResult(
            status=ProcessingStatus.FAILED,
            source=source,
            schema_metadata=SchemaMetadata(
                schema_id=schema.schema_id,
                schema_version=schema.schema_version,
            ),
            records=[],
            validation=ValidationOutcome(status=ValidationStatus.NOT_VALIDATED),
            errors=list(errors),
            warnings=list(warnings or ()),
            **extra_kwargs,
        )

    def build(
        self,
        *,
        document: PreparedDocument,
        schema: ExtractionSchema,
        strategy_output: StrategyOutput,
        document_warnings: list[DiagnosticIssue] | None = None,
        extraction_id: str | None = None,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractionResult:
        """Assemble full canonical ExtractionResult from prepared document and strategy output."""
        call_calibrator = validate_calibrator(calibrator)
        result_errors = list(strategy_output.errors)
        result_warnings = list(document_warnings or ()) + list(strategy_output.warnings)

        if strategy_output.failed and not strategy_output.records:
            return self.build_failed(
                schema=schema,
                source=document.source,
                errors=result_errors
                or [
                    DiagnosticIssue(
                        code="extraction_failed",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.EXTRACTION,
                        message="Extraction strategy failed without returning any records.",
                    )
                ],
                warnings=result_warnings,
                extraction_id=extraction_id,
            )

        built_records: list[ExtractedRecord] = []
        any_record_partial_or_failed = False

        for idx, proposed_record in enumerate(strategy_output.records, start=1):
            record_id = f"record-{idx:03d}"
            built = self._build_record(
                record_id=record_id,
                document=document,
                schema=schema,
                proposed_record=proposed_record,
                default_confidence_method=strategy_output.default_confidence_method,
                calibrator=call_calibrator,
            )
            if built.status != ProcessingStatus.COMPLETED:
                any_record_partial_or_failed = True
            for rec_warn in built.warnings:
                if rec_warn.stage == PipelineStage.VALIDATION:
                    result_warnings.append(rec_warn)
            built_records.append(built)

        overall_validation = self._validator.validate_result_cardinality(
            [rec.validation for rec in built_records],
            schema,
            has_records=bool(built_records),
            failed_before_validation=strategy_output.failed,
        )

        # Processing status is strictly independent of schema validation status!
        has_processing_errors = bool(result_errors) or strategy_output.partial
        if strategy_output.failed and not built_records:
            overall_status = ProcessingStatus.FAILED
        elif (
            strategy_output.failed
            or strategy_output.partial
            or has_processing_errors
            or any_record_partial_or_failed
        ):
            overall_status = ProcessingStatus.PARTIAL
        else:
            overall_status = ProcessingStatus.COMPLETED

        extra_kwargs: dict[str, Any] = {}
        if extraction_id is not None:
            extra_kwargs["extraction_id"] = extraction_id

        return ExtractionResult(
            status=overall_status,
            source=document.source,
            schema_metadata=SchemaMetadata(
                schema_id=schema.schema_id,
                schema_version=schema.schema_version,
            ),
            records=built_records,
            validation=overall_validation,
            errors=result_errors,
            warnings=result_warnings,
            **extra_kwargs,
        )

    def _build_record(
        self,
        *,
        record_id: str,
        document: PreparedDocument,
        schema: ExtractionSchema,
        proposed_record: ProposedRecord,
        default_confidence_method: str,
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ExtractedRecord:
        record_errors = list(proposed_record.errors)
        record_warnings = list(proposed_record.warnings)

        # Prepare deterministic field map covering all declared schema fields + any extra fields
        validation_input: dict[str, tuple[FieldPresence, Any]] = {}
        ordered_field_names: list[str] = list(schema.fields.keys())
        for extra_name in sorted(set(proposed_record.fields.keys()) - set(schema.fields.keys())):
            ordered_field_names.append(extra_name)

        for fname in ordered_field_names:
            pf = proposed_record.fields.get(fname)
            if pf is None or pf.presence == FieldPresence.MISSING:
                validation_input[fname] = (FieldPresence.MISSING, None)
            else:
                validation_input[fname] = (FieldPresence.PRESENT, pf.value)

        val_result = self._validator.validate_record(
            validation_input,
            schema,
            record_id=record_id,
        )
        record_warnings.extend(val_result.warnings)

        fields_map: dict[str, ExtractedField] = {}
        any_field_errors = False

        for fname in ordered_field_names:
            pf = proposed_record.fields.get(fname)
            spec = schema.fields.get(fname)
            field_val_outcome = val_result.field_outcomes[fname]
            value_type = spec.field_type if spec is not None else None

            if pf is None:
                fields_map[fname] = ExtractedField(
                    presence=FieldPresence.MISSING,
                    value=None,
                    value_type=value_type,
                    confidence=None,
                    provenance=[],
                    validation=field_val_outcome,
                    errors=[],
                    warnings=[],
                )
                continue

            presence = pf.presence
            value = None if presence == FieldPresence.MISSING else pf.value
            field_errors = list(pf.errors)
            field_warnings = list(pf.warnings)
            if field_errors:
                any_field_errors = True

            if pf.ambiguous:
                field_warnings.append(
                    DiagnosticIssue(
                        code="ambiguous_source_value",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=f"Source document contains ambiguous candidates for '{fname}'.",
                        record_id=record_id,
                        field_path=fname,
                    )
                )

            conf_res = self._confidence.normalize(
                raw_value=pf.confidence,
                scale=pf.confidence_scale,
                method=pf.confidence_method,
                record_id=record_id,
                field_path=fname,
                default_method=default_confidence_method,
                calibrator=calibrator,
            )
            field_warnings.extend(conf_res.warnings)

            try:
                prov_res = self._provenance.associate(
                    document=document,
                    proposed_evidence=pf.evidence,
                    field_name=fname,
                    presence=presence,
                    value=value,
                    record_id=record_id,
                )
                provenance_entries = prov_res.evidence
                field_warnings.extend(prov_res.warnings)
            except Exception:
                provenance_entries = []
                field_errors.append(
                    DiagnosticIssue(
                        code="provenance_association_failed",
                        severity=IssueSeverity.ERROR,
                        stage=PipelineStage.PROVENANCE,
                        message=f"Provenance association failed for field '{fname}'.",
                        record_id=record_id,
                        field_path=fname,
                    )
                )
                any_field_errors = True

            fields_map[fname] = ExtractedField(
                presence=presence,
                value=value,
                value_type=value_type,
                confidence=conf_res.confidence,
                provenance=provenance_entries,
                validation=field_val_outcome,
                errors=field_errors,
                warnings=field_warnings,
            )

        if proposed_record.failed:
            record_status = ProcessingStatus.FAILED
        elif proposed_record.partial or record_errors or any_field_errors:
            record_status = ProcessingStatus.PARTIAL
        else:
            record_status = ProcessingStatus.COMPLETED

        return ExtractedRecord(
            record_id=record_id,
            status=record_status,
            fields=fields_map,
            validation=val_result.record_outcome,
            errors=record_errors,
            warnings=record_warnings,
        )
