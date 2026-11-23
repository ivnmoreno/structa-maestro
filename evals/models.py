"""Pydantic v2 domain models for the repository-internal golden evaluation suite (evals)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from structa_maestro.contracts.enums import (
    DocumentFormat,
    FieldPresence,
    ProcessingStatus,
    ValidationStatus,
)

REPORT_VERSION: Literal["1.0"] = "1.0"


class DocumentCategory(StrEnum):
    """All 8 target business document categories (FR-001)."""

    INVOICES = "invoices"
    CONTRACTS = "contracts"
    EMPLOYMENT_DOCUMENTS = "employment_documents"
    APPLICATIONS = "applications"
    BUSINESS_REPORTS = "business_reports"
    CORRESPONDENCE = "correspondence"
    FINANCIAL_DOCUMENTS = "financial_documents"
    ADMINISTRATIVE_FORMS = "administrative_forms"


class ChallengeProfile(StrEnum):
    """All 10 mandatory golden evaluation challenge profiles (FR-002)."""

    STRAIGHTFORWARD = "straightforward"
    MISSING_INFORMATION = "missing_information"
    AMBIGUOUS_VALUES = "ambiguous_values"
    CONFLICTING_VALUES = "conflicting_values"
    MULTIPLE_RECORDS = "multiple_records"
    NESTED_RECORDS = "nested_records"
    COMPLEX_FORMATTING = "complex_formatting"
    UNSUPPORTED_OR_INCOMPLETE = "unsupported_or_incomplete"
    ADVERSARIAL_INSTRUCTIONS = "adversarial_instructions"
    MALFORMED_DOCUMENT = "malformed_document"


class MatchMode(StrEnum):
    """Per-field comparison mode; defaults strictly to exact match (FR-008)."""

    EXACT = "exact"
    DECIMAL_EQUAL = "decimal_equal"
    DATE_ISO = "date_iso"
    CASEFOLD_WS = "casefold_ws"


class RecordOrderMode(StrEnum):
    """Multi-record alignment rule for evaluation scoring."""

    ORDERED = "ordered"
    UNORDERED = "unordered"


class MaterializationKind(StrEnum):
    """Declarative on-disk file materialization recipe kind (FR-003)."""

    TXT = "txt"
    PDF = "pdf"
    DOCX = "docx"
    ZERO_BYTE = "zero_byte"
    WHITESPACE_ONLY = "whitespace_only"
    IMAGE_ONLY_PDF = "image_only_pdf"
    CORRUPT_PDF = "corrupt_pdf"
    CORRUPT_DOCX = "corrupt_docx"
    ZIP_BOMB_DOCX = "zip_bomb_docx"
    MISLEADING_PDF_AS_TXT = "misleading_pdf_as_txt"
    MISLEADING_TXT_AS_PDF = "misleading_txt_as_pdf"


class _EvalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentRecipe(_EvalModel):
    """Declarative recipe for deterministic on-disk golden document synthesis."""

    kind: MaterializationKind
    filename: str = Field(min_length=1)
    lines: list[str] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)
    encoding: str = Field(default="utf-8", min_length=1)
    uncompressed_bytes: int | None = Field(default=None, gt=0)

    @field_validator("filename")
    @classmethod
    def _validate_safe_basename(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("DocumentRecipe.filename must be a safe basename without separators.")
        return value


class ExpectedFieldSpec(_EvalModel):
    """Expected extraction outcome for a single field inside an expected record."""

    presence: FieldPresence
    value: Any = None
    match_mode: MatchMode = MatchMode.EXACT
    expected_evidence: list[str] = Field(default_factory=list)
    expect_ambiguous: bool = False

    @model_validator(mode="after")
    def _check_missing_value(self) -> ExpectedFieldSpec:
        if self.presence == FieldPresence.MISSING and self.value is not None:
            raise ValueError("ExpectedFieldSpec.value must be None when presence is 'missing'.")
        return self


class ExpectedRecordSpec(_EvalModel):
    """Expected fields and optional validation status for one expected record."""

    fields: dict[str, ExpectedFieldSpec] = Field(min_length=1)
    expected_validation: ValidationStatus | None = None


class GoldenCase(_EvalModel):
    """Single manually verified golden evaluation case (FR-001..FR-004)."""

    case_id: str = Field(min_length=1)
    category: DocumentCategory
    format: DocumentFormat
    challenge_profiles: list[ChallengeProfile] = Field(min_length=1)
    description: str = Field(min_length=1)
    document: DocumentRecipe
    schema_definition: dict[str, Any]
    expected_status: ProcessingStatus
    expected_validation: ValidationStatus
    expected_records: list[ExpectedRecordSpec] = Field(default_factory=list)
    record_order: RecordOrderMode = RecordOrderMode.ORDERED
    match_key_field: str | None = None
    expected_error_codes: list[str] = Field(default_factory=list)
    forbidden_extra_fields: list[str] = Field(default_factory=list)


class GoldenDatasetManifest(_EvalModel):
    """Versioned root catalog of curated synthetic golden cases."""

    dataset_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    cases: list[GoldenCase] = Field(min_length=1)


class FieldScore(_EvalModel):
    """Per-field evaluation verdict for a single trial."""

    field_name: str = Field(min_length=1)
    expected_presence: FieldPresence
    actual_presence: FieldPresence
    matched: bool
    hallucinated: bool
    provenance_expected: bool
    provenance_verified: bool
    ambiguity_expected: bool
    ambiguity_handled: bool


class CaseOutcome(_EvalModel):
    """Detailed evaluation outcome for one golden case in one trial."""

    case_id: str = Field(min_length=1)
    category: DocumentCategory
    format: DocumentFormat
    challenge_profiles: list[ChallengeProfile]
    trial_index: int = Field(ge=1)
    expected_status: ProcessingStatus
    actual_status: ProcessingStatus
    status_matched: bool
    expected_validation: ValidationStatus
    actual_validation: ValidationStatus
    validation_matched: bool
    error_codes_matched: bool
    runtime_error: str | None = None
    latency_ms: float = Field(ge=0.0)
    field_scores: list[FieldScore] = Field(default_factory=list)
    record_completeness: float = Field(ge=0.0, le=1.0)
    record_exact_accuracy: float = Field(ge=0.0, le=1.0)
    passed: bool


class MetricSummary(_EvalModel):
    """Zero-denominator-safe aggregate evaluation metrics and raw denominator counts."""

    total_cases: int = Field(ge=0)
    total_fields: int = Field(ge=0)
    expected_present_fields: int = Field(ge=0)
    expected_missing_fields: int = Field(ge=0)
    hallucination_count: int = Field(ge=0)
    expected_provenance_fields: int = Field(ge=0)
    field_exact_match_accuracy: float = Field(ge=0.0, le=1.0)
    field_precision: float = Field(ge=0.0, le=1.0)
    field_recall: float = Field(ge=0.0, le=1.0)
    field_f1: float = Field(ge=0.0, le=1.0)
    record_completeness: float = Field(ge=0.0, le=1.0)
    record_level_accuracy: float = Field(ge=0.0, le=1.0)
    false_extraction_rate: float = Field(ge=0.0, le=1.0)
    missing_field_rate: float = Field(ge=0.0, le=1.0)
    hallucination_rate: float = Field(ge=0.0, le=1.0)
    provenance_accuracy: float = Field(ge=0.0, le=1.0)
    validation_pass_rate: float = Field(ge=0.0, le=1.0)
    processing_failure_rate: float = Field(ge=0.0, le=1.0)
    mean_latency_ms: float = Field(ge=0.0)
    max_latency_ms: float = Field(ge=0.0)


class TrialStat(_EvalModel):
    """Summary distribution (mean, min, max) for one metric across repeated trials."""

    mean: float
    min: float
    max: float


TrialDistribution = dict[str, TrialStat]


class EvaluationFilter(_EvalModel):
    """Active subset filters recorded transparently inside EvaluationReport."""

    categories: list[DocumentCategory] = Field(default_factory=list)
    formats: list[DocumentFormat] = Field(default_factory=list)
    profiles: list[ChallengeProfile] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    is_filtered: bool = False

    @model_validator(mode="after")
    def _sync_is_filtered(self) -> EvaluationFilter:
        computed = bool(self.categories or self.formats or self.profiles or self.case_ids)
        if self.is_filtered != computed:
            object.__setattr__(self, "is_filtered", computed)
        return self


class EvaluationReport(_EvalModel):
    """Versioned, serializable evaluation artifact (EvaluationReport v1.0)."""

    report_version: Literal["1.0"] = REPORT_VERSION
    dataset_version: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    executed_at: str = Field(min_length=1)
    trials: int = Field(ge=1)
    filters: EvaluationFilter = Field(default_factory=EvaluationFilter)
    summary: MetricSummary
    trial_summaries: list[MetricSummary] = Field(min_length=1)
    trial_distribution: TrialDistribution | None = None
    by_category: dict[str, MetricSummary] = Field(default_factory=dict)
    by_profile: dict[str, MetricSummary] = Field(default_factory=dict)
    cases: list[CaseOutcome] = Field(default_factory=list)


class RegressionThresholds(_EvalModel):
    """Baseline regression tolerances with strict zero-regression defaults (Clarification Q3)."""

    max_exact_match_drop: float = Field(default=0.0, ge=0.0)
    max_f1_drop: float = Field(default=0.0, ge=0.0)
    max_provenance_drop: float = Field(default=0.0, ge=0.0)
    max_validation_drop: float = Field(default=0.0, ge=0.0)
    max_hallucination_increase: float = Field(default=0.0, ge=0.0)
    max_failure_rate_increase: float = Field(default=0.0, ge=0.0)
    allow_dataset_version_mismatch: bool = False


class CaseDelta(_EvalModel):
    """Per-case attribution of regressions, improvements, or strategy divergences."""

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    direction: Literal["regressed", "improved", "diverged"]
    details: list[str] = Field(default_factory=list)


class ComparisonReport(_EvalModel):
    """Candidate vs. stored baseline regression comparison report."""

    baseline_dataset_version: str
    candidate_dataset_version: str
    dataset_version_match: bool
    baseline_strategy: str
    candidate_strategy: str
    thresholds: RegressionThresholds
    metric_deltas: dict[str, float]
    regressed_cases: list[CaseDelta] = Field(default_factory=list)
    improved_cases: list[CaseDelta] = Field(default_factory=list)
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)


class DifferentialReport(_EvalModel):
    """Side-by-side differential comparison between two strategies or configurations."""

    dataset_version_a: str
    dataset_version_b: str
    dataset_version_match: bool
    strategy_a: str
    strategy_b: str
    metric_deltas: dict[str, float]
    divergent_cases: list[CaseDelta] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
