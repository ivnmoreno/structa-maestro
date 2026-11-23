"""End-to-end cross-field evaluation tests: multi-record isolation & determinism (T014)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from structa_maestro import (
    DeterministicTestStrategy,
    FieldPresence,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
    extract,
)


@pytest.mark.evaluation
def test_multi_record_cross_field_isolation_and_line_item_reconciliation(tmp_path: Path) -> None:
    doc = tmp_path / "batch_invoices.txt"
    doc.write_text(
        "Record 1: Issue 2026-03-01 Due 2026-03-31 Subtotal 100.00 Tax 19.00 Total 119.00\n"
        "Record 2: Issue 2026-04-20 Due 2026-04-10 Subtotal 200.00 Tax 38.00 Total 250.00\n",
        encoding="utf-8",
    )
    schema = {
        "schema_id": "multi-invoice-rules",
        "schema_version": "1.0",
        "cardinality": "multiple",
        "fields": {
            "issue_date": {"type": "date", "required": True},
            "due_date": {"type": "date", "required": True},
            "subtotal": {"type": "decimal", "required": True},
            "tax": {"type": "decimal", "required": True},
            "total_amount": {"type": "decimal", "required": True},
            "line_items": {
                "type": "array",
                "required": True,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "required": True},
                        "amount": {"type": "decimal", "required": True},
                    },
                },
            },
        },
        "rules": [
            {
                "rule_id": "issue-before-due",
                "kind": "comparison",
                "left_field": "issue_date",
                "operator": "lte",
                "right_field": "due_date",
            },
            {
                "rule_id": "total-check",
                "kind": "arithmetic",
                "terms": [{"field": "subtotal"}, {"field": "tax"}],
                "target_field": "total_amount",
                "tolerance": "0.01",
            },
            {
                "rule_id": "line-item-sum",
                "kind": "arithmetic",
                "terms": [{"field": "line_items", "item_field": "amount"}],
                "target_field": "subtotal",
                "severity": "warning",
            },
        ],
    }

    shared_output = StrategyOutput(
        records=(
            ProposedRecord(
                fields={
                    "issue_date": ProposedField(
                        value=date(2026, 3, 1),
                        evidence=(ProposedEvidence(quote="Issue 2026-03-01"),),
                    ),
                    "due_date": ProposedField(
                        value=date(2026, 3, 31),
                        evidence=(ProposedEvidence(quote="Due 2026-03-31"),),
                    ),
                    "subtotal": ProposedField(value=Decimal("100.00")),
                    "tax": ProposedField(value=Decimal("19.00")),
                    "total_amount": ProposedField(value=Decimal("119.00")),
                    "line_items": ProposedField(
                        value=[
                            {"name": "Part A", "amount": Decimal("60.00")},
                            {"name": "Part B", "amount": Decimal("40.00")},
                        ]
                    ),
                }
            ),
            ProposedRecord(
                fields={
                    "issue_date": ProposedField(value=date(2026, 4, 20)),
                    "due_date": ProposedField(value=date(2026, 4, 10)),
                    "subtotal": ProposedField(value=Decimal("200.00")),
                    "tax": ProposedField(value=Decimal("38.00")),
                    "total_amount": ProposedField(value=Decimal("250.00")),
                    "line_items": ProposedField(
                        value=[{"name": "Only", "amount": Decimal("180.00")}]
                    ),
                }
            ),
        )
    )
    strat_a = DeterministicTestStrategy(shared_output, name="strategy-a")
    strat_b = DeterministicTestStrategy(shared_output, name="strategy-b")

    res_a = extract(doc, schema, strategy=strat_a, document_id="doc-multi")
    res_b = extract(doc, schema, strategy=strat_b, document_id="doc-multi")

    # Determinism: identical validation outcomes independently of strategy identity
    assert res_a.validation == res_b.validation
    assert res_a.warnings == res_b.warnings

    # Per-record isolation: record 1 stays VALID with 0 warnings; record 2 is INVALID + 1 warning
    assert res_a.status == ProcessingStatus.COMPLETED
    assert res_a.records[0].validation.status == ValidationStatus.VALID
    assert res_a.records[0].warnings == []
    assert res_a.records[1].validation.status == ValidationStatus.INVALID
    assert [e.code for e in res_a.records[1].validation.errors] == [
        "cross_field_comparison_failed",
        "cross_field_arithmetic_mismatch",
    ]
    assert all(e.record_id == "record-002" for e in res_a.records[1].validation.errors)
    assert [w.code for w in res_a.records[1].warnings] == ["cross_field_arithmetic_mismatch"]
    assert [w.code for w in res_a.warnings] == ["cross_field_arithmetic_mismatch"]
    assert res_a.validation.status == ValidationStatus.INVALID

    # Sanity check: empty string vs missing in pipeline
    assert res_a.records[0].fields["issue_date"].presence == FieldPresence.PRESENT
