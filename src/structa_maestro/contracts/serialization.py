"""Lossless JSON/dict serialization and strict deserialization for Canonical Contract v1.0."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from structa_maestro.contracts.enums import FieldType
from structa_maestro.contracts.models import CONTRACT_VERSION, ExtractionResult
from structa_maestro.exceptions import ContractDeserializationError

_REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "extraction_id",
        "status",
        "source",
        "schema",
        "records",
        "validation",
        "errors",
        "warnings",
    }
)


def _encode_json_value(value: Any) -> Any:
    """Recursively convert Python scalar types into lossless JSON-native values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_encode_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_encode_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _encode_json_value(v) for k, v in value.items()}
    return str(value)


def _restore_typed_field_value(value: Any, value_type: str | None) -> Any:
    """Restore Decimal and date Python types on deserialization when value_type is declared."""
    if value is None or value_type is None:
        return value
    if value_type == FieldType.DECIMAL.value:
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            try:
                return Decimal(str(value))
            except InvalidOperation:
                return value
        if isinstance(value, float):
            return Decimal(str(value))
        return value
    if value_type == FieldType.DATE.value:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        return value
    return value


def serialize_result_to_dict(result: ExtractionResult) -> dict[str, Any]:
    """Convert an ExtractionResult to a canonical JSON-compatible dictionary."""
    raw = result.model_dump(mode="python", by_alias=True)
    # Encode enums & special scalar values cleanly for JSON schema compliance
    encoded = _normalize_for_json_dict(raw)
    if not isinstance(encoded, dict):
        raise ContractDeserializationError("Serialized canonical result must be a dictionary.")
    return encoded


def _normalize_for_json_dict(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_normalize_for_json_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _normalize_for_json_dict(v) for k, v in obj.items()}
    return _encode_json_value(obj)


def serialize_result_to_json(result: ExtractionResult, *, indent: int | None = None) -> str:
    """Serialize an ExtractionResult to canonical UTF-8 JSON preserving exact field meaning."""
    payload = serialize_result_to_dict(result)
    return json.dumps(payload, ensure_ascii=False, indent=indent, allow_nan=False)


def deserialize_result_from_dict(data: Any) -> ExtractionResult:
    """Strictly deserialize and validate a canonical dictionary into ExtractionResult."""
    if not isinstance(data, dict):
        raise ContractDeserializationError("Canonical result payload must be a JSON object.")

    missing = _REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        missing_str = ", ".join(sorted(missing))
        raise ContractDeserializationError(
            f"Missing required canonical contract properties: {missing_str}"
        )

    version = data.get("contract_version")
    if version != CONTRACT_VERSION:
        raise ContractDeserializationError(
            f"Unsupported contract_version {version!r}; expected {CONTRACT_VERSION!r}."
        )

    # Restore typed scalar values (Decimal / date) on fields before Pydantic validation
    prepared = dict(data)
    raw_records = prepared.get("records")
    if isinstance(raw_records, list):
        restored_records: list[Any] = []
        for rec in raw_records:
            if isinstance(rec, dict) and isinstance(rec.get("fields"), dict):
                new_fields: dict[str, Any] = {}
                for fname, fdata in rec["fields"].items():
                    if isinstance(fdata, dict):
                        fcopy = dict(fdata)
                        fcopy["value"] = _restore_typed_field_value(
                            fcopy.get("value"), fcopy.get("value_type")
                        )
                        new_fields[fname] = fcopy
                    else:
                        new_fields[fname] = fdata
                rec_copy = dict(rec)
                rec_copy["fields"] = new_fields
                restored_records.append(rec_copy)
            else:
                restored_records.append(rec)
        prepared["records"] = restored_records

    try:
        return ExtractionResult.model_validate(prepared)
    except PydanticValidationError as exc:
        raise ContractDeserializationError(
            f"Canonical result failed contract validation: {exc.error_count()} error(s)."
        ) from exc


def deserialize_result_from_json(raw_json: str) -> ExtractionResult:
    """Strictly parse and deserialize canonical JSON into ExtractionResult."""
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise ContractDeserializationError("Canonical JSON input must be a non-empty string.")
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ContractDeserializationError(
            f"Invalid JSON syntax in canonical result at line {exc.lineno}, col {exc.colno}."
        ) from exc
    return deserialize_result_from_dict(parsed)
