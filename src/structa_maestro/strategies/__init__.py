"""Extraction strategy protocols, OpenAI/Anthropic/Gemini LLM strategies, and test doubles."""

from __future__ import annotations

from structa_maestro.strategies.base import (
    AsyncExtractionStrategy,
    ExtractionStrategy,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)
from structa_maestro.strategies.llm_anthropic import (
    AnthropicLLMStrategy,
    AsyncAnthropicLLMStrategy,
)
from structa_maestro.strategies.llm_gemini import (
    AsyncGeminiLLMStrategy,
    GeminiLLMStrategy,
)
from structa_maestro.strategies.llm_openai_compat import (
    AsyncOpenAICompatibleLLMStrategy,
    OpenAICompatibleLLMStrategy,
)
from structa_maestro.strategies.testing import (
    AsyncDeterministicTestStrategy,
    DeterministicTestStrategy,
)

__all__ = [
    "AnthropicLLMStrategy",
    "AsyncAnthropicLLMStrategy",
    "AsyncDeterministicTestStrategy",
    "AsyncExtractionStrategy",
    "AsyncGeminiLLMStrategy",
    "AsyncOpenAICompatibleLLMStrategy",
    "DeterministicTestStrategy",
    "ExtractionStrategy",
    "GeminiLLMStrategy",
    "OpenAICompatibleLLMStrategy",
    "ProposedEvidence",
    "ProposedField",
    "ProposedRecord",
    "StrategyOutput",
]
