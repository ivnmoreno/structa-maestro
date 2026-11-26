"""Unit tests for GeminiLLMStrategy, AsyncGeminiLLMStrategy, scrubbing, and usage mapping (T005)."""

from __future__ import annotations

import math

import httpx
import pytest

from structa_maestro import (
    AsyncExtractionStrategy,
    AsyncGeminiLLMStrategy,
    ConfigurationError,
    ExtractionEngine,
    ExtractionStrategy,
    GeminiLLMStrategy,
    InputBudgetPolicy,
    RetryPolicy,
    SecretValue,
    StructuredOutputMode,
    TokenUsage,
)
from structa_maestro.config import sanitize_message
from structa_maestro.telemetry import parse_gemini_token_usage


def test_gemini_strategies_protocols_url_model_normalization_and_redacted_repr() -> None:
    secret = "AIzaSyUnitTestSecretValue1234567890"
    sync_s = GeminiLLMStrategy(
        model="models/gemini-2.5-flash",
        api_key=SecretValue(secret),
        base_url="https://generativelanguage.googleapis.com/",
    )
    async_s = AsyncGeminiLLMStrategy(
        model="gemini-2.5-flash",
        api_key=secret,
        base_url="https://generativelanguage.googleapis.com/v1beta/",
    )

    assert isinstance(sync_s, ExtractionStrategy)
    assert not isinstance(sync_s, AsyncExtractionStrategy)
    assert isinstance(async_s, AsyncExtractionStrategy)
    assert not isinstance(async_s, ExtractionStrategy)

    assert sync_s.name == "gemini-llm:gemini-2.5-flash"
    assert async_s.name == "gemini-llm:gemini-2.5-flash"
    expected_ep = (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert sync_s.generate_content_endpoint == expected_ep
    assert async_s.generate_content_endpoint == expected_ep
    assert sync_s.max_output_tokens == 4096
    assert sync_s.structured_output_mode == StructuredOutputMode.STRICT_WITH_FALLBACK
    assert isinstance(sync_s.retry_policy, RetryPolicy)
    assert isinstance(sync_s.input_budget, InputBudgetPolicy)

    assert secret not in repr(sync_s)
    assert secret not in repr(async_s)
    assert "SecretValue('***')" in repr(sync_s)

    # Sync ExtractionEngine.extract eagerly rejects AsyncGeminiLLMStrategy
    engine = ExtractionEngine(async_s)
    with pytest.raises(ConfigurationError, match="AsyncExtractionStrategy only"):
        engine.extract(
            "x.txt",
            {"schema_id": "s", "schema_version": "1.0", "fields": {"a": {"type": "string"}}},
        )


def test_gemini_strategies_eager_config_validation() -> None:
    # Model validation (blank, non-str, empty after models/, URL-unsafe characters)
    for bad_model in (
        "   ",
        "models/",
        "gemini 2.5",
        "gemini-2.5-flash?key=AIzaSyBad1234567890",
        "gemini-2.5-flash#frag",
        "../gemini-2.5-flash",
        "models/extra/gemini-2.5-flash",
        123,
    ):
        with pytest.raises(ConfigurationError):
            GeminiLLMStrategy(model=bad_model, api_key="AIzaSyTest123456789012")  # type: ignore[arg-type]

    # API key validation
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(model="gemini-2.5-flash", api_key="   ")

    # Base URL validation (scheme, netloc, query/fragment never echoing leaked key)
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            base_url=123,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            base_url="ftp://generativelanguage.googleapis.com",
        )
    leaked_query_key = "AIzaSyQueryLeakSecret999888777"
    with pytest.raises(ConfigurationError) as exc_q:
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            base_url=f"https://generativelanguage.googleapis.com?key={leaked_query_key}",
        )
    assert leaked_query_key not in str(exc_q.value)
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            base_url="https://generativelanguage.googleapis.com#section",
        )

    # Numeric parameter bounds (timeout, max_output_tokens, temperature, bool/non-finite rejection)
    for bad_timeout in (0, -1.0, math.nan, math.inf, True):
        with pytest.raises(ConfigurationError):
            GeminiLLMStrategy(
                model="gemini-2.5-flash",
                api_key="AIzaSyTest123456789012",
                timeout_seconds=bad_timeout,
            )
    for bad_max in (0, -10, True, 1.5):
        with pytest.raises(ConfigurationError):
            GeminiLLMStrategy(
                model="gemini-2.5-flash",
                api_key="AIzaSyTest123456789012",
                max_output_tokens=bad_max,  # type: ignore[arg-type]
            )
    for bad_temp in (-0.1, 2.1, math.nan, math.inf, True):
        with pytest.raises(ConfigurationError):
            GeminiLLMStrategy(
                model="gemini-2.5-flash",
                api_key="AIzaSyTest123456789012",
                temperature=bad_temp,
            )
    # Boundary temperatures 0.0 and 2.0 are valid for Gemini
    s_max_temp = GeminiLLMStrategy(
        model="gemini-2.5-flash",
        api_key="AIzaSyTest123456789012",
        temperature=2.0,
    )
    assert s_max_temp.name == "gemini-llm:gemini-2.5-flash"

    # Policy & client type checks
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            structured_output_mode="invalid-mode",
        )
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            retry_policy="bad",  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            input_budget="bad",  # type: ignore[arg-type]
        )
    with httpx.Client() as sync_client, pytest.raises(ConfigurationError):
        AsyncGeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            http_client=sync_client,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError):
        GeminiLLMStrategy(
            model="gemini-2.5-flash",
            api_key="AIzaSyTest123456789012",
            http_client=object(),  # type: ignore[arg-type]
        )


def test_sanitize_message_scrubs_gemini_credentials() -> None:
    aiza_key = "AIzaSyDemoSecretToken1234567890"
    custom_key = "custom_goog_header_secret_999"
    query_key = "query_secret_value_888777"

    msg_hdr = sanitize_message(f"Header failed x-goog-api-key: {custom_key}")
    assert custom_key not in msg_hdr
    assert "[REDACTED]" in msg_hdr

    msg_json_hdr = sanitize_message(f'{{"x-goog-api-key": "{custom_key}"}}')
    assert custom_key not in msg_json_hdr

    msg_aiza = sanitize_message(f"Upstream rejected token {aiza_key} during request")
    assert aiza_key not in msg_aiza
    assert "[REDACTED]" in msg_aiza

    msg_q1 = sanitize_message(
        f"POST https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={query_key}&alt=json"
    )
    assert query_key not in msg_q1
    assert "?key=[REDACTED]&alt=json" in msg_q1

    msg_q2 = sanitize_message(f"GET https://example.test/api?foo=1&key={query_key}")
    assert query_key not in msg_q2
    assert "&key=[REDACTED]" in msg_q2

    # Non-secret words ending in key= without URL query prefix remain untouched
    assert sanitize_message("monkey=banana") == "monkey=banana"


def test_parse_gemini_token_usage_epistemic_honesty() -> None:
    assert parse_gemini_token_usage("bad") is None
    assert parse_gemini_token_usage({}) is None
    assert parse_gemini_token_usage({"usageMetadata": "bad"}) is None
    assert (
        parse_gemini_token_usage(
            {
                "usageMetadata": {
                    "promptTokenCount": -1,
                    "candidatesTokenCount": True,
                    "totalTokenCount": "100",
                }
            }
        )
        is None
    )

    # Full counts preserved exactly
    full = parse_gemini_token_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 210,
                "candidatesTokenCount": 85,
                "totalTokenCount": 295,
            }
        }
    )
    assert full == TokenUsage(prompt_tokens=210, completion_tokens=85, total_tokens=295)

    # Partial counts leave omitted fields as None (never synthesizes missing values)
    partial = parse_gemini_token_usage(
        {"usageMetadata": {"promptTokenCount": 140, "totalTokenCount": 190}}
    )
    assert partial == TokenUsage(prompt_tokens=140, completion_tokens=None, total_tokens=190)

    # Zero counts preserved honestly
    zeros = parse_gemini_token_usage(
        {
            "usageMetadata": {
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "totalTokenCount": 0,
            }
        }
    )
    assert zeros == TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
