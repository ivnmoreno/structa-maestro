"""Built-in offline deterministic test strategies (sync and async) for automated testing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from structa_maestro.documents.models import PreparedDocument
from structa_maestro.exceptions import ConfigurationError
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import StrategyOutput

StrategyHandler = Callable[[PreparedDocument, ExtractionSchema], StrategyOutput]
AsyncStrategyHandler = Callable[[PreparedDocument, ExtractionSchema], Awaitable[StrategyOutput]]


class DeterministicTestStrategy:
    """Deterministic offline extraction strategy for unit, contract, and integration testing."""

    def __init__(
        self,
        handler: StrategyHandler | StrategyOutput,
        *,
        name: str = "deterministic-test",
    ) -> None:
        self._handler = handler
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def extract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        if isinstance(self._handler, StrategyOutput):
            return self._handler
        return self._handler(document, schema)


class AsyncDeterministicTestStrategy:
    """Deterministic offline asyncio extraction strategy implementing AsyncExtractionStrategy."""

    def __init__(
        self,
        handler: AsyncStrategyHandler | StrategyOutput,
        *,
        name: str = "async-deterministic-test",
    ) -> None:
        if not isinstance(handler, StrategyOutput) and not callable(handler):
            raise ConfigurationError(
                "AsyncDeterministicTestStrategy handler must be a StrategyOutput or async callable."
            )
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("AsyncDeterministicTestStrategy name must be a non-empty str.")
        self._handler = handler
        self._name = name.strip()

    @property
    def name(self) -> str:
        return self._name

    async def aextract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        if isinstance(self._handler, StrategyOutput):
            return self._handler
        return await self._handler(document, schema)
