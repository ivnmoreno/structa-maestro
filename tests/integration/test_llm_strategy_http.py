"""MockTransport HTTP tests for OpenAICompatibleLLMStrategy errors, refusals, and truncation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from structa_maestro import (
    ConfigurationError,
    OpenAICompatibleLLMStrategy,
    ProcessingStatus,
    ValidationStatus,
    extract,
)


def _run_with_handler(
    doc: Path,
    schema: dict[str, Any],
    handler: Any,
) -> Any:
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key="sk-test-secret-9999999",
            base_url="https://api.example.test/v1",
            http_client=client,
        )
        return extract(doc, schema, strategy=strategy)


def test_llm_strategy_configuration_validation() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="", api_key="sk-12345678")
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="m", api_key="sk-12345678", base_url="ftp://bad")
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="m", api_key="sk-12345678", timeout_seconds=0)


def test_llm_strategy_http_error_classifications(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    cases = [
        (401, "provider_auth_error"),
        (403, "provider_auth_error"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
        (400, "provider_request_error"),
    ]
    for status_code, expected_code in cases:
        res = _run_with_handler(
            doc,
            invoice_schema_dict,
            lambda _req, sc=status_code: httpx.Response(sc, text="error"),
        )
        assert res.status == ProcessingStatus.FAILED
        assert res.validation.status == ValidationStatus.NOT_VALIDATED
        assert any(e.code == expected_code for e in res.errors)

    # Timeout
    def _timeout(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    r_to = _run_with_handler(doc, invoice_schema_dict, _timeout)
    assert r_to.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_timeout" for e in r_to.errors)

    # Refusal
    r_ref = _run_with_handler(
        doc,
        invoice_schema_dict,
        lambda _req: httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"refusal": "Policy refusal"}}]},
        ),
    )
    assert r_ref.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_refusal" for e in r_ref.errors)

    # Malformed model JSON
    r_bad = _run_with_handler(
        doc,
        invoice_schema_dict,
        lambda _req: httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{broken"}}]},
        ),
    )
    assert r_bad.status == ProcessingStatus.FAILED
    assert any(e.code == "malformed_model_output" for e in r_bad.errors)

    # Truncated finish_reason with parseable partial record -> PARTIAL + warning
    partial_json = json.dumps(
        {
            "records": [
                {
                    "fields": {
                        "invoice_number": {"presence": "present", "value": "INV-2026-001"},
                    }
                },
                "malformed-second-record",
            ]
        }
    )
    r_trunc = _run_with_handler(
        doc,
        invoice_schema_dict,
        lambda _req: httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": partial_json}}]},
        ),
    )
    assert r_trunc.status == ProcessingStatus.PARTIAL
    assert len(r_trunc.records) == 1
    assert r_trunc.records[0].fields["invoice_number"].value == "INV-2026-001"
    assert any(w.code == "response_truncated" for w in r_trunc.warnings)
    assert any(e.code == "malformed_record" for e in r_trunc.errors)
