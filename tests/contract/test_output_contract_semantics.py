"""Semantic contract tests covering CON-001 through CON-015 (docs/TESTING.md Section 3.2)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from structa_maestro import (
    CONTRACT_VERSION,
    DeterministicTestStrategy,
    DiagnosticIssue,
    EvidenceRelationship,
    FieldPresence,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    ValidationStatus,
    extract,
)


def test_con_001_to_006_dimension_independence_valid_low_conf_invalid_strong_evidence(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        # CON-001: Valid + high confidence
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=0.98,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        # CON-002: Valid + low confidence + ambiguous
                        "vendor_name": ProposedField(
                            value="Acme Industrial Supply Ltd.",
                            confidence=0.32,
                            ambiguous=True,
                            evidence=(
                                ProposedEvidence(quote="Vendor: Acme Industrial Supply Ltd."),
                            ),
                        ),
                        # CON-004: Valid + no provenance
                        "issue_date": ProposedField(
                            value=date(2026, 3, 15),
                            confidence=0.80,
                            evidence=(),
                        ),
                        # CON-003: Invalid value with strong evidence retained
                        "total_amount": ProposedField(
                            value=Decimal("-50.00"),
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote="Total Amount: 1250.50"),),
                        ),
                        # CON-005: Missing required field (currency omitted)
                        # CON-006: Missing optional field (notes omitted)
                    }
                ),
            )
        )
    )

    res = extract(doc, invoice_schema_dict, strategy=strategy)
    # CON-009: Extraction completed while validation is invalid
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.INVALID
    assert res.contract_version == CONTRACT_VERSION

    fields = res.records[0].fields
    # CON-001
    assert fields["invoice_number"].validation.status == ValidationStatus.VALID
    assert fields["invoice_number"].confidence is not None
    assert fields["invoice_number"].confidence.value == 0.98
    # CON-002
    assert fields["vendor_name"].validation.status == ValidationStatus.VALID
    assert fields["vendor_name"].confidence is not None
    assert fields["vendor_name"].confidence.value == 0.32
    assert any(w.code == "ambiguous_source_value" for w in fields["vendor_name"].warnings)
    # CON-003 & CON-012 (evidence retained and conflict visible)
    assert fields["total_amount"].validation.status == ValidationStatus.INVALID
    assert len(fields["total_amount"].provenance) == 1
    assert fields["total_amount"].provenance[0].relationship == EvidenceRelationship.CONFLICTING
    # CON-004
    assert fields["issue_date"].validation.status == ValidationStatus.VALID
    assert fields["issue_date"].provenance == []
    # CON-005
    assert fields["currency"].presence == FieldPresence.MISSING
    assert fields["currency"].validation.status == ValidationStatus.INVALID
    # CON-006
    assert fields["notes"].presence == FieldPresence.MISSING
    assert fields["notes"].validation.status == ValidationStatus.VALID


def test_con_007_008_010_011_013_partial_failed_multiple_errors_and_out_of_range_confidence(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    # CON-007 + CON-010 + CON-011 + CON-013
    partial_strategy = DeterministicTestStrategy(
        StrategyOutput(
            partial=True,
            errors=(
                DiagnosticIssue(
                    code="partial_extraction",
                    severity=IssueSeverity.ERROR,
                    stage=PipelineStage.EXTRACTION,
                    message="One section could not be parsed.",
                ),
            ),
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=1.8,  # CON-013: out of range -> value=None + warning
                            evidence=(
                                ProposedEvidence(quote="Invoice Number: INV-2026-001"),
                                ProposedEvidence(quote="INV-2026-001"),
                            ),
                        ),
                        "vendor_name": ProposedField(value="Acme Industrial Supply Ltd."),
                        "issue_date": ProposedField(value=date(2026, 3, 15)),
                        "total_amount": ProposedField(value=Decimal("1250.50")),
                        "currency": ProposedField(value="USD"),
                    }
                ),
            ),
        )
    )
    p_res = extract(doc, invoice_schema_dict, strategy=partial_strategy)
    assert p_res.status == ProcessingStatus.PARTIAL
    assert p_res.validation.status == ValidationStatus.VALID
    assert len(p_res.records[0].fields["invoice_number"].provenance) == 2
    assert p_res.records[0].fields["invoice_number"].confidence is not None
    assert p_res.records[0].fields["invoice_number"].confidence.value is None
    assert any(
        w.code == "invalid_confidence_range"
        for w in p_res.records[0].fields["invoice_number"].warnings
    )

    # CON-008: Failed extraction
    failed_strategy = DeterministicTestStrategy(StrategyOutput(failed=True))
    f_res = extract(doc, invoice_schema_dict, strategy=failed_strategy)
    assert f_res.status == ProcessingStatus.FAILED
    assert f_res.validation.status == ValidationStatus.NOT_VALIDATED
    assert len(f_res.errors) >= 1
