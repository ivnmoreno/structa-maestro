"""Pure-Python formal reference predicates and Contract v1.0 conformance test vectors.

These predicates mirror the exact mathematical definitions in `formal/StructaFormal/Models.lean`,
`ValidationInvariants.lean`, `ConfidenceInvariants.lean`, `ProvenanceInvariants.lean`, and
`StatusAssembly.lean`. Layer 1 tests run them directly in any Python environment; Layer 2 tests
cross-check the exact same JSON vectors against the compiled Lean 4 `structa-formal-oracle`.
"""

from __future__ import annotations

import math
from typing import Any

from structa_maestro.contracts.enums import (
    EvidenceRelationship,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)

_VALID_SEVERITIES = {e.value for e in IssueSeverity}
_VALID_STAGES = {e.value for e in PipelineStage}
_VALID_VALIDATION_STATUSES = {e.value for e in ValidationStatus}
_VALID_PROCESSING_STATUSES = {e.value for e in ProcessingStatus}
_VALID_PRESENCES = {e.value for e in FieldPresence}
_VALID_RELATIONSHIPS = {e.value for e in EvidenceRelationship}


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and len(value) >= 1


def is_valid_source_location_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidSourceLocation`."""
    for key in ("page", "paragraph", "line_start", "line_end"):
        val = payload.get(key)
        if val is not None and (not _is_strict_int(val) or val < 1):
            return False
    for key in ("char_start", "char_end"):
        val = payload.get(key)
        if val is not None and (not _is_strict_int(val) or val < 0):
            return False
    section = payload.get("section")
    if section is not None and not isinstance(section, str):
        return False
    ls, le = payload.get("line_start"), payload.get("line_end")
    if ls is not None and le is not None and le < ls:
        return False
    cs, ce = payload.get("char_start"), payload.get("char_end")
    return not (cs is not None and ce is not None and ce < cs)


def is_valid_confidence_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidConfidenceAssessment`."""
    scale = payload.get("scale", "0-1")
    method = payload.get("method", "extraction-assessment")
    calibrated = payload.get("calibrated", False)
    if not _is_nonempty_str(scale) or not _is_nonempty_str(method):
        return False
    if not isinstance(calibrated, bool):
        return False
    val = payload.get("value")
    if val is None:
        return True
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    if math.isnan(float(val)) or math.isinf(float(val)):
        return False
    return not (scale == "0-1" and not (0.0 <= float(val) <= 1.0))


def is_valid_diagnostic_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidDiagnosticIssue`."""
    return (
        _is_nonempty_str(payload.get("code"))
        and _is_nonempty_str(payload.get("message"))
        and payload.get("severity") in _VALID_SEVERITIES
        and payload.get("stage") in _VALID_STAGES
    )


def is_valid_validation_outcome_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidValidationOutcome`."""
    status = payload.get("status")
    if status not in _VALID_VALIDATION_STATUSES:
        return False
    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        return False
    for err in errors:
        if not isinstance(err, dict) or not is_valid_diagnostic_dict(err):
            return False
        if err.get("severity") != IssueSeverity.ERROR.value:
            return False
        if err.get("stage") != PipelineStage.VALIDATION.value:
            return False
    return not (status != ValidationStatus.INVALID.value and len(errors) > 0)


def is_valid_provenance_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidProvenanceEvidence`."""
    if not _is_nonempty_str(payload.get("source_document_id")):
        return False
    if payload.get("relationship") not in _VALID_RELATIONSHIPS:
        return False
    if not isinstance(payload.get("evidence", ""), str):
        return False
    if not isinstance(payload.get("truncated", False), bool):
        return False
    loc = payload.get("location")
    return loc is None or (isinstance(loc, dict) and is_valid_source_location_dict(loc))


def is_valid_extracted_field_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidExtractedField`."""
    presence = payload.get("presence")
    if presence not in _VALID_PRESENCES:
        return False
    if presence == FieldPresence.MISSING.value and payload.get("value") is not None:
        return False
    conf = payload.get("confidence")
    if conf is not None and (not isinstance(conf, dict) or not is_valid_confidence_dict(conf)):
        return False
    prov = payload.get("provenance", [])
    if not isinstance(prov, list) or not all(
        isinstance(ev, dict) and is_valid_provenance_dict(ev) for ev in prov
    ):
        return False
    val = payload.get("validation")
    if not isinstance(val, dict) or not is_valid_validation_outcome_dict(val):
        return False
    for key in ("errors", "warnings"):
        items = payload.get(key, [])
        if not isinstance(items, list) or not all(
            isinstance(d, dict) and is_valid_diagnostic_dict(d) for d in items
        ):
            return False
    return True


def is_valid_extraction_result_dict(payload: dict[str, Any]) -> bool:
    """Reference mirror of `StructaFormal.isValidExtractionResult`."""
    if payload.get("contract_version", "1.0") != "1.0":
        return False
    if not _is_nonempty_str(payload.get("extraction_id")):
        return False
    if payload.get("status") not in _VALID_PROCESSING_STATUSES:
        return False
    source = payload.get("source")
    expected_doc_id: str | None = None
    if source is not None:
        if not isinstance(source, dict) or not _is_nonempty_str(source.get("document_id")):
            return False
        expected_doc_id = source["document_id"]
    schema = payload.get("schema", payload.get("schema_metadata"))
    if (
        not isinstance(schema, dict)
        or not _is_nonempty_str(schema.get("schema_id"))
        or not _is_nonempty_str(schema.get("schema_version"))
    ):
        return False
    records = payload.get("records", [])
    if not isinstance(records, list):
        return False
    seen_ids: set[str] = set()
    for rec in records:
        if not isinstance(rec, dict):
            return False
        rid = rec.get("record_id")
        if not _is_nonempty_str(rid) or rid in seen_ids:
            return False
        seen_ids.add(rid)
        if rec.get("status") not in _VALID_PROCESSING_STATUSES:
            return False
        fields = rec.get("fields", {})
        if not isinstance(fields, dict):
            return False
        for ef in fields.values():
            if not isinstance(ef, dict) or not is_valid_extracted_field_dict(ef):
                return False
            if expected_doc_id is not None:
                for ev in ef.get("provenance", []):
                    if ev.get("source_document_id") != expected_doc_id:
                        return False
        rec_val = rec.get("validation")
        if not isinstance(rec_val, dict) or not is_valid_validation_outcome_dict(rec_val):
            return False
        for key in ("errors", "warnings"):
            items = rec.get(key, [])
            if not isinstance(items, list) or not all(
                isinstance(d, dict) and is_valid_diagnostic_dict(d) for d in items
            ):
                return False
    top_val = payload.get("validation")
    if not isinstance(top_val, dict) or not is_valid_validation_outcome_dict(top_val):
        return False
    for key in ("errors", "warnings"):
        items = payload.get(key, [])
        if not isinstance(items, list) or not all(
            isinstance(d, dict) and is_valid_diagnostic_dict(d) for d in items
        ):
            return False
    return True


def formal_assemble_record_status(
    *,
    record_failed: bool,
    record_partial: bool,
    has_record_errors: bool,
    has_field_errors: bool,
) -> ProcessingStatus:
    """Reference mirror of `StructaFormal.assembleRecordStatus`."""
    if record_failed:
        return ProcessingStatus.FAILED
    if record_partial or has_record_errors or has_field_errors:
        return ProcessingStatus.PARTIAL
    return ProcessingStatus.COMPLETED


def formal_assemble_overall_status(
    *,
    strategy_failed: bool,
    strategy_partial: bool,
    has_result_errors: bool,
    has_records: bool,
    any_record_not_completed: bool,
) -> ProcessingStatus:
    """Reference mirror of `StructaFormal.assembleOverallStatus`."""
    if strategy_failed and not has_records:
        return ProcessingStatus.FAILED
    if strategy_failed or strategy_partial or has_result_errors or any_record_not_completed:
        return ProcessingStatus.PARTIAL
    return ProcessingStatus.COMPLETED


def formal_make_result_cardinality_status(
    *,
    failed_before_validation: bool,
    has_records: bool,
    has_validation_errors: bool,
) -> ValidationStatus:
    """Reference mirror of `StructaFormal.makeResultCardinalityOutcome`."""
    if failed_before_validation and not has_records:
        return ValidationStatus.NOT_VALIDATED
    return ValidationStatus.INVALID if has_validation_errors else ValidationStatus.VALID


def build_source_location_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated valid, invalid, and boundary SourceLocation test vectors."""
    return [
        ({}, True),
        ({"page": 1}, True),
        ({"paragraph": 1, "section": "Totals"}, True),
        ({"line_start": 3}, True),
        ({"line_end": 5}, True),
        ({"line_start": 3, "line_end": 3}, True),
        ({"line_start": 3, "line_end": 7}, True),
        ({"char_start": 0, "char_end": 0}, True),
        ({"char_start": 0, "char_end": 42}, True),
        ({"char_end": 10}, True),
        ({"page": 0}, False),
        ({"paragraph": 0}, False),
        ({"line_start": 0}, False),
        ({"line_end": 0}, False),
        ({"char_start": -1}, False),
        ({"char_end": -1}, False),
        ({"line_start": 5, "line_end": 4}, False),
        ({"char_start": 10, "char_end": 9}, False),
    ]


def build_confidence_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated valid, invalid, and boundary ConfidenceAssessment JSON vectors."""
    return [
        ({"value": None, "scale": "0-1", "method": "llm", "calibrated": False}, True),
        ({"value": 0.0, "scale": "0-1", "method": "llm", "calibrated": False}, True),
        ({"value": 1.0, "scale": "0-1", "method": "llm", "calibrated": False}, True),
        ({"value": 0.96, "scale": "0-1", "method": "platt", "calibrated": True}, True),
        ({"value": 85.0, "scale": "0-100", "method": "custom", "calibrated": False}, True),
        ({"value": -0.0001, "scale": "0-1", "method": "llm", "calibrated": False}, False),
        ({"value": 1.0001, "scale": "0-1", "method": "llm", "calibrated": False}, False),
        ({"value": 0.5, "scale": "", "method": "llm", "calibrated": False}, False),
        ({"value": 0.5, "scale": "0-1", "method": "", "calibrated": False}, False),
    ]


def build_validation_outcome_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated ValidationOutcome JSON vectors (including structural INVALID + [])."""
    val_err = {
        "code": "missing_required",
        "severity": "error",
        "stage": "validation",
        "message": "Required field is missing.",
    }
    val_warn = {
        "code": "soft_rule",
        "severity": "warning",
        "stage": "validation",
        "message": "Soft rule warning.",
    }
    ext_err = {
        "code": "extraction_failed",
        "severity": "error",
        "stage": "extraction",
        "message": "Extraction failed.",
    }
    return [
        ({"status": "valid", "errors": []}, True),
        ({"status": "not_validated", "errors": []}, True),
        ({"status": "invalid", "errors": []}, True),
        ({"status": "invalid", "errors": [val_err]}, True),
        ({"status": "valid", "errors": [val_err]}, False),
        ({"status": "not_validated", "errors": [val_err]}, False),
        ({"status": "invalid", "errors": [val_warn]}, False),
        ({"status": "invalid", "errors": [ext_err]}, False),
    ]


def build_extracted_field_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated ExtractedField JSON vectors covering missing/null/non-null & independence."""
    ok_val = {"status": "valid", "errors": []}
    bad_val = {
        "status": "invalid",
        "errors": [
            {
                "code": "missing_required",
                "severity": "error",
                "stage": "validation",
                "message": "Missing required field.",
            }
        ],
    }
    nv_val = {"status": "not_validated", "errors": []}
    return [
        ({"presence": "missing", "value": None, "validation": ok_val}, True),
        ({"presence": "missing", "value": None, "validation": bad_val}, True),
        ({"presence": "missing", "value": None, "validation": nv_val}, True),
        ({"presence": "present", "value": None, "validation": ok_val}, True),
        ({"presence": "present", "value": "", "validation": ok_val}, True),
        ({"presence": "present", "value": "INV-1", "validation": ok_val}, True),
        ({"presence": "present", "value": "INV-1", "validation": bad_val}, True),
        ({"presence": "missing", "value": "", "validation": ok_val}, False),
        ({"presence": "missing", "value": 0, "validation": ok_val}, False),
        ({"presence": "missing", "value": False, "validation": ok_val}, False),
        ({"presence": "missing", "value": "INV-1", "validation": ok_val}, False),
    ]


def build_extraction_result_vectors() -> list[tuple[dict[str, Any], bool]]:
    """Curated top-level ExtractionResult JSON vectors covering identity, binding, and status."""
    ok_val = {"status": "valid", "errors": []}
    nv_val = {"status": "not_validated", "errors": []}
    bad_val = {
        "status": "invalid",
        "errors": [
            {
                "code": "type_mismatch",
                "severity": "error",
                "stage": "validation",
                "message": "Type mismatch.",
            }
        ],
    }

    def _base(**overrides: Any) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "contract_version": "1.0",
            "extraction_id": "ext-001",
            "status": "completed",
            "source": {"document_id": "doc-001", "filename": "a.txt", "media_type": "text/plain"},
            "schema": {"schema_id": "inv", "schema_version": "1.0"},
            "records": [],
            "validation": ok_val,
            "errors": [],
            "warnings": [],
        }
        doc.update(overrides)
        return doc

    rec_same_doc = {
        "record_id": "record-001",
        "status": "completed",
        "fields": {
            "f": {
                "presence": "present",
                "value": "ok",
                "confidence": None,
                "provenance": [
                    {
                        "source_document_id": "doc-001",
                        "location": {"page": 1, "char_start": 0, "char_end": 2},
                        "evidence": "ok",
                        "relationship": "direct",
                        "truncated": False,
                    }
                ],
                "validation": ok_val,
                "errors": [],
                "warnings": [],
            }
        },
        "validation": ok_val,
        "errors": [],
        "warnings": [],
    }
    rec_foreign_doc = {
        "record_id": "record-001",
        "status": "completed",
        "fields": {
            "f": {
                "presence": "present",
                "value": "ok",
                "confidence": None,
                "provenance": [
                    {
                        "source_document_id": "doc-OTHER",
                        "location": None,
                        "evidence": "ok",
                        "relationship": "unverified",
                        "truncated": False,
                    }
                ],
                "validation": ok_val,
                "errors": [],
                "warnings": [],
            }
        },
        "validation": ok_val,
        "errors": [],
        "warnings": [],
    }
    rec_second_same_id = {
        "record_id": "record-001",
        "status": "completed",
        "fields": {},
        "validation": ok_val,
        "errors": [],
        "warnings": [],
    }

    return [
        (_base(records=[rec_same_doc]), True),
        (_base(status="partial", validation=ok_val, records=[rec_same_doc]), True),
        (_base(status="completed", validation=bad_val, records=[rec_same_doc]), True),
        (_base(status="failed", validation=nv_val, records=[]), True),
        (_base(source=None, records=[rec_foreign_doc]), True),
        (_base(records=[rec_foreign_doc]), False),
        (_base(records=[rec_same_doc, rec_second_same_id]), False),
        (_base(contract_version="2.0"), False),
        (_base(extraction_id=""), False),
        (_base(schema={"schema_id": "", "schema_version": "1.0"}), False),
    ]
