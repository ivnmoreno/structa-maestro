"""Comprehensive unit, contract, property, async/batch, and CLI tests for confidence calibration."""

from __future__ import annotations

import asyncio
import io
import json
import math
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from structa_maestro import (
    CALIBRATION_ARTIFACT_VERSION,
    MAX_CALIBRATION_ARTIFACT_BYTES,
    BatchOptions,
    BinningStrategy,
    CalibrationMethod,
    CalibrationModel,
    CalibrationSample,
    ConfidenceNormalizer,
    ConfigurationError,
    DeterministicTestStrategy,
    DocumentInput,
    ExtractionEngine,
    ExtractionResult,
    ExtractionSchema,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    ResultBuilder,
    StrategyOutput,
    ValidationStatus,
    aextract,
    aextract_batch,
    compute_calibration_metrics,
    extract,
    extract_batch,
    fit_calibrator,
)
from structa_maestro.cli import run_cli


def _overconfident_samples(count: int = 80) -> list[CalibrationSample]:
    """Synthetic dataset where raw scores overestimate true probability by ~0.25."""
    samples: list[CalibrationSample] = []
    for i in range(count):
        # Raw scores spread cleanly in [0.25, 0.99]
        raw = round(0.25 + 0.74 * (i / (count - 1)), 4)
        # True probability is roughly raw - 0.25 (clamped)
        true_p = max(0.05, min(0.80, raw - 0.25))
        # Deterministic quasi-random threshold matching true_p closely
        is_correct = ((i * 7 + 3) % 20) / 20.0 < true_p
        samples.append(CalibrationSample(raw_score=raw, is_correct=is_correct))
    return samples


def _underconfident_samples(count: int = 80) -> list[CalibrationSample]:
    """Synthetic dataset where raw scores underestimate true probability by ~0.25."""
    samples: list[CalibrationSample] = []
    for i in range(count):
        raw = round(0.05 + 0.65 * (i / (count - 1)), 4)
        true_p = max(0.20, min(0.95, raw + 0.25))
        is_correct = ((i * 7 + 3) % 20) / 20.0 < true_p
        samples.append(CalibrationSample(raw_score=raw, is_correct=is_correct))
    return samples


def _simple_schema() -> ExtractionSchema:
    return ExtractionSchema.from_dict(
        {
            "schema_id": "cal-test-schema",
            "schema_version": "1.0",
            "fields": {
                "code": {"type": "string", "required": True},
                "note": {"type": "string", "required": False},
            },
        }
    )


def _simple_strategy(
    *,
    confidence: Any = 0.92,
    scale: str = "0-1",
    method: str | None = "llm-self-assessment",
    code_value: Any = "INV-1",
) -> DeterministicTestStrategy:
    return DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "code": ProposedField(
                            presence=FieldPresence.PRESENT,
                            value=code_value,
                            confidence=confidence,
                            confidence_scale=scale,
                            confidence_method=method,
                            evidence=(
                                ProposedEvidence(quote="Code: INV-1", relationship="direct"),
                            ),
                        ),
                        "note": ProposedField(
                            presence=FieldPresence.MISSING,
                            value=None,
                            confidence=None,
                        ),
                    }
                ),
            )
        )
    )


class _BrokenCalibrator:
    """Custom ConfidenceCalibrator double injecting exceptions or invalid return values."""

    def __init__(
        self,
        *,
        scale: str = "0-1",
        method: str = "broken",
        return_value: Any = None,
        exc: Exception | None = None,
    ) -> None:
        self._scale = scale
        self._method = method
        self._return_value = return_value
        self._exc = exc

    @property
    def scale(self) -> str:
        return self._scale

    @property
    def method(self) -> str:
        return self._method

    def calibrate(self, raw_score: float) -> float:
        if self._exc is not None:
            raise self._exc
        return self._return_value  # type: ignore[no-any-return]


# =============================================================================
# User Story 1: Fitting, Metrics, Boundary Cases & Strict Artifact Persistence
# =============================================================================


@pytest.mark.parametrize(
    ("method", "binning"),
    [
        (CalibrationMethod.ISOTONIC, BinningStrategy.UNIFORM),
        (CalibrationMethod.PLATT, BinningStrategy.UNIFORM),
        (CalibrationMethod.HISTOGRAM, BinningStrategy.UNIFORM),
        (CalibrationMethod.HISTOGRAM, BinningStrategy.QUANTILE),
    ],
)
@pytest.mark.parametrize("dataset_fn", [_overconfident_samples, _underconfident_samples])
def test_all_methods_reduce_ece_and_round_trip_deterministically(
    method: CalibrationMethod,
    binning: BinningStrategy,
    dataset_fn: Any,
    tmp_path: Path,
) -> None:
    samples = dataset_fn(80)
    raw_metrics = compute_calibration_metrics(samples, num_bins=10)
    assert raw_metrics.ece > 0.10

    model = fit_calibrator(
        samples,
        method=method,
        num_bins=8,
        binning_strategy=binning,
        schema_id="inv-schema",
        strategy_name="demo-strategy",
        description="Test artifact",
    )
    assert model.artifact_version == CALIBRATION_ARTIFACT_VERSION
    assert model.scale == "0-1"
    assert model.method == method.value
    assert model.metadata.sample_count == 80
    assert model.metadata.positive_count + model.metadata.negative_count == 80
    assert model.metadata.schema_id == "inv-schema"
    assert model.metadata.strategy_name == "demo-strategy"
    assert model.metadata.ece_after <= raw_metrics.ece * 0.5, (
        f"Expected >=50% ECE drop for {method}/{binning}: "
        f"before={raw_metrics.ece}, after={model.metadata.ece_after}"
    )

    # Check bounds at exact endpoints and grid points; check monotonicity for isotonic & Platt
    grid = [i / 20.0 for i in range(21)]
    preds = [model.calibrate(x) for x in grid]
    assert all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in preds)
    if method in (CalibrationMethod.ISOTONIC, CalibrationMethod.PLATT):
        for i in range(len(preds) - 1):
            assert preds[i] <= preds[i + 1] + 1e-12

    # Dict, JSON, and File round-trip equality and identical inference
    assert CalibrationModel.from_dict(model.to_dict()) == model
    assert CalibrationModel.from_json(model.to_json()) == model
    artifact_file = tmp_path / f"{method.value}_{binning.value}.json"
    model.to_file(artifact_file)
    reloaded = CalibrationModel.from_file(artifact_file)
    assert reloaded == model
    assert [reloaded.calibrate(x) for x in grid] == preds


def test_fit_calibrator_handles_zero_variance_x_and_inverted_labels() -> None:
    zero_var = [(0.7, i % 4 != 0) for i in range(24)]
    for method in CalibrationMethod:
        model = fit_calibrator(zero_var, method=method, binning_strategy=BinningStrategy.QUANTILE)
        out = model.calibrate(0.7)
        assert math.isfinite(out) and 0.0 <= out <= 1.0
        assert abs(out - 0.75) < 0.05

    # Inverted scores (higher raw score -> lower accuracy): Platt projects slope >= 0 cleanly
    inverted = [(0.9, False)] * 12 + [(0.1, True)] * 12
    platt = fit_calibrator(inverted, method=CalibrationMethod.PLATT)
    assert platt.platt is not None
    assert platt.platt.slope >= 0.0
    assert platt.calibrate(0.0) <= platt.calibrate(0.5) <= platt.calibrate(1.0)


def test_fit_calibrator_eagerly_rejects_invalid_inputs() -> None:
    good = _overconfident_samples(24)

    with pytest.raises(ConfigurationError, match="Unsupported calibration method"):
        fit_calibrator(good, method="unknown")
    with pytest.raises(ConfigurationError, match="Unsupported binning_strategy"):
        fit_calibrator(good, method="histogram", binning_strategy="weird")
    with pytest.raises(ConfigurationError, match="min_samples"):
        fit_calibrator(good, min_samples=3)
    with pytest.raises(ConfigurationError, match="at least 20"):
        fit_calibrator(good[:10])
    with pytest.raises(ConfigurationError, match="both positive"):
        fit_calibrator([(0.8, True)] * 20)
    with pytest.raises(ConfigurationError, match="both positive"):
        fit_calibrator([(0.2, False)] * 20)
    with pytest.raises(ConfigurationError, match="Invalid calibration sample"):
        fit_calibrator([(math.nan, True), *good[1:]])
    with pytest.raises(ConfigurationError, match="Invalid calibration sample"):
        fit_calibrator([(1.5, True), *good[1:]])
    with pytest.raises(ConfigurationError, match="Invalid calibration sample"):
        fit_calibrator([(0.5, 1), *good[1:]])  # type: ignore[list-item]
    with pytest.raises(ConfigurationError, match="num_bins"):
        fit_calibrator(good, method="histogram", num_bins=1)


def test_calibration_model_from_dict_json_file_strict_validation(tmp_path: Path) -> None:
    valid_model = fit_calibrator(_overconfident_samples(24), method=CalibrationMethod.ISOTONIC)
    base_dict = valid_model.to_dict()

    # Unknown extra key rejected
    extra = dict(base_dict)
    extra["unexpected"] = 123
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(extra)

    # Unsupported version rejected
    bad_ver = dict(base_dict)
    bad_ver["artifact_version"] = "2.0"
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(bad_ver)

    # Non-monotonic isotonic y_knots rejected
    bad_iso = json.loads(valid_model.to_json())
    bad_iso["isotonic"] = {"x_knots": [0.1, 0.5, 0.9], "y_knots": [0.2, 0.8, 0.4]}
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(bad_iso)

    # Non-increasing isotonic x_knots rejected
    bad_x = json.loads(valid_model.to_json())
    bad_x["isotonic"] = {"x_knots": [0.5, 0.5], "y_knots": [0.2, 0.4]}
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(bad_x)

    # Negative Platt slope rejected
    platt_model = fit_calibrator(_overconfident_samples(24), method=CalibrationMethod.PLATT)
    platt_dict = platt_model.to_dict()
    platt_dict["platt"]["slope"] = -0.5
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(platt_dict)

    # Bad histogram edges rejected
    hist_dict = fit_calibrator(
        _overconfident_samples(24), method=CalibrationMethod.HISTOGRAM
    ).to_dict()
    hist_dict["histogram"]["bin_edges"] = [0.1, 0.5, 1.0]
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_dict(hist_dict)

    # Malformed JSON & non-object JSON
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_json("{not json")
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_json("[1, 2, 3]")

    # Missing file & empty path & oversized (> 5 MiB) file
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_file("")
    with pytest.raises(ConfigurationError):
        CalibrationModel.from_file(tmp_path / "does_not_exist.json")

    huge_file = tmp_path / "huge.json"
    huge_file.write_bytes(b" " * (MAX_CALIBRATION_ARTIFACT_BYTES + 1))
    with pytest.raises(ConfigurationError, match="maximum size limit"):
        CalibrationModel.from_file(huge_file)


# =============================================================================
# User Story 2: Normalizer, ResultBuilder, Engine (Sync/Async/Batch) & Fault Isolation
# =============================================================================


def test_normalizer_honest_calibration_and_edge_cases() -> None:
    cal = fit_calibrator(_overconfident_samples(40), method=CalibrationMethod.ISOTONIC)
    norm = ConfidenceNormalizer(calibrator=cal)
    assert norm.calibrator is cal

    # Valid 0-1 score -> calibrated=True + transparent method attribution
    ok = norm.normalize(
        raw_value=0.92,
        scale="0-1",
        method="llm-self-assessment",
        record_id="record-001",
        field_path="code",
    )
    assert ok.confidence is not None
    assert ok.confidence.calibrated is True
    assert ok.confidence.scale == "0-1"
    assert ok.confidence.value == round(cal.calibrate(0.92), 6)
    assert ok.confidence.method == "llm-self-assessment+calibrated:isotonic"
    assert ok.warnings == []

    # Duplicate suffix avoided when method already carries it
    dup = norm.normalize(raw_value=0.92, method="llm+calibrated:isotonic")
    assert dup.confidence is not None
    assert dup.confidence.method == "llm+calibrated:isotonic"

    # Missing / non-numeric / bool / NaN / inf / out-of-range NEVER calibrated
    none_res = norm.normalize(raw_value=None)
    assert none_res.confidence is None and none_res.warnings == []

    for bad, expected_code in (
        ("0.8", "invalid_confidence_value"),
        (True, "invalid_confidence_value"),
        (math.nan, "invalid_confidence_range"),
        (math.inf, "invalid_confidence_range"),
        (-0.01, "invalid_confidence_range"),
        (1.01, "invalid_confidence_range"),
    ):
        res = norm.normalize(raw_value=bad, field_path="code")  # type: ignore[arg-type]
        assert res.confidence is not None
        assert res.confidence.value is None
        assert res.confidence.calibrated is False
        assert [w.code for w in res.warnings] == [expected_code]

    # Scale mismatch -> preserved uncalibrated score + warning
    mismatch = norm.normalize(raw_value=85.0, scale="0-100", method="custom", field_path="score")
    assert mismatch.confidence is not None
    assert mismatch.confidence.value == 85.0
    assert mismatch.confidence.scale == "0-100"
    assert mismatch.confidence.calibrated is False
    assert [w.code for w in mismatch.warnings] == ["confidence_calibration_scale_mismatch"]
    assert mismatch.warnings[0].severity == IssueSeverity.WARNING
    assert mismatch.warnings[0].stage == PipelineStage.EXTRACTION


@pytest.mark.parametrize(
    "broken",
    [
        _BrokenCalibrator(exc=RuntimeError("Boom secret sk-9999999999999999999999")),
        _BrokenCalibrator(return_value=math.nan),
        _BrokenCalibrator(return_value=math.inf),
        _BrokenCalibrator(return_value=-0.1),
        _BrokenCalibrator(return_value=1.2),
        _BrokenCalibrator(return_value=True),
        _BrokenCalibrator(return_value="0.5"),
    ],
)
def test_calibrator_fault_isolation_falls_back_cleanly(broken: _BrokenCalibrator) -> None:
    doc = DocumentInput.from_text("Code: INV-1", filename="doc.txt")
    res = extract(doc, _simple_schema(), strategy=_simple_strategy(), calibrator=broken)

    # Processing status, validation, and provenance unaffected by calibrator failure
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    field = res.records[0].fields["code"]
    assert field.confidence is not None
    assert field.confidence.value == 0.92
    assert field.confidence.calibrated is False
    assert field.confidence.method == "llm-self-assessment"
    assert [w.code for w in field.warnings] == ["confidence_calibration_failed"]
    assert "sk-9999999999999999999999" not in field.warnings[0].message


def test_engine_builder_conflicts_call_override_contract_roundtrip_and_async_batch() -> None:
    iso = fit_calibrator(_overconfident_samples(40), method=CalibrationMethod.ISOTONIC)
    platt = fit_calibrator(_overconfident_samples(40), method=CalibrationMethod.PLATT)
    strat = _simple_strategy()
    schema = _simple_schema()
    doc = DocumentInput.from_text("Code: INV-1", filename="doc.txt")

    # Eager ConfigurationError on invalid calibrator or conflicting constructor args
    with pytest.raises(ConfigurationError):
        ConfidenceNormalizer(calibrator="not-a-calibrator")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        ResultBuilder(confidence_normalizer=ConfidenceNormalizer(), calibrator=iso)
    with pytest.raises(ConfigurationError):
        ExtractionEngine(strat, result_builder=ResultBuilder(), calibrator=iso)

    # Engine-level calibrator + non-mutating call-level override
    engine = ExtractionEngine(strat, calibrator=iso)
    res_engine = engine.extract(doc, schema)
    res_override = engine.extract(doc, schema, calibrator=platt)
    res_after = engine.extract(doc, schema)

    c_eng = res_engine.records[0].fields["code"].confidence
    c_ovr = res_override.records[0].fields["code"].confidence
    c_aft = res_after.records[0].fields["code"].confidence
    assert c_eng is not None and c_ovr is not None and c_aft is not None
    assert c_eng.calibrated is True and c_eng.method.endswith("+calibrated:isotonic")
    assert c_ovr.calibrated is True and c_ovr.method.endswith("+calibrated:platt")
    assert c_aft.calibrated is True and c_aft.method.endswith("+calibrated:isotonic")

    # Contract v1.0 lossless JSON round-trip with calibrated fields
    assert ExtractionResult.from_json(res_engine.to_json()) == res_engine

    # Independence: invalid field still calibrates; uncalibrated default still False
    bad_type_res = extract(
        doc,
        schema,
        strategy=_simple_strategy(code_value=12345),
        calibrator=iso,
    )
    assert bad_type_res.status == ProcessingStatus.COMPLETED
    assert bad_type_res.validation.status == ValidationStatus.INVALID
    f_bad = bad_type_res.records[0].fields["code"]
    assert f_bad.confidence is not None and f_bad.confidence.calibrated is True

    uncal_res = extract(doc, schema, strategy=strat)
    f_uncal = uncal_res.records[0].fields["code"]
    assert f_uncal.confidence is not None and f_uncal.confidence.calibrated is False

    # Concurrent sync batch + async single + async batch
    docs = [DocumentInput.from_text("Code: INV-1", filename=f"d{i}.txt") for i in range(4)]
    batch_sync = extract_batch(
        docs, schema, strategy=strat, options=BatchOptions(max_workers=4), calibrator=iso
    )
    assert all(
        r.records[0].fields["code"].confidence is not None
        and r.records[0].fields["code"].confidence.calibrated is True
        for r in batch_sync.results
    )

    async def _run_async() -> tuple[ExtractionResult, list[ExtractionResult]]:
        single = await aextract(doc, schema, strategy=strat, calibrator=platt)
        batch_a = await aextract_batch(
            docs, schema, strategy=strat, options=BatchOptions(max_workers=4), calibrator=iso
        )
        return single, list(batch_a.results)

    async_single, async_batch_results = asyncio.run(_run_async())
    f_async = async_single.records[0].fields["code"]
    assert f_async.confidence is not None
    assert f_async.confidence.calibrated is True
    assert f_async.confidence.method.endswith("+calibrated:platt")
    assert all(
        r.records[0].fields["code"].confidence is not None
        and r.records[0].fields["code"].confidence.calibrated is True
        for r in async_batch_results
    )


# =============================================================================
# User Story 3: Thin CLI `--calibrator` Support
# =============================================================================


def test_cli_extract_and_extract_batch_with_calibrator(tmp_path: Path) -> None:
    cal = fit_calibrator(_overconfident_samples(40), method=CalibrationMethod.ISOTONIC)
    cal_path = tmp_path / "calibrator.json"
    cal.to_file(cal_path)

    schema_path = tmp_path / "schema.json"
    schema_path.write_text(_simple_schema().to_json(indent=2), encoding="utf-8")

    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    doc1 = doc_dir / "a.txt"
    doc2 = doc_dir / "b.txt"
    doc1.write_text("Code: INV-1\n", encoding="utf-8")
    doc2.write_text("Code: INV-1\n", encoding="utf-8")

    # Single extract with valid --calibrator
    out, err = io.StringIO(), io.StringIO()
    rc = run_cli(
        ["extract", str(doc1), "--schema", str(schema_path), "--calibrator", str(cal_path)],
        stdout=out,
        stderr=err,
        strategy_override=_simple_strategy(),
    )
    assert rc == 0, err.getvalue()
    parsed = ExtractionResult.from_json(out.getvalue())
    conf = parsed.records[0].fields["code"].confidence
    assert conf is not None
    assert conf.calibrated is True
    assert conf.method == "llm-self-assessment+calibrated:isotonic"

    # Batch extract with valid --calibrator
    out_b, err_b = io.StringIO(), io.StringIO()
    rc_b = run_cli(
        [
            "extract-batch",
            str(doc_dir),
            "--schema",
            str(schema_path),
            "--calibrator",
            str(cal_path),
        ],
        stdout=out_b,
        stderr=err_b,
        strategy_override=_simple_strategy(),
    )
    assert rc_b == 0, err_b.getvalue()
    lines = [line for line in out_b.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        item = ExtractionResult.from_json(line)
        item_conf = item.records[0].fields["code"].confidence
        assert item_conf is not None and item_conf.calibrated is True

    # Missing & corrupt --calibrator fail eagerly with code 1 and empty stdout
    bad_cal = tmp_path / "corrupt.json"
    bad_cal.write_text('{"artifact_version": "1.0", "bad": true}', encoding="utf-8")
    for cmd_args in (
        ["extract", str(doc1), "--schema", str(schema_path), "--calibrator", str(bad_cal)],
        [
            "extract-batch",
            str(doc_dir),
            "--schema",
            str(schema_path),
            "--calibrator",
            str(tmp_path / "missing.json"),
        ],
    ):
        o_fail, e_fail = io.StringIO(), io.StringIO()
        rc_fail = run_cli(
            cmd_args,
            stdout=o_fail,
            stderr=e_fail,
            strategy_override=_simple_strategy(),
        )
        assert rc_fail == 1
        assert o_fail.getvalue() == ""
        assert "Error:" in e_fail.getvalue()


# =============================================================================
# Property-Based Tests (Hypothesis, 100 examples each — SC-003)
# =============================================================================


@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(
    method=st.sampled_from(list(CalibrationMethod)),
    binning=st.sampled_from(list(BinningStrategy)),
    scores=st.lists(
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=6,
        max_size=30,
    ),
    query_a=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    query_b=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property_calibration_bounds_monotonicity_determinism_and_roundtrip(
    method: CalibrationMethod,
    binning: BinningStrategy,
    scores: list[float],
    query_a: float,
    query_b: float,
) -> None:
    # Ensure both True and False labels exist cleanly
    samples = [
        CalibrationSample(raw_score=s, is_correct=(idx % 2 == 0)) for idx, s in enumerate(scores)
    ]
    model = fit_calibrator(
        samples,
        method=method,
        binning_strategy=binning,
        num_bins=5,
        min_samples=4,
    )
    lo, hi = (query_a, query_b) if query_a <= query_b else (query_b, query_a)
    p_lo = model.calibrate(lo)
    p_hi = model.calibrate(hi)

    assert math.isfinite(p_lo) and 0.0 <= p_lo <= 1.0
    assert math.isfinite(p_hi) and 0.0 <= p_hi <= 1.0
    assert p_lo == model.calibrate(lo)  # Pure determinism

    if method in (CalibrationMethod.ISOTONIC, CalibrationMethod.PLATT):
        assert p_lo <= p_hi + 1e-12

    reloaded = CalibrationModel.from_json(model.to_json())
    assert reloaded == model
    assert reloaded.calibrate(lo) == p_lo
    assert reloaded.calibrate(hi) == p_hi


@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(
    raw_score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    bad_out=st.one_of(
        st.just(math.nan),
        st.just(math.inf),
        st.just(-math.inf),
        st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False),
        st.booleans(),
        st.text(max_size=10),
    ),
)
def test_property_faulty_calibrator_never_fabricates_calibrated_true(
    raw_score: float,
    bad_out: Any,
) -> None:
    norm = ConfidenceNormalizer(calibrator=_BrokenCalibrator(return_value=bad_out))
    res = norm.normalize(raw_value=raw_score, field_path="f")
    assert res.confidence is not None
    assert res.confidence.calibrated is False
    assert res.confidence.value == round(raw_score, 6)
    assert [w.code for w in res.warnings] == ["confidence_calibration_failed"]
