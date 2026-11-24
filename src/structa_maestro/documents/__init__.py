"""Document input descriptors, safe loaders, and prepared document models."""

from __future__ import annotations

from structa_maestro.documents.inputs import (
    SUPPORTED_MEDIA_TYPES,
    DocumentInput,
    DocumentInputKind,
    normalize_media_type,
    sanitize_filename,
)
from structa_maestro.documents.loader import (
    DocumentLoadResult,
    load_document,
    load_document_input,
    read_stream_bounded,
)
from structa_maestro.documents.models import DocumentSegment, PreparedDocument

__all__ = [
    "SUPPORTED_MEDIA_TYPES",
    "DocumentInput",
    "DocumentInputKind",
    "DocumentLoadResult",
    "DocumentSegment",
    "PreparedDocument",
    "load_document",
    "load_document_input",
    "normalize_media_type",
    "read_stream_bounded",
    "sanitize_filename",
]
