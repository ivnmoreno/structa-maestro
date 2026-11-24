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
def test_concurrent_batch_never_leaks_host_paths_secrets_or_document_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from structa_maestro import BatchOptions, RetryPolicy, extract_batch

    secret_key = "sk-batch-concurrent-secret-777777"
    nested = tmp_path / "private_host_folder" / "sub"
    nested.mkdir(parents=True)
    docs: list[Path] = []
    secrets_in_docs: list[str] = []
    for i in range(4):
        tok = f"CONFIDENTIAL-PAYLOAD-{i:03d}"
        secrets_in_docs.append(tok)
        p = nested / f"rec_{i}.txt"
        p.write_text(f"Account: {tok}\n", encoding="utf-8")
        docs.append(p)

    schema = {
        "schema_id": "acct-batch",
        "schema_version": "1.0",
        "fields": {"account": {"type": "string", "required": True}},
    }

    def _fail_provider(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Error with Bearer {secret_key} and {secrets_in_docs[0]}")

    transport = httpx.MockTransport(_fail_provider)
    with (
        caplog.at_level(logging.DEBUG, logger="structa_maestro"),
        httpx.Client(transport=transport) as client,
    ):
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key=secret_key,
            http_client=client,
            retry_policy=RetryPolicy(max_retries=1),
            sleep_fn=lambda _s: None,
        )
        batch = extract_batch(docs, schema, strategy=strategy, options=BatchOptions(max_workers=4))

    jsonl = batch.to_jsonl()
    summary_json = batch.summary.to_json()
    assert batch.summary.failed_count == 4
    assert secret_key not in jsonl
    assert secret_key not in summary_json
    assert secret_key not in caplog.text
    assert "private_host_folder" not in jsonl
    assert "private_host_folder" not in summary_json
    for tok in secrets_in_docs:
        assert tok not in jsonl
        assert tok not in summary_json
        assert tok not in caplog.text
    for idx, res in enumerate(batch.results):
        assert res.source is not None
        assert res.source.filename == f"rec_{idx}.txt"


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


@pytest.mark.security
def test_in_memory_stream_dos_zip_bomb_path_traversal_and_concurrent_secret_isolation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from tests.conftest import create_zip_bomb_docx

    from structa_maestro import (
        BatchOptions,
        ConfigurationError,
        DocumentInput,
        DocumentLimits,
        RetryPolicy,
        extract_batch,
    )

    schema = {
        "schema_id": "sec-inmem",
        "schema_version": "1.0",
        "fields": {"account": {"type": "string", "required": True}},
    }

    # 1. Adversarial unbounded stream stops after max_file_bytes + 1 bytes
    class _InfiniteStream:
        closed = False

        def __init__(self) -> None:
            self.read_bytes = 0

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:
            n = 4096 if size < 0 else min(4096, size)
            self.read_bytes += n
            return b"Z" * n

    inf = _InfiniteStream()
    res_inf = extract(
        DocumentInput.from_stream(inf, filename="../../etc/huge.txt"),  # type: ignore[arg-type]
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=())),
        limits=DocumentLimits(max_file_bytes=1024),
    )
    assert res_inf.status == ProcessingStatus.FAILED
    assert any(e.code == "resource_limit_exceeded" for e in res_inf.errors)
    assert inf.read_bytes == 1025
    assert res_inf.source is not None
    assert res_inf.source.filename == "huge.txt"
    assert "etc" not in res_inf.to_json()

    # 2. ZIP-bomb DOCX supplied as raw bytes -> resource_limit_exceeded
    bomb_bytes = create_zip_bomb_docx(
        tmp_path / "bomb.docx", uncompressed_bytes=2 * 1024 * 1024
    ).read_bytes()
    res_bomb = extract(
        DocumentInput.from_bytes(bomb_bytes, filename=r"C:\secret\dir\bomb.docx"),
        schema,
        strategy=DeterministicTestStrategy(StrategyOutput(records=())),
        limits=DocumentLimits(max_file_bytes=5 * 1024 * 1024, max_decompressed_bytes=512 * 1024),
    )
    assert res_bomb.status == ProcessingStatus.FAILED
    assert any(e.code == "resource_limit_exceeded" for e in res_bomb.errors)
    assert res_bomb.source is not None
    assert res_bomb.source.filename == "bomb.docx"
    assert "C:" not in res_bomb.to_json()

    # 3. Null-byte filename fails eagerly
    with pytest.raises(ConfigurationError):
        DocumentInput.from_text("hello", filename="bad\x00.txt")

    # 4. Concurrent in-memory batch failure never leaks secrets, raw text, or host prefixes
    secret_key = "sk-inmem-batch-secret-999999"
    markers = [f"CONFIDENTIAL-MEM-{i:02d}" for i in range(4)]
    inputs = [
        DocumentInput.from_text(f"Account: {markers[0]}\n", filename="/private/a/doc0.txt"),
        DocumentInput.from_bytes(
            f"Account: {markers[1]}\n".encode(), filename=r"C:\private\b\doc1.txt"
        ),
        DocumentInput.from_stream(
            __import__("io").BytesIO(f"Account: {markers[2]}\n".encode()),
            filename="../../private/c/doc2.txt",
        ),
        DocumentInput.from_text(f"Account: {markers[3]}\n", filename="doc3.txt"),
    ]
    for idx, inp in enumerate(inputs):
        assert markers[idx] not in repr(inp)
        assert "private" not in repr(inp)

    def _fail_provider(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Failed with Bearer {secret_key} echoing {markers[0]}")

    transport = httpx.MockTransport(_fail_provider)
    with (
        caplog.at_level(logging.DEBUG, logger="structa_maestro"),
        httpx.Client(transport=transport) as client,
    ):
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key=secret_key,
            http_client=client,
            retry_policy=RetryPolicy(max_retries=1),
            sleep_fn=lambda _s: None,
        )
        batch = extract_batch(
            inputs,
            schema,
            strategy=strategy,
            options=BatchOptions(max_workers=4),
        )

    jsonl = batch.to_jsonl()
    summary_json = batch.summary.to_json()
    assert batch.summary.failed_count == 4
    assert secret_key not in jsonl
    assert secret_key not in summary_json
    assert secret_key not in caplog.text
    assert "private" not in jsonl
    assert "private" not in summary_json
    for tok in markers:
        assert tok not in jsonl
        assert tok not in summary_json
        assert tok not in caplog.text
    assert [r.source.filename if r.source else None for r in batch.results] == [
        "doc0.txt",
        "doc1.txt",
        "doc2.txt",
        "doc3.txt",
    ]


@pytest.mark.security
def test_record_binding_error_never_leaks_sensitive_field_values() -> None:
    from pydantic import BaseModel, Field

    from structa_maestro import (
        ExtractedField,
        ExtractedRecord,
        FieldPresence,
        RecordBindingError,
        ValidationOutcome,
        ValidationStatus,
        bind_record,
    )

    class SensitiveCustomer(BaseModel):
        account_id: int = Field(ge=1)
        ssn: str = Field(pattern=r"^\d{3}-\d{2}-\d{4}$")

    secret_val = "SSN-999-88-7777-TOP-SECRET"
    secret_acct = "SECRET-ACCOUNT-TOKEN-444555"
    rec = ExtractedRecord(
        record_id="rec-sec-1",
        status=ProcessingStatus.COMPLETED,
        fields={
            "account_id": ExtractedField(
                presence=FieldPresence.PRESENT,
                value=secret_acct,
                validation=ValidationOutcome(status=ValidationStatus.VALID),
            ),
            "ssn": ExtractedField(
                presence=FieldPresence.PRESENT,
                value=secret_val,
                validation=ValidationOutcome(status=ValidationStatus.VALID),
            ),
        },
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )

    with pytest.raises(RecordBindingError) as exc_info:
        bind_record(rec, SensitiveCustomer)

    err_msg = str(exc_info.value)
    all_issues = " | ".join(exc_info.value.issues)
    assert secret_val not in err_msg
    assert secret_acct not in err_msg
    assert secret_val not in all_issues
    assert secret_acct not in all_issues
    assert "account_id" in err_msg
    assert "ssn" in err_msg


@pytest.mark.security
def test_concurrent_async_batch_never_leaks_secrets_raw_text_or_host_paths(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    from structa_maestro import (
        AsyncOpenAICompatibleLLMStrategy,
        BatchExtractionResult,
        BatchOptions,
        DocumentInput,
        RetryPolicy,
        aextract_batch,
    )

    secret_key = "sk-async-concurrent-secret-666666"
    markers = [f"ASYNC-CONFIDENTIAL-{i:02d}" for i in range(4)]
    inputs = [
        DocumentInput.from_text(f"Account: {markers[0]}\n", filename="/private/async/d0.txt"),
        DocumentInput.from_bytes(
            f"Account: {markers[1]}\n".encode(), filename=r"C:\private\async\d1.txt"
        ),
        DocumentInput.from_text(f"Account: {markers[2]}\n", filename="../../private/async/d2.txt"),
        DocumentInput.from_text(f"Account: {markers[3]}\n", filename="d3.txt"),
    ]
    schema = {
        "schema_id": "async-sec",
        "schema_version": "1.0",
        "fields": {"account": {"type": "string", "required": True}},
    }

    async def _fail_provider(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Connect error Bearer {secret_key} echoing {markers[0]}")

    async def _no_sleep(_d: float) -> None:
        return None

    async def _run() -> BatchExtractionResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_fail_provider)) as client:
            strategy = AsyncOpenAICompatibleLLMStrategy(
                model="gpt-4o-mini",
                api_key=secret_key,
                http_client=client,
                retry_policy=RetryPolicy(max_retries=1),
                async_sleep_fn=_no_sleep,
            )
            assert secret_key not in repr(strategy)
            return await aextract_batch(
                inputs,
                schema,
                strategy=strategy,
                options=BatchOptions(max_workers=4),
            )

    with caplog.at_level(logging.DEBUG, logger="structa_maestro"):
        batch = asyncio.run(_run())

    jsonl = batch.to_jsonl()
    summary_json = batch.summary.to_json()
    assert batch.summary.failed_count == 4
    assert secret_key not in jsonl
    assert secret_key not in summary_json
    assert secret_key not in caplog.text
    assert "private" not in jsonl
    assert "private" not in summary_json
    for tok in markers:
        assert tok not in jsonl
        assert tok not in summary_json
        assert tok not in caplog.text
    assert [r.source.filename if r.source else None for r in batch.results] == [
        "d0.txt",
        "d1.txt",
        "d2.txt",
        "d3.txt",
    ]


@pytest.mark.security
def test_telemetry_events_summaries_and_structured_logs_never_leak_sensitive_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio
    import json

    from structa_maestro import (
        AsyncOpenAICompatibleLLMStrategy,
        BatchOptions,
        DocumentInput,
        ExtractionEvent,
        InMemoryTelemetryObserver,
        OpenAICompatibleLLMStrategy,
        RetryPolicy,
        StructuredLoggingObserver,
        aextract_batch,
        extract_batch,
    )

    api_secret = "sk-telemetry-privacy-secret-777888"
    pii_ssn = "SSN-987-65-4321"
    raw_quote = "CONFIDENTIAL SALARY PAYLOAD 995000 USD"
    host_dir = "super_secret_tenant_dir"

    inputs = [
        DocumentInput.from_text(
            f"Employee SSN: {pii_ssn}\nNote: {raw_quote}\n",
            filename=f"/{host_dir}/nested/payroll_0.txt",
        ),
        DocumentInput.from_text(
            f"Employee SSN: {pii_ssn}\nNote: {raw_quote}\n",
            filename=rf"C:\{host_dir}\payroll_1.txt",
        ),
    ]
    schema = {
        "schema_id": "payroll-privacy",
        "schema_version": "1.0",
        "fields": {"ssn": {"type": "string", "required": True}},
    }

    ok_body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "records": [
                                {
                                    "fields": {
                                        "ssn": {
                                            "presence": "present",
                                            "value": pii_ssn,
                                            "confidence": 0.99,
                                            "ambiguous": False,
                                            "evidence": [
                                                {
                                                    "quote": f"Employee SSN: {pii_ssn}",
                                                    "relationship": "direct",
                                                }
                                            ],
                                        }
                                    }
                                }
                            ]
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }

    class _LeakyErrorObserver:
        def on_event(self, _event: ExtractionEvent) -> None:
            raise RuntimeError(f"Callback failed with api_key={api_secret}")

    # Sync concurrent batch
    sync_mem = InMemoryTelemetryObserver()
    sync_log = StructuredLoggingObserver(logger_name="structa_maestro.sec_tel", level=logging.INFO)
    with (
        caplog.at_level(logging.INFO, logger="structa_maestro"),
        httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=ok_body))
        ) as sclient,
    ):
        s_strat = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key=api_secret,
            http_client=sclient,
            retry_policy=RetryPolicy(max_retries=0),
        )
        extract_batch(
            inputs,
            schema,
            strategy=s_strat,
            options=BatchOptions(max_workers=2),
            observers=[sync_mem, sync_log, _LeakyErrorObserver()],
        )

    # Async concurrent batch
    async_mem = InMemoryTelemetryObserver()

    async def _run_async() -> None:
        async def _aok(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=ok_body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(_aok)) as aclient:
            a_strat = AsyncOpenAICompatibleLLMStrategy(
                model="gpt-4o-mini",
                api_key=api_secret,
                http_client=aclient,
                retry_policy=RetryPolicy(max_retries=0),
            )
            await aextract_batch(
                inputs,
                schema,
                strategy=a_strat,
                options=BatchOptions(max_workers=2),
                observers=[async_mem, sync_log, _LeakyErrorObserver()],
            )

    with caplog.at_level(logging.INFO, logger="structa_maestro"):
        asyncio.run(_run_async())

    all_serialized = "\n".join(
        [
            *[e.to_json() for e in sync_mem.events()],
            sync_mem.summary().to_json(),
            *[e.to_json() for e in async_mem.events()],
            async_mem.summary().to_json(),
            caplog.text,
        ]
    )
    for forbidden in (api_secret, pii_ssn, raw_quote, host_dir):
        assert forbidden not in all_serialized

    assert sync_mem.summary().total_tokens == 140
    assert async_mem.summary().total_tokens == 140
