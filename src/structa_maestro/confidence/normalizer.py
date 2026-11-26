"""Confidence assessment normalization enforcing range bounds and honest calibration semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from structa_maestro.confidence.calibration import ConfidenceCalibrator, validate_calibrator
from structa_maestro.contracts.enums import IssueSeverity, PipelineStage
from structa_maestro.contracts.models import ConfidenceAssessment, DiagnosticIssue


@dataclass(frozen=True, slots=True)
class ConfidenceNormalizationResult:
    """Normalized ConfidenceAssessment plus any range/metadata/calibration warnings."""

    confidence: ConfidenceAssessment | None
    warnings: list[DiagnosticIssue] = field(default_factory=list)


class ConfidenceNormalizer:
    """Enforces ConfidenceAssessment bounds, uncalibrated defaults, and optional calibration."""

    def __init__(self, *, calibrator: ConfidenceCalibrator | None = None) -> None:
        self._calibrator = validate_calibrator(calibrator)

    @property
    def calibrator(self) -> ConfidenceCalibrator | None:
        """Optional fitted empirical calibrator applied to valid matching-scale scores."""
        return self._calibrator

    def normalize(
        self,
        *,
        raw_value: float | None,
        scale: str = "0-1",
        method: str | None = None,
        record_id: str | None = None,
        field_path: str | None = None,
        default_method: str = "extraction-assessment",
        calibrator: ConfidenceCalibrator | None = None,
    ) -> ConfidenceNormalizationResult:
        """Normalize raw confidence metadata honestly, applying calibration only when warranted."""
        active_calibrator = (
            validate_calibrator(calibrator) if calibrator is not None else self._calibrator
        )
        clean_scale = scale.strip() if isinstance(scale, str) and scale.strip() else "0-1"
        clean_method = (
            method.strip()
            if isinstance(method, str) and method.strip()
            else (default_method or "unknown")
        )

        if raw_value is None:
            return ConfidenceNormalizationResult(confidence=None)

        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=None,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                ),
                warnings=[
                    DiagnosticIssue(
                        code="invalid_confidence_value",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=(
                            f"Non-numeric confidence hint for field '{field_path}' was ignored."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                    )
                ],
            )

        numeric = float(raw_value)
        if math.isnan(numeric) or math.isinf(numeric):
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=None,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                ),
                warnings=[
                    DiagnosticIssue(
                        code="invalid_confidence_range",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=f"Non-finite confidence score for field '{field_path}' discarded.",
                        record_id=record_id,
                        field_path=field_path,
                    )
                ],
            )

        if clean_scale == "0-1" and not (0.0 <= numeric <= 1.0):
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=None,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                ),
                warnings=[
                    DiagnosticIssue(
                        code="invalid_confidence_range",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=(
                            f"Confidence value for field '{field_path}' fell outside declared "
                            "scale '0-1' and was marked unavailable."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                    )
                ],
            )

        base_value = round(numeric, 6)
        if active_calibrator is None:
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=base_value,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                )
            )

        calibrator_scale = active_calibrator.scale.strip()
        if clean_scale != calibrator_scale:
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=base_value,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                ),
                warnings=[
                    DiagnosticIssue(
                        code="confidence_calibration_scale_mismatch",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=(
                            f"Confidence calibration skipped for field '{field_path}' because "
                            f"scale '{clean_scale}' does not match calibrator scale "
                            f"'{calibrator_scale}'."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                    )
                ],
            )

        try:
            raw_cal = active_calibrator.calibrate(numeric)
            if isinstance(raw_cal, bool) or not isinstance(raw_cal, (int, float)):
                raise ValueError("Calibrator returned non-numeric value.")
            cal_val = float(raw_cal)
            if not math.isfinite(cal_val) or not (0.0 <= cal_val <= 1.0):
                raise ValueError("Calibrator returned out-of-bounds value.")
            cal_method_tag = active_calibrator.method.strip()
            if not cal_method_tag:
                raise ValueError("Calibrator method must be non-empty.")
        except Exception:
            return ConfidenceNormalizationResult(
                confidence=ConfidenceAssessment(
                    value=base_value,
                    scale=clean_scale,
                    method=clean_method,
                    calibrated=False,
                ),
                warnings=[
                    DiagnosticIssue(
                        code="confidence_calibration_failed",
                        severity=IssueSeverity.WARNING,
                        stage=PipelineStage.EXTRACTION,
                        message=(
                            f"Confidence calibration failed for field '{field_path}'; "
                            "preserved uncalibrated confidence score."
                        ),
                        record_id=record_id,
                        field_path=field_path,
                    )
                ],
            )

        suffix = f"+calibrated:{cal_method_tag}"
        calibrated_method = (
            clean_method if clean_method.endswith(suffix) else f"{clean_method}{suffix}"
        )
        return ConfidenceNormalizationResult(
            confidence=ConfidenceAssessment(
                value=round(cal_val, 6),
                scale=clean_scale,
                method=calibrated_method,
                calibrated=True,
            )
        )
