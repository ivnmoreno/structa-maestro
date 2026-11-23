"""MockTransport HTTP tests for hardened OpenAICompatibleLLMStrategy (US1, US2, US3)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from structa_maestro import (
    ConfigurationError,
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    OpenAICompatibleLLMStrategy,
    ProcessingStatus,
    RetryPolicy,
    StructuredOutputMode,
    ValidationStatus,
    extract,
)


def _ok_invoice_payload(
    *,
    content_override: str | None = None,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    body_obj = {
        "records": [
            {
                "fields": {
                    "invoice_number": {
                        "presence": "present",
                        "value": "INV-2026-001",
                        "confidence": 0.98,
                        "ambiguous": False,
                        "evidence": [
                            {"quote": "Invoice Number: INV-2026-001", "relationship": "direct"}
                        ],
                    },
                    "vendor_name": {
                        "presence": "present",
                        "value": "Acme Industrial Supply Ltd.",
                        "confidence": 0.95,
                        "ambiguous": False,
                        "evidence": [
                            {
                                "quote": "Vendor: Acme Industrial Supply Ltd.",
                                "relationship": "direct",
                            }
                        ],
                    },
                    "issue_date": {
                        "presence": "present",
                        "value": "2026-03-15",
                        "confidence": 0.97,
                        "ambiguous": False,
                        "evidence": [{"quote": "Issue Date: 2026-03-15", "relationship": "direct"}],
                    },
                    "total_amount": {
                        "presence": "present",
                        "value": "1250.50",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "evidence": [{"quote": "Total Amount: 1250.50", "relationship": "direct"}],
                    },
                    "currency": {
                        "presence": "present",
                        "value": "USD",
                        "confidence": 0.99,
                        "ambiguous": False,
                        "evidence": [{"quote": "Currency: USD", "relationship": "direct"}],
                    },
                    "notes": {
                        "presence": "missing",
                        "value": None,
                        "confidence": None,
                        "ambiguous": False,
                        "evidence": [],
                    },
                }
            }
        ]
    }
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {
                    "content": content_override
                    if content_override is not None
                    else json.dumps(body_obj)
                },
            }
        ]
    }


def _run_with_handler(
    doc: Path,
    schema: dict[str, Any],
    handler: Any,
    **strategy_kwargs: Any,
) -> Any:
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        kwargs: dict[str, Any] = {
            "model": "gpt-4o-mini",
            "api_key": "sk-test-secret-9999999",
            "base_url": "https://api.example.test/v1",
            "http_client": client,
            "sleep_fn": lambda _s: None,
        }
        kwargs.update(strategy_kwargs)
        strategy = OpenAICompatibleLLMStrategy(**kwargs)
        return extract(doc, schema, strategy=strategy)


def test_llm_strategy_configuration_validation() -> None:
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="", api_key="sk-12345678")
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="m", api_key="sk-12345678", base_url="ftp://bad")
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(model="m", api_key="sk-12345678", timeout_seconds=0)
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(
            model="m", api_key="sk-12345678", structured_output_mode="invalid"
        )
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(
            model="m",
            api_key="sk-12345678",
            retry_policy="bad",  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError):
        OpenAICompatibleLLMStrategy(
            model="m",
            api_key="sk-12345678",
            input_budget="bad",  # type: ignore[arg-type]
        )


def test_llm_strategy_http_error_classifications(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"
    no_retry = RetryPolicy(max_retries=0)

    cases = [
        (401, "provider_auth_error"),
        (403, "provider_auth_error"),
        (408, "provider_timeout"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
        (400, "provider_request_error"),
        (404, "provider_request_error"),
    ]
    for status_code, expected_code in cases:
        calls = 0

        def _handler(_req: httpx.Request, sc: int = status_code) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(sc, text="generic error")

        res = _run_with_handler(doc, invoice_schema_dict, _handler, retry_policy=no_retry)
        assert calls == 1
        assert res.status == ProcessingStatus.FAILED
        assert res.validation.status == ValidationStatus.NOT_VALIDATED
        assert any(e.code == expected_code for e in res.errors)

    # Timeout
    def _timeout(_req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    r_to = _run_with_handler(doc, invoice_schema_dict, _timeout, retry_policy=no_retry)
    assert r_to.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_timeout" for e in r_to.errors)

    # Refusal (permanent -> zero retries even with default retry policy)
    refusal_calls = 0

    def _refusal(_req: httpx.Request) -> httpx.Response:
        nonlocal refusal_calls
        refusal_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"refusal": "Policy refusal"}}]},
        )

    r_ref = _run_with_handler(doc, invoice_schema_dict, _refusal)
    assert refusal_calls == 1
    assert r_ref.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_refusal" for e in r_ref.errors)

    # Malformed model JSON with retry_on_malformed_output=False -> 1 attempt
    bad_calls = 0

    def _bad_json(_req: httpx.Request) -> httpx.Response:
        nonlocal bad_calls
        bad_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{broken"}}]},
        )

    r_bad = _run_with_handler(
        doc,
        invoice_schema_dict,
        _bad_json,
        retry_policy=RetryPolicy(max_retries=2, retry_on_malformed_output=False),
    )
    assert bad_calls == 1
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
        retry_policy=no_retry,
    )
    assert r_trunc.status == ProcessingStatus.PARTIAL
    assert len(r_trunc.records) == 1
    assert r_trunc.records[0].fields["invoice_number"].value == "INV-2026-001"
    assert any(w.code == "response_truncated" for w in r_trunc.warnings)
    assert any(e.code == "malformed_record" for e in r_trunc.errors)


def test_strict_structured_output_fallback_non_oscillation_and_strict_only(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    # 1. Default strict_with_fallback sends compiled json_schema; if rejected with 400
    #    json_schema unsupported, switches to json_object AND stays in json_object across a 503.
    seen_formats: list[str] = []

    def _fallback_then_503_then_ok(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        fmt_type = body["response_format"]["type"]
        seen_formats.append(fmt_type)
        if len(seen_formats) == 1:
            assert body["response_format"]["json_schema"]["strict"] is True
            return httpx.Response(400, text='{"error":"json_schema response_format not supported"}')
        if len(seen_formats) == 2:
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(200, json=_ok_invoice_payload())

    res = _run_with_handler(doc, invoice_schema_dict, _fallback_then_503_then_ok)
    assert seen_formats == ["json_schema", "json_object", "json_object"]
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    warn_codes = {w.code for w in res.warnings}
    assert "structured_output_fallback" in warn_codes
    assert "provider_retry_succeeded" in warn_codes

    # 2. Strict-only mode fails explicitly without downgrading
    strict_calls = 0

    def _reject_strict(_req: httpx.Request) -> httpx.Response:
        nonlocal strict_calls
        strict_calls += 1
        return httpx.Response(400, text='{"error":"json_schema response_format not supported"}')

    res_strict = _run_with_handler(
        doc,
        invoice_schema_dict,
        _reject_strict,
        structured_output_mode=StructuredOutputMode.STRICT,
    )
    assert strict_calls == 1
    assert res_strict.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_request_error" for e in res_strict.errors)

    # 3. allow_additional_fields=True: fallback mode -> json_object + warning; strict -> fail 0 call
    open_schema = dict(invoice_schema_dict, allow_additional_fields=True)
    open_calls = 0

    def _capture_open(req: httpx.Request) -> httpx.Response:
        nonlocal open_calls
        open_calls += 1
        body = json.loads(req.content.decode("utf-8"))
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=_ok_invoice_payload())

    res_open_fb = _run_with_handler(doc, open_schema, _capture_open)
    assert open_calls == 1
    assert res_open_fb.status == ProcessingStatus.COMPLETED
    assert any(w.code == "structured_output_fallback" for w in res_open_fb.warnings)

    strict_open_calls = 0

    def _should_not_run(_req: httpx.Request) -> httpx.Response:
        nonlocal strict_open_calls
        strict_open_calls += 1
        return httpx.Response(200, json=_ok_invoice_payload())

    res_open_strict = _run_with_handler(
        doc,
        open_schema,
        _should_not_run,
        structured_output_mode=StructuredOutputMode.STRICT,
    )
    assert strict_open_calls == 0
    assert res_open_strict.status == ProcessingStatus.FAILED
    assert any(e.code == "strict_schema_incompatible" for e in res_open_strict.errors)


def test_fenced_json_recovery_and_recursive_nested_decimal_date_coercion(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "order.txt"
    doc.write_text("Order: ORD-1\nItem: Valve 49.99 due 2026-05-01\n", encoding="utf-8")
    nested_schema: dict[str, Any] = {
        "schema_id": "order-nested",
        "schema_version": "1.0",
        "fields": {
            "order_id": {"type": "string", "required": True},
            "items": {
                "type": "array",
                "required": True,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "required": True},
                        "price": {"type": "decimal", "required": True},
                        "due_date": {"type": "date", "required": True},
                    },
                },
            },
        },
    }
    raw_json = json.dumps(
        {
            "records": [
                {
                    "fields": {
                        "order_id": {
                            "presence": "present",
                            "value": "ORD-1",
                            "confidence": 0.99,
                            "ambiguous": False,
                            "evidence": [{"quote": "Order: ORD-1", "relationship": "direct"}],
                        },
                        "items": {
                            "presence": "present",
                            "value": [
                                {"name": "Valve", "price": "49.99", "due_date": "2026-05-01"}
                            ],
                            "confidence": 0.95,
                            "ambiguous": False,
                            "evidence": [
                                {
                                    "quote": "Item: Valve 49.99 due 2026-05-01",
                                    "relationship": "direct",
                                }
                            ],
                        },
                    }
                }
            ]
        }
    )
    fenced_content = f"Here is the extracted JSON:\n```json\n{raw_json}\n```"
    res = _run_with_handler(
        doc,
        nested_schema,
        lambda _req: httpx.Response(200, json=_ok_invoice_payload(content_override=fenced_content)),
    )
    assert res.status == ProcessingStatus.COMPLETED
    assert res.validation.status == ValidationStatus.VALID
    assert any(w.code == "model_output_sanitized" for w in res.warnings)
    item = res.records[0].fields["items"].value[0]
    assert item["price"] == Decimal("49.99")
    assert item["due_date"] == date(2026, 5, 1)


def test_bounded_retries_retry_after_deadline_and_malformed_recovery(
    fixtures_dir: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = fixtures_dir / "documents" / "txt" / "invoice_001.txt"

    # 1. 429 with Retry-After + malformed JSON -> recovers on attempt 3
    sleeps: list[float] = []
    attempt = 0

    def _flaky_handler(_req: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, text="rate limited")
        if attempt == 2:
            return httpx.Response(
                200,
                json={"choices": [{"finish_reason": "stop", "message": {"content": "{invalid"}}]},
            )
        return httpx.Response(200, json=_ok_invoice_payload())

    res = _run_with_handler(
        doc,
        invoice_schema_dict,
        _flaky_handler,
        retry_policy=RetryPolicy(
            max_retries=2,
            initial_backoff_seconds=0.5,
            max_backoff_seconds=8.0,
            jitter=False,
        ),
        sleep_fn=sleeps.append,
    )
    assert attempt == 3
    assert sleeps == [2.0, 1.0]
    assert res.status == ProcessingStatus.COMPLETED
    assert any(w.code == "provider_retry_succeeded" for w in res.warnings)

    # 2. Retry-After exceeding remaining max_total_seconds aborts immediately without sleeping
    deadline_sleeps: list[float] = []
    now_t = 100.0

    def _clock() -> float:
        return now_t

    res_deadline = _run_with_handler(
        doc,
        invoice_schema_dict,
        lambda _req: httpx.Response(429, headers={"Retry-After": "30"}, text="slow down"),
        retry_policy=RetryPolicy(max_retries=3, max_total_seconds=5.0),
        sleep_fn=deadline_sleeps.append,
        clock_fn=_clock,
    )
    assert deadline_sleeps == []
    assert res_deadline.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_rate_limited" for e in res_deadline.errors)

    # 3. Permanent 401 fails immediately with 0 retries and 0 sleeps
    auth_calls = 0
    auth_sleeps: list[float] = []

    def _auth_fail(_req: httpx.Request) -> httpx.Response:
        nonlocal auth_calls
        auth_calls += 1
        return httpx.Response(401, text="invalid key")

    res_auth = _run_with_handler(
        doc,
        invoice_schema_dict,
        _auth_fail,
        sleep_fn=auth_sleeps.append,
    )
    assert auth_calls == 1
    assert auth_sleeps == []
    assert res_auth.status == ProcessingStatus.FAILED
    assert any(e.code == "provider_auth_error" for e in res_auth.errors)


def test_input_budget_reject_truncate_and_disabled(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    doc = tmp_path / "long_invoice.txt"
    prefix = (
        "Invoice Number: INV-2026-001\n"
        "Vendor: Acme Industrial Supply Ltd.\n"
        "Issue Date: 2026-03-15\n"
        "Total Amount: 1250.50\n"
        "Currency: USD\n"
    )
    doc.write_text(prefix + ("Appendix filler line.\n" * 50), encoding="utf-8")

    # 1. REJECT makes zero outbound HTTP requests
    reject_calls = 0

    def _count_reject(_req: httpx.Request) -> httpx.Response:
        nonlocal reject_calls
        reject_calls += 1
        return httpx.Response(200, json=_ok_invoice_payload())

    res_reject = _run_with_handler(
        doc,
        invoice_schema_dict,
        _count_reject,
        input_budget=InputBudgetPolicy(
            max_input_chars=len(prefix) + 10,
            overflow_policy=InputBudgetOverflowPolicy.REJECT,
        ),
    )
    assert reject_calls == 0
    assert res_reject.status == ProcessingStatus.FAILED
    assert any(e.code == "input_budget_exceeded" for e in res_reject.errors)

    # 2. TRUNCATE extracts from clean prefix, marks PARTIAL, emits input_truncated, keeps provenance
    sent_bodies: list[str] = []

    def _capture_truncate(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content.decode("utf-8"))
        sent_bodies.append(body["messages"][1]["content"])
        return httpx.Response(200, json=_ok_invoice_payload())

    res_trunc = _run_with_handler(
        doc,
        invoice_schema_dict,
        _capture_truncate,
        input_budget=InputBudgetPolicy(
            max_input_chars=len(prefix) + 5,
            overflow_policy=InputBudgetOverflowPolicy.TRUNCATE,
        ),
    )
    assert len(sent_bodies) == 1
    assert "Appendix filler" not in sent_bodies[0]
    assert res_trunc.status == ProcessingStatus.PARTIAL
    assert any(w.code == "input_truncated" for w in res_trunc.warnings)
    assert len(res_trunc.records[0].fields["invoice_number"].provenance) == 1

    # 3. max_input_chars=None passes entire document unmodified -> COMPLETED without warning
    res_unbounded = _run_with_handler(
        doc,
        invoice_schema_dict,
        lambda _req: httpx.Response(200, json=_ok_invoice_payload()),
        input_budget=InputBudgetPolicy(max_input_chars=None),
    )
    assert res_unbounded.status == ProcessingStatus.COMPLETED
    assert not any(w.code == "input_truncated" for w in res_unbounded.warnings)
