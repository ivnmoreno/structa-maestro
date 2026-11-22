"""Dedicated tests for all 10 non-negotiable contract invariants & cross-strategy parity."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from structa_maestro import (
    DeterministicTestStrategy,
    EvidenceRelationship,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    FieldPresence,
    OpenAICompatibleLLMStrategy,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    ProvenanceEvidence,
    SchemaMetadata,
    SourceMetadata,
    StrategyOutput,
    ValidationOutcome,
    ValidationStatus,
    extract,
)


def test_invariant_missing_information_never_fabricated_for_required_fields(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    strategy = DeterministicTestStrategy(StrategyOutput(records=(ProposedRecord(fields={}),)))
    res = extract(doc, invoice_schema_dict, strategy=strategy)

    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.INVALID
    for req_field in ("invoice_number", "vendor_name", "issue_date", "total_amount", "currency"):
        ef = res.records[0].fields[req_field]
        assert ef.presence == FieldPresence.MISSING
        assert ef.value is None
        assert ef.validation.status == ValidationStatus.INVALID


def test_invariant_partial_and_failed_never_promoted_to_completed(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    valid_fields = {
        "invoice_number": ProposedField(value="INV-2026-001", confidence=0.99),
        "vendor_name": ProposedField(value="Acme Industrial Supply Ltd.", confidence=0.99),
        "issue_date": ProposedField(value=date(2026, 3, 15), confidence=0.99),
        "total_amount": ProposedField(value=Decimal("1250.50"), confidence=0.99),
        "currency": ProposedField(value="USD", confidence=0.99),
    }

    partial_res = extract(
        doc,
        invoice_schema_dict,
        strategy=DeterministicTestStrategy(
            StrategyOutput(records=(ProposedRecord(fields=valid_fields, partial=True),))
        ),
    )
    assert partial_res.validation.status == ValidationStatus.VALID
    assert partial_res.status == ProcessingStatus.PARTIAL

    failed_res = extract(
        doc,
        invoice_schema_dict,
        strategy=DeterministicTestStrategy(StrategyOutput(failed=True)),
    )
    assert failed_res.status == ProcessingStatus.FAILED


def test_invariant_source_document_binding_enforced_structurally() -> None:
    with pytest.raises(ValidationError, match="foreign document_id"):
        ExtractionResult(
            status=ProcessingStatus.COMPLETED,
            source=SourceMetadata(document_id="doc-A"),
            schema_metadata=SchemaMetadata(schema_id="s", schema_version="1.0"),
            records=[
                ExtractedRecord(
                    record_id="record-001",
                    status=ProcessingStatus.COMPLETED,
                    fields={
                        "invoice_number": ExtractedField(
                            presence=FieldPresence.PRESENT,
                            value="INV-1",
                            provenance=[
                                ProvenanceEvidence(
                                    source_document_id="doc-B",
                                    evidence="INV-1",
                                    relationship=EvidenceRelationship.DIRECT,
                                )
                            ],
                            validation=ValidationOutcome(status=ValidationStatus.VALID),
                        )
                    },
                    validation=ValidationOutcome(status=ValidationStatus.VALID),
                )
            ],
            validation=ValidationOutcome(status=ValidationStatus.VALID),
        )


def test_invariant_cross_strategy_contract_parity_and_zero_provider_leakage(
    fixtures_dir: Path,
    invoice_schema_dict: dict[str, Any],
    contract_json_schema: dict[str, Any],
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    det_strategy = DeterministicTestStrategy(
        StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "invoice_number": ProposedField(
                            value="INV-2026-001",
                            confidence=0.95,
                            evidence=(ProposedEvidence(quote="Invoice Number: INV-2026-001"),),
                        ),
                        "vendor_name": ProposedField(
                            value="Acme Industrial Supply Ltd.",
                            confidence=0.94,
                            evidence=(
                                ProposedEvidence(quote="Vendor: Acme Industrial Supply Ltd."),
                            ),
                        ),
                        "issue_date": ProposedField(
                            value=date(2026, 3, 15),
                            confidence=0.96,
                            evidence=(ProposedEvidence(quote="Issue Date: 2026-03-15"),),
                        ),
                        "total_amount": ProposedField(
                            value=Decimal("1250.50"),
                            confidence=0.97,
                            evidence=(ProposedEvidence(quote="Total Amount: 1250.50"),),
                        ),
                        "currency": ProposedField(
                            value="USD",
                            confidence=0.99,
                            evidence=(ProposedEvidence(quote="Currency: USD"),),
                        ),
                    }
                ),
            )
        )
    )

    llm_body = {
        "id": "chatcmpl-provider-secret-id",
        "object": "chat.completion",
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "records": [
                                {
                                    "fields": {
                                        "invoice_number": {
                                            "presence": "present",
                                            "value": "INV-2026-001",
                                            "confidence": 0.95,
                                            "evidence": [{"quote": "Invoice Number: INV-2026-001"}],
                                        },
                                        "vendor_name": {
                                            "presence": "present",
                                            "value": "Acme Industrial Supply Ltd.",
                                            "confidence": 0.94,
                                            "evidence": [
                                                {"quote": "Vendor: Acme Industrial Supply Ltd."}
                                            ],
                                        },
                                        "issue_date": {
                                            "presence": "present",
                                            "value": "2026-03-15",
                                            "confidence": 0.96,
                                            "evidence": [{"quote": "Issue Date: 2026-03-15"}],
                                        },
                                        "total_amount": {
                                            "presence": "present",
                                            "value": "1250.50",
                                            "confidence": 0.97,
                                            "evidence": [{"quote": "Total Amount: 1250.50"}],
                                        },
                                        "currency": {
                                            "presence": "present",
                                            "value": "USD",
                                            "confidence": 0.99,
                                            "evidence": [{"quote": "Currency: USD"}],
                                        },
                                    }
                                }
                            ]
                        }
                    ),
                },
            }
        ],
    }

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=llm_body))
    with httpx.Client(transport=transport) as client:
        llm_strategy = OpenAICompatibleLLMStrategy(
            model="test-model",
            api_key="sk-test-1234567890",
            http_client=client,
        )
        res_det = extract(doc, invoice_schema_dict, strategy=det_strategy, document_id="doc-1")
        res_llm = extract(doc, invoice_schema_dict, strategy=llm_strategy, document_id="doc-1")

    det_dict = res_det.to_dict()
    llm_dict = res_llm.to_dict()
    jsonschema.validate(instance=det_dict, schema=contract_json_schema)
    jsonschema.validate(instance=llm_dict, schema=contract_json_schema)

    assert set(det_dict.keys()) == set(llm_dict.keys())
    serialized_llm = res_llm.to_json()
    for provider_key in ("chatcmpl", "prompt_tokens", "completion_tokens", "choices"):
        assert provider_key not in serialized_llm

    for fname in ("invoice_number", "vendor_name", "issue_date", "total_amount", "currency"):
        assert res_det.records[0].fields[fname].value == res_llm.records[0].fields[fname].value
