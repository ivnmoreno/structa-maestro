"""Unit tests for AnthropicLLMStrategy, AsyncAnthropicLLMStrategy, and usage mapping (T003)."""

from __future__ import annotations

import httpx
import pytest

from structa_maestro import (
    AnthropicLLMStrategy,
    AsyncAnthropicLLMStrategy,
    AsyncExtractionStrategy,
    ConfigurationError,
    ExtractionEngine,
    ExtractionStrategy,
    SecretValue,
    TokenUsage,
)
from structa_maestro.telemetry import parse_anthropic_token_usage


def test_anthropic_strategies_protocols_url_normalization_and_redacted_repr() -> None:
    secret = "sk-ant-api03-unit-test-secret-999999"
    sync_s = AnthropicLLMStrategy(
        model="claude-sonnet-4-5",
        api_key=SecretValue(secret),
        base_url="https://api.anthropic.com/",
    )
    async_s = AsyncAnthropicLLMStrategy(
        model="claude-sonnet-4-5",
        api_key=secret,
        base_url="https://api.anthropic.com/v1/",
    )

    assert isinstance(sync_s, ExtractionStrategy)
    assert not isinstance(sync_s, AsyncExtractionStrategy)
    assert isinstance(async_s, AsyncExtractionStrategy)
    assert not isinstance(async_s, ExtractionStrategy)

    assert sync_s.name == "anthropic-llm:claude-sonnet-4-5"
    assert async_s.name == "anthropic-llm:claude-sonnet-4-5"
    assert sync_s.messages_endpoint == "https://api.anthropic.com/v1/messages"
    assert async_s.messages_endpoint == "https://api.anthropic.com/v1/messages"
    assert sync_s.max_tokens == 4096
    assert sync_s.anthropic_version == "2023-06-01"

    assert secret not in repr(sync_s)
    assert secret not in repr(async_s)
    assert "SecretValue('***')" in repr(sync_s)

    # Sync ExtractionEngine.extract eagerly rejects AsyncAnthropicLLMStrategy
    engine = ExtractionEngine(async_s)
    with pytest.raises(ConfigurationError, match="AsyncExtractionStrategy only"):
        engine.extract(
            "x.txt",
            {"schema_id": "s", "schema_version": "1.0", "fields": {"a": {"type": "string"}}},
        )


def test_anthropic_strategies_eager_config_validation() -> None:
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="   ", api_key="sk-ant-123456")
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="   ")
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", base_url="ftp://x")
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", timeout_seconds=0)
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", max_tokens=0)
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", max_tokens=True)
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", temperature=1.5)
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", temperature=True)
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(model="claude-sonnet-4-5", api_key="sk-ant-123", anthropic_version=" ")

    with httpx.Client() as sync_client, pytest.raises(ConfigurationError):
        AsyncAnthropicLLMStrategy(
            model="claude-sonnet-4-5",
            api_key="sk-ant-123",
            http_client=sync_client,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigurationError):
        AnthropicLLMStrategy(
            model="claude-sonnet-4-5",
            api_key="sk-ant-123",
            http_client=object(),  # type: ignore[arg-type]
        )


def test_parse_anthropic_token_usage_epistemic_honesty() -> None:
    assert parse_anthropic_token_usage("bad") is None
    assert parse_anthropic_token_usage({}) is None
    assert (
        parse_anthropic_token_usage({"usage": {"input_tokens": -1, "output_tokens": True}}) is None
    )

    # Leaves omitted total_tokens as None (never fabricates sum)
    u = parse_anthropic_token_usage({"usage": {"input_tokens": 120, "output_tokens": 45}})
    assert u == TokenUsage(prompt_tokens=120, completion_tokens=45, total_tokens=None)

    # Zero tokens preserved honestly
    u0 = parse_anthropic_token_usage({"usage": {"input_tokens": 0, "output_tokens": 0}})
    assert u0 == TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=None)
