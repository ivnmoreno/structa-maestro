"""Output-injection inert serialization tests (docs/TESTING.md Section 9.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionResult,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
    extract,
)


@pytest.mark.security
@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert('xss')</script>",
        "'; DROP TABLE invoices; --",
        "$(rm -rf /tmp/never-run)",
        "[click](javascript:alert(1))",
        '{"nested": "escaped \\"quote\\""}',
        "<?xml version='1.0'?><!DOCTYPE foo>",
        "Right-to-left \u202eoverride\u202c text",
    ],
)
def test_output_injection_payloads_serialize_inertly_and_round_trip(
    tmp_path: Path, payload: str
) -> None:
    doc = tmp_path / "payload.txt"
    doc.write_text(f"Note: {payload}\n", encoding="utf-8")
    schema = {
        "schema_id": "notes",
        "schema_version": "1.0",
        "fields": {"note": {"type": "string", "required": True}},
    }
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "note": ProposedField(
                            value=payload,
                            evidence=(ProposedEvidence(quote=f"Note: {payload}"),),
                        )
                    }
                ),
            )
        )
    )
    res = extract(doc, schema, strategy=strategy)
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID

    restored = ExtractionResult.from_json(res.to_json())
    assert restored == res
    assert restored.records[0].fields["note"].value == payload
