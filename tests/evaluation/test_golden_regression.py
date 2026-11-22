"""Golden dataset evaluation and regression suite across 5 categories and TXT/PDF/DOCX."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from conftest import create_minimal_docx, create_minimal_pdf
from evaluation.evaluator import evaluate_golden_results
from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionResult,
    ExtractionSchema,
    FieldPresence,
    FieldType,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract,
)

_FIELD_LABEL_MAP: dict[str, tuple[str, ...]] = {
    "invoice_number": ("Invoice Number",),
    "vendor_name": ("Vendor",),
    "issue_date": ("Issue Date",),
    "total_amount": ("Total Amount",),
    "currency": ("Currency",),
    "notes": ("Notes",),
    "contract_id": ("Contract ID",),
    "party_a": ("Party A",),
    "party_b": ("Party B",),
    "effective_date": ("Effective Date",),
    "termination_fee": ("Termination Fee",),
    "employee_name": ("Employee Name",),
    "position": ("Position",),
    "employer": ("Employer",),
    "start_date": ("Start Date",),
    "applicant_name": ("Applicant",),
    "program": ("Program",),
    "national_id": ("National ID",),
    "sender": ("From",),
    "recipient": ("To",),
    "sent_date": ("Date",),
    "subject": ("Subject",),
}


def _baseline_key_value_handler(doc: PreparedDocument, schema: ExtractionSchema) -> StrategyOutput:
    """Deterministic key-value reference strategy for reproducible offline golden baseline runs."""
    fields: dict[str, ProposedField] = {}
    for fname, spec in schema.fields.items():
        labels = _FIELD_LABEL_MAP.get(fname, (fname,))
        matched_val: str | None = None
        matched_quote: str | None = None
        for label in labels:
            m = re.search(rf"^{re.escape(label)}:\s*(.+)$", doc.full_text, flags=re.M)
            if m:
                matched_val = m.group(1).strip()
                matched_quote = m.group(0).strip()
                break

        if matched_val is None:
            fields[fname] = ProposedField(presence=FieldPresence.MISSING, value=None)
            continue

        typed_val: Any = matched_val
        if spec.field_type == FieldType.DECIMAL:
            typed_val = Decimal(matched_val)
        elif spec.field_type == FieldType.DATE:
            typed_val = date.fromisoformat(matched_val)

        fields[fname] = ProposedField(
            presence=FieldPresence.PRESENT,
            value=typed_val,
            confidence=0.95,
            confidence_method="baseline-deterministic",
            evidence=(ProposedEvidence(quote=matched_quote or matched_val),),
        )
    return StrategyOutput(records=(ProposedRecord(fields=fields),))


@pytest.mark.evaluation
def test_golden_dataset_regression_baseline_across_5_categories_and_3_formats(
    tmp_path: Path,
    fixtures_dir: Path,
    contract_json_schema: dict[str, Any],
) -> None:
    dataset = json.loads((fixtures_dir / "golden" / "dataset.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = dataset["cases"]
    categories = {c["category"] for c in cases}
    formats = {c["format"] for c in cases}
    assert len(categories) >= 5
    assert formats == {"txt", "pdf", "docx"}

    strategy = DeterministicTestStrategy(_baseline_key_value_handler, name="golden-baseline")
    results: list[ExtractionResult] = []

    for case in cases:
        cid = case["case_id"]
        fmt = case["format"]
        lines = case["content_lines"]
        if fmt == "txt":
            doc_path = tmp_path / f"{cid}.txt"
            doc_path.write_text("\n".join(lines), encoding="utf-8")
        elif fmt == "pdf":
            doc_path = create_minimal_pdf(tmp_path / f"{cid}.pdf", ["\n".join(lines)])
        else:
            doc_path = create_minimal_docx(tmp_path / f"{cid}.docx", paragraphs=lines)

        res = extract(doc_path, case["schema"], strategy=strategy, document_id=cid)
        jsonschema.validate(instance=res.to_dict(), schema=contract_json_schema)
        assert res.status.value == case["expected_status"]
        results.append(res)

    report = evaluate_golden_results(
        dataset_version=dataset["dataset_version"],
        strategy_name=strategy.name,
        cases=cases,
        results=results,
    )
    assert report.total_cases == len(cases)
    assert report.field_exact_match_accuracy == 1.0
    assert report.field_precision == 1.0
    assert report.field_recall == 1.0
    assert report.field_f1 == 1.0
    assert report.provenance_accuracy == 1.0
    assert report.validation_pass_rate == 1.0
    assert report.hallucination_rate == 0.0
