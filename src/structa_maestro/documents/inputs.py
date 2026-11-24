"""Immutable in-memory and path-based document input descriptors and sanitizers."""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO

from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.exceptions import ConfigurationError

SUPPORTED_MEDIA_TYPES: dict[str, DocumentFormat] = {
    "text/plain": DocumentFormat.TXT,
    "application/pdf": DocumentFormat.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentFormat.DOCX,
    "text/html": DocumentFormat.HTML,
    "text/markdown": DocumentFormat.MARKDOWN,
    "message/rfc822": DocumentFormat.EML,
}


class DocumentInputKind(StrEnum):
    """Mutually exclusive origin kind for a DocumentInput descriptor."""

    PATH = "path"
    BYTES = "bytes"
    TEXT = "text"
    STREAM = "stream"


def sanitize_filename(filename: str | None) -> str | None:
    """Sanitize a caller-supplied filename to a safe basename only.

    - Rejects non-string values and embedded null bytes eagerly with ConfigurationError.
    - Strips POSIX and Windows directory prefixes (e.g. ``/a/b/c.pdf``, ``C:\\a\\b.docx``,
      ``../../etc/passwd``) so directory paths never leak into canonical metadata or logs.
    - Returns ``None`` when no safe basename remains (blank, ``/``, ``.``, ``..``, drive root).
    """
    if filename is None:
        return None
    if not isinstance(filename, str):
        raise ConfigurationError("filename must be a string or None.")
    if "\x00" in filename:
        raise ConfigurationError("filename cannot contain null bytes.")

    normalized = filename.replace("\\", "/").strip()
    if not normalized:
        return None

    parts = [segment.strip() for segment in normalized.split("/") if segment.strip()]
    if not parts:
        return None

    candidate = parts[-1]
    if candidate in (".", "..") or candidate.endswith(":"):
        return None
    return candidate


def normalize_media_type(media_type: str | None) -> str | None:
    """Normalize and validate an optional MIME media_type hint eagerly."""
    if media_type is None:
        return None
    if not isinstance(media_type, str) or not media_type.strip():
        raise ConfigurationError("media_type must be a non-empty MIME type string or None.")
    base_type = media_type.split(";", 1)[0].strip().lower()
    if base_type not in SUPPORTED_MEDIA_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
        raise ConfigurationError(
            f"Unsupported media_type hint {media_type!r}. Supported values: {allowed}."
        )
    return base_type


def _validate_optional_document_id(document_id: str | None) -> str | None:
    if document_id is None:
        return None
    if not isinstance(document_id, str) or not document_id.strip():
        raise ConfigurationError("document_id must be a non-empty string or None.")
    return document_id.strip()


def _validate_binary_stream(stream: Any) -> BinaryIO:
    if stream is None or isinstance(stream, (str, bytes, bytearray, memoryview, io.TextIOBase)):
        raise ConfigurationError("DocumentInput.from_stream requires a readable binary stream.")
    read_fn = getattr(stream, "read", None)
    if not callable(read_fn):
        raise ConfigurationError("DocumentInput.from_stream requires a stream with a read method.")
    if getattr(stream, "closed", False) is True:
        raise ConfigurationError("DocumentInput.from_stream cannot accept a closed stream.")
    readable_fn = getattr(stream, "readable", None)
    if callable(readable_fn):
        try:
            if not readable_fn():
                raise ConfigurationError(
                    "DocumentInput.from_stream requires a readable binary stream."
                )
        except ValueError as exc:
            raise ConfigurationError(
                "DocumentInput.from_stream cannot accept a closed or unusable stream."
            ) from exc
    return stream  # type: ignore[no-any-return]


@dataclass(frozen=True, slots=True, repr=False)
class DocumentInput:
    """Immutable descriptor for path, raw bytes, plain text, or binary stream extraction inputs."""

    kind: DocumentInputKind
    path: Path | None = None
    content: bytes | None = None
    text: str | None = None
    stream: BinaryIO | None = None
    filename: str | None = None
    document_id: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DocumentInputKind):
            raise ConfigurationError("DocumentInput.kind must be a DocumentInputKind value.")

        object.__setattr__(self, "document_id", _validate_optional_document_id(self.document_id))
        object.__setattr__(self, "media_type", normalize_media_type(self.media_type))
        sanitized_name = sanitize_filename(self.filename)
        object.__setattr__(self, "filename", sanitized_name)

        if self.kind == DocumentInputKind.PATH:
            if self.content is not None or self.text is not None or self.stream is not None:
                raise ConfigurationError(
                    "PATH DocumentInput cannot specify bytes, text, or stream."
                )
            raw_path: Any = self.path
            if isinstance(raw_path, str):
                if not raw_path.strip():
                    raise ConfigurationError("DocumentInput.path must be a non-empty file path.")
                if "\x00" in raw_path:
                    raise ConfigurationError("DocumentInput.path cannot contain null bytes.")
                resolved_path = Path(raw_path)
            elif isinstance(raw_path, Path):
                path_str = str(raw_path)
                if not path_str.strip():
                    raise ConfigurationError("DocumentInput.path must be a non-empty file path.")
                if "\x00" in path_str:
                    raise ConfigurationError("DocumentInput.path cannot contain null bytes.")
                resolved_path = raw_path
            else:
                raise ConfigurationError("DocumentInput.path must be a str or pathlib.Path.")
            object.__setattr__(self, "path", resolved_path)
            if sanitized_name is None:
                object.__setattr__(self, "filename", sanitize_filename(resolved_path.name))

        elif self.kind == DocumentInputKind.BYTES:
            if self.path is not None or self.text is not None or self.stream is not None:
                raise ConfigurationError(
                    "BYTES DocumentInput cannot specify path, text, or stream."
                )
            raw_content: Any = self.content
            if not isinstance(raw_content, (bytes, bytearray, memoryview)):
                raise ConfigurationError(
                    "DocumentInput.from_bytes requires bytes, bytearray, or memoryview."
                )
            # Snapshot mutable buffers to immutable bytes for thread safety & isolation
            object.__setattr__(self, "content", bytes(raw_content))

        elif self.kind == DocumentInputKind.TEXT:
            if self.path is not None or self.content is not None or self.stream is not None:
                raise ConfigurationError(
                    "TEXT DocumentInput cannot specify path, bytes, or stream."
                )
            if not isinstance(self.text, str):
                raise ConfigurationError("DocumentInput.from_text requires a str payload.")
            if self.media_type is not None and self.media_type not in (
                "text/plain",
                "text/html",
                "text/markdown",
                "message/rfc822",
            ):
                raise ConfigurationError(
                    "DocumentInput.from_text only supports text-based media_type values "
                    "('text/plain', 'text/html', 'text/markdown', 'message/rfc822') or None."
                )

        elif self.kind == DocumentInputKind.STREAM:
            if self.path is not None or self.content is not None or self.text is not None:
                raise ConfigurationError(
                    "STREAM DocumentInput cannot specify path, bytes, or text."
                )
            validated_stream = _validate_binary_stream(self.stream)
            object.__setattr__(self, "stream", validated_stream)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        filename: str | None = None,
        document_id: str | None = None,
        media_type: str | None = None,
    ) -> DocumentInput:
        """Create a DocumentInput referencing a filesystem path."""
        return cls(
            kind=DocumentInputKind.PATH,
            path=path,  # type: ignore[arg-type]
            filename=filename,
            document_id=document_id,
            media_type=media_type,
        )

    @classmethod
    def from_bytes(
        cls,
        content: bytes | bytearray | memoryview,
        *,
        filename: str | None = None,
        document_id: str | None = None,
        media_type: str | None = None,
    ) -> DocumentInput:
        """Create a DocumentInput from an immutable snapshot of raw binary content."""
        return cls(
            kind=DocumentInputKind.BYTES,
            content=content,  # type: ignore[arg-type]
            filename=filename,
            document_id=document_id,
            media_type=media_type,
        )

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        filename: str | None = None,
        document_id: str | None = None,
        media_type: str | None = None,
    ) -> DocumentInput:
        """Create a DocumentInput directly from plain text content."""
        return cls(
            kind=DocumentInputKind.TEXT,
            text=text,
            filename=filename,
            document_id=document_id,
            media_type=media_type,
        )

    @classmethod
    def from_stream(
        cls,
        stream: BinaryIO,
        *,
        filename: str | None = None,
        document_id: str | None = None,
        media_type: str | None = None,
    ) -> DocumentInput:
        """Create a DocumentInput wrapping a readable binary stream (caller owns closing)."""
        return cls(
            kind=DocumentInputKind.STREAM,
            stream=stream,
            filename=filename,
            document_id=document_id,
            media_type=media_type,
        )

    def __repr__(self) -> str:
        """Privacy-safe representation that never discloses raw document content or host paths."""
        parts = [
            f"kind={self.kind.value!r}",
            f"filename={self.filename!r}",
            f"document_id={self.document_id!r}",
            f"media_type={self.media_type!r}",
        ]
        if self.kind == DocumentInputKind.BYTES and self.content is not None:
            parts.append(f"size_bytes={len(self.content)}")
        elif self.kind == DocumentInputKind.TEXT and self.text is not None:
            parts.append(f"size_chars={len(self.text)}")
        return f"DocumentInput({', '.join(parts)})"
