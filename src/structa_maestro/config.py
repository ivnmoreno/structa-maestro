"""Configuration models, resource limits, secret masking, and privacy-safe logging helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

from structa_maestro.exceptions import ConfigurationError

_DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
_DEFAULT_MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB
_DEFAULT_MAX_PAGES = 200

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?)([^\s'\",;}{]{6,})"),
    re.compile(r"(?i)(authorization\s*[:=]\s*['\"]?)([^\s'\",;}{]{6,})"),
)


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    """Configurable safety limits enforced during document input and parsing.

    Canonical fields per specs/001-structured-extraction-mvp/data-model.md Section 2.1:
    - DocumentLimits.max_file_bytes
    - DocumentLimits.max_decompressed_bytes
    - DocumentLimits.max_pages
    """

    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    max_decompressed_bytes: int = _DEFAULT_MAX_DECOMPRESSED_BYTES
    max_pages: int = _DEFAULT_MAX_PAGES

    def __post_init__(self) -> None:
        if isinstance(self.max_file_bytes, bool) or self.max_file_bytes <= 0:
            raise ConfigurationError("DocumentLimits.max_file_bytes must be a positive integer.")
        if isinstance(self.max_decompressed_bytes, bool) or self.max_decompressed_bytes <= 0:
            raise ConfigurationError(
                "DocumentLimits.max_decompressed_bytes must be a positive integer."
            )
        if isinstance(self.max_pages, bool) or self.max_pages <= 0:
            raise ConfigurationError("DocumentLimits.max_pages must be a positive integer.")


class SecretValue:
    """Redacted container for API keys and credentials preventing accidental leakage."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError("SecretValue requires a non-empty string.")
        self._value = value

    def get_secret_value(self) -> str:
        """Return the underlying raw secret value for authorized internal use."""
        return self._value

    def __repr__(self) -> str:
        return "SecretValue('***')"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            return self._value == other._value
        return False

    def __hash__(self) -> int:
        return hash(self._value)


def sanitize_message(
    message: str,
    *,
    secrets: Iterable[str] = (),
    max_length: int = 500,
) -> str:
    """Scrub known secrets and credential patterns from diagnostic messages."""
    sanitized = message
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_PATTERNS[1].sub("[REDACTED]", sanitized)
    sanitized = _SECRET_PATTERNS[2].sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_PATTERNS[3].sub(r"\1[REDACTED]", sanitized)
    if len(sanitized) > max_length:
        sanitized = sanitized[: max_length - 3] + "..."
    return sanitized


def get_logger(name: str) -> logging.Logger:
    """Return a library logger configured with a NullHandler (no default document logging)."""
    root = logging.getLogger("structa_maestro")
    if not root.handlers:
        root.addHandler(logging.NullHandler())
    return logging.getLogger(name)
