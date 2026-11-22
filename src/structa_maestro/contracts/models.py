"""Canonical Output Record Contract v1.0 models (data-model.md Sections 2.2 & 3.1-3.8)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)

CONTRACT_VERSION: Literal["1.0"] = "1.0"


class _ContractModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        ser_json_timedelta="iso8601",
    )


class SourceMetadata(_ContractModel):
    """Public source document identity (data-model.md Section 2.2)."""

    document_id: str = Field(min_length=1)
    filename: str | None = None
    media_type: str | None = None


class SchemaMetadata(_ContractModel):
    """Public schema identity (data-model.md Section 3.8)."""

    schema_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)


class SourceLocation(_ContractModel):
    """Optional granular location within the originating document (data-model.md Section 3.1)."""

    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    section: str | None = None

    @model_validator(mode="after")
    def _check_spans(self) -> SourceLocation:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("SourceLocation.line_end must be >= line_start.")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("SourceLocation.char_end must be >= char_start.")
        return self


class ProvenanceEvidence(_ContractModel):
    """Supporting source evidence linked to an extracted field (data-model.md Section 3.2)."""

    source_document_id: str = Field(min_length=1)
    location: SourceLocation | None = None
    evidence: str
    relationship: EvidenceRelationship
    truncated: bool = False


class ConfidenceAssessment(_ContractModel):
    """Explicit reliability assessment independent of validation (data-model.md Section 3.3)."""

    value: float | None = None
    scale: str = Field(default="0-1", min_length=1)
    method: str = Field(default="extraction-assessment", min_length=1)
    calibrated: bool = False

    @model_validator(mode="after")
    def _check_value_bounds(self) -> ConfidenceAssessment:
        if self.value is not None and self.scale == "0-1" and not (0.0 <= self.value <= 1.0):
            raise ValueError("ConfidenceAssessment.value must be within [0.0, 1.0] for scale 0-1.")
        return self


class DiagnosticIssue(_ContractModel):
    """Structured, privacy-safe error or warning (data-model.md Section 3.4)."""

    code: str = Field(min_length=1)
    severity: IssueSeverity
    stage: PipelineStage
    message: str = Field(min_length=1)
    record_id: str | None = None
    field_path: str | None = None
    expected: str | None = None
    actual: str | None = None


class ValidationOutcome(_ContractModel):
    """Independent structural and constraint validation result (data-model.md Section 3.5)."""

    status: ValidationStatus
    errors: list[DiagnosticIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_validation_errors(self) -> ValidationOutcome:
        for err in self.errors:
            if err.severity != IssueSeverity.ERROR or err.stage != PipelineStage.VALIDATION:
                raise ValueError(
                    "ValidationOutcome.errors must contain only validation-stage errors."
                )
        if self.status != ValidationStatus.INVALID and self.errors:
            raise ValueError("ValidationOutcome.errors must be empty unless status is 'invalid'.")
        return self


class ExtractedField(_ContractModel):
    """Single extracted field with independent quality dimensions (data-model.md Section 3.6)."""

    presence: FieldPresence
    value: Any = None
    value_type: FieldType | None = None
    confidence: ConfidenceAssessment | None = None
    provenance: list[ProvenanceEvidence] = Field(default_factory=list)
    validation: ValidationOutcome
    errors: list[DiagnosticIssue] = Field(default_factory=list)
    warnings: list[DiagnosticIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_presence_value(self) -> ExtractedField:
        if self.presence == FieldPresence.MISSING and self.value is not None:
            raise ValueError("ExtractedField.value must be None when presence is 'missing'.")
        return self


class ExtractedRecord(_ContractModel):
    """Single bounded structured record within an extraction result (data-model.md Section 3.7)."""

    record_id: str = Field(min_length=1)
    status: ProcessingStatus
    fields: dict[str, ExtractedField] = Field(default_factory=dict)
    validation: ValidationOutcome
    errors: list[DiagnosticIssue] = Field(default_factory=list)
    warnings: list[DiagnosticIssue] = Field(default_factory=list)


class ExtractionResult(_ContractModel):
    """Top-level Canonical Output Record Contract v1.0 (data-model.md Section 3.8).

    Uses ``schema_metadata`` with ``alias="schema"`` so canonical JSON uses the exact
    ``"schema"`` key without shadowing Pydantic v2's deprecated ``BaseModel.schema()``.
    """

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    extraction_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    status: ProcessingStatus
    source: SourceMetadata | None = None
    schema_metadata: SchemaMetadata = Field(
        validation_alias=AliasChoices("schema", "schema_metadata"),
        serialization_alias="schema",
    )
    records: list[ExtractedRecord] = Field(default_factory=list)
    validation: ValidationOutcome
    errors: list[DiagnosticIssue] = Field(default_factory=list)
    warnings: list[DiagnosticIssue] = Field(default_factory=list)

    @field_validator("records")
    @classmethod
    def _check_unique_record_ids(cls, value: list[ExtractedRecord]) -> list[ExtractedRecord]:
        seen: set[str] = set()
        for rec in value:
            if rec.record_id in seen:
                raise ValueError(f"Duplicate record_id detected: {rec.record_id!r}")
            seen.add(rec.record_id)
        return value

    @model_validator(mode="after")
    def _check_provenance_source_binding(self) -> ExtractionResult:
        if self.source is not None:
            expected_doc_id = self.source.document_id
            for record in self.records:
                for field_name, extracted_field in record.fields.items():
                    for ev in extracted_field.provenance:
                        if ev.source_document_id != expected_doc_id:
                            raise ValueError(
                                f"Provenance for field {field_name!r} references foreign "
                                f"document_id {ev.source_document_id!r} != {expected_doc_id!r}."
                            )
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize this result to a canonical JSON-compatible dictionary."""
        from structa_maestro.contracts.serialization import serialize_result_to_dict

        return serialize_result_to_dict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize this result to canonical UTF-8 JSON."""
        from structa_maestro.contracts.serialization import serialize_result_to_json

        return serialize_result_to_json(self, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionResult:
        """Strictly deserialize a canonical dictionary into an ExtractionResult."""
        from structa_maestro.contracts.serialization import deserialize_result_from_dict

        return deserialize_result_from_dict(data)

    @classmethod
    def from_json(cls, raw_json: str) -> ExtractionResult:
        """Strictly deserialize canonical JSON into an ExtractionResult."""
        from structa_maestro.contracts.serialization import deserialize_result_from_json

        return deserialize_result_from_json(raw_json)
