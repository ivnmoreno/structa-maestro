"""Typed public exceptions for structa-maestro (contracts/python-api-and-cli.md Section 1.2)."""

from __future__ import annotations


class StructaMaestroError(Exception):
    """Base exception for configuration, schema, binding, and contract deserialization errors."""


class SchemaDefinitionError(StructaMaestroError):
    """Raised before extraction when a user-supplied schema is invalid or unsupported."""


class ConfigurationError(StructaMaestroError):
    """Raised when engine, limits, or strategy configuration is invalid."""


class ContractDeserializationError(StructaMaestroError):
    """Raised when deserializing malformed or unsupported canonical output contract payloads."""


class RecordBindingError(StructaMaestroError):
    """Raised when binding canonical extraction records to a typed Pydantic model fails.

    Messages and ``issues`` identify failing record/field locations and sanitized reasons
    without embedding raw extracted field values or document secrets.
    """

    def __init__(
        self,
        message: str,
        *,
        record_id: str | None = None,
        issues: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.record_id: str | None = record_id
        self.issues: tuple[str, ...] = tuple(issues)
