"""Extraction strategy protocols, OpenAI-compatible LLM strategies, and test doubles."""

from __future__ import annotations

from structa_maestro.strategies.base import (
    AsyncExtractionStrategy,
    ExtractionStrategy,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
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
    "AsyncDeterministicTestStrategy",
    "AsyncExtractionStrategy",
    "AsyncOpenAICompatibleLLMStrategy",
    "DeterministicTestStrategy",
    "ExtractionStrategy",
    "OpenAICompatibleLLMStrategy",
    "ProposedEvidence",
    "ProposedField",
    "ProposedRecord",
    "StrategyOutput",
]
