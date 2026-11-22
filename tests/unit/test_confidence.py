"""Confidence normalization unit tests (docs/TESTING.md Section 6.3-6.4)."""

from __future__ import annotations

import math

from structa_maestro.confidence.normalizer import ConfidenceNormalizer


def test_confidence_valid_range_always_uncalibrated_in_mvp() -> None:
    norm = ConfidenceNormalizer()
    res = norm.normalize(raw_value=0.875, method="llm-self-assessment", field_path="total_amount")
    assert res.confidence is not None
    assert res.confidence.value == 0.875
    assert res.confidence.scale == "0-1"
    assert res.confidence.method == "llm-self-assessment"
    assert res.confidence.calibrated is False
    assert res.warnings == []


def test_confidence_unavailable_and_unknown_method() -> None:
    norm = ConfidenceNormalizer()
    none_res = norm.normalize(raw_value=None, field_path="notes")
    assert none_res.confidence is None
    assert none_res.warnings == []

    default_method = norm.normalize(raw_value=0.5, method=None, default_method="unknown")
    assert default_method.confidence is not None
    assert default_method.confidence.method == "unknown"


def test_confidence_out_of_range_nan_and_non_numeric_handled_honestly() -> None:
    norm = ConfidenceNormalizer()

    for bad in (-0.1, 1.5, math.nan, math.inf):
        res = norm.normalize(raw_value=bad, field_path="amount", record_id="record-001")
        assert res.confidence is not None
        assert res.confidence.value is None
        assert res.confidence.calibrated is False
        assert any(w.code == "invalid_confidence_range" for w in res.warnings)
