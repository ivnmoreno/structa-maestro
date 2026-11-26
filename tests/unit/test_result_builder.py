"""Unit tests for ResultBuilder status aggregation and error/warning separation."""

from __future__ import annotations

from structa_maestro import (
    DiagnosticIssue,
    DocumentFormat,
    ExtractionSchema,
    IssueSeverity,
    PipelineStage,
    PreparedDocument,
    ProcessingStatus,
    ProposedField,
    ProposedRecord,
    ResultBuilder,
    SourceMetadata,
    StrategyOutput,
    ValidationStatus,
)


def _doc() -> PreparedDocument:
    return PreparedDocument(
        source=SourceMetadata(document_id="d1", filename="doc.txt", media_type="text/plain"),
        format=DocumentFormat.TXT,
        full_text="Name: Alice",
    )


def _schema() -> ExtractionSchema:
    return ExtractionSchema.from_dict(
        {"schema_id": "s", "schema_version": "1.0", "fields": {"name": {"type": "string"}}}
    )


def test_result_builder_separates_errors_and_warnings_and_aggregates_partial_status() -> None:
    builder = ResultBuilder()
    out = StrategyOutput(
        warnings=(
            DiagnosticIssue(
                code="minor_notice",
                severity=IssueSeverity.WARNING,
                stage=PipelineStage.EXTRACTION,
                message="Minor notice.",
            ),
        ),
        records=(
            ProposedRecord(
                fields={
                    "name": ProposedField(
                        value="Alice",
                        errors=(
                            DiagnosticIssue(
                                code="field_extraction_issue",
                                severity=IssueSeverity.ERROR,
                                stage=PipelineStage.EXTRACTION,
                                message="Partial field issue.",
                                field_path="name",
                            ),
                        ),
                    )
                }
            ),
        ),
    )

    res = builder.build(document=_doc(), schema=_schema(), strategy_output=out)
    assert res.status == ProcessingStatus.PARTIAL
    assert res.records[0].status == ProcessingStatus.PARTIAL
    assert res.validation.status == ValidationStatus.VALID
    assert len(res.warnings) == 1
    assert len(res.records[0].fields["name"].errors) == 1


def test_result_builder_warnings_alone_keep_completed_status() -> None:
    builder = ResultBuilder()
    out = StrategyOutput(
        warnings=(
            DiagnosticIssue(
                code="info_warning",
                severity=IssueSeverity.WARNING,
                stage=PipelineStage.EXTRACTION,
                message="Non-fatal warning.",
            ),
        ),
        records=(ProposedRecord(fields={"name": ProposedField(value="Alice")}),),
    )
    res = builder.build(document=_doc(), schema=_schema(), strategy_output=out)
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    assert len(res.warnings) == 1
    assert res.errors == []


def test_result_builder_calibrator_preserves_dimension_independence() -> None:
    from structa_maestro import CalibrationMethod, CalibrationSample, fit_calibrator

    samples = [CalibrationSample(raw_score=0.9, is_correct=(i % 3 != 0)) for i in range(12)] + [
        CalibrationSample(raw_score=0.2, is_correct=(i % 4 == 0)) for i in range(12)
    ]
    cal = fit_calibrator(samples, method=CalibrationMethod.ISOTONIC)
    builder = ResultBuilder(calibrator=cal)
    out = StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "name": ProposedField(
                        value="Alice",
                        confidence=0.9,
                        confidence_method="llm",
                    )
                }
            ),
        )
    )
    res = builder.build(document=_doc(), schema=_schema(), strategy_output=out)
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    conf = res.records[0].fields["name"].confidence
    assert conf is not None
    assert conf.calibrated is True
    assert conf.method == "llm+calibrated:isotonic"
    assert conf.value == round(cal.calibrate(0.9), 6)
