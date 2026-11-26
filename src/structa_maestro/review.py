"""Stateless human review triage, immutable correction application, and privacy-safe auditing.

Provides pure, zero-dependency programmatic review primitives operating over Canonical Output
Record Contract v1.0 (`ExtractionResult`) without databases, queues, or web UIs:
- `plan_review(result, policy=...) -> ReviewPlan`: deterministic multi-dimension triage
- `apply_review(result, schema, submission, redact_values=True) -> ReviewApplicationResult`:
  immutable decision application (`APPROVE`, `CORRECT`, `MARK_MISSING`, `REJECT_RECORD`) with
  mandatory `DeterministicValidator` single-field, cross-field, and cardinality re-validation
  plus an immutable, privacy-safe `ReviewAudit` trail.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    FieldType,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    ConfidenceAssessment,
    DiagnosticIssue,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    ProvenanceEvidence,
)
from structa_maestro.contracts.serialization import (
    _encode_json_value,
    _restore_typed_field_value,
)
from structa_maestro.exceptions import ConfigurationError, SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.validation.validator import DeterministicValidator

REVIEW_PLAN_VERSION: Literal["1.0"] = "1.0"
REVIEW_SUBMISSION_VERSION: Literal["1.0"] = "1.0"
REVIEW_AUDIT_VERSION: Literal["1.0"] = "1.0"
MAX_REVIEW_PAYLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
_MAX_REASON_NOTE_CHARS = 500

_SUPERSEDED_FIELD_WARNING_CODES = frozenset(
    {
        "ambiguous_source_value",
        "unverified_evidence",
        "evidence_value_conflict",
        "invalid_confidence_value",
        "invalid_confidence_range",
        "confidence_calibration_scale_mismatch",
        "confidence_calibration_failed",
    }
)


class ReviewReason(StrEnum):
    """Orthogonal triage reasons preserving separation across all five quality dimensions."""

    PARTIAL_OR_FAILED_STATUS = "partial_or_failed_status"
    RESULT_VALIDATION_FAILED = "result_validation_failed"
    RECORD_VALIDATION_FAILED = "record_validation_failed"
    FIELD_VALIDATION_FAILED = "field_validation_failed"
    FIELD_ERROR = "field_error"
    LOW_CONFIDENCE = "low_confidence"
    UNCALIBRATED_CONFIDENCE = "uncalibrated_confidence"
    MISSING_CONFIDENCE = "missing_confidence"
    UNVERIFIED_PROVENANCE = "unverified_provenance"
    CONFLICTING_PROVENANCE = "conflicting_provenance"
    MISSING_PROVENANCE = "missing_provenance"
    AMBIGUOUS_VALUE = "ambiguous_value"


_REASON_ORDER: dict[ReviewReason, int] = {reason: idx for idx, reason in enumerate(ReviewReason)}


class ReviewAction(StrEnum):
    """Structured human review actions applied immutably via `apply_review`."""

    APPROVE = "approve"
    CORRECT = "correct"
    MARK_MISSING = "mark_missing"
    REJECT_RECORD = "reject_record"


def _require_strict_bool(value: Any, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean.")
    return value


def _require_nonblank_str(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string.")
    return value.strip()


def _require_optional_note(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string or None.")
    cleaned = value.strip()
    if len(cleaned) > _MAX_REASON_NOTE_CHARS:
        raise ConfigurationError(f"{name} must be <= {_MAX_REASON_NOTE_CHARS} characters.")
    return cleaned


def _require_optional_tag(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    return _require_nonblank_str(value, name=name)


def _require_unit_float_or_none(value: Any, *, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a finite float in [0.0, 1.0] or None.")
    num = float(value)
    if not math.isfinite(num) or not (0.0 <= num <= 1.0):
        raise ConfigurationError(f"{name} must be finite and within [0.0, 1.0].")
    return num


def _coerce_schema(schema: ExtractionSchema | Mapping[str, Any]) -> ExtractionSchema:
    if isinstance(schema, ExtractionSchema):
        return schema
    if isinstance(schema, Mapping):
        return ExtractionSchema.from_dict(schema)
    raise SchemaDefinitionError(
        f"Expected ExtractionSchema or mapping schema definition, got {type(schema).__name__}."
    )


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    """Immutable triage policy configuring opt-in review triggers across all quality dimensions."""

    min_confidence: float | None = 0.85
    require_calibrated_confidence: bool = False
    flag_missing_confidence: bool = False
    flag_unverified_provenance: bool = True
    flag_conflicting_provenance: bool = True
    flag_missing_provenance: bool = False
    flag_ambiguous_fields: bool = True
    flag_field_errors: bool = True
    flag_invalid_fields: bool = True
    flag_invalid_records: bool = True
    flag_partial_or_failed_results: bool = True

    def __post_init__(self) -> None:
        normalized_min = _require_unit_float_or_none(
            self.min_confidence, name="ReviewPolicy.min_confidence"
        )
        object.__setattr__(self, "min_confidence", normalized_min)
        for flag_name in (
            "require_calibrated_confidence",
            "flag_missing_confidence",
            "flag_unverified_provenance",
            "flag_conflicting_provenance",
            "flag_missing_provenance",
            "flag_ambiguous_fields",
            "flag_field_errors",
            "flag_invalid_fields",
            "flag_invalid_records",
            "flag_partial_or_failed_results",
        ):
            _require_strict_bool(getattr(self, flag_name), name=f"ReviewPolicy.{flag_name}")


@dataclass(frozen=True, slots=True)
class ProvenanceSummary:
    """Counts of provenance evidence entries by relationship category for a reviewed field."""

    total_count: int
    direct_count: int
    inferred_count: int
    conflicting_count: int
    unverified_count: int

    def __post_init__(self) -> None:
        for fname in (
            "total_count",
            "direct_count",
            "inferred_count",
            "conflicting_count",
            "unverified_count",
        ):
            val = getattr(self, fname)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ConfigurationError(f"ProvenanceSummary.{fname} must be a non-negative int.")
        expected = (
            self.direct_count + self.inferred_count + self.conflicting_count + self.unverified_count
        )
        if self.total_count != expected:
            raise ConfigurationError("ProvenanceSummary.total_count must equal sum of counts.")

    def to_dict(self) -> dict[str, int]:
        return {
            "total_count": self.total_count,
            "direct_count": self.direct_count,
            "inferred_count": self.inferred_count,
            "conflicting_count": self.conflicting_count,
            "unverified_count": self.unverified_count,
        }


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """Single deterministic triage finding targeting result, record, or field scope."""

    record_id: str | None
    field_path: str | None
    reasons: tuple[ReviewReason, ...]
    presence: FieldPresence | None
    validation_status: ValidationStatus
    processing_status: ProcessingStatus | None
    confidence: ConfidenceAssessment | None
    provenance_summary: ProvenanceSummary | None
    error_codes: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.field_path is not None and self.record_id is None:
            raise ConfigurationError("ReviewItem.field_path requires a non-None record_id.")
        if not self.reasons:
            raise ConfigurationError("ReviewItem.reasons must be non-empty.")
        ordered = tuple(sorted(set(self.reasons), key=lambda r: _REASON_ORDER[r]))
        object.__setattr__(self, "reasons", ordered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field_path": self.field_path,
            "reasons": [r.value for r in self.reasons],
            "presence": self.presence.value if self.presence is not None else None,
            "validation_status": self.validation_status.value,
            "processing_status": (
                self.processing_status.value if self.processing_status is not None else None
            ),
            "confidence": (
                self.confidence.model_dump(mode="json") if self.confidence is not None else None
            ),
            "provenance_summary": (
                self.provenance_summary.to_dict() if self.provenance_summary is not None else None
            ),
            "error_codes": list(self.error_codes),
            "warning_codes": list(self.warning_codes),
        }


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    """Deterministic triage summary returned by `plan_review`."""

    extraction_id: str
    needs_review: bool
    total_items: int
    reason_counts: dict[str, int]
    items: tuple[ReviewItem, ...]
    plan_version: Literal["1.0"] = REVIEW_PLAN_VERSION

    def __post_init__(self) -> None:
        _require_nonblank_str(self.extraction_id, name="ReviewPlan.extraction_id")
        if self.total_items != len(self.items):
            raise ConfigurationError("ReviewPlan.total_items must equal len(items).")
        if self.needs_review != (len(self.items) > 0):
            raise ConfigurationError("ReviewPlan.needs_review must match len(items) > 0.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_version": self.plan_version,
            "extraction_id": self.extraction_id,
            "needs_review": self.needs_review,
            "total_items": self.total_items,
            "reason_counts": dict(self.reason_counts),
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, allow_nan=False)


def _summarize_provenance(field: ExtractedField) -> ProvenanceSummary:
    direct = sum(1 for ev in field.provenance if ev.relationship == EvidenceRelationship.DIRECT)
    inferred = sum(1 for ev in field.provenance if ev.relationship == EvidenceRelationship.INFERRED)
    conflicting = sum(
        1 for ev in field.provenance if ev.relationship == EvidenceRelationship.CONFLICTING
    )
    unverified = sum(
        1 for ev in field.provenance if ev.relationship == EvidenceRelationship.UNVERIFIED
    )
    return ProvenanceSummary(
        total_count=len(field.provenance),
        direct_count=direct,
        inferred_count=inferred,
        conflicting_count=conflicting,
        unverified_count=unverified,
    )


def plan_review(
    result: ExtractionResult,
    *,
    policy: ReviewPolicy | None = None,
) -> ReviewPlan:
    """Pure, non-mutating deterministic triage across all five Contract v1.0 quality dimensions."""
    if not isinstance(result, ExtractionResult):
        raise ConfigurationError("plan_review requires an ExtractionResult instance.")
    if policy is not None and not isinstance(policy, ReviewPolicy):
        raise ConfigurationError("policy must be a ReviewPolicy instance or None.")
    active_policy = policy or ReviewPolicy()

    items: list[ReviewItem] = []

    # 1. Result-level triage
    result_reasons: list[ReviewReason] = []
    if active_policy.flag_partial_or_failed_results and result.status != ProcessingStatus.COMPLETED:
        result_reasons.append(ReviewReason.PARTIAL_OR_FAILED_STATUS)
    if active_policy.flag_invalid_records and result.validation.status == ValidationStatus.INVALID:
        has_result_scoped_err = any(e.record_id is None for e in result.validation.errors)
        if has_result_scoped_err or not result.records or not result.validation.errors:
            result_reasons.append(ReviewReason.RESULT_VALIDATION_FAILED)
    if result_reasons:
        res_err_codes = tuple(
            e.code
            for e in (*result.errors, *(e for e in result.validation.errors if e.record_id is None))
        )
        res_warn_codes = tuple(w.code for w in result.warnings)
        items.append(
            ReviewItem(
                record_id=None,
                field_path=None,
                reasons=tuple(result_reasons),
                presence=None,
                validation_status=result.validation.status,
                processing_status=result.status,
                confidence=None,
                provenance_summary=None,
                error_codes=res_err_codes,
                warning_codes=res_warn_codes,
            )
        )

    # 2. Record-level & field-level triage in canonical order
    for rec in result.records:
        rec_reasons: list[ReviewReason] = []
        if (
            active_policy.flag_partial_or_failed_results
            and rec.status != ProcessingStatus.COMPLETED
        ):
            rec_reasons.append(ReviewReason.PARTIAL_OR_FAILED_STATUS)

        field_val_err_sigs = {
            (e.field_path, e.code, e.message)
            for f in rec.fields.values()
            for e in f.validation.errors
        }
        rec_only_val_errs = [
            e
            for e in rec.validation.errors
            if (e.field_path, e.code, e.message) not in field_val_err_sigs
        ]
        if (
            active_policy.flag_invalid_records
            and rec.validation.status == ValidationStatus.INVALID
            and (rec_only_val_errs or not rec.validation.errors)
        ):
            rec_reasons.append(ReviewReason.RECORD_VALIDATION_FAILED)

        if rec_reasons:
            items.append(
                ReviewItem(
                    record_id=rec.record_id,
                    field_path=None,
                    reasons=tuple(rec_reasons),
                    presence=None,
                    validation_status=rec.validation.status,
                    processing_status=rec.status,
                    confidence=None,
                    provenance_summary=None,
                    error_codes=tuple(e.code for e in (*rec.errors, *rec_only_val_errs)),
                    warning_codes=tuple(w.code for w in rec.warnings),
                )
            )

        for fname in sorted(rec.fields.keys()):
            field = rec.fields[fname]
            f_reasons: list[ReviewReason] = []
            if active_policy.flag_invalid_fields and (
                field.validation.status == ValidationStatus.INVALID
            ):
                f_reasons.append(ReviewReason.FIELD_VALIDATION_FAILED)
            if active_policy.flag_field_errors and field.errors:
                f_reasons.append(ReviewReason.FIELD_ERROR)

            if field.presence == FieldPresence.PRESENT:
                conf = field.confidence
                if conf is None or conf.value is None:
                    if active_policy.flag_missing_confidence:
                        f_reasons.append(ReviewReason.MISSING_CONFIDENCE)
                else:
                    if (
                        active_policy.min_confidence is not None
                        and conf.scale == "0-1"
                        and conf.value < active_policy.min_confidence
                    ):
                        f_reasons.append(ReviewReason.LOW_CONFIDENCE)
                    if active_policy.require_calibrated_confidence and not conf.calibrated:
                        f_reasons.append(ReviewReason.UNCALIBRATED_CONFIDENCE)

                if not field.provenance:
                    if active_policy.flag_missing_provenance:
                        f_reasons.append(ReviewReason.MISSING_PROVENANCE)
                else:
                    if active_policy.flag_unverified_provenance and any(
                        ev.relationship == EvidenceRelationship.UNVERIFIED
                        for ev in field.provenance
                    ):
                        f_reasons.append(ReviewReason.UNVERIFIED_PROVENANCE)
                    if active_policy.flag_conflicting_provenance and any(
                        ev.relationship == EvidenceRelationship.CONFLICTING
                        for ev in field.provenance
                    ):
                        f_reasons.append(ReviewReason.CONFLICTING_PROVENANCE)

                if active_policy.flag_ambiguous_fields and any(
                    w.code == "ambiguous_source_value" for w in field.warnings
                ):
                    f_reasons.append(ReviewReason.AMBIGUOUS_VALUE)

            if f_reasons:
                items.append(
                    ReviewItem(
                        record_id=rec.record_id,
                        field_path=fname,
                        reasons=tuple(f_reasons),
                        presence=field.presence,
                        validation_status=field.validation.status,
                        processing_status=None,
                        confidence=field.confidence,
                        provenance_summary=_summarize_provenance(field),
                        error_codes=tuple(
                            e.code for e in (*field.errors, *field.validation.errors)
                        ),
                        warning_codes=tuple(w.code for w in field.warnings),
                    )
                )

    counts: dict[str, int] = {}
    for item in items:
        for reason in item.reasons:
            counts[reason.value] = counts.get(reason.value, 0) + 1
    sorted_counts = dict(sorted(counts.items()))

    return ReviewPlan(
        extraction_id=result.extraction_id,
        needs_review=bool(items),
        total_items=len(items),
        reason_counts=sorted_counts,
        items=tuple(items),
    )


@dataclass(frozen=True, slots=True)
class FieldReviewDecision:
    """Immutable human review decision targeting a single `(record_id, field_path)`."""

    record_id: str
    field_path: str
    action: ReviewAction
    value: Any = None
    value_type: FieldType | None = None
    preserve_provenance: bool = False
    reason_note: str | None = None

    def __post_init__(self) -> None:
        clean_rid = _require_nonblank_str(self.record_id, name="FieldReviewDecision.record_id")
        clean_fp = _require_nonblank_str(self.field_path, name="FieldReviewDecision.field_path")
        object.__setattr__(self, "record_id", clean_rid)
        object.__setattr__(self, "field_path", clean_fp)
        try:
            resolved_action = (
                self.action if isinstance(self.action, ReviewAction) else ReviewAction(self.action)
            )
        except ValueError as exc:
            raise ConfigurationError("Invalid FieldReviewDecision.action.") from exc
        if resolved_action == ReviewAction.REJECT_RECORD:
            raise ConfigurationError(
                "FieldReviewDecision.action cannot be 'reject_record'; use RecordReviewDecision."
            )
        object.__setattr__(self, "action", resolved_action)
        if resolved_action in (ReviewAction.APPROVE, ReviewAction.MARK_MISSING) and (
            self.value is not None
        ):
            raise ConfigurationError(
                f"FieldReviewDecision.value must be None when action is '{resolved_action.value}'."
            )
        if self.value_type is not None:
            try:
                resolved_vt = (
                    self.value_type
                    if isinstance(self.value_type, FieldType)
                    else FieldType(self.value_type)
                )
            except ValueError as exc:
                raise ConfigurationError("Invalid FieldReviewDecision.value_type.") from exc
            object.__setattr__(self, "value_type", resolved_vt)
        _require_strict_bool(
            self.preserve_provenance, name="FieldReviewDecision.preserve_provenance"
        )
        clean_note = _require_optional_note(
            self.reason_note, name="FieldReviewDecision.reason_note"
        )
        object.__setattr__(self, "reason_note", clean_note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field_path": self.field_path,
            "action": self.action.value,
            "value": _encode_json_value(self.value),
            "value_type": self.value_type.value if self.value_type is not None else None,
            "preserve_provenance": self.preserve_provenance,
            "reason_note": self.reason_note,
        }


@dataclass(frozen=True, slots=True)
class RecordReviewDecision:
    """Immutable human review decision rejecting an entire extracted record."""

    record_id: str
    action: Literal[ReviewAction.REJECT_RECORD] = ReviewAction.REJECT_RECORD
    reason_note: str | None = None

    def __post_init__(self) -> None:
        clean_rid = _require_nonblank_str(self.record_id, name="RecordReviewDecision.record_id")
        object.__setattr__(self, "record_id", clean_rid)
        try:
            resolved_action = (
                self.action if isinstance(self.action, ReviewAction) else ReviewAction(self.action)
            )
        except ValueError as exc:
            raise ConfigurationError("Invalid RecordReviewDecision.action.") from exc
        if resolved_action != ReviewAction.REJECT_RECORD:
            raise ConfigurationError("RecordReviewDecision.action must be 'reject_record'.")
        object.__setattr__(self, "action", resolved_action)
        clean_note = _require_optional_note(
            self.reason_note, name="RecordReviewDecision.reason_note"
        )
        object.__setattr__(self, "reason_note", clean_note)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "action": self.action.value,
            "reason_note": self.reason_note,
        }


_SUBMISSION_ALLOWED_KEYS = frozenset(
    {"submission_version", "reviewer_id", "field_decisions", "record_decisions"}
)
_FIELD_DECISION_ALLOWED_KEYS = frozenset(
    {
        "record_id",
        "field_path",
        "action",
        "value",
        "value_type",
        "preserve_provenance",
        "reason_note",
    }
)
_RECORD_DECISION_ALLOWED_KEYS = frozenset({"record_id", "action", "reason_note"})


@dataclass(frozen=True, slots=True)
class ReviewSubmission:
    """Immutable, strictly validated batch of human field and record review decisions."""

    field_decisions: tuple[FieldReviewDecision, ...] = ()
    record_decisions: tuple[RecordReviewDecision, ...] = ()
    reviewer_id: str | None = None
    submission_version: Literal["1.0"] = REVIEW_SUBMISSION_VERSION

    def __post_init__(self) -> None:
        if self.submission_version != REVIEW_SUBMISSION_VERSION:
            raise ConfigurationError(
                f"Unsupported ReviewSubmission.submission_version; expected "
                f"'{REVIEW_SUBMISSION_VERSION}'."
            )
        clean_reviewer = _require_optional_tag(
            self.reviewer_id, name="ReviewSubmission.reviewer_id"
        )
        object.__setattr__(self, "reviewer_id", clean_reviewer)

        f_decs = tuple(self.field_decisions)
        r_decs = tuple(self.record_decisions)
        for dec in f_decs:
            if not isinstance(dec, FieldReviewDecision):
                raise ConfigurationError(
                    "ReviewSubmission.field_decisions must contain FieldReviewDecision instances."
                )
        for rdec in r_decs:
            if not isinstance(rdec, RecordReviewDecision):
                raise ConfigurationError(
                    "ReviewSubmission.record_decisions must contain RecordReviewDecision instances."
                )

        seen_records: set[str] = set()
        for rdec in r_decs:
            if rdec.record_id in seen_records:
                raise ConfigurationError(
                    f"Duplicate RecordReviewDecision for record_id '{rdec.record_id}'."
                )
            seen_records.add(rdec.record_id)

        seen_fields: set[tuple[str, str]] = set()
        for fdec in f_decs:
            if fdec.record_id in seen_records:
                raise ConfigurationError(
                    f"Conflicting review decisions: record '{fdec.record_id}' has both "
                    "record-level rejection and field-level decisions."
                )
            key = (fdec.record_id, fdec.field_path)
            if key in seen_fields:
                raise ConfigurationError(
                    f"Duplicate FieldReviewDecision for ('{fdec.record_id}', '{fdec.field_path}')."
                )
            seen_fields.add(key)

        object.__setattr__(self, "field_decisions", f_decs)
        object.__setattr__(self, "record_decisions", r_decs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_version": self.submission_version,
            "reviewer_id": self.reviewer_id,
            "field_decisions": [d.to_dict() for d in self.field_decisions],
            "record_decisions": [d.to_dict() for d in self.record_decisions],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, allow_nan=False)

    def to_file(self, path: str | Path, *, indent: int = 2) -> Path:
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.to_json(indent=indent) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"Failed to write ReviewSubmission ({type(exc).__name__})."
            ) from exc
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReviewSubmission:
        if not isinstance(data, Mapping):
            raise ConfigurationError("ReviewSubmission payload must be a JSON object.")
        extra = set(data.keys()) - _SUBMISSION_ALLOWED_KEYS
        if extra:
            raise ConfigurationError(
                f"Unexpected key(s) in ReviewSubmission: {', '.join(sorted(map(str, extra)))}."
            )
        version = data.get("submission_version", REVIEW_SUBMISSION_VERSION)
        if version != REVIEW_SUBMISSION_VERSION:
            raise ConfigurationError(
                f"Unsupported submission_version; expected '{REVIEW_SUBMISSION_VERSION}'."
            )
        raw_field_decs = data.get("field_decisions", [])
        raw_record_decs = data.get("record_decisions", [])
        if not isinstance(raw_field_decs, Sequence) or isinstance(raw_field_decs, (str, bytes)):
            raise ConfigurationError("ReviewSubmission.field_decisions must be an array.")
        if not isinstance(raw_record_decs, Sequence) or isinstance(raw_record_decs, (str, bytes)):
            raise ConfigurationError("ReviewSubmission.record_decisions must be an array.")

        parsed_fields: list[FieldReviewDecision] = []
        for idx, entry in enumerate(raw_field_decs):
            if not isinstance(entry, Mapping):
                raise ConfigurationError(f"field_decisions[{idx}] must be an object.")
            f_extra = set(entry.keys()) - _FIELD_DECISION_ALLOWED_KEYS
            if f_extra:
                raise ConfigurationError(
                    f"Unexpected key(s) in field_decisions[{idx}]: "
                    f"{', '.join(sorted(map(str, f_extra)))}."
                )
            vt_raw = entry.get("value_type")
            val = entry.get("value")
            if vt_raw is not None:
                try:
                    vt_enum = FieldType(vt_raw)
                except ValueError as exc:
                    raise ConfigurationError(
                        f"Invalid value_type in field_decisions[{idx}]."
                    ) from exc
                if vt_enum in (FieldType.DECIMAL, FieldType.DATE):
                    val = _restore_typed_field_value(val, vt_enum.value)
            else:
                vt_enum = None
            parsed_fields.append(
                FieldReviewDecision(
                    record_id=entry.get("record_id"),  # type: ignore[arg-type]
                    field_path=entry.get("field_path"),  # type: ignore[arg-type]
                    action=entry.get("action"),  # type: ignore[arg-type]
                    value=val,
                    value_type=vt_enum,
                    preserve_provenance=entry.get("preserve_provenance", False),
                    reason_note=entry.get("reason_note"),
                )
            )

        parsed_records: list[RecordReviewDecision] = []
        for idx, entry in enumerate(raw_record_decs):
            if not isinstance(entry, Mapping):
                raise ConfigurationError(f"record_decisions[{idx}] must be an object.")
            r_extra = set(entry.keys()) - _RECORD_DECISION_ALLOWED_KEYS
            if r_extra:
                raise ConfigurationError(
                    f"Unexpected key(s) in record_decisions[{idx}]: "
                    f"{', '.join(sorted(map(str, r_extra)))}."
                )
            parsed_records.append(
                RecordReviewDecision(
                    record_id=entry.get("record_id"),  # type: ignore[arg-type]
                    action=entry.get("action", ReviewAction.REJECT_RECORD),
                    reason_note=entry.get("reason_note"),
                )
            )

        return cls(
            submission_version=REVIEW_SUBMISSION_VERSION,
            reviewer_id=data.get("reviewer_id"),
            field_decisions=tuple(parsed_fields),
            record_decisions=tuple(parsed_records),
        )

    @classmethod
    def from_json(cls, raw_json: str) -> ReviewSubmission:
        if not isinstance(raw_json, str) or not raw_json.strip():
            raise ConfigurationError("ReviewSubmission JSON must be a non-empty string.")
        if len(raw_json.encode("utf-8")) > MAX_REVIEW_PAYLOAD_BYTES:
            raise ConfigurationError(
                f"ReviewSubmission exceeds maximum size limit ({MAX_REVIEW_PAYLOAD_BYTES} bytes)."
            )
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("ReviewSubmission is not valid JSON.") from exc
        if not isinstance(parsed, Mapping):
            raise ConfigurationError("ReviewSubmission JSON root must be an object.")
        return cls.from_dict(parsed)

    @classmethod
    def from_file(cls, path: str | Path) -> ReviewSubmission:
        if isinstance(path, str) and not path.strip():
            raise ConfigurationError("ReviewSubmission file path must be non-empty.")
        target = Path(path)
        try:
            if not target.exists() or not target.is_file():
                raise ConfigurationError(f"ReviewSubmission file not found: '{target.name}'.")
            if target.stat().st_size > MAX_REVIEW_PAYLOAD_BYTES:
                raise ConfigurationError(
                    f"ReviewSubmission exceeds maximum size limit "
                    f"({MAX_REVIEW_PAYLOAD_BYTES} bytes)."
                )
            raw_text = target.read_text(encoding="utf-8")
        except ConfigurationError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(
                f"Unable to read ReviewSubmission ({type(exc).__name__})."
            ) from exc
        return cls.from_json(raw_text)


@dataclass(frozen=True, slots=True)
class ReviewAuditEntry:
    """Single immutable audit entry recording one applied human review decision."""

    record_id: str
    field_path: str | None
    action: ReviewAction
    value_changed: bool
    before_presence: FieldPresence | None
    after_presence: FieldPresence | None
    before_validation_status: ValidationStatus
    after_validation_status: ValidationStatus | None
    before_value: Any = None
    after_value: Any = None
    reason_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field_path": self.field_path,
            "action": self.action.value,
            "value_changed": self.value_changed,
            "before_presence": (
                self.before_presence.value if self.before_presence is not None else None
            ),
            "after_presence": (
                self.after_presence.value if self.after_presence is not None else None
            ),
            "before_validation_status": self.before_validation_status.value,
            "after_validation_status": (
                self.after_validation_status.value
                if self.after_validation_status is not None
                else None
            ),
            "before_value": _encode_json_value(self.before_value),
            "after_value": _encode_json_value(self.after_value),
            "reason_note": self.reason_note,
        }


@dataclass(frozen=True, slots=True)
class ReviewAudit:
    """Immutable, privacy-safe audit trail paired with every `apply_review` result."""

    extraction_id: str
    reviewer_id: str | None
    reviewed_at: str
    values_redacted: bool
    total_decisions: int
    approved_count: int
    corrected_count: int
    marked_missing_count: int
    rejected_record_count: int
    entries: tuple[ReviewAuditEntry, ...]
    audit_version: Literal["1.0"] = REVIEW_AUDIT_VERSION

    def __post_init__(self) -> None:
        _require_nonblank_str(self.extraction_id, name="ReviewAudit.extraction_id")
        _require_nonblank_str(self.reviewed_at, name="ReviewAudit.reviewed_at")
        _require_strict_bool(self.values_redacted, name="ReviewAudit.values_redacted")
        expected_total = (
            self.approved_count
            + self.corrected_count
            + self.marked_missing_count
            + self.rejected_record_count
        )
        if self.total_decisions != len(self.entries) or self.total_decisions != expected_total:
            raise ConfigurationError("ReviewAudit decision counts are inconsistent.")
        if self.values_redacted:
            for entry in self.entries:
                if entry.before_value is not None or entry.after_value is not None:
                    raise ConfigurationError(
                        "Redacted ReviewAudit entries must not contain raw values."
                    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_version": self.audit_version,
            "extraction_id": self.extraction_id,
            "reviewer_id": self.reviewer_id,
            "reviewed_at": self.reviewed_at,
            "values_redacted": self.values_redacted,
            "total_decisions": self.total_decisions,
            "approved_count": self.approved_count,
            "corrected_count": self.corrected_count,
            "marked_missing_count": self.marked_missing_count,
            "rejected_record_count": self.rejected_record_count,
            "entries": [e.to_dict() for e in self.entries],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, allow_nan=False)


@dataclass(frozen=True, slots=True)
class ReviewApplicationResult:
    """Immutable output pairing the updated Contract v1.0 ExtractionResult and its ReviewAudit."""

    result: ExtractionResult
    audit: ReviewAudit


@dataclass(frozen=True, slots=True)
class _StagedField:
    presence: FieldPresence
    value: Any
    value_type: FieldType | None
    confidence: ConfidenceAssessment | None
    provenance: list[ProvenanceEvidence]
    errors: list[DiagnosticIssue]
    warnings: list[DiagnosticIssue]


def _values_equal_strict(a: Any, b: Any) -> bool:
    return type(a) is type(b) and bool(a == b)


def apply_review(
    result: ExtractionResult,
    schema: ExtractionSchema | Mapping[str, Any],
    submission: ReviewSubmission,
    *,
    redact_values: bool = True,
    validator: DeterministicValidator | None = None,
) -> ReviewApplicationResult:
    """Immutably apply human review decisions, re-validate deterministically, and emit audit."""
    if not isinstance(result, ExtractionResult):
        raise ConfigurationError("apply_review requires an ExtractionResult instance.")
    if not isinstance(submission, ReviewSubmission):
        raise ConfigurationError("apply_review requires a ReviewSubmission instance.")
    _require_strict_bool(redact_values, name="redact_values")
    if validator is not None and not isinstance(validator, DeterministicValidator):
        raise ConfigurationError("validator must be a DeterministicValidator instance or None.")

    parsed_schema = _coerce_schema(schema)
    active_validator = validator or DeterministicValidator()

    records_by_id: dict[str, ExtractedRecord] = {rec.record_id: rec for rec in result.records}
    for rdec in submission.record_decisions:
        if rdec.record_id not in records_by_id:
            raise ConfigurationError(
                f"Unknown record_id '{rdec.record_id}' in ReviewSubmission.record_decisions."
            )
    for fdec in submission.field_decisions:
        if fdec.record_id not in records_by_id:
            raise ConfigurationError(
                f"Unknown record_id '{fdec.record_id}' in ReviewSubmission.field_decisions."
            )

    rejected_ids = {rdec.record_id for rdec in submission.record_decisions}
    decisions_by_record: dict[str, dict[str, FieldReviewDecision]] = {}
    for fdec in submission.field_decisions:
        decisions_by_record.setdefault(fdec.record_id, {})[fdec.field_path] = fdec

    # Track whether original result was PARTIAL for non-record reasons (e.g., strategy partial)
    orig_non_record_partial = result.status == ProcessingStatus.PARTIAL and all(
        r.status == ProcessingStatus.COMPLETED for r in result.records
    )

    updated_records: list[ExtractedRecord] = []
    updated_records_by_id: dict[str, ExtractedRecord] = {}
    field_before_snapshots: dict[tuple[str, str], tuple[FieldPresence, Any, ValidationStatus]] = {}

    for rec in result.records:
        if rec.record_id in rejected_ids:
            continue

        rec_field_decs = decisions_by_record.get(rec.record_id, {})
        ordered_names: list[str] = list(parsed_schema.fields.keys())
        extra_keys = sorted(
            (set(rec.fields.keys()) | set(rec_field_decs.keys())) - set(parsed_schema.fields.keys())
        )
        ordered_names.extend(extra_keys)

        # Stage updated field values/metadata prior to deterministic re-validation
        staged: dict[str, _StagedField] = {}
        validation_input: dict[str, tuple[FieldPresence, Any]] = {}

        for fname in ordered_names:
            existing = rec.fields.get(fname)
            dec = rec_field_decs.get(fname)
            spec = parsed_schema.fields.get(fname)
            value_type = (
                spec.field_type
                if spec is not None
                else (existing.value_type if existing is not None else None)
            )

            if existing is not None:
                before_pres = existing.presence
                before_val = existing.value
                before_vstat = existing.validation.status
            else:
                before_pres = FieldPresence.MISSING
                before_val = None
                before_vstat = ValidationStatus.NOT_VALIDATED

            if dec is not None:
                field_before_snapshots[(rec.record_id, fname)] = (
                    before_pres,
                    before_val,
                    before_vstat,
                )

            if dec is None or dec.action == ReviewAction.APPROVE:
                if existing is not None:
                    staged_field = _StagedField(
                        presence=existing.presence,
                        value=existing.value,
                        value_type=value_type,
                        confidence=existing.confidence,
                        provenance=list(existing.provenance),
                        errors=list(existing.errors),
                        warnings=list(existing.warnings),
                    )
                else:
                    staged_field = _StagedField(
                        presence=FieldPresence.MISSING,
                        value=None,
                        value_type=value_type,
                        confidence=None,
                        provenance=[],
                        errors=[],
                        warnings=[],
                    )
            elif dec.action == ReviewAction.MARK_MISSING:
                kept_warnings = (
                    [w for w in existing.warnings if w.code not in _SUPERSEDED_FIELD_WARNING_CODES]
                    if existing is not None
                    else []
                )
                kept_errors = (
                    [
                        e
                        for e in existing.errors
                        if e.stage not in (PipelineStage.EXTRACTION, PipelineStage.PROVENANCE)
                    ]
                    if existing is not None
                    else []
                )
                staged_field = _StagedField(
                    presence=FieldPresence.MISSING,
                    value=None,
                    value_type=value_type,
                    confidence=None,
                    provenance=[],
                    errors=kept_errors,
                    warnings=kept_warnings,
                )
            else:
                # ReviewAction.CORRECT
                changed = (before_pres != FieldPresence.PRESENT) or (
                    not _values_equal_strict(before_val, dec.value)
                )
                kept_prov = (
                    list(existing.provenance)
                    if existing is not None and (dec.preserve_provenance or not changed)
                    else []
                )
                kept_warnings = (
                    [w for w in existing.warnings if w.code not in _SUPERSEDED_FIELD_WARNING_CODES]
                    if existing is not None
                    else []
                )
                kept_errors = (
                    [
                        e
                        for e in existing.errors
                        if e.stage not in (PipelineStage.EXTRACTION, PipelineStage.PROVENANCE)
                    ]
                    if existing is not None
                    else []
                )
                staged_field = _StagedField(
                    presence=FieldPresence.PRESENT,
                    value=dec.value,
                    value_type=value_type,
                    confidence=ConfidenceAssessment(
                        value=1.0,
                        scale="0-1",
                        method="human-review",
                        calibrated=False,
                    ),
                    provenance=kept_prov,
                    errors=kept_errors,
                    warnings=kept_warnings,
                )

            staged[fname] = staged_field
            validation_input[fname] = (staged_field.presence, staged_field.value)

        val_result = active_validator.validate_record(
            validation_input,
            parsed_schema,
            record_id=rec.record_id,
        )

        new_fields: dict[str, ExtractedField] = {}
        for fname in ordered_names:
            sf = staged[fname]
            new_fields[fname] = ExtractedField(
                presence=sf.presence,
                value=sf.value,
                value_type=sf.value_type,
                confidence=sf.confidence,
                provenance=sf.provenance,
                validation=val_result.field_outcomes[fname],
                errors=sf.errors,
                warnings=sf.warnings,
            )

        orig_had_field_errors = any(bool(f.errors) for f in rec.fields.values())
        orig_explicit_partial = (
            rec.status == ProcessingStatus.PARTIAL and not rec.errors and not orig_had_field_errors
        )
        new_any_field_errors = any(bool(f.errors) for f in new_fields.values())

        if rec.status == ProcessingStatus.FAILED:
            new_rec_status = ProcessingStatus.FAILED
        elif rec.errors or new_any_field_errors or orig_explicit_partial:
            new_rec_status = ProcessingStatus.PARTIAL
        else:
            new_rec_status = ProcessingStatus.COMPLETED

        non_val_rec_warnings = [w for w in rec.warnings if w.stage != PipelineStage.VALIDATION]
        new_rec = ExtractedRecord(
            record_id=rec.record_id,
            status=new_rec_status,
            fields=new_fields,
            validation=val_result.record_outcome,
            errors=list(rec.errors),
            warnings=[*non_val_rec_warnings, *val_result.warnings],
        )
        updated_records.append(new_rec)
        updated_records_by_id[new_rec.record_id] = new_rec

    failed_before_val = result.status == ProcessingStatus.FAILED and not result.records
    overall_validation = active_validator.validate_result_cardinality(
        [r.validation for r in updated_records],
        parsed_schema,
        has_records=bool(updated_records),
        failed_before_validation=failed_before_val,
    )

    any_rec_not_completed = any(r.status != ProcessingStatus.COMPLETED for r in updated_records)
    if result.status == ProcessingStatus.FAILED and not updated_records:
        overall_status = ProcessingStatus.FAILED
    elif (
        result.status == ProcessingStatus.FAILED
        or bool(result.errors)
        or orig_non_record_partial
        or any_rec_not_completed
    ):
        overall_status = ProcessingStatus.PARTIAL
    else:
        overall_status = ProcessingStatus.COMPLETED

    non_val_top_warnings = [w for w in result.warnings if w.stage != PipelineStage.VALIDATION]
    propagated_val_warnings: list[DiagnosticIssue] = [
        w for r in updated_records for w in r.warnings if w.stage == PipelineStage.VALIDATION
    ]

    updated_result = ExtractionResult(
        extraction_id=result.extraction_id,
        status=overall_status,
        source=result.source,
        schema_metadata=result.schema_metadata,
        records=updated_records,
        validation=overall_validation,
        errors=list(result.errors),
        warnings=[*non_val_top_warnings, *propagated_val_warnings],
    )

    # Assemble deterministic ReviewAudit entries in submission order: record_decisions, then fields
    audit_entries: list[ReviewAuditEntry] = []
    approved_count = 0
    corrected_count = 0
    marked_missing_count = 0
    rejected_record_count = 0

    for rdec in submission.record_decisions:
        orig_rec = records_by_id[rdec.record_id]
        rejected_record_count += 1
        audit_entries.append(
            ReviewAuditEntry(
                record_id=rdec.record_id,
                field_path=None,
                action=ReviewAction.REJECT_RECORD,
                value_changed=True,
                before_presence=None,
                after_presence=None,
                before_validation_status=orig_rec.validation.status,
                after_validation_status=None,
                before_value=None,
                after_value=None,
                reason_note=rdec.reason_note,
            )
        )

    for fdec in submission.field_decisions:
        before_pres, before_val, before_vstat = field_before_snapshots[
            (fdec.record_id, fdec.field_path)
        ]
        after_field = updated_records_by_id[fdec.record_id].fields[fdec.field_path]
        after_pres = after_field.presence
        after_val = after_field.value
        after_vstat = after_field.validation.status
        changed = (before_pres != after_pres) or (not _values_equal_strict(before_val, after_val))

        if fdec.action == ReviewAction.APPROVE:
            approved_count += 1
        elif fdec.action == ReviewAction.CORRECT:
            corrected_count += 1
        elif fdec.action == ReviewAction.MARK_MISSING:
            marked_missing_count += 1

        audit_entries.append(
            ReviewAuditEntry(
                record_id=fdec.record_id,
                field_path=fdec.field_path,
                action=fdec.action,
                value_changed=changed,
                before_presence=before_pres,
                after_presence=after_pres,
                before_validation_status=before_vstat,
                after_validation_status=after_vstat,
                before_value=None if redact_values else _encode_json_value(before_val),
                after_value=None if redact_values else _encode_json_value(after_val),
                reason_note=fdec.reason_note,
            )
        )

    audit = ReviewAudit(
        extraction_id=result.extraction_id,
        reviewer_id=submission.reviewer_id,
        reviewed_at=datetime.now(UTC).isoformat(),
        values_redacted=redact_values,
        total_decisions=len(audit_entries),
        approved_count=approved_count,
        corrected_count=corrected_count,
        marked_missing_count=marked_missing_count,
        rejected_record_count=rejected_record_count,
        entries=tuple(audit_entries),
    )

    return ReviewApplicationResult(result=updated_result, audit=audit)
