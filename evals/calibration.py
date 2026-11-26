"""Repository-internal calibration sample extraction, K-fold CV, and reporting (`evals`)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evals.models import GoldenCase
from evals.scoring import _align_records, _compare_values
from structa_maestro.confidence.calibration import (
    DEFAULT_CALIBRATION_MIN_SAMPLES,
    MAX_CALIBRATION_ARTIFACT_BYTES,
    MIN_CALIBRATION_SAMPLES_FLOOR,
    BinningStrategy,
    CalibrationMethod,
    CalibrationMetrics,
    CalibrationModel,
    CalibrationSample,
    compute_calibration_metrics,
    fit_calibrator,
)
from structa_maestro.contracts.enums import FieldPresence
from structa_maestro.contracts.models import ExtractionResult
from structa_maestro.exceptions import ConfigurationError


class CalibrationFitReport(BaseModel):
    """Deterministic calibration fitting and cross-validation summary report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: CalibrationMethod
    sample_count: int = Field(ge=MIN_CALIBRATION_SAMPLES_FLOOR)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    cv_folds_requested: int = Field(ge=2)
    cv_folds_executed: int = Field(ge=0)
    raw_metrics: CalibrationMetrics
    in_sample_metrics: CalibrationMetrics
    cv_metrics: CalibrationMetrics | None = None
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_counts_and_cv(self) -> CalibrationFitReport:
        if self.positive_count + self.negative_count != self.sample_count:
            raise ValueError("positive_count + negative_count must equal sample_count.")
        if self.cv_folds_executed == 0:
            if self.cv_metrics is not None:
                raise ValueError("cv_metrics must be None when cv_folds_executed == 0.")
        else:
            if not (2 <= self.cv_folds_executed <= self.cv_folds_requested):
                raise ValueError("cv_folds_executed must be 0 or in [2, cv_folds_requested].")
            if self.cv_metrics is None:
                raise ValueError("cv_metrics must be populated when cross-validation runs.")
        return self


def _parse_sample_mapping(obj: Any, *, idx: int) -> CalibrationSample:
    if not isinstance(obj, Mapping):
        raise ConfigurationError(f"Sample entry at index {idx} must be a JSON object.")
    if "raw_score" not in obj or "is_correct" not in obj:
        raise ConfigurationError(
            f"Sample entry at index {idx} must contain 'raw_score' and 'is_correct'."
        )
    try:
        return CalibrationSample(raw_score=obj["raw_score"], is_correct=obj["is_correct"])
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid sample entry at index {idx}.") from exc


def load_calibration_samples(path: str | Path) -> list[CalibrationSample]:
    """Load labeled CalibrationSamples from JSON array, ``{"samples": [...]}``, or JSONL."""
    if isinstance(path, str) and not path.strip():
        raise ConfigurationError("Samples file path must be non-empty.")
    target = Path(path)
    try:
        if not target.exists() or not target.is_file():
            raise ConfigurationError(f"Samples file not found: '{target.name}'.")
        if target.stat().st_size > MAX_CALIBRATION_ARTIFACT_BYTES:
            raise ConfigurationError(
                f"Samples file exceeds maximum size limit ({MAX_CALIBRATION_ARTIFACT_BYTES} bytes)."
            )
        raw_text = target.read_text(encoding="utf-8")
    except ConfigurationError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Unable to read samples file ({type(exc).__name__}).") from exc

    stripped = raw_text.strip()
    if not stripped:
        raise ConfigurationError("Samples file is empty.")

    # Attempt standard JSON document first (list or {"samples": [...]})
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None

    if parsed is not None:
        if isinstance(parsed, list):
            return [_parse_sample_mapping(entry, idx=i) for i, entry in enumerate(parsed)]
        if isinstance(parsed, Mapping):
            if "samples" not in parsed or not isinstance(parsed["samples"], list):
                raise ConfigurationError("JSON object samples file must contain a 'samples' array.")
            return [
                _parse_sample_mapping(entry, idx=i) for i, entry in enumerate(parsed["samples"])
            ]
        raise ConfigurationError("Samples JSON root must be a list or an object with 'samples'.")

    # Fall back to JSON Lines (one JSON object per non-blank line)
    samples: list[CalibrationSample] = []
    for line_idx, line in enumerate(raw_text.splitlines()):
        clean = line.strip()
        if not clean:
            continue
        try:
            obj = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                f"Malformed JSONL line {line_idx + 1} in samples file."
            ) from exc
        samples.append(_parse_sample_mapping(obj, idx=line_idx))
    return samples


def extract_samples_from_eval_results(
    collected: Sequence[tuple[GoldenCase, ExtractionResult]],
) -> list[CalibrationSample]:
    """Extract labeled (raw_confidence, is_correct) pairs from evaluated golden cases."""
    samples: list[CalibrationSample] = []
    for case, result in collected:
        aligned = _align_records(
            result.records,
            case.expected_records,
            case.record_order,
            case.match_key_field,
        )
        for exp_rec, act_rec in aligned:
            if act_rec is None:
                continue
            for fname, exp_field in exp_rec.fields.items():
                act_field = act_rec.fields.get(fname)
                if act_field is None or act_field.presence != FieldPresence.PRESENT:
                    continue
                conf = act_field.confidence
                if conf is None or conf.value is None or conf.scale != "0-1":
                    continue
                if not math.isfinite(conf.value) or not (0.0 <= conf.value <= 1.0):
                    continue
                if exp_field.presence == FieldPresence.MISSING:
                    is_correct = False  # Hallucinated field
                else:
                    is_correct = _compare_values(
                        act_field.value,
                        exp_field.value,
                        exp_field.match_mode,
                    )
                samples.append(CalibrationSample(raw_score=conf.value, is_correct=is_correct))
    return samples


def _stratified_fold_indices(
    samples: Sequence[CalibrationSample],
    folds: int,
) -> list[list[int]]:
    pos_indices = [idx for idx, s in enumerate(samples) if s.is_correct]
    neg_indices = [idx for idx, s in enumerate(samples) if not s.is_correct]
    buckets: list[list[int]] = [[] for _ in range(folds)]
    for i, idx in enumerate(pos_indices):
        buckets[i % folds].append(idx)
    for i, idx in enumerate(neg_indices):
        buckets[i % folds].append(idx)
    return buckets


def fit_and_evaluate_calibrator(
    samples: Sequence[CalibrationSample | tuple[float, bool]],
    *,
    method: CalibrationMethod | str = CalibrationMethod.ISOTONIC,
    num_bins: int = 10,
    binning_strategy: BinningStrategy | str = BinningStrategy.UNIFORM,
    min_samples: int = DEFAULT_CALIBRATION_MIN_SAMPLES,
    cv_folds: int = 5,
    metric_bins: int = 10,
    schema_id: str | None = None,
    strategy_name: str | None = None,
    description: str | None = None,
) -> tuple[CalibrationModel, CalibrationFitReport]:
    """Fit a CalibrationModel and compute deterministic stratified K-fold CV calibration metrics."""
    if isinstance(cv_folds, bool) or not isinstance(cv_folds, int) or cv_folds < 2:
        raise ConfigurationError("cv_folds must be an integer >= 2.")

    model = fit_calibrator(
        samples,
        method=method,
        num_bins=num_bins,
        binning_strategy=binning_strategy,
        min_samples=min_samples,
        metric_bins=metric_bins,
        schema_id=schema_id,
        strategy_name=strategy_name,
        description=description,
    )

    coerced = [
        s
        if isinstance(s, CalibrationSample)
        else CalibrationSample(raw_score=s[0], is_correct=s[1])
        for s in samples
    ]
    raw_metrics = compute_calibration_metrics(coerced, num_bins=metric_bins)
    in_sample_metrics = compute_calibration_metrics(
        [
            CalibrationSample(raw_score=model.calibrate(s.raw_score), is_correct=s.is_correct)
            for s in coerced
        ],
        num_bins=metric_bins,
    )

    pos_count = model.metadata.positive_count
    neg_count = model.metadata.negative_count
    minority = min(pos_count, neg_count)
    notes: list[str] = []
    cv_metrics: CalibrationMetrics | None = None
    folds_executed = 0

    if minority < 2:
        notes.append("Cross-validation skipped: minority class count is < 2.")
    else:
        effective_folds = min(cv_folds, minority)
        if effective_folds < cv_folds:
            notes.append(
                f"Cross-validation folds capped from {cv_folds} to {effective_folds} "
                f"due to minority class count ({minority})."
            )
        buckets = _stratified_fold_indices(coerced, effective_folds)
        min_train_len = min(len(coerced) - len(b) for b in buckets)
        if min_train_len < MIN_CALIBRATION_SAMPLES_FLOOR:
            notes.append(
                f"Cross-validation skipped: training fold size ({min_train_len}) is below "
                f"minimum floor ({MIN_CALIBRATION_SAMPLES_FLOOR})."
            )
        else:
            oof_predictions: list[CalibrationSample] = []
            for fold_idx in range(effective_folds):
                val_set = set(buckets[fold_idx])
                train_split = [coerced[i] for i in range(len(coerced)) if i not in val_set]
                val_split = [coerced[i] for i in buckets[fold_idx]]
                fold_model = fit_calibrator(
                    train_split,
                    method=model.metadata.method,
                    num_bins=num_bins,
                    binning_strategy=binning_strategy,
                    min_samples=MIN_CALIBRATION_SAMPLES_FLOOR,
                    metric_bins=metric_bins,
                )
                for item in val_split:
                    oof_predictions.append(
                        CalibrationSample(
                            raw_score=fold_model.calibrate(item.raw_score),
                            is_correct=item.is_correct,
                        )
                    )
            cv_metrics = compute_calibration_metrics(oof_predictions, num_bins=metric_bins)
            folds_executed = effective_folds

    report = CalibrationFitReport(
        method=model.metadata.method,
        sample_count=model.metadata.sample_count,
        positive_count=pos_count,
        negative_count=neg_count,
        cv_folds_requested=cv_folds,
        cv_folds_executed=folds_executed,
        raw_metrics=raw_metrics,
        in_sample_metrics=in_sample_metrics,
        cv_metrics=cv_metrics,
        notes=tuple(notes),
    )
    return model, report


def render_calibration_report_markdown(
    model: CalibrationModel,
    report: CalibrationFitReport,
) -> str:
    """Render a deterministic Markdown calibration evaluation summary."""
    lines = [
        f"# Confidence Calibration Report (`{model.artifact_version}`)",
        "",
        f"- **Method:** `{report.method.value}` (`scale={model.scale}`)",
        f"- **Samples:** `{report.sample_count}` "
        f"(positive=`{report.positive_count}`, negative=`{report.negative_count}`)",
        f"- **Cross-Validation Folds:** `{report.cv_folds_executed}` "
        f"(requested `{report.cv_folds_requested}`)",
        f"- **Fitted At:** `{model.metadata.fitted_at}`",
    ]
    if model.metadata.schema_id:
        lines.append(f"- **Schema Scope Tag:** `{model.metadata.schema_id}`")
    if model.metadata.strategy_name:
        lines.append(f"- **Strategy Scope Tag:** `{model.metadata.strategy_name}`")

    lines.extend(
        [
            "",
            "## Calibration Quality Metrics",
            "",
            "| Stage | ECE | MCE | Brier Score |",
            "| --- | ---: | ---: | ---: |",
            f"| Raw (Uncalibrated) | {report.raw_metrics.ece:.6f} | "
            f"{report.raw_metrics.mce:.6f} | {report.raw_metrics.brier_score:.6f} |",
            f"| Calibrated (In-Sample) | {report.in_sample_metrics.ece:.6f} | "
            f"{report.in_sample_metrics.mce:.6f} | {report.in_sample_metrics.brier_score:.6f} |",
        ]
    )
    if report.cv_metrics is not None:
        lines.append(
            f"| Calibrated ({report.cv_folds_executed}-Fold CV) | {report.cv_metrics.ece:.6f} | "
            f"{report.cv_metrics.mce:.6f} | {report.cv_metrics.brier_score:.6f} |"
        )
    if report.notes:
        lines.extend(["", "## Notes", ""])
        for note in report.notes:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)
