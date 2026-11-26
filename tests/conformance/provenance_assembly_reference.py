"""Pure-Python provenance association & ResultBuilder conformance helpers (Spec 015).

Mirrors `formal/StructaFormal/ProvenanceAssociation.lean` and `ResultAssembly.lean`, providing:
- Lossless JSON encoders for `DocumentSegment`, `ProposedEvidence`, `ProposedField`,
  `ProposedRecord`, `StrategyOutput`, and `DiagnosticIssue`
- Curated vectors for `_location_from_span`, `ProvenanceAssociator.associate`, and
  `ResultBuilder.build` (including span overlap/clamping/fallback, excerpt bounding, foreign-doc
  rejection, blank skip, unlocated `UNVERIFIED`, `DIRECT` conflict downgrade, `MISSING` value
  sanitization, `ambiguous`, provenance fault isolation, validation-only warning propagation,
  empty failure `extraction_failed` synthesis, failed strategy with preserved records, and
  multi-record + cross-field rule assembly).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from tests.conformance.cross_field_reference import encode_schema_with_rules
from tests.conformance.schema_reference import encode_runtime_value

from structa_maestro.contracts.enums import (
    DocumentFormat,
    ExtractionCardinality,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
)
from structa_maestro.contracts.models import DiagnosticIssue, SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.provenance.associator import (
    _find_span,
    _scalar_value_supported_by_excerpt,
)
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.schema.rules import (
    ArithmeticRule,
    ArithmeticTerm,
    GroupPresenceKind,
    GroupPresenceRule,
    RuleSeverity,
)
from structa_maestro.strategies.base import (
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)


def encode_document_segment(seg: DocumentSegment) -> dict[str, Any]:
    return {
        "char_start": seg.char_start,
        "char_end": seg.char_end,
        "page": seg.page,
        "paragraph": seg.paragraph,
        "line_start": seg.line_start,
        "line_end": seg.line_end,
        "heading": seg.heading,
    }


def encode_diagnostic_issue(diag: DiagnosticIssue) -> dict[str, Any]:
    return {
        "code": diag.code,
        "severity": diag.severity.value,
        "stage": diag.stage.value,
        "message": diag.message,
        "record_id": diag.record_id,
        "field_path": diag.field_path,
        "expected": diag.expected,
        "actual": diag.actual,
    }


def encode_confidence_input(raw_value: Any) -> dict[str, Any]:
    if raw_value is None:
        return {"input_kind": "missing", "value": None}
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return {"input_kind": "non_numeric", "value": None}
    num = float(raw_value)
    if math.isnan(num) or math.isinf(num):
        return {"input_kind": "non_finite", "value": None}
    return {"input_kind": "finite", "value": num}


def encode_proposed_evidence(
    item: ProposedEvidence,
    *,
    full_text: str,
    field_value: Any,
) -> dict[str, Any]:
    """Encode ProposedEvidence with exact abstracted span & scalar-support verdicts."""
    quote_stripped = item.quote.strip()
    span = _find_span(full_text, quote_stripped) if quote_stripped else None
    matched_text = full_text[span[0] : span[1]] if span is not None else ""
    scalar_supported = (
        _scalar_value_supported_by_excerpt(field_value, matched_text) if span is not None else True
    )
    return {
        "quote": item.quote,
        "source_document_id": item.source_document_id,
        "relationship": item.relationship,
        "matched_span": [span[0], span[1]] if span is not None else None,
        "matched_text": matched_text,
        "scalar_supported": scalar_supported,
    }


def encode_build_result_request(
    *,
    extraction_id: str,
    document: PreparedDocument,
    schema: ExtractionSchema,
    strategy_output: StrategyOutput,
    document_warnings: list[DiagnosticIssue] | None = None,
    provenance_failed_fields: set[tuple[int, str]] | None = None,
    max_excerpt_chars: int = 500,
) -> dict[str, Any]:
    """Encode full `ResultBuilder.build` invocation inputs for `structa-formal-oracle`."""
    failed_set = provenance_failed_fields or set()
    encoded_records: list[dict[str, Any]] = []
    for rec_idx, rec in enumerate(strategy_output.records):
        encoded_fields: dict[str, Any] = {}
        for fname, pf in rec.fields.items():
            eff_val = None if pf.presence == FieldPresence.MISSING else pf.value
            encoded_fields[fname] = {
                "presence": pf.presence.value,
                "value": encode_runtime_value(pf.value),
                "confidence_input": encode_confidence_input(pf.confidence),
                "confidence_scale": pf.confidence_scale,
                "confidence_method": pf.confidence_method,
                "evidence": [
                    encode_proposed_evidence(ev, full_text=document.full_text, field_value=eff_val)
                    for ev in pf.evidence
                ],
                "ambiguous": pf.ambiguous,
                "provenance_failed": (rec_idx, fname) in failed_set,
                "errors": [encode_diagnostic_issue(e) for e in pf.errors],
                "warnings": [encode_diagnostic_issue(w) for w in pf.warnings],
            }
        encoded_records.append(
            {
                "fields": encoded_fields,
                "partial": rec.partial,
                "failed": rec.failed,
                "errors": [encode_diagnostic_issue(e) for e in rec.errors],
                "warnings": [encode_diagnostic_issue(w) for w in rec.warnings],
            }
        )

    return {
        "kind": "build_result",
        "payload": {
            "extraction_id": extraction_id,
            "source": {
                "document_id": document.source.document_id,
                "filename": document.source.filename,
                "media_type": document.source.media_type,
            },
            "segments": [encode_document_segment(s) for s in document.segments],
            "schema": encode_schema_with_rules(schema),
            "document_warnings": [encode_diagnostic_issue(w) for w in (document_warnings or [])],
            "strategy_output": {
                "records": encoded_records,
                "partial": strategy_output.partial,
                "failed": strategy_output.failed,
                "errors": [encode_diagnostic_issue(e) for e in strategy_output.errors],
                "warnings": [encode_diagnostic_issue(w) for w in strategy_output.warnings],
                "default_confidence_method": strategy_output.default_confidence_method,
            },
            "pattern_results": {},
            "max_excerpt_chars": max_excerpt_chars,
        },
    }


def build_span_location_vectors() -> list[tuple[str, tuple[DocumentSegment, ...], int, int]]:
    """Curated `(segments, char_start, char_end)` vectors covering overlap, clamping & fallback."""
    segs = (
        DocumentSegment(
            text="Header line",
            char_start=0,
            char_end=20,
            page=1,
            paragraph=1,
            line_start=5,
            line_end=7,
            heading="Header",
        ),
        DocumentSegment(
            text="Body line",
            char_start=21,
            char_end=50,
            page=2,
            paragraph=2,
            line_start=1,
            line_end=3,
            heading="Body",
        ),
    )
    return [
        ("single_segment_overlap", segs, 2, 10),
        ("multi_segment_clamps_reversed_line_end", segs, 15, 30),
        ("no_segment_overlap_fallback", segs, 60, 75),
        ("empty_segments_fallback", (), 0, 10),
        ("boundary_touch_char_end_eq_start", segs, 20, 21),
    ]


def build_sample_prepared_document() -> PreparedDocument:
    text = "Invoice INV-100\nSubtotal: 100.00\nTax: 10.00\nTotal: 110.00\nNotes: Approved\n"
    segs = (
        DocumentSegment(
            text="Invoice INV-100",
            char_start=0,
            char_end=15,
            page=1,
            paragraph=1,
            line_start=1,
            line_end=1,
            heading="Invoice",
        ),
        DocumentSegment(
            text="Subtotal: 100.00\nTax: 10.00\nTotal: 110.00",
            char_start=16,
            char_end=57,
            page=1,
            paragraph=2,
            line_start=2,
            line_end=4,
            heading="Totals",
        ),
        DocumentSegment(
            text="Notes: Approved",
            char_start=58,
            char_end=73,
            page=1,
            paragraph=3,
            line_start=5,
            line_end=5,
            heading="Notes",
        ),
    )
    return PreparedDocument(
        source=SourceMetadata(
            document_id="doc-inv-1",
            filename="invoice.txt",
            media_type="text/plain",
        ),
        format=DocumentFormat.TXT,
        full_text=text,
        segments=segs,
        page_count=1,
    )


def build_assembly_test_schema(
    *, cardinality: ExtractionCardinality = ExtractionCardinality.SINGLE
) -> ExtractionSchema:
    base = ExtractionSchema.from_dict(
        {
            "schema_id": "assembly-inv",
            "schema_version": "1.0",
            "cardinality": cardinality.value,
            "fields": {
                "invoice_number": {"type": "string", "required": True},
                "subtotal": {"type": "decimal", "required": False},
                "tax": {"type": "decimal", "required": False},
                "total": {"type": "decimal", "required": True},
                "note": {"type": "string", "required": False, "nullable": True},
                "alt_id": {"type": "string", "required": False},
            },
        }
    )
    return ExtractionSchema(
        schema_id=base.schema_id,
        schema_version=base.schema_version,
        fields=dict(base.fields),
        cardinality=base.cardinality,
        allow_additional_fields=base.allow_additional_fields,
        max_depth=base.max_depth,
        rules=(
            ArithmeticRule(
                rule_id="balance",
                terms=(
                    ArithmeticTerm(field="subtotal", sign=1),
                    ArithmeticTerm(field="tax", sign=1, treat_missing_as_zero=True),
                ),
                target_field="total",
                tolerance=Decimal("0.01"),
                severity=RuleSeverity.ERROR,
            ),
            GroupPresenceRule(
                rule_id="prefer_note_or_alt",
                kind=GroupPresenceKind.AT_LEAST_ONE_OF,
                fields=("note", "alt_id"),
                severity=RuleSeverity.WARNING,
            ),
        ),
    )


def build_curated_result_builder_scenarios() -> list[
    tuple[
        str,
        PreparedDocument,
        ExtractionSchema,
        StrategyOutput,
        list[DiagnosticIssue],
        set[tuple[int, str]],
    ]
]:
    """Curated ResultBuilder.build scenarios covering every firewall, status & warning rule."""
    doc = build_sample_prepared_document()
    single_schema = build_assembly_test_schema(cardinality=ExtractionCardinality.SINGLE)
    multi_schema = build_assembly_test_schema(cardinality=ExtractionCardinality.MULTIPLE)

    clean_record = ProposedRecord(
        fields={
            "invoice_number": ProposedField(
                presence=FieldPresence.PRESENT,
                value="INV-100",
                confidence=0.95,
                evidence=(ProposedEvidence(quote="Invoice INV-100", relationship="direct"),),
            ),
            "subtotal": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("100.00"),
                confidence=0.9,
                evidence=(ProposedEvidence(quote="Subtotal: 100.00", relationship="direct"),),
            ),
            "tax": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("10.00"),
                confidence=0.9,
            ),
            "total": ProposedField(
                presence=FieldPresence.PRESENT,
                value=Decimal("110.00"),
                confidence=0.98,
                evidence=(ProposedEvidence(quote="Total: 110.00", relationship="direct"),),
            ),
            "note": ProposedField(
                presence=FieldPresence.PRESENT,
                value="Approved",
                evidence=(ProposedEvidence(quote="Notes: Approved", relationship="direct"),),
            ),
        }
    )

    doc_warn = DiagnosticIssue(
        code="document_notice",
        severity=IssueSeverity.WARNING,
        stage=PipelineStage.DOCUMENT_PROCESSING,
        message="Minor parser notice.",
    )
    strat_err = DiagnosticIssue(
        code="provider_timeout",
        severity=IssueSeverity.ERROR,
        stage=PipelineStage.EXTRACTION,
        message="Provider timed out.",
    )
    rec_ext_warn = DiagnosticIssue(
        code="record_hint",
        severity=IssueSeverity.WARNING,
        stage=PipelineStage.EXTRACTION,
        message="Record-scoped extraction warning.",
    )

    return [
        (
            "clean_single_completed_valid",
            doc,
            single_schema,
            StrategyOutput(records=(clean_record,)),
            [],
            set(),
        ),
        (
            "empty_failed_synthesizes_extraction_failed",
            doc,
            single_schema,
            StrategyOutput(records=(), failed=True, errors=()),
            [doc_warn],
            set(),
        ),
        (
            "empty_failed_preserves_explicit_error",
            doc,
            single_schema,
            StrategyOutput(records=(), failed=True, errors=(strat_err,)),
            [],
            set(),
        ),
        (
            "failed_with_preserved_record_is_partial_and_validated",
            doc,
            single_schema,
            StrategyOutput(records=(clean_record,), failed=True, errors=(strat_err,)),
            [],
            set(),
        ),
        (
            "missing_value_sanitized_ambiguous_and_bad_confidence",
            doc,
            single_schema,
            StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            "invoice_number": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value="INV-100",
                                confidence=1.5,
                                ambiguous=True,
                                evidence=(
                                    ProposedEvidence(
                                        quote="Invoice INV-999",
                                        source_document_id="foreign-doc",
                                    ),
                                    ProposedEvidence(
                                        quote="Nonexistent quote",
                                        relationship="direct",
                                    ),
                                    ProposedEvidence(
                                        quote="Total: 110.00",
                                        relationship="direct",
                                    ),
                                ),
                            ),
                            "subtotal": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("100.00"),
                            ),
                            "total": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("100.00"),
                            ),
                            "note": ProposedField(
                                presence=FieldPresence.MISSING,
                                value="SHOULD_BE_SANITIZED",
                            ),
                        },
                        warnings=(rec_ext_warn,),
                    ),
                )
            ),
            [],
            set(),
        ),
        (
            "validation_warning_propagates_non_validation_stays_record_scoped",
            doc,
            single_schema,
            StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            "invoice_number": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value="INV-100",
                            ),
                            "subtotal": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("100.00"),
                            ),
                            "total": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("100.00"),
                            ),
                        },
                        warnings=(rec_ext_warn,),
                    ),
                )
            ),
            [],
            set(),
        ),
        (
            "single_cardinality_two_records_violation",
            doc,
            single_schema,
            StrategyOutput(records=(clean_record, clean_record)),
            [],
            set(),
        ),
        (
            "multiple_cardinality_two_records_unique_ids_and_valid",
            doc,
            multi_schema,
            StrategyOutput(records=(clean_record, clean_record)),
            [],
            set(),
        ),
        (
            "provenance_fault_isolated_forces_partial",
            doc,
            single_schema,
            StrategyOutput(records=(clean_record,)),
            [],
            {(0, "invoice_number")},
        ),
        (
            "cross_field_error_invalidates_completed_record",
            doc,
            single_schema,
            StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            **clean_record.fields,
                            "total": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("999.00"),
                            ),
                        }
                    ),
                )
            ),
            [],
            set(),
        ),
    ]
