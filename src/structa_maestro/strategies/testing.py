"""Built-in offline DeterministicTestStrategy for reproducible automated testing."""

from __future__ import annotations

from collections.abc import Callable

from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema
from structa_maestro.strategies.base import StrategyOutput

StrategyHandler = Callable[[PreparedDocument, ExtractionSchema], StrategyOutput]


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
