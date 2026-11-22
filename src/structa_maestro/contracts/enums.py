"""Canonical enumerations for structa-maestro (data-model.md Section 1)."""

from __future__ import annotations

from enum import StrEnum


class DocumentFormat(StrEnum):
    """Supported MVP input document formats."""

    TXT = "txt"
    PDF = "pdf"
    DOCX = "docx"


class FieldType(StrEnum):
    """Supported MVP schema field data types."""

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    ARRAY = "array"
    OBJECT = "object"


class ExtractionCardinality(StrEnum):
    """Whether a schema targets a single record or multiple records per document."""

    SINGLE = "single"
    MULTIPLE = "multiple"


class ProcessingStatus(StrEnum):
    """Operational completion state of an extraction or record."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ValidationStatus(StrEnum):
    """Schema validation outcome of a field, record, or overall result."""

    VALID = "valid"
    INVALID = "invalid"
    NOT_VALIDATED = "not_validated"


class FieldPresence(StrEnum):
    """Distinguishes an absent/unfound field from an explicitly extracted null or empty value."""

    PRESENT = "present"
    MISSING = "missing"


class EvidenceRelationship(StrEnum):
    """Relationship between a cited source excerpt and an extracted field value."""

    DIRECT = "direct"
    INFERRED = "inferred"
    CONFLICTING = "conflicting"
    UNVERIFIED = "unverified"


class IssueSeverity(StrEnum):
    """Diagnostic severity separating blocking errors from non-invalidating warnings."""

    ERROR = "error"
    WARNING = "warning"


class PipelineStage(StrEnum):
    """Originating processing stage for classified diagnostic issues."""

    INPUT = "input"
    DOCUMENT_PROCESSING = "document_processing"
    SCHEMA = "schema"
    EXTRACTION = "extraction"
    PROVENANCE = "provenance"
    VALIDATION = "validation"
    SERIALIZATION = "serialization"
