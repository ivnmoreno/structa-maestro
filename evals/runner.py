"""Golden evaluation runner orchestrating materialization, repeated trials, and fault isolation."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from evals.dataset import filter_cases
from evals.materializer import materialize_document
from evals.models import (
    CaseOutcome,
    EvaluationFilter,
    EvaluationReport,
    GoldenCase,
    GoldenDatasetManifest,
    MetricSummary,
)
from evals.scoring import (
    compute_breakdowns,
    compute_metric_summary,
    compute_trial_distribution,
    score_case,
)
from structa_maestro.config import DocumentLimits, sanitize_message
from structa_maestro.contracts.models import ExtractionResult
from structa_maestro.engine import ExtractionEngine
from structa_maestro.strategies.base import ExtractionStrategy


class EvaluationRunner:
    """Executes golden cases across N trials with isolated materialization and fault capture."""

    def __init__(
        self,
        manifest: GoldenDatasetManifest,
        *,
        limits: DocumentLimits | None = None,
    ) -> None:
        self._manifest = manifest
        self._limits = limits or DocumentLimits()

    def run(
        self,
        *,
        strategy: ExtractionStrategy,
        config_version: str = "default",
        trials: int = 1,
        filters: EvaluationFilter | None = None,
        secrets_to_scrub: Iterable[str] = (),
        collect_results: list[tuple[GoldenCase, ExtractionResult]] | None = None,
    ) -> EvaluationReport:
        """Execute selected golden cases for N >= 1 trials and return an EvaluationReport v1.0."""
        if isinstance(trials, bool) or trials < 1:
            raise ValueError("trials must be an integer >= 1.")

        active_filter = filters or EvaluationFilter()
        selected_cases = filter_cases(self._manifest, active_filter)
        engine = ExtractionEngine(strategy=strategy, limits=self._limits)
        scrub_tuple = tuple(secrets_to_scrub)

        all_outcomes: list[CaseOutcome] = []
        trial_summaries: list[MetricSummary] = []

        with tempfile.TemporaryDirectory(prefix="structa-evals-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            materialized_paths: dict[str, Path] = {}
            for case in selected_cases:
                case_dir = tmp_root / case.case_id
                materialized_paths[case.case_id] = materialize_document(case.document, case_dir)

            for trial_idx in range(1, trials + 1):
                trial_outcomes: list[CaseOutcome] = []
                for case in selected_cases:
                    doc_path = materialized_paths[case.case_id]
                    start = time.perf_counter()
                    result: ExtractionResult | None = None
                    runtime_err: str | None = None
                    try:
                        result = engine.extract(
                            doc_path,
                            case.schema_definition,
                            document_id=case.case_id,
                        )
                        if collect_results is not None and trial_idx == 1:
                            collect_results.append((case, result))
                    except Exception as exc:
                        runtime_err = sanitize_message(
                            f"{type(exc).__name__}: {exc}",
                            secrets=scrub_tuple,
                            max_length=240,
                        )
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    outcome = score_case(
                        case=case,
                        result=result,
                        trial_index=trial_idx,
                        latency_ms=elapsed_ms,
                        runtime_error=runtime_err,
                    )
                    trial_outcomes.append(outcome)

                trial_summaries.append(compute_metric_summary(trial_outcomes))
                all_outcomes.extend(trial_outcomes)

        overall_summary = compute_metric_summary(all_outcomes)
        by_category, by_profile = compute_breakdowns(all_outcomes)
        distribution = compute_trial_distribution(trial_summaries)

        return EvaluationReport(
            dataset_version=self._manifest.dataset_version,
            strategy_name=strategy.name,
            config_version=config_version,
            executed_at=datetime.now(UTC).isoformat(),
            trials=trials,
            filters=active_filter,
            summary=overall_summary,
            trial_summaries=trial_summaries,
            trial_distribution=distribution,
            by_category=by_category,
            by_profile=by_profile,
            cases=all_outcomes,
        )
