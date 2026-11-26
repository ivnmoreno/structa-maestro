"""Confidence normalization and zero-dependency empirical confidence calibration."""

from __future__ import annotations

from structa_maestro.confidence.calibration import (
    CALIBRATION_ARTIFACT_VERSION,
    DEFAULT_CALIBRATION_MIN_SAMPLES,
    MAX_CALIBRATION_ARTIFACT_BYTES,
    MIN_CALIBRATION_SAMPLES_FLOOR,
    BinningStrategy,
    CalibrationBin,
    CalibrationMetadata,
    CalibrationMethod,
    CalibrationMetrics,
    CalibrationModel,
    CalibrationSample,
    ConfidenceCalibrator,
    HistogramParameters,
    IsotonicParameters,
    PlattParameters,
    compute_calibration_metrics,
    fit_calibrator,
)
from structa_maestro.confidence.normalizer import (
    ConfidenceNormalizationResult,
    ConfidenceNormalizer,
)

__all__ = [
    "CALIBRATION_ARTIFACT_VERSION",
    "DEFAULT_CALIBRATION_MIN_SAMPLES",
    "MAX_CALIBRATION_ARTIFACT_BYTES",
    "MIN_CALIBRATION_SAMPLES_FLOOR",
    "BinningStrategy",
    "CalibrationBin",
    "CalibrationMetadata",
    "CalibrationMethod",
    "CalibrationMetrics",
    "CalibrationModel",
    "CalibrationSample",
    "ConfidenceCalibrator",
    "ConfidenceNormalizationResult",
    "ConfidenceNormalizer",
    "HistogramParameters",
    "IsotonicParameters",
    "PlattParameters",
    "compute_calibration_metrics",
    "fit_calibrator",
]
