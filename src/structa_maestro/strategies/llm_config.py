"""Immutable configuration models and enums for OpenAICompatibleLLMStrategy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from structa_maestro.exceptions import ConfigurationError

DEFAULT_MAX_INPUT_CHARS = 120_000


class StructuredOutputMode(StrEnum):
    """Structured output enforcement mode for OpenAI-compatible Chat Completions."""

    STRICT_WITH_FALLBACK = "strict_with_fallback"
    STRICT = "strict"
    JSON_OBJECT = "json_object"


class InputBudgetOverflowPolicy(StrEnum):
    """Action taken when prepared document text exceeds InputBudgetPolicy.max_input_chars."""

    REJECT = "reject"
    TRUNCATE = "truncate"


def parse_structured_output_mode(value: StructuredOutputMode | str) -> StructuredOutputMode:
    """Validate and normalize a StructuredOutputMode enum or string."""
    if isinstance(value, StructuredOutputMode):
        return value
    if isinstance(value, str):
        try:
            return StructuredOutputMode(value.strip().lower())
        except ValueError as exc:
            valid = ", ".join(m.value for m in StructuredOutputMode)
            raise ConfigurationError(
                f"Invalid structured_output_mode {value!r}; expected one of: {valid}."
            ) from exc
    raise ConfigurationError(
        "structured_output_mode must be a StructuredOutputMode or valid string."
    )


def parse_overflow_policy(value: InputBudgetOverflowPolicy | str) -> InputBudgetOverflowPolicy:
    """Validate and normalize an InputBudgetOverflowPolicy enum or string."""
    if isinstance(value, InputBudgetOverflowPolicy):
        return value
    if isinstance(value, str):
        try:
            return InputBudgetOverflowPolicy(value.strip().lower())
        except ValueError as exc:
            valid = ", ".join(p.value for p in InputBudgetOverflowPolicy)
            raise ConfigurationError(
                f"Invalid overflow_policy {value!r}; expected one of: {valid}."
            ) from exc
    raise ConfigurationError(
        "overflow_policy must be an InputBudgetOverflowPolicy or valid string."
    )


def parse_max_input_chars_arg(raw: str) -> int | None:
    """Parse CLI --max-input-chars accepting a positive int or case-insensitive 'none'."""
    cleaned = raw.strip()
    if cleaned.lower() == "none":
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise ConfigurationError(
            f"Invalid --max-input-chars value {raw!r}; expected a positive integer or 'none'."
        ) from exc
    if parsed <= 0:
        raise ConfigurationError("InputBudgetPolicy.max_input_chars must be a positive integer.")
    return parsed


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry configuration for transient provider and output failures."""

    max_retries: int = 2
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    jitter: bool = True
    retry_on_malformed_output: bool = True
    max_total_seconds: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise ConfigurationError("RetryPolicy.max_retries must be an integer >= 0.")
        if self.max_retries < 0:
            raise ConfigurationError("RetryPolicy.max_retries must be an integer >= 0.")

        if (
            isinstance(self.initial_backoff_seconds, bool)
            or not isinstance(self.initial_backoff_seconds, (int, float))
            or not math.isfinite(self.initial_backoff_seconds)
            or self.initial_backoff_seconds < 0.0
        ):
            raise ConfigurationError(
                "RetryPolicy.initial_backoff_seconds must be a finite number >= 0.0."
            )
        object.__setattr__(self, "initial_backoff_seconds", float(self.initial_backoff_seconds))

        if (
            isinstance(self.max_backoff_seconds, bool)
            or not isinstance(self.max_backoff_seconds, (int, float))
            or not math.isfinite(self.max_backoff_seconds)
            or self.max_backoff_seconds < self.initial_backoff_seconds
        ):
            raise ConfigurationError(
                "RetryPolicy.max_backoff_seconds must be a finite number "
                ">= initial_backoff_seconds."
            )
        object.__setattr__(self, "max_backoff_seconds", float(self.max_backoff_seconds))

        if not isinstance(self.jitter, bool):
            raise ConfigurationError("RetryPolicy.jitter must be a boolean.")
        if not isinstance(self.retry_on_malformed_output, bool):
            raise ConfigurationError("RetryPolicy.retry_on_malformed_output must be a boolean.")

        if self.max_total_seconds is not None:
            if (
                isinstance(self.max_total_seconds, bool)
                or not isinstance(self.max_total_seconds, (int, float))
                or not math.isfinite(self.max_total_seconds)
                or self.max_total_seconds <= 0.0
            ):
                raise ConfigurationError(
                    "RetryPolicy.max_total_seconds must be a finite number > 0.0 or None."
                )
            object.__setattr__(self, "max_total_seconds", float(self.max_total_seconds))


@dataclass(frozen=True, slots=True)
class InputBudgetPolicy:
    """Configurable character-based input budget and overflow handling policy."""

    max_input_chars: int | None = DEFAULT_MAX_INPUT_CHARS
    overflow_policy: InputBudgetOverflowPolicy = InputBudgetOverflowPolicy.REJECT

    def __post_init__(self) -> None:
        if self.max_input_chars is not None and (
            isinstance(self.max_input_chars, bool)
            or not isinstance(self.max_input_chars, int)
            or self.max_input_chars <= 0
        ):
            raise ConfigurationError(
                "InputBudgetPolicy.max_input_chars must be a positive integer or None."
            )
        normalized = parse_overflow_policy(self.overflow_policy)
        object.__setattr__(self, "overflow_policy", normalized)
