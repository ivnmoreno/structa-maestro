"""Schema definition models, declarative/Pydantic loaders, and JSON serializers."""

from __future__ import annotations

from structa_maestro.schema.loader import load_schema_from_dict, load_schema_from_json_file
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.schema.pydantic_adapter import load_schema_from_pydantic
from structa_maestro.schema.serializer import serialize_schema_to_dict, serialize_schema_to_json

__all__ = [
    "ExtractionSchema",
    "FieldConstraints",
    "FieldSpec",
    "load_schema_from_dict",
    "load_schema_from_json_file",
    "load_schema_from_pydantic",
    "serialize_schema_to_dict",
    "serialize_schema_to_json",
]
