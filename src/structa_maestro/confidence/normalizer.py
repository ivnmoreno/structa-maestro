"""Confidence assessment normalization enforcing range bounds and uncalibrated MVP semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from structa_maestro.contracts.enums import IssueSeverity, PipelineStage
from structa_maestro.contracts.models import ConfidenceAssessment, DiagnosticIssue


@dataclass(frozen=True, slots=True)
class ConfidenceNormalizationResult:
    """Normalized ConfidenceAssessment plus any range/metadata warnings."""

    confidence: ConfidenceAssessment | None
    warnings: list[DiagnosticIssue] = field(default_factory=list)


class ConfidenceNormalizer:
    """Enforces ConfidenceAssessment bounds and guarantees calibrated=False in MVP."""

    def normalize(
        self,
        *,
        raw_value: float | None,
        scale: str = "0-1",
        method: str | None = None,
        record_id: str | None = None,
        field_path: str | None = None,
        default_method: str = "extraction-assessment",
    ) -> ConfidenceNormalizationResult:
        """Normalize raw confidence metadata honestly without claiming probability calibration."""
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

        return ConfidenceNormalizationResult(
            confidence=ConfidenceAssessment(
                value=round(numeric, 6),
                scale=clean_scale,
                method=clean_method,
                calibrated=False,
            )
        )
