"""Sensitive-data redaction, log hygiene, and cross-extraction isolation tests."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from structa_maestro import (
    DeterministicTestStrategy,
    ExtractionEngine,
    OpenAICompatibleLLMStrategy,
    ProcessingStatus,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    SecretValue,
    StrategyOutput,
    extract,
)
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema


@pytest.mark.security
def test_secret_value_and_strategy_repr_never_expose_api_key() -> None:
    secret = SecretValue("sk-live-very-secret-token-987654")
    assert "sk-live" not in repr(secret)
    assert "sk-live" not in str(secret)

    strategy = OpenAICompatibleLLMStrategy(model="gpt-4o-mini", api_key=secret)
    assert "sk-live" not in repr(strategy)
    assert "sk-live" not in str(strategy)


@pytest.mark.security
def test_sensitive_document_text_and_api_key_excluded_from_logs_and_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sensitive_ssn = "SSN-999-88-7777-CONFIDENTIAL"
    doc = tmp_path / "secret.txt"
    doc.write_text(f"Applicant Secret: {sensitive_ssn}\n", encoding="utf-8")
    schema = {
        "schema_id": "app",
        "schema_version": "1.0",
        "fields": {"ssn": {"type": "string", "required": True, "pattern": r"^\d{3}$"}},
    }

    def _fail(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection failed with Bearer sk-topsecret123456789")

    transport = httpx.MockTransport(_fail)
    with (
        caplog.at_level(logging.DEBUG, logger="structa_maestro"),
        httpx.Client(transport=transport) as client,
    ):
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key="sk-topsecret123456789",
            http_client=client,
            sleep_fn=lambda _s: None,
        )
        res = extract(doc, schema, strategy=strategy)

    assert res.status == ProcessingStatus.FAILED
    serialized = res.to_json()
    assert "sk-topsecret" not in serialized
    assert sensitive_ssn not in serialized
    assert "sk-topsecret" not in caplog.text
    assert sensitive_ssn not in caplog.text
    # Full local host directory path should not leak in SourceMetadata.filename
    assert res.source is not None
    assert res.source.filename == "secret.txt"
    assert str(tmp_path) not in serialized


@pytest.mark.security
def test_fallback_and_retry_error_bodies_never_leak_secrets_or_document_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_key = "sk-retry-fallback-secret-888888"
    doc_secret = "CONFIDENTIAL-ACCOUNT-444555"
    doc = tmp_path / "acct.txt"
    doc.write_text(f"Account: {doc_secret}\n", encoding="utf-8")
    schema = {
        "schema_id": "acct",
        "schema_version": "1.0",
        "fields": {"account": {"type": "string", "required": True}},
    }
    calls = 0

    def _noisy_provider(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                400,
                text=f"json_schema unsupported for key {secret_key} and text {doc_secret}",
            )
        return httpx.Response(
            503,
            text=f"upstream 503 with Bearer {secret_key} echoing {doc_secret}",
        )

    transport = httpx.MockTransport(_noisy_provider)
    with (
        caplog.at_level(logging.DEBUG, logger="structa_maestro"),
        httpx.Client(transport=transport) as client,
    ):
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key=secret_key,
            http_client=client,
            sleep_fn=lambda _s: None,
        )
        res = extract(doc, schema, strategy=strategy)

    assert res.status == ProcessingStatus.FAILED
    serialized = res.to_json()
    assert secret_key not in serialized
    assert doc_secret not in serialized
    assert secret_key not in caplog.text
    assert doc_secret not in caplog.text


@pytest.mark.security
def test_concurrent_extractions_remain_isolated_with_unique_ids(tmp_path: Path) -> None:
    doc_a = tmp_path / "a.txt"
    doc_b = tmp_path / "b.txt"
    doc_a.write_text("Customer: ALPHA-SECRET\n", encoding="utf-8")
    doc_b.write_text("Customer: BETA-SECRET\n", encoding="utf-8")
    schema = {
        "schema_id": "cust",
        "schema_version": "1.0",
        "fields": {"customer": {"type": "string", "required": True}},
    }

    def _handler(doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        val = doc.full_text.split("Customer:", 1)[1].strip()
        return StrategyOutput(
            records=(
                ProposedRecord(
                    fields={
                        "customer": ProposedField(
                            value=val,
                            evidence=(ProposedEvidence(quote=f"Customer: {val}"),),
                        )
                    }
                ),
            )
        )

    engine = ExtractionEngine(strategy=DeterministicTestStrategy(_handler))

    def _run(pair: tuple[Path, str]) -> tuple[str, str, str]:
        p, did = pair
        r = engine.extract(p, schema, document_id=did)
        return (
            r.extraction_id,
            str(r.records[0].fields["customer"].value),
            r.records[0].fields["customer"].provenance[0].source_document_id,
        )

    jobs = [(doc_a, f"doc-a-{i}") if i % 2 == 0 else (doc_b, f"doc-b-{i}") for i in range(12)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_run, jobs))

    extraction_ids = [r[0] for r in results]
    assert len(set(extraction_ids)) == len(jobs)
    for idx, (_eid, val, prov_doc_id) in enumerate(results):
        if idx % 2 == 0:
            assert val == "ALPHA-SECRET"
            assert prov_doc_id == f"doc-a-{idx}"
        else:
            assert val == "BETA-SECRET"
            assert prov_doc_id == f"doc-b-{idx}"
