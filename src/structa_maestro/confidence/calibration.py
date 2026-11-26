"""Zero-dependency empirical confidence calibration models, fitters, metrics, and persistence.

Implements pure-stdlib deterministic fitting and inference for:
- Isotonic regression (Pool Adjacent Violators Algorithm — PAVA + piecewise-linear interpolation)
- Platt / logistic scaling on clamped logits (regularized damped Newton with non-negative slope)
- Histogram / quantile binning (with empirical neighbor fallback for empty bins)

Calibration artifacts are immutable, versioned (`1.0`), pure JSON (`extra="forbid"`), and strictly
validated on load with zero third-party ML runtime dependencies.
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from structa_maestro.exceptions import ConfigurationError

CALIBRATION_ARTIFACT_VERSION: Literal["1.0"] = "1.0"
DEFAULT_CALIBRATION_MIN_SAMPLES = 20
MIN_CALIBRATION_SAMPLES_FLOOR = 4
MAX_CALIBRATION_ARTIFACT_BYTES = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_PLATT_EPSILON = 1e-6
_PLATT_L2_REG = 1e-4
_MAX_HISTOGRAM_BINS = 50
_MAX_METRIC_BINS = 100


class CalibrationMethod(StrEnum):
    """Supported deterministic empirical confidence calibration methods."""

    ISOTONIC = "isotonic"
    PLATT = "platt"
    HISTOGRAM = "histogram"


class BinningStrategy(StrEnum):
    """Bin boundary strategy for histogram confidence calibration."""

    UNIFORM = "uniform"
    QUANTILE = "quantile"


@runtime_checkable
class ConfidenceCalibrator(Protocol):
    """Runtime-checkable protocol for stateless, deterministic confidence calibrators."""

    @property
    def scale(self) -> str:
        """Confidence scale supported by this calibrator (e.g., ``'0-1'``)."""
        ...

    @property
    def method(self) -> str:
        """Non-empty calibration method identifier (e.g., ``'isotonic'``)."""
        ...

    def calibrate(self, raw_score: float) -> float:
        """Map a finite raw confidence score on ``scale`` to a calibrated probability in [0, 1]."""
        ...


def _validate_strict_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite numeric value.")
    num = float(value)
    if not math.isfinite(num):
        raise ValueError(f"{field_name} must be finite (NaN/inf not allowed).")
    return num


def _validate_unit_float(value: Any, *, field_name: str) -> float:
    num = _validate_strict_float(value, field_name=field_name)
    if not (0.0 <= num <= 1.0):
        raise ValueError(f"{field_name} must be within [0.0, 1.0].")
    return num


def _validate_strict_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer.")
    return value


def _validate_optional_nonblank(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string or None.")
    return value.strip()


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _safe_logit(p: float, eps: float) -> float:
    pc = min(max(p, eps), 1.0 - eps)
    return math.log(pc / (1.0 - pc))


def _safe_sigmoid(u: float) -> float:
    if u >= 0.0:
        return 1.0 / (1.0 + math.exp(-u))
    eu = math.exp(u)
    return eu / (1.0 + eu)


def _log1p_exp(u: float) -> float:
    if u > 0.0:
        return u + math.log1p(math.exp(-u))
    return math.log1p(math.exp(u))


class _CalibrationBaseModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CalibrationSample(_CalibrationBaseModel):
    """Labeled observation pairing a raw [0, 1] confidence score with observed correctness."""

    raw_score: float
    is_correct: bool

    @field_validator("raw_score", mode="before")
    @classmethod
    def _check_raw_score(cls, value: Any) -> float:
        return _validate_unit_float(value, field_name="CalibrationSample.raw_score")

    @field_validator("is_correct", mode="before")
    @classmethod
    def _check_is_correct(cls, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError("CalibrationSample.is_correct must be a strict boolean.")
        return value


class CalibrationBin(_CalibrationBaseModel):
    """Reliability summary for a single equal-width confidence interval."""

    bin_index: int = Field(ge=0)
    lower_bound: float
    upper_bound: float
    count: int = Field(ge=0)
    mean_confidence: float | None = None
    empirical_accuracy: float | None = None
    calibration_gap: float | None = None

    @field_validator("bin_index", "count", mode="before")
    @classmethod
    def _check_ints(cls, value: Any) -> int:
        return _validate_strict_int(value, field_name="CalibrationBin integer field")

    @field_validator("lower_bound", "upper_bound", mode="before")
    @classmethod
    def _check_bounds(cls, value: Any) -> float:
        return _validate_unit_float(value, field_name="CalibrationBin bound")

    @field_validator("mean_confidence", "empirical_accuracy", "calibration_gap", mode="before")
    @classmethod
    def _check_opt_unit(cls, value: Any) -> float | None:
        if value is None:
            return None
        return _validate_unit_float(value, field_name="CalibrationBin metric")

    @model_validator(mode="after")
    def _check_consistency(self) -> CalibrationBin:
        if not (self.lower_bound < self.upper_bound):
            raise ValueError("CalibrationBin.lower_bound must be < upper_bound.")
        if self.count == 0:
            if (
                self.mean_confidence is not None
                or self.empirical_accuracy is not None
                or self.calibration_gap is not None
            ):
                raise ValueError("Empty CalibrationBin must have None mean/accuracy/gap.")
        else:
            if (
                self.mean_confidence is None
                or self.empirical_accuracy is None
                or self.calibration_gap is None
            ):
                raise ValueError("Non-empty CalibrationBin must populate mean/accuracy/gap.")
        return self


class CalibrationMetrics(_CalibrationBaseModel):
    """Aggregate empirical calibration quality metrics (ECE, MCE, Brier score, and bins)."""

    sample_count: int = Field(ge=0)
    num_bins: int = Field(ge=1, le=_MAX_METRIC_BINS)
    ece: float
    mce: float
    brier_score: float
    bins: tuple[CalibrationBin, ...]

    @field_validator("sample_count", "num_bins", mode="before")
    @classmethod
    def _check_ints(cls, value: Any) -> int:
        return _validate_strict_int(value, field_name="CalibrationMetrics integer field")

    @field_validator("ece", "mce", "brier_score", mode="before")
    @classmethod
    def _check_metrics(cls, value: Any) -> float:
        return _validate_unit_float(value, field_name="CalibrationMetrics score")

    @model_validator(mode="after")
    def _check_bin_count(self) -> CalibrationMetrics:
        if len(self.bins) != self.num_bins:
            raise ValueError("CalibrationMetrics.bins length must equal num_bins.")
        return self


class CalibrationMetadata(_CalibrationBaseModel):
    """Immutable provenance and quality summary stored inside every calibration artifact."""

    artifact_version: Literal["1.0"] = CALIBRATION_ARTIFACT_VERSION
    method: CalibrationMethod
    scale: str = "0-1"
    sample_count: int = Field(ge=MIN_CALIBRATION_SAMPLES_FLOOR)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    fitted_at: str
    ece_before: float
    ece_after: float
    mce_before: float
    mce_after: float
    brier_before: float
    brier_after: float
    schema_id: str | None = None
    strategy_name: str | None = None
    description: str | None = None

    @field_validator("sample_count", "positive_count", "negative_count", mode="before")
    @classmethod
    def _check_counts(cls, value: Any) -> int:
        return _validate_strict_int(value, field_name="CalibrationMetadata count")

    @field_validator("scale", mode="before")
    @classmethod
    def _check_scale(cls, value: Any) -> str:
        if not isinstance(value, str) or value.strip() != "0-1":
            raise ValueError("CalibrationMetadata.scale must be '0-1' for built-in artifacts.")
        return "0-1"

    @field_validator("fitted_at", mode="before")
    @classmethod
    def _check_fitted_at(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("CalibrationMetadata.fitted_at must be a non-empty ISO timestamp.")
        return value.strip()

    @field_validator(
        "ece_before",
        "ece_after",
        "mce_before",
        "mce_after",
        "brier_before",
        "brier_after",
        mode="before",
    )
    @classmethod
    def _check_unit_metrics(cls, value: Any) -> float:
        return _validate_unit_float(value, field_name="CalibrationMetadata metric")

    @field_validator("schema_id", "strategy_name", "description", mode="before")
    @classmethod
    def _check_optional_tags(cls, value: Any) -> str | None:
        return _validate_optional_nonblank(value, field_name="CalibrationMetadata tag")

    @model_validator(mode="after")
    def _check_count_sum(self) -> CalibrationMetadata:
        if self.positive_count + self.negative_count != self.sample_count:
            raise ValueError(
                "CalibrationMetadata.positive_count + negative_count must equal sample_count."
            )
        return self


class IsotonicParameters(_CalibrationBaseModel):
    """Strictly ordered x-knots paired with monotonically non-decreasing y-knots in [0, 1]."""

    x_knots: tuple[float, ...]
    y_knots: tuple[float, ...]

    @field_validator("x_knots", "y_knots", mode="before")
    @classmethod
    def _check_knot_tuples(cls, value: Any) -> tuple[float, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("Isotonic knots must be a sequence of floats.")
        return tuple(_validate_unit_float(v, field_name="Isotonic knot") for v in value)

    @model_validator(mode="after")
    def _check_monotonicity(self) -> IsotonicParameters:
        if len(self.x_knots) < 1:
            raise ValueError("IsotonicParameters.x_knots must contain at least one knot.")
        if len(self.x_knots) != len(self.y_knots):
            raise ValueError("IsotonicParameters x_knots and y_knots must have equal length.")
        for idx in range(len(self.x_knots) - 1):
            if not (self.x_knots[idx] < self.x_knots[idx + 1]):
                raise ValueError("IsotonicParameters.x_knots must be strictly increasing.")
            if not (self.y_knots[idx] <= self.y_knots[idx + 1]):
                raise ValueError("IsotonicParameters.y_knots must be monotonically non-decreasing.")
        return self


class PlattParameters(_CalibrationBaseModel):
    """Monotonic logistic scaling parameters evaluated on clamped logits."""

    slope: float
    intercept: float
    epsilon: float = _DEFAULT_PLATT_EPSILON

    @field_validator("slope", mode="before")
    @classmethod
    def _check_slope(cls, value: Any) -> float:
        num = _validate_strict_float(value, field_name="PlattParameters.slope")
        if num < 0.0:
            raise ValueError("PlattParameters.slope must be >= 0.0 to preserve monotonicity.")
        return num

    @field_validator("intercept", mode="before")
    @classmethod
    def _check_intercept(cls, value: Any) -> float:
        return _validate_strict_float(value, field_name="PlattParameters.intercept")

    @field_validator("epsilon", mode="before")
    @classmethod
    def _check_epsilon(cls, value: Any) -> float:
        num = _validate_strict_float(value, field_name="PlattParameters.epsilon")
        if not (0.0 < num < 0.5):
            raise ValueError("PlattParameters.epsilon must lie strictly within (0.0, 0.5).")
        return num


class HistogramParameters(_CalibrationBaseModel):
    """Strictly increasing [0.0, ..., 1.0] bin edges paired with calibrated empirical rates."""

    binning_strategy: BinningStrategy
    bin_edges: tuple[float, ...]
    bin_values: tuple[float, ...]

    @field_validator("bin_edges", "bin_values", mode="before")
    @classmethod
    def _check_sequences(cls, value: Any) -> tuple[float, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("Histogram edges/values must be sequences of floats.")
        return tuple(_validate_unit_float(v, field_name="Histogram parameter") for v in value)

    @model_validator(mode="after")
    def _check_edges_and_values(self) -> HistogramParameters:
        if len(self.bin_edges) < 2:
            raise ValueError("HistogramParameters.bin_edges must have at least 2 edges.")
        if self.bin_edges[0] != 0.0 or self.bin_edges[-1] != 1.0:
            raise ValueError("HistogramParameters.bin_edges must start at 0.0 and end at 1.0.")
        for idx in range(len(self.bin_edges) - 1):
            if not (self.bin_edges[idx] < self.bin_edges[idx + 1]):
                raise ValueError("HistogramParameters.bin_edges must be strictly increasing.")
        if len(self.bin_values) != len(self.bin_edges) - 1:
            raise ValueError("HistogramParameters.bin_values length must equal len(bin_edges) - 1.")
        return self


class CalibrationModel(_CalibrationBaseModel):
    """Immutable, versioned, JSON-serializable empirical confidence calibration artifact."""

    artifact_version: Literal["1.0"] = CALIBRATION_ARTIFACT_VERSION
    metadata: CalibrationMetadata
    isotonic: IsotonicParameters | None = None
    platt: PlattParameters | None = None
    histogram: HistogramParameters | None = None

    @model_validator(mode="after")
    def _check_method_payload(self) -> CalibrationModel:
        if self.artifact_version != self.metadata.artifact_version:
            raise ValueError("CalibrationModel.artifact_version must match metadata version.")
        expected = self.metadata.method
        present = {
            CalibrationMethod.ISOTONIC: self.isotonic is not None,
            CalibrationMethod.PLATT: self.platt is not None,
            CalibrationMethod.HISTOGRAM: self.histogram is not None,
        }
        for method_key, is_present in present.items():
            if method_key == expected and not is_present:
                raise ValueError(
                    f"CalibrationModel missing parameter payload for method '{expected.value}'."
                )
            if method_key != expected and is_present:
                raise ValueError(
                    f"CalibrationModel contains unexpected payload for '{method_key.value}' "
                    f"when metadata.method is '{expected.value}'."
                )
        return self

    @property
    def scale(self) -> str:
        """Confidence scale supported by this artifact."""
        return self.metadata.scale

    @property
    def method(self) -> str:
        """Calibration algorithm identifier."""
        return self.metadata.method.value

    def calibrate(self, raw_score: float) -> float:
        """Map a finite raw confidence score in [0.0, 1.0] to a calibrated probability."""
        x = _validate_unit_float(raw_score, field_name="raw_score")
        if self.isotonic is not None:
            return _eval_isotonic(self.isotonic, x)
        if self.platt is not None:
            return _eval_platt(self.platt, x)
        if self.histogram is not None:
            return _eval_histogram(self.histogram, x)
        raise ConfigurationError("CalibrationModel has no active calibration parameters.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize this calibration artifact into a plain JSON-compatible dictionary."""
        return self.model_dump(mode="json")

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize this calibration artifact to deterministic UTF-8 JSON."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_file(self, path: str | Path, *, indent: int = 2) -> Path:
        """Persist this calibration artifact to disk as UTF-8 JSON."""
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.to_json(indent=indent) + "\n", encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(
                f"Failed to write calibration artifact ({type(exc).__name__})."
            ) from exc
        return target

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationModel:
        """Validate and load a CalibrationModel from a mapping, raising ConfigurationError."""
        if not isinstance(data, Mapping):
            raise ConfigurationError("Calibration artifact payload must be a JSON object.")
        try:
            return cls.model_validate(dict(data))
        except ValidationError as exc:
            first_loc = ".".join(str(p) for p in exc.errors()[0].get("loc", ())) or "<root>"
            first_type = exc.errors()[0].get("type", "validation_error")
            raise ConfigurationError(
                f"Invalid calibration artifact at '{first_loc}' ({first_type})."
            ) from exc

    @classmethod
    def from_json(cls, raw_json: str) -> CalibrationModel:
        """Validate and load a CalibrationModel from a JSON string."""
        if not isinstance(raw_json, str) or not raw_json.strip():
            raise ConfigurationError("Calibration artifact JSON must be a non-empty string.")
        if len(raw_json.encode("utf-8")) > MAX_CALIBRATION_ARTIFACT_BYTES:
            raise ConfigurationError(
                f"Calibration artifact exceeds maximum size limit "
                f"({MAX_CALIBRATION_ARTIFACT_BYTES} bytes)."
            )
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ConfigurationError("Calibration artifact is not valid JSON.") from exc
        if not isinstance(parsed, Mapping):
            raise ConfigurationError("Calibration artifact JSON root must be an object.")
        return cls.from_dict(parsed)

    @classmethod
    def from_file(cls, path: str | Path) -> CalibrationModel:
        """Validate and load a CalibrationModel from a JSON file with strict size limits."""
        if isinstance(path, str) and not path.strip():
            raise ConfigurationError("Calibration artifact file path must be non-empty.")
        target = Path(path)
        try:
            if not target.exists() or not target.is_file():
                raise ConfigurationError(f"Calibration artifact file not found: '{target.name}'.")
            size = target.stat().st_size
            if size > MAX_CALIBRATION_ARTIFACT_BYTES:
                raise ConfigurationError(
                    f"Calibration artifact exceeds maximum size limit "
                    f"({MAX_CALIBRATION_ARTIFACT_BYTES} bytes)."
                )
            raw_text = target.read_text(encoding="utf-8")
        except ConfigurationError:
            raise
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(
                f"Unable to read calibration artifact ({type(exc).__name__})."
            ) from exc
        return cls.from_json(raw_text)


def _eval_isotonic(params: IsotonicParameters, x: float) -> float:
    xs = params.x_knots
    ys = params.y_knots
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    idx = bisect_right(xs, x)
    if idx <= 0:
        return ys[0]
    if idx >= len(xs):
        return ys[-1]
    x0, x1 = xs[idx - 1], xs[idx]
    y0, y1 = ys[idx - 1], ys[idx]
    span = x1 - x0
    if span <= 0.0:
        return y1
    frac = (x - x0) / span
    return _clamp_unit(y0 + frac * (y1 - y0))


def _eval_platt(params: PlattParameters, x: float) -> float:
    z = _safe_logit(x, params.epsilon)
    return _clamp_unit(_safe_sigmoid(params.slope * z + params.intercept))


def _eval_histogram(params: HistogramParameters, x: float) -> float:
    idx = bisect_right(params.bin_edges, x) - 1
    if idx < 0:
        idx = 0
    elif idx >= len(params.bin_values):
        idx = len(params.bin_values) - 1
    return params.bin_values[idx]


def _coerce_samples(
    samples: Sequence[CalibrationSample | tuple[float, bool]],
) -> list[CalibrationSample]:
    if isinstance(samples, (str, bytes)) or not isinstance(samples, Sequence):
        raise ConfigurationError(
            "samples must be a sequence of CalibrationSample or (score, bool)."
        )
    coerced: list[CalibrationSample] = []
    for idx, item in enumerate(samples):
        if isinstance(item, CalibrationSample):
            coerced.append(item)
            continue
        if isinstance(item, tuple) and len(item) == 2:
            raw_score, is_correct = item
            try:
                coerced.append(CalibrationSample(raw_score=raw_score, is_correct=is_correct))
            except ValidationError as exc:
                raise ConfigurationError(f"Invalid calibration sample at index {idx}.") from exc
            continue
        raise ConfigurationError(
            f"Calibration sample at index {idx} must be a CalibrationSample or (float, bool) tuple."
        )
    return coerced


def compute_calibration_metrics(
    samples: Sequence[CalibrationSample | tuple[float, bool]],
    *,
    num_bins: int = 10,
) -> CalibrationMetrics:
    """Compute deterministic ECE, MCE, Brier score, and per-bin reliability statistics."""
    if isinstance(num_bins, bool) or not isinstance(num_bins, int) or not (1 <= num_bins <= 100):
        raise ConfigurationError("num_bins must be an integer between 1 and 100.")

    coerced = _coerce_samples(samples)
    total = len(coerced)
    counts = [0] * num_bins
    sum_conf = [0.0] * num_bins
    sum_acc = [0.0] * num_bins
    brier_total = 0.0

    for sample in coerced:
        y = 1.0 if sample.is_correct else 0.0
        diff = sample.raw_score - y
        brier_total += diff * diff
        bin_idx = min(int(sample.raw_score * num_bins), num_bins - 1)
        counts[bin_idx] += 1
        sum_conf[bin_idx] += sample.raw_score
        sum_acc[bin_idx] += y

    bins: list[CalibrationBin] = []
    ece_acc = 0.0
    mce_val = 0.0
    for idx in range(num_bins):
        lower = idx / num_bins
        upper = 1.0 if idx == num_bins - 1 else (idx + 1) / num_bins
        cnt = counts[idx]
        if cnt == 0:
            bins.append(
                CalibrationBin(
                    bin_index=idx,
                    lower_bound=round(lower, 6),
                    upper_bound=round(upper, 6),
                    count=0,
                )
            )
        else:
            mean_c = _clamp_unit(sum_conf[idx] / cnt)
            emp_a = _clamp_unit(sum_acc[idx] / cnt)
            gap = _clamp_unit(abs(emp_a - mean_c))
            ece_acc += (cnt / total) * gap
            if gap > mce_val:
                mce_val = gap
            bins.append(
                CalibrationBin(
                    bin_index=idx,
                    lower_bound=round(lower, 6),
                    upper_bound=round(upper, 6),
                    count=cnt,
                    mean_confidence=round(mean_c, 6),
                    empirical_accuracy=round(emp_a, 6),
                    calibration_gap=round(gap, 6),
                )
            )

    brier = _clamp_unit(brier_total / total) if total > 0 else 0.0
    return CalibrationMetrics(
        sample_count=total,
        num_bins=num_bins,
        ece=round(_clamp_unit(ece_acc), 6),
        mce=round(_clamp_unit(mce_val), 6),
        brier_score=round(brier, 6),
        bins=tuple(bins),
    )


def _fit_isotonic(samples: list[CalibrationSample]) -> IsotonicParameters:
    ordered = sorted(samples, key=lambda s: s.raw_score)
    grouped: list[tuple[float, float, int]] = []
    for sample in ordered:
        y = 1.0 if sample.is_correct else 0.0
        if grouped and grouped[-1][0] == sample.raw_score:
            prev_x, prev_sum, prev_cnt = grouped[-1]
            grouped[-1] = (prev_x, prev_sum + y, prev_cnt + 1)
        else:
            grouped.append((sample.raw_score, y, 1))

    # Pool Adjacent Violators Algorithm (PAVA): blocks are [x_min, x_max, sum_y, weight]
    blocks: list[list[float]] = []
    for x_val, sum_y, cnt in grouped:
        blocks.append([x_val, x_val, sum_y, float(cnt)])
        while len(blocks) >= 2:
            prev_mean = blocks[-2][2] / blocks[-2][3]
            curr_mean = blocks[-1][2] / blocks[-1][3]
            if prev_mean > curr_mean:
                merged_sum = blocks[-2][2] + blocks[-1][2]
                merged_weight = blocks[-2][3] + blocks[-1][3]
                blocks[-2][1] = blocks[-1][1]
                blocks[-2][2] = merged_sum
                blocks[-2][3] = merged_weight
                blocks.pop()
            else:
                break

    x_knots: list[float] = []
    y_knots: list[float] = []
    prev_y = 0.0
    for x_min, x_max, sum_y, weight in blocks:
        y_val = _clamp_unit(sum_y / weight)
        if y_val < prev_y:
            y_val = prev_y
        prev_y = y_val
        x_knots.append(x_min)
        y_knots.append(y_val)
        if x_max > x_min:
            x_knots.append(x_max)
            y_knots.append(y_val)

    return IsotonicParameters(x_knots=tuple(x_knots), y_knots=tuple(y_knots))


def _fit_platt(samples: list[CalibrationSample]) -> PlattParameters:
    eps = _DEFAULT_PLATT_EPSILON
    n = float(len(samples))
    zs = [_safe_logit(s.raw_score, eps) for s in samples]
    ys = [1.0 if s.is_correct else 0.0 for s in samples]

    def _loss(a: float, b: float) -> float:
        nll = 0.0
        for z, y in zip(zs, ys, strict=True):
            u = a * z + b
            nll += _log1p_exp(u) - y * u
        return (nll / n) + 0.5 * _PLATT_L2_REG * ((a - 1.0) ** 2 + b * b)

    a = 1.0
    b = 0.0
    for _ in range(100):
        ga = 0.0
        gb = 0.0
        haa = 0.0
        hab = 0.0
        hbb = 0.0
        for z, y in zip(zs, ys, strict=True):
            q = _safe_sigmoid(a * z + b)
            err = q - y
            w = max(q * (1.0 - q), 1e-12)
            ga += err * z
            gb += err
            haa += w * z * z
            hab += w * z
            hbb += w
        ga = (ga / n) + _PLATT_L2_REG * (a - 1.0)
        gb = (gb / n) + _PLATT_L2_REG * b
        haa = (haa / n) + _PLATT_L2_REG
        hab = hab / n
        hbb = (hbb / n) + _PLATT_L2_REG

        if max(abs(ga), abs(gb)) < 1e-9:
            break

        det = haa * hbb - hab * hab
        if det <= 1e-15:
            da = -ga
            db = -gb
        else:
            da = -(hbb * ga - hab * gb) / det
            db = -(-hab * ga + haa * gb) / det

        cur_loss = _loss(a, b)
        step = 1.0
        improved = False
        for _ in range(30):
            cand_a = a + step * da
            cand_b = b + step * db
            if _loss(cand_a, cand_b) < cur_loss:
                a, b = cand_a, cand_b
                improved = True
                break
            step *= 0.5
        if not improved:
            break

    # Project slope >= 0.0 to mathematically preserve monotonicity over [0.0, 1.0]
    if a < 0.0:
        a = 0.0
        pos_rate = sum(ys) / n
        b = _safe_logit(pos_rate, eps)
        for _ in range(50):
            gb = 0.0
            hbb = 0.0
            for y in ys:
                q = _safe_sigmoid(b)
                gb += q - y
                hbb += max(q * (1.0 - q), 1e-12)
            gb = (gb / n) + _PLATT_L2_REG * b
            hbb = (hbb / n) + _PLATT_L2_REG
            if abs(gb) < 1e-9:
                break
            b -= gb / hbb

    return PlattParameters(slope=a, intercept=b, epsilon=eps)


def _fit_histogram(
    samples: list[CalibrationSample],
    *,
    num_bins: int,
    binning_strategy: BinningStrategy,
) -> HistogramParameters:
    if (
        isinstance(num_bins, bool)
        or not isinstance(num_bins, int)
        or not (2 <= num_bins <= _MAX_HISTOGRAM_BINS)
    ):
        raise ConfigurationError(
            f"num_bins for histogram calibration must be an integer in [2, {_MAX_HISTOGRAM_BINS}]."
        )

    if binning_strategy == BinningStrategy.UNIFORM:
        edges = [0.0] + [i / num_bins for i in range(1, num_bins)] + [1.0]
    else:
        sorted_scores = sorted(s.raw_score for s in samples)
        n = len(sorted_scores)
        raw_edges: list[float] = [0.0]
        for k in range(1, num_bins):
            pos = (k * (n - 1)) / num_bins
            lo = math.floor(pos)
            hi = min(math.ceil(pos), n - 1)
            frac = pos - lo
            q_val = sorted_scores[lo] + frac * (sorted_scores[hi] - sorted_scores[lo])
            if 0.0 < q_val < 1.0:
                raw_edges.append(q_val)
        raw_edges.append(1.0)
        edges = [0.0]
        for edge in raw_edges[1:]:
            if edge > edges[-1]:
                edges.append(edge)
        if edges[-1] != 1.0:
            edges[-1] = 1.0

    b_count = len(edges) - 1
    counts = [0] * b_count
    sum_y = [0.0] * b_count
    for sample in samples:
        idx = bisect_right(edges, sample.raw_score) - 1
        if idx < 0:
            idx = 0
        elif idx >= b_count:
            idx = b_count - 1
        counts[idx] += 1
        sum_y[idx] += 1.0 if sample.is_correct else 0.0

    populated: list[float | None] = [
        _clamp_unit(sum_y[i] / counts[i]) if counts[i] > 0 else None for i in range(b_count)
    ]

    # Deterministic nearest-populated-neighbor fallback for empty bins
    global_pos_rate = _clamp_unit(sum(1.0 for s in samples if s.is_correct) / len(samples))
    bin_values: list[float] = []
    for i in range(b_count):
        val = populated[i]
        if val is not None:
            bin_values.append(val)
            continue
        left_idx: int | None = None
        for j in range(i - 1, -1, -1):
            if populated[j] is not None:
                left_idx = j
                break
        right_idx: int | None = None
        for j in range(i + 1, b_count):
            if populated[j] is not None:
                right_idx = j
                break
        left_val = populated[left_idx] if left_idx is not None else None
        right_val = populated[right_idx] if right_idx is not None else None
        if (
            left_idx is not None
            and right_idx is not None
            and left_val is not None
            and right_val is not None
        ):
            frac = (i - left_idx) / (right_idx - left_idx)
            bin_values.append(_clamp_unit(left_val + frac * (right_val - left_val)))
        elif left_val is not None:
            bin_values.append(left_val)
        elif right_val is not None:
            bin_values.append(right_val)
        else:
            bin_values.append(global_pos_rate)

    return HistogramParameters(
        binning_strategy=binning_strategy,
        bin_edges=tuple(edges),
        bin_values=tuple(bin_values),
    )


def fit_calibrator(
    samples: Sequence[CalibrationSample | tuple[float, bool]],
    *,
    method: CalibrationMethod | str = CalibrationMethod.ISOTONIC,
    num_bins: int = 10,
    binning_strategy: BinningStrategy | str = BinningStrategy.UNIFORM,
    min_samples: int = DEFAULT_CALIBRATION_MIN_SAMPLES,
    metric_bins: int = 10,
    schema_id: str | None = None,
    strategy_name: str | None = None,
    description: str | None = None,
) -> CalibrationModel:
    """Deterministically fit an immutable, verified CalibrationModel from labeled samples."""
    try:
        resolved_method = (
            method if isinstance(method, CalibrationMethod) else CalibrationMethod(method)
        )
    except ValueError as exc:
        raise ConfigurationError(f"Unsupported calibration method: {method!r}.") from exc

    try:
        resolved_binning = (
            binning_strategy
            if isinstance(binning_strategy, BinningStrategy)
            else BinningStrategy(binning_strategy)
        )
    except ValueError as exc:
        raise ConfigurationError(f"Unsupported binning_strategy: {binning_strategy!r}.") from exc

    if (
        isinstance(min_samples, bool)
        or not isinstance(min_samples, int)
        or min_samples < MIN_CALIBRATION_SAMPLES_FLOOR
    ):
        raise ConfigurationError(
            f"min_samples must be an integer >= {MIN_CALIBRATION_SAMPLES_FLOOR}."
        )

    coerced = _coerce_samples(samples)
    if len(coerced) < min_samples:
        raise ConfigurationError(
            f"Calibration requires at least {min_samples} labeled samples; got {len(coerced)}."
        )

    pos_count = sum(1 for s in coerced if s.is_correct)
    neg_count = len(coerced) - pos_count
    if pos_count < 1 or neg_count < 1:
        raise ConfigurationError(
            "Calibration requires both positive (True) and negative (False) labeled outcomes; "
            f"got positive={pos_count}, negative={neg_count}."
        )

    raw_metrics = compute_calibration_metrics(coerced, num_bins=metric_bins)

    isotonic_params: IsotonicParameters | None = None
    platt_params: PlattParameters | None = None
    histogram_params: HistogramParameters | None = None

    if resolved_method == CalibrationMethod.ISOTONIC:
        isotonic_params = _fit_isotonic(coerced)
        calibrated_samples = [
            CalibrationSample(
                raw_score=_eval_isotonic(isotonic_params, s.raw_score),
                is_correct=s.is_correct,
            )
            for s in coerced
        ]
    elif resolved_method == CalibrationMethod.PLATT:
        platt_params = _fit_platt(coerced)
        calibrated_samples = [
            CalibrationSample(
                raw_score=_eval_platt(platt_params, s.raw_score),
                is_correct=s.is_correct,
            )
            for s in coerced
        ]
    else:
        histogram_params = _fit_histogram(
            coerced,
            num_bins=num_bins,
            binning_strategy=resolved_binning,
        )
        calibrated_samples = [
            CalibrationSample(
                raw_score=_eval_histogram(histogram_params, s.raw_score),
                is_correct=s.is_correct,
            )
            for s in coerced
        ]

    after_metrics = compute_calibration_metrics(calibrated_samples, num_bins=metric_bins)

    try:
        metadata = CalibrationMetadata(
            artifact_version=CALIBRATION_ARTIFACT_VERSION,
            method=resolved_method,
            scale="0-1",
            sample_count=len(coerced),
            positive_count=pos_count,
            negative_count=neg_count,
            fitted_at=datetime.now(UTC).isoformat(),
            ece_before=raw_metrics.ece,
            ece_after=after_metrics.ece,
            mce_before=raw_metrics.mce,
            mce_after=after_metrics.mce,
            brier_before=raw_metrics.brier_score,
            brier_after=after_metrics.brier_score,
            schema_id=schema_id,
            strategy_name=strategy_name,
            description=description,
        )
        return CalibrationModel(
            artifact_version=CALIBRATION_ARTIFACT_VERSION,
            metadata=metadata,
            isotonic=isotonic_params,
            platt=platt_params,
            histogram=histogram_params,
        )
    except ValidationError as exc:
        raise ConfigurationError("Failed to construct validated CalibrationModel.") from exc


def validate_calibrator(calibrator: Any) -> ConfidenceCalibrator | None:
    """Validate an optional calibrator argument eagerly, raising ConfigurationError if invalid."""
    if calibrator is None:
        return None
    if not isinstance(calibrator, ConfidenceCalibrator):
        raise ConfigurationError(
            "calibrator must implement ConfidenceCalibrator (scale, method, calibrate) or be None."
        )
    scale = calibrator.scale
    method = calibrator.method
    if not isinstance(scale, str) or not scale.strip():
        raise ConfigurationError("ConfidenceCalibrator.scale must be a non-empty string.")
    if not isinstance(method, str) or not method.strip():
        raise ConfigurationError("ConfidenceCalibrator.method must be a non-empty string.")
    return calibrator
