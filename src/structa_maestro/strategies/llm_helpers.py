"""Pure deterministic helpers for LLM strategy hardening (zero external dependencies)."""

from __future__ import annotations

import math
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

from structa_maestro.contracts.enums import FieldType
from structa_maestro.schema.models import FieldSpec
from structa_maestro.strategies.llm_config import InputBudgetPolicy, RetryPolicy

_UNTRUSTED_TAG_RE = re.compile(r"<(\s*/?\s*untrusted_document\b)", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n(.*?)\n?\s*```\s*$",
    re.DOTALL,
)
_STRUCTURED_OUTPUT_TOKENS: tuple[str, ...] = (
    "response_format",
    "json_schema",
    "structured output",
    "structured_output",
    "additionalproperties",
)
_UNSUPPORTED_HINTS: tuple[str, ...] = (
    "unsupported",
    "not supported",
    "does not support",
    "invalid_request",
    "unknown variant",
    "invalid value",
    "not allowed",
    "strict",
)


def default_rng() -> float:
    """Return a uniform float in [0.0, 1.0) using stdlib secrets (Bandit S311-safe)."""
    return secrets.randbelow(10_000) / 10_000.0


def neutralize_untrusted_delimiters(text: str) -> str:
    """Escape embedded <untrusted_document / </untrusted_document tags inside document text."""
    return _UNTRUSTED_TAG_RE.sub(r"&lt;\1", text)


def apply_input_budget(
    full_text: str,
    budget: InputBudgetPolicy,
) -> tuple[str, bool, int, int]:
    """Apply InputBudgetPolicy to prepared document text.

    Returns ``(retained_text, exceeded, included_chars, total_chars)``.
    When ``exceeded`` is True and ``budget.overflow_policy`` is ``TRUNCATE``,
    ``retained_text`` is deterministically truncated on a clean newline boundary
    when one exists in the second half of the budget, otherwise at ``max_input_chars``.
    """
    total_chars = len(full_text)
    limit = budget.max_input_chars
    if limit is None or total_chars <= limit:
        return full_text, False, total_chars, total_chars

    prefix = full_text[:limit]
    last_nl = prefix.rfind("\n")
    retained = prefix[:last_nl] if last_nl >= limit // 2 else prefix
    return retained, True, len(retained), total_chars


def parse_retry_after(
    header_value: str | None,
    *,
    now_epoch: float | None = None,
) -> float | None:
    """Parse HTTP Retry-After header (seconds or RFC 7231 HTTP-date) into non-negative seconds."""
    if not header_value or not isinstance(header_value, str):
        return None
    raw = header_value.strip()
    if not raw:
        return None

    # 1. Numeric delay-seconds
    try:
        seconds = float(raw)
    except ValueError:
        seconds = math.nan
    if math.isfinite(seconds):
        return seconds if seconds >= 0.0 else None

    # 2. HTTP-date (RFC 7231)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now_ts = time.time() if now_epoch is None else now_epoch
    delta = dt.timestamp() - datetime.fromtimestamp(now_ts, tz=UTC).timestamp()
    return max(0.0, delta)


def compute_backoff_delay(
    attempt: int,
    policy: RetryPolicy,
    *,
    retry_after: float | None = None,
    remaining_budget: float | None = None,
    rng: Callable[[], float] | None = None,
) -> float | None:
    """Compute bounded retry delay in seconds, or None if waiting would exceed remaining_budget.

    Per Spec 003 U1: if ``remaining_budget`` is set and a valid parsed ``retry_after``
    meets or exceeds ``remaining_budget``, return ``None`` immediately instead of clamping
    and retrying prematurely.
    """
    if remaining_budget is not None and remaining_budget <= 0.0:
        return None

    if retry_after is not None:
        if remaining_budget is not None and retry_after >= remaining_budget:
            return None
        delay = min(policy.max_backoff_seconds, max(0.0, retry_after))
    else:
        exponent = max(0, attempt - 1)
        base = min(policy.max_backoff_seconds, policy.initial_backoff_seconds * (2**exponent))
        if policy.jitter and base > 0.0:
            draw = (rng or default_rng)()
            clamped_draw = min(1.0, max(0.0, float(draw)))
            delay = base * (0.5 + 0.5 * clamped_draw)
        else:
            delay = base

    if remaining_budget is not None and delay >= remaining_budget:
        return None
    return delay


def extract_json_object_text(raw_content: str) -> tuple[str, bool]:
    """Recover top-level JSON object text from Markdown fences or surrounding prose.

    Returns ``(candidate_json_text, sanitized)`` without mutating any values inside the JSON.
    """
    stripped = raw_content.strip()
    if not stripped:
        return stripped, False

    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped, False

    fenced = _CODE_FENCE_RE.match(stripped)
    if fenced is not None:
        inner = fenced.group(1).strip()
        if inner.startswith("{") and inner.endswith("}"):
            return inner, True
        stripped = inner

    # Locate the first balanced top-level JSON object using a string-aware brace scanner.
    start_idx = stripped.find("{")
    if start_idx == -1:
        return raw_content.strip(), False

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start_idx, len(stripped)):
        ch = stripped[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start_idx : idx + 1]
                was_modified = candidate != raw_content.strip()
                return candidate, was_modified

    return raw_content.strip(), False


def coerce_llm_json_value(value: Any, spec: FieldSpec | None) -> Any:
    """Recursively convert clean JSON-wire Decimal/date values without mutating invalid inputs."""
    if value is None or spec is None:
        return value

    if spec.field_type == FieldType.DECIMAL:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return Decimal(str(value))
        if isinstance(value, str):
            try:
                return Decimal(value.strip())
            except InvalidOperation:
                return value
        return value

    if spec.field_type == FieldType.DATE:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                return value
        return value

    if spec.field_type == FieldType.OBJECT and isinstance(value, dict) and spec.properties:
        return {
            k: coerce_llm_json_value(v, spec.properties.get(k)) if isinstance(k, str) else v
            for k, v in value.items()
        }

    if spec.field_type == FieldType.ARRAY and isinstance(value, list) and spec.items is not None:
        return [coerce_llm_json_value(item, spec.items) for item in value]

    return value


def is_structured_output_unsupported(status_code: int, response_text: str) -> bool:
    """Detect HTTP 400/422 responses specifically rejecting strict json_schema response_format."""
    if status_code not in (400, 422):
        return False
    lowered = response_text.lower()
    if not any(tok in lowered for tok in _STRUCTURED_OUTPUT_TOKENS):
        return False
    return any(hint in lowered for hint in _UNSUPPORTED_HINTS) or "json_schema" in lowered
