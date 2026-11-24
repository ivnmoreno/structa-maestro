"""Unit tests for AsyncExtractionStrategy, AsyncDeterministicTestStrategy & async LLM config."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from structa_maestro import (
    AsyncDeterministicTestStrategy,
    AsyncExtractionStrategy,
    AsyncOpenAICompatibleLLMStrategy,
    ConfigurationError,
    ExtractionEngine,
    ExtractionSchema,
    PreparedDocument,
    SecretValue,
    StrategyOutput,
)


def test_async_strategy_protocol_and_deterministic_test_strategy() -> None:
    static_out = StrategyOutput(records=())
    strat = AsyncDeterministicTestStrategy(static_out, name="offline-async")
    assert isinstance(strat, AsyncExtractionStrategy)
    assert strat.name == "offline-async"

    async def _handler(_doc: PreparedDocument, _schema: ExtractionSchema) -> StrategyOutput:
        await asyncio.sleep(0)
        return static_out

    callable_strat = AsyncDeterministicTestStrategy(_handler)
    assert isinstance(callable_strat, AsyncExtractionStrategy)
    assert callable_strat.name == "async-deterministic-test"

    with pytest.raises(ConfigurationError):
        AsyncDeterministicTestStrategy("not-callable-or-output")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        AsyncDeterministicTestStrategy(static_out, name="   ")


def test_async_llm_strategy_eager_validation_repr_and_sync_engine_guard() -> None:
    # Rejects sync httpx.Client eagerly
    with httpx.Client() as sync_client, pytest.raises(ConfigurationError, match="AsyncClient"):
        AsyncOpenAICompatibleLLMStrategy(
            model="gpt-4o-mini",
            api_key="sk-test-key-12345",
            http_client=sync_client,  # type: ignore[arg-type]
        )

    # SecretValue redacted in repr/str
    strategy = AsyncOpenAICompatibleLLMStrategy(
        model="gpt-4o-mini",
        api_key=SecretValue("sk-async-secret-999999"),
    )
    assert isinstance(strategy, AsyncExtractionStrategy)
    assert strategy.name == "openai-compatible-llm:gpt-4o-mini"
    assert "sk-async-secret" not in repr(strategy)
    assert "sk-async-secret" not in str(strategy)

    # Calling synchronous extract/extract_batch on an engine with async-only strategy raises eagerly
    engine = ExtractionEngine(strategy=strategy)
    schema = {
        "schema_id": "s",
        "schema_version": "1.0",
        "fields": {"name": {"type": "string", "required": True}},
    }
    with pytest.raises(ConfigurationError, match="aextract"):
        engine.extract("nonexistent.txt", schema)
    with pytest.raises(ConfigurationError, match="aextract_batch"):
        engine.extract_batch(["nonexistent.txt"], schema)

    # Invalid strategy passed to ExtractionEngine raises ConfigurationError
    with pytest.raises(ConfigurationError):
        ExtractionEngine(strategy=object())  # type: ignore[arg-type]
