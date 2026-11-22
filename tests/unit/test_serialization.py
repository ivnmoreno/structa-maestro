"""Unit tests for canonical serialization helpers."""

from __future__ import annotations

import pytest

from structa_maestro.contracts.enums import ProcessingStatus, ValidationStatus
from structa_maestro.contracts.models import ExtractionResult, SchemaMetadata, ValidationOutcome
from structa_maestro.contracts.serialization import (
    deserialize_result_from_dict,
    deserialize_result_from_json,
    serialize_result_to_dict,
    serialize_result_to_json,
)
from structa_maestro.exceptions import ContractDeserializationError


def test_serialize_and_deserialize_minimal_result() -> None:
    result = ExtractionResult(
        status=ProcessingStatus.COMPLETED,
        source=None,
        schema_metadata=SchemaMetadata(schema_id="s1", schema_version="1.0"),
        records=[],
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )
    d = serialize_result_to_dict(result)
    assert d["schema"] == {"schema_id": "s1", "schema_version": "1.0"}
    assert deserialize_result_from_dict(d) == result

    j = serialize_result_to_json(result, indent=2)
    assert deserialize_result_from_json(j) == result


def test_empty_json_string_raises_deserialization_error() -> None:
    with pytest.raises(ContractDeserializationError):
        deserialize_result_from_json("   ")
