"""JSON report persistence and privacy-safe Markdown report/diff renderers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from evals.models import ComparisonReport, DifferentialReport, EvaluationReport
from structa_maestro.config import sanitize_message


def save_report(
    report: EvaluationReport,
    output_path: str | Path,
    *,
    secrets_to_scrub: Iterable[str] = (),
) -> Path:
    """Serialize EvaluationReport v1.0 cleanly to UTF-8 JSON with credential scrubbing."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_json = json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
    secret_list = tuple(s for s in secrets_to_scrub if s)
    if secret_list:
        for secret in secret_list:
            raw_json = raw_json.replace(secret, "[REDACTED]")
    target.write_text(raw_json + "\n", encoding="utf-8")
    return target


def load_report(path: str | Path) -> EvaluationReport:
    """Load and validate a persisted EvaluationReport v1.0 JSON file."""
    src = Path(path)
    if not src.exists():
        raise ValueError(f"Evaluation report file not found: {src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    return EvaluationReport.model_validate(data)


def render_report_markdown(report: EvaluationReport) -> str:
    """Render a concise, human-readable Markdown summary for PR reviews and release notes."""
    s = report.summary
    filter_note = "Full corpus"
    if report.filters.is_filtered:
        parts: list[str] = []
        if report.filters.categories:
            parts.append("categories=" + ",".join(c.value for c in report.filters.categories))
        if report.filters.formats:
            parts.append("formats=" + ",".join(f.value for f in report.filters.formats))
        if report.filters.profiles:
            parts.append("profiles=" + ",".join(p.value for p in report.filters.profiles))
        if report.filters.case_ids:
            parts.append("case_ids=" + ",".join(report.filters.case_ids))
        filter_note = "Filtered (" + "; ".join(parts) + ")"

    lines = [
        f"# Golden Evaluation Report (v{report.report_version})",
        "",
        f"- **Dataset Version:** `{report.dataset_version}` ({filter_note})",
        f"- **Strategy:** `{sanitize_message(report.strategy_name)}` "
        f"(config: `{sanitize_message(report.config_version)}`)",
        f"- **Executed At:** `{report.executed_at}` | **Trials:** `{report.trials}`",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total Cases | {s.total_cases} |",
        f"| Field Exact-Match Accuracy | {s.field_exact_match_accuracy:.4f} |",
        f"| Field Precision | {s.field_precision:.4f} |",
        f"| Field Recall | {s.field_recall:.4f} |",
        f"| Field F1 | {s.field_f1:.4f} |",
        f"| Record Completeness | {s.record_completeness:.4f} |",
        f"| Record-Level Accuracy | {s.record_level_accuracy:.4f} |",
        f"| Hallucination Rate | {s.hallucination_rate:.4f} "
        f"({s.hallucination_count}/{s.expected_missing_fields}) |",
        f"| Provenance Accuracy | {s.provenance_accuracy:.4f} |",
        f"| Validation Pass Rate | {s.validation_pass_rate:.4f} |",
        f"| Processing Failure Rate | {s.processing_failure_rate:.4f} |",
        f"| Mean / Max Latency (ms) | {s.mean_latency_ms:.2f} / {s.max_latency_ms:.2f} |",
    ]

    if report.trials > 1 and report.trial_distribution:
        lines.extend(
            [
                "",
                "## Repeated Trial Distribution",
                "",
                "| Metric | Mean | Min | Max |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for mname, stat in report.trial_distribution.items():
            lines.append(f"| `{mname}` | {stat.mean:.4f} | {stat.min:.4f} | {stat.max:.4f} |")

    if report.by_category:
        lines.extend(
            [
                "",
                "## Breakdown by Category",
                "",
                "| Category | Cases | Exact Match | F1 | Provenance | Hallucination | Validation |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for cat, ms in report.by_category.items():
            lines.append(
                f"| `{cat}` | {ms.total_cases} | {ms.field_exact_match_accuracy:.4f} | "
                f"{ms.field_f1:.4f} | {ms.provenance_accuracy:.4f} | "
                f"{ms.hallucination_rate:.4f} | {ms.validation_pass_rate:.4f} |"
            )

    failed_cases = [c for c in report.cases if not c.passed]
    if failed_cases:
        lines.extend(["", "## Failed Cases", ""])
        for fc in failed_cases[:25]:
            err_note = f" (runtime_error={fc.runtime_error})" if fc.runtime_error else ""
            lines.append(
                f"- `{fc.case_id}` (trial {fc.trial_index}): status={fc.actual_status.value}, "
                f"validation={fc.actual_validation.value}{err_note}"
            )

    return "\n".join(lines) + "\n"


def render_comparison_markdown(comparison: ComparisonReport) -> str:
    """Render a Markdown summary of a baseline regression comparison."""
    verdict = "PASS" if comparison.passed else "FAIL"
    lines = [
        f"## Baseline Regression Gate: {verdict}",
        "",
        f"- **Baseline:** `{comparison.baseline_strategy}` "
        f"(dataset `{comparison.baseline_dataset_version}`)",
        f"- **Candidate:** `{comparison.candidate_strategy}` "
        f"(dataset `{comparison.candidate_dataset_version}`)",
        f"- **Dataset Version Match:** `{comparison.dataset_version_match}`",
        "",
        "| Metric | Delta (Candidate - Baseline) |",
        "| --- | ---: |",
    ]
    for metric, delta in comparison.metric_deltas.items():
        sign = "+" if delta > 0 else ""
        lines.append(f"| `{metric}` | {sign}{delta:.4f} |")

    if comparison.failure_reasons:
        lines.extend(["", "### Failure Reasons", ""])
        for reason in comparison.failure_reasons:
            lines.append(f"- {reason}")

    if comparison.regressed_cases:
        lines.extend(["", "### Regressed Cases", ""])
        for item in comparison.regressed_cases:
            lines.append(f"- `{item.case_id}` (`{item.category}`): {'; '.join(item.details)}")

    if comparison.improved_cases:
        lines.extend(["", "### Improved Cases", ""])
        for item in comparison.improved_cases:
            lines.append(f"- `{item.case_id}` (`{item.category}`): {'; '.join(item.details)}")

    return "\n".join(lines) + "\n"


def render_differential_markdown(diff: DifferentialReport) -> str:
    """Render a side-by-side Markdown comparison between two strategy reports."""
    lines = [
        "## Differential Strategy Comparison",
        "",
        f"- **Strategy A:** `{diff.strategy_a}` (dataset `{diff.dataset_version_a}`)",
        f"- **Strategy B:** `{diff.strategy_b}` (dataset `{diff.dataset_version_b}`)",
        f"- **Dataset Version Match:** `{diff.dataset_version_match}`",
        "",
        "| Metric | Delta (B - A) |",
        "| --- | ---: |",
    ]
    for metric, delta in diff.metric_deltas.items():
        sign = "+" if delta > 0 else ""
        lines.append(f"| `{metric}` | {sign}{delta:.4f} |")

    if diff.warnings:
        lines.extend(["", "### Warnings", ""])
        for warning in diff.warnings:
            lines.append(f"- {warning}")

    if diff.divergent_cases:
        lines.extend(["", f"### Divergent Cases ({len(diff.divergent_cases)})", ""])
        for item in diff.divergent_cases:
            lines.append(f"- `{item.case_id}` (`{item.category}`): {'; '.join(item.details)}")

    return "\n".join(lines) + "\n"
