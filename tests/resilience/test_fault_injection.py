"""Fault-injection resilience tests across component boundaries (docs/TESTING.md Section 13.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionEngine,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    ProvenanceAssociator,
    ResultBuilder,
    StrategyOutput,
    ValidationStatus,
)
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema


class _ExplodingProvenanceAssociator(ProvenanceAssociator):
    def associate(self, **kwargs: Any) -> Any:
        if kwargs.get("field_name") == "vendor_name":
            raise RuntimeError("simulated provenance failure")
        return super().associate(**kwargs)


def test_strategy_exception_isolated_to_canonical_failed_result(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    def _boom(_doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        raise RuntimeError("simulated downstream outage sk-supersecret123456")

    res = ExtractionEngine(strategy=DeterministicTestStrategy(_boom)).extract(
        doc, invoice_schema_dict
    )
    assert res.status == ProcessingStatus.FAILED
    assert res.validation.status == ValidationStatus.NOT_VALIDATED
    assert any(e.code == "extraction_strategy_error" for e in res.errors)
    # Secret must not leak in error message
    assert "sk-supersecret" not in res.to_json()


def test_selective_provenance_fault_preserves_extracted_fields_and_marks_partial(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "vendor_name": ProposedField(
                            value="Acme Industrial Supply Ltd.",
                            evidence=(
                                ProposedEvidence(quote="Vendor: Acme Industrial Supply Ltd."),
                            ),
                        ),
                    }
                ),
            )
        )
    )
    builder = ResultBuilder(provenance_associator=_ExplodingProvenanceAssociator())
    res = ExtractionEngine(strategy=strategy, result_builder=builder).extract(
        doc, invoice_schema_dict
    )

    assert res.status == ProcessingStatus.PARTIAL
    assert res.records[0].fields["invoice_number"].value == "INV-2026-001"
    assert len(res.records[0].fields["invoice_number"].provenance) == 1
    assert res.records[0].fields["vendor_name"].value == "Acme Industrial Supply Ltd."
    assert any(
        e.code == "provenance_association_failed"
        for e in res.records[0].fields["vendor_name"].errors
    )
