"""Typed public exceptions for structa-maestro (contracts/python-api-and-cli.md Section 1.2)."""

from __future__ import annotations


class StructaMaestroError(Exception):
    """Base exception for configuration, schema definition, and contract deserialization errors."""


class SchemaDefinitionError(StructaMaestroError):
    """Raised before extraction when a user-supplied schema is invalid or unsupported."""


class ConfigurationError(StructaMaestroError):
    """Raised when engine, limits, or strategy configuration is invalid."""


class ContractDeserializationError(StructaMaestroError):
    """Raised when deserializing malformed or unsupported canonical output contract payloads."""
