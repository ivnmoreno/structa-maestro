"""Offline example: fitting, verifying, persisting, and applying empirical confidence calibration.

Demonstrates:
1. Measuring raw Expected Calibration Error (ECE) and Brier score on an overconfident synthetic
   evaluation dataset using zero third-party ML libraries.
2. Fitting an immutable, JSON-serializable isotonic `CalibrationModel` via `fit_calibrator(...)`.
3. Persisting and reloading the calibration artifact via `to_file(...)` / `from_file(...)`.
4. Running canonical extraction without vs. with the calibrator, illustrating Contract v1.0's
   honest `calibrated: false` vs. `calibrated: true` distinction while preserving validation,
   provenance, and processing-status independence.
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from structa_maestro import (
    CalibrationMethod,
    CalibrationModel,
    CalibrationSample,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionSchema,
    FieldPresence,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    compute_calibration_metrics,
    extract,
    fit_calibrator,
)


def _build_synthetic_overconfident_samples() -> list[CalibrationSample]:
    """Build 60 deterministic labeled observations simulating an overconfident extractor."""
    samples: list[CalibrationSample] = []
    # High raw scores (0.90-0.96) that are only ~70% accurate
    for i in range(30):
        score = round(0.90 + (i % 7) * 0.01, 4)
        samples.append(CalibrationSample(raw_score=score, is_correct=(i % 10) < 7))
    # Mid raw scores (0.65-0.74) that are ~45% accurate
    for i in range(20):
        score = round(0.65 + (i % 10) * 0.01, 4)
        samples.append(CalibrationSample(raw_score=score, is_correct=(i % 20) < 9))
    # Low raw scores (0.20-0.29) that are ~10% accurate
    for i in range(10):
        score = round(0.20 + i * 0.01, 4)
        samples.append(CalibrationSample(raw_score=score, is_correct=(i == 9)))
    return samples


def main() -> None:
    samples = _build_synthetic_overconfident_samples()
    raw_metrics = compute_calibration_metrics(samples, num_bins=10)

    # 1. Fit deterministic isotonic calibration artifact offline
    calibrator = fit_calibrator(
        samples,
        method=CalibrationMethod.ISOTONIC,
        schema_id="invoice-schema",
        strategy_name="demo-overconfident-strategy",
        description="Offline isotonic calibrator fitted on 60 labeled field observations.",
    )
    print(
        f"Fitted {calibrator.method} calibrator on {calibrator.metadata.sample_count} samples:\n"
        f"  ECE:   {raw_metrics.ece:.4f} -> {calibrator.metadata.ece_after:.4f}\n"
        f"  Brier: {raw_metrics.brier_score:.4f} -> {calibrator.metadata.brier_after:.4f}\n"
    )

    # 2. Save and reload artifact as pure JSON
    with tempfile.TemporaryDirectory(prefix="structa-cal-demo-") as tmp_dir:
        artifact_path = Path(tmp_dir) / "invoice_calibrator.json"
        calibrator.to_file(artifact_path)
        reloaded = CalibrationModel.from_file(artifact_path)

        schema = ExtractionSchema.from_dict(
            {
                "schema_id": "invoice-schema",
                "schema_version": "1.0",
                "fields": {
                    "invoice_number": {"type": "string", "required": True},
                    "total_amount": {"type": "decimal", "required": True, "min_value": 0},
                },
            }
        )
        doc = DocumentInput.from_text(
            "Invoice INV-2026-042\nTotal Amount: 245.50\n",
            filename="invoice.txt",
            document_id="doc-cal-001",
        )
        strategy = DeterministicTestStrategy(
            StrategyOutput(
                records=(
                    ProposedRecord(
                        fields={
                            "invoice_number": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value="INV-2026-042",
                                confidence=0.95,
                                confidence_method="llm-self-assessment",
                                evidence=(
                                    ProposedEvidence(
                                        quote="Invoice INV-2026-042",
                                        relationship="direct",
                                    ),
                                ),
                            ),
                            "total_amount": ProposedField(
                                presence=FieldPresence.PRESENT,
                                value=Decimal("245.50"),
                                confidence=0.70,
                                confidence_method="llm-self-assessment",
                                evidence=(
                                    ProposedEvidence(
                                        quote="Total Amount: 245.50",
                                        relationship="direct",
                                    ),
                                ),
                            ),
                        }
                    ),
                )
            )
        )

        # 3. Default extraction (no calibrator -> calibrated=False)
        uncalibrated = extract(doc, schema, strategy=strategy)
        # 4. Calibrated extraction (reloaded artifact -> calibrated=True)
        calibrated = extract(doc, schema, strategy=strategy, calibrator=reloaded)

        for label, res in (("Uncalibrated (default)", uncalibrated), ("Calibrated", calibrated)):
            rec = res.records[0]
            print(
                f"=== {label} (status={res.status.value}, "
                f"validation={res.validation.status.value}) ==="
            )
            for fname, field in rec.fields.items():
                assert field.confidence is not None
                print(
                    f"  {fname}: value={field.value!r}, "
                    f"confidence={field.confidence.value}, "
                    f"calibrated={field.confidence.calibrated}, "
                    f"method={field.confidence.method!r}"
                )


if __name__ == "__main__":
    main()
