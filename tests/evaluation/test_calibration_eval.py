"""Tests for calibration evaluation metrics, K-fold CV, and `python -m evals calibrate` CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from evals.calibration import (
    extract_samples_from_eval_results,
    fit_and_evaluate_calibrator,
    load_calibration_samples,
    render_calibration_report_markdown,
)
from evals.cli import run_eval_cli
from evals.dataset import DEFAULT_MANIFEST_PATH, load_dataset
from evals.models import GoldenCase
from evals.reference_strategy import ReferenceDeterministicStrategy
from evals.runner import EvaluationRunner
from structa_maestro import (
    CalibrationMethod,
    CalibrationModel,
    CalibrationSample,
    ConfigurationError,
    DocumentInput,
    ExtractionResult,
    ExtractionSchema,
    FieldPresence,
    PreparedDocument,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    compute_calibration_metrics,
    extract,
)


class _MiscalibratedEvalStrategy(ReferenceDeterministicStrategy):
    """Test strategy wrapping ReferenceDeterministicStrategy with overconfident errors."""

    @property
    def name(self) -> str:
        return "miscalibrated-test-strategy"

    def extract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        base = super().extract_raw(document, schema)
        if not base.records:
            return base
        # Introduce deterministic wrong value on every 3rd case's first present field
        case_num = sum(ord(ch) for ch in document.source.document_id)
        new_records: list[ProposedRecord] = []
        for rec in base.records:
            updated_fields: dict[str, ProposedField] = {}
            mutated = False
            for fname, pf in rec.fields.items():
                if not mutated and pf.presence == FieldPresence.PRESENT and case_num % 3 == 0:
                    updated_fields[fname] = ProposedField(
                        presence=pf.presence,
                        value="WRONG_VALUE_FOR_CALIBRATION",
                        confidence=0.92,
                        evidence=pf.evidence,
                        ambiguous=pf.ambiguous,
                    )
                    mutated = True
                else:
                    updated_fields[fname] = ProposedField(
                        presence=pf.presence,
                        value=pf.value,
                        confidence=0.95 if pf.presence == FieldPresence.PRESENT else None,
                        evidence=pf.evidence,
                        ambiguous=pf.ambiguous,
                    )
            new_records.append(
                ProposedRecord(
                    fields=updated_fields,
                    partial=rec.partial,
                    failed=rec.failed,
                    errors=rec.errors,
                    warnings=rec.warnings,
                )
            )
        return StrategyOutput(
            records=tuple(new_records),
            partial=base.partial,
            failed=base.failed,
            errors=base.errors,
            warnings=base.warnings,
            default_confidence_method="miscalibrated-demo",
        )


def test_compute_calibration_metrics_empty_perfect_and_analytical() -> None:
    empty = compute_calibration_metrics([], num_bins=5)
    assert empty.sample_count == 0
    assert empty.ece == 0.0
    assert empty.mce == 0.0
    assert empty.brier_score == 0.0
    assert len(empty.bins) == 5
    assert all(b.count == 0 and b.mean_confidence is None for b in empty.bins)

    # Analytical check: 2 samples at 0.8 (1 right, 1 wrong -> acc=0.5, gap=0.3)
    # and 2 samples at 0.2 (0 right, 2 wrong -> acc=0.0, gap=0.2)
    # ECE = 0.5*0.3 + 0.5*0.2 = 0.25; MCE = 0.3; Brier = (0.04 + 0.64 + 0.04 + 0.04)/4 = 0.19
    analytical = compute_calibration_metrics(
        [(0.8, True), (0.8, False), (0.2, False), (0.2, False)],
        num_bins=10,
    )
    assert analytical.sample_count == 4
    assert analytical.ece == pytest.approx(0.25, abs=1e-6)
    assert analytical.mce == pytest.approx(0.3, abs=1e-6)
    assert analytical.brier_score == pytest.approx(0.19, abs=1e-6)


def test_load_calibration_samples_json_array_object_and_jsonl(tmp_path: Path) -> None:
    raw = [
        {"raw_score": 0.9, "is_correct": True},
        {"raw_score": 0.4, "is_correct": False},
        {"raw_score": 0.7, "is_correct": True},
        {"raw_score": 0.2, "is_correct": False},
    ]

    p_arr = tmp_path / "arr.json"
    p_arr.write_text(json.dumps(raw), encoding="utf-8")
    assert len(load_calibration_samples(p_arr)) == 4

    p_obj = tmp_path / "obj.json"
    p_obj.write_text(json.dumps({"samples": raw}), encoding="utf-8")
    assert len(load_calibration_samples(p_obj)) == 4

    p_jsonl = tmp_path / "samples.jsonl"
    p_jsonl.write_text("\n".join(json.dumps(x) for x in raw) + "\n\n", encoding="utf-8")
    assert len(load_calibration_samples(p_jsonl)) == 4

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"raw_score": 0.5}\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_calibration_samples(bad)


def test_fit_and_evaluate_calibrator_cv_capping_and_skipping() -> None:
    # Minority count = 1 -> CV skipped cleanly
    one_neg = [CalibrationSample(raw_score=0.9, is_correct=True) for _ in range(19)] + [
        CalibrationSample(raw_score=0.2, is_correct=False)
    ]
    _, rep_skip = fit_and_evaluate_calibrator(
        one_neg,
        method=CalibrationMethod.ISOTONIC,
        cv_folds=5,
    )
    assert rep_skip.cv_folds_executed == 0
    assert rep_skip.cv_metrics is None
    assert any("skipped" in n.lower() for n in rep_skip.notes)

    # Minority count = 3, requested folds = 5 -> capped cleanly to 3
    three_neg = [CalibrationSample(raw_score=0.85, is_correct=True) for _ in range(21)] + [
        CalibrationSample(raw_score=0.25, is_correct=False) for _ in range(3)
    ]
    model, rep_cap = fit_and_evaluate_calibrator(
        three_neg, method=CalibrationMethod.PLATT, cv_folds=5
    )
    assert rep_cap.cv_folds_executed == 3
    assert rep_cap.cv_metrics is not None
    assert any("capped" in n.lower() for n in rep_cap.notes)
    md = render_calibration_report_markdown(model, rep_cap)
    assert "3-Fold CV" in md


def test_evals_calibrate_cli_from_samples_and_strategy_override(tmp_path: Path) -> None:
    # 1. From explicit --samples JSONL file
    samples_path = tmp_path / "samples.jsonl"
    rows = [
        {"raw_score": round(0.2 + 0.75 * (i / 39), 4), "is_correct": (i % 3 != 0)}
        for i in range(40)
    ]
    samples_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out_artifact = tmp_path / "cal_from_samples.json"
    out_md = tmp_path / "cal_report.md"

    out, err = io.StringIO(), io.StringIO()
    rc = run_eval_cli(
        [
            "calibrate",
            "--samples",
            str(samples_path),
            "--method",
            "platt",
            "--folds",
            "5",
            "--output",
            str(out_artifact),
            "--report-md",
            str(out_md),
        ],
        stdout=out,
        stderr=err,
    )
    assert rc == 0, err.getvalue()
    assert out_artifact.exists() and out_md.exists()
    assert "5-Fold CV" in out.getvalue()
    reloaded = CalibrationModel.from_file(out_artifact)
    assert reloaded.method == "platt"

    # Verify exported artifact works directly in library extract()
    schema = ExtractionSchema.from_dict(
        {"schema_id": "s", "schema_version": "1.0", "fields": {"k": {"type": "string"}}}
    )
    res = extract(
        DocumentInput.from_text("k: val", filename="doc.txt"),
        schema,
        strategy=ReferenceDeterministicStrategy(),
        calibrator=reloaded,
    )
    assert res.status is not None

    # 2. From evaluation runner with miscalibrated strategy override
    out_strat_art = tmp_path / "cal_from_eval.json"
    o2, e2 = io.StringIO(), io.StringIO()
    rc2 = run_eval_cli(
        [
            "calibrate",
            "--method",
            "isotonic",
            "--output",
            str(out_strat_art),
        ],
        stdout=o2,
        stderr=e2,
        strategy_override=_MiscalibratedEvalStrategy(),
    )
    assert rc2 == 0, e2.getvalue()
    strat_model = CalibrationModel.from_file(out_strat_art)
    assert strat_model.metadata.strategy_name == "miscalibrated-test-strategy"
    assert strat_model.metadata.negative_count >= 1
    assert strat_model.metadata.positive_count >= 1

    # 3. Single-class deterministic strategy (100% accurate -> 0 negatives) fails cleanly (code 1)
    bad_out = tmp_path / "should_not_exist.json"
    o3, e3 = io.StringIO(), io.StringIO()
    rc3 = run_eval_cli(
        ["calibrate", "--strategy", "deterministic", "--output", str(bad_out)],
        stdout=o3,
        stderr=e3,
    )
    assert rc3 == 1
    assert not bad_out.exists()
    assert "both positive" in e3.getvalue()


def test_extract_samples_from_eval_results_direct() -> None:
    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=False)
    collected: list[tuple[GoldenCase, ExtractionResult]] = []
    EvaluationRunner(manifest).run(
        strategy=_MiscalibratedEvalStrategy(),
        trials=1,
        collect_results=collected,
    )
    extracted = extract_samples_from_eval_results(collected)
    assert len(extracted) >= 50
    assert any(s.is_correct for s in extracted)
    assert any(not s.is_correct for s in extracted)
