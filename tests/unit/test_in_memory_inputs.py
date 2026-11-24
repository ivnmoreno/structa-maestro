"""Unit tests for DocumentInput descriptors, filename sanitization, and bounded streams (T003)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from structa_maestro import ConfigurationError, DocumentInput, DocumentInputKind
from structa_maestro.documents.inputs import normalize_media_type, sanitize_filename
from structa_maestro.documents.loader import read_stream_bounded


class _CountingInfiniteStream:
    """Adversarial binary stream yielding 1 KiB chunks forever while counting bytes read."""

    def __init__(self, chunk_size: int = 1024) -> None:
        self._chunk = b"X" * chunk_size
        self.total_bytes_yielded = 0
        self.closed = False

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        n = len(self._chunk) if size < 0 else min(len(self._chunk), size)
        self.total_bytes_yielded += n
        return self._chunk[:n]


class _FailingStream:
    """Binary stream raising OSError during read."""

    closed = False

    def readable(self) -> bool:
        return True

    def read(self, _size: int = -1) -> bytes:
        raise OSError("broken pipe")


class _TextReturningStream:
    """Broken stream claiming to be binary but returning str on read()."""

    closed = False

    def readable(self) -> bool:
        return True

    def read(self, _size: int = -1) -> Any:
        return "not-bytes"


def test_sanitize_filename_strips_paths_and_rejects_null_bytes() -> None:
    assert sanitize_filename(None) is None
    assert sanitize_filename("invoice.pdf") == "invoice.pdf"
    assert sanitize_filename("/var/private/uploads/invoice.pdf") == "invoice.pdf"
    assert sanitize_filename("2026/03/invoices/inv_001.pdf") == "inv_001.pdf"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename(r"C:\Users\Alice\Docs\contract.docx") == "contract.docx"
    assert sanitize_filename("  folder/ my file.txt  ") == "my file.txt"

    for empty_like in ("", "   ", "/", "///", ".", "..", "../..", "C:\\", "C:"):
        assert sanitize_filename(empty_like) is None

    with pytest.raises(ConfigurationError):
        sanitize_filename("invoice\x00.pdf")
    with pytest.raises(ConfigurationError):
        sanitize_filename(123)  # type: ignore[arg-type]


def test_normalize_media_type_accepts_supported_and_rejects_unknown() -> None:
    assert normalize_media_type(None) is None
    assert normalize_media_type("TEXT/PLAIN; charset=utf-8") == "text/plain"
    assert normalize_media_type("Application/PDF") == "application/pdf"
    assert (
        normalize_media_type(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

    for bad in ("", "   ", "application/json", "image/png", 42):
        with pytest.raises(ConfigurationError):
            normalize_media_type(bad)  # type: ignore[arg-type]


def test_document_input_factories_snapshot_immutability_and_redacted_repr() -> None:
    # 1. from_path derives basename & strips host directories in repr
    p_in = DocumentInput.from_path("/secret/host/dir/inv.pdf", document_id="  doc-1  ")
    assert p_in.kind == DocumentInputKind.PATH
    assert p_in.path == Path("/secret/host/dir/inv.pdf")
    assert p_in.filename == "inv.pdf"
    assert p_in.document_id == "doc-1"
    assert "/secret/host/dir" not in repr(p_in)
    assert "inv.pdf" in repr(p_in)

    # 2. from_bytes snapshots mutable bytearray & memoryview immutably
    buf = bytearray(b"Invoice Number: SECRET-999")
    b_in = DocumentInput.from_bytes(buf, filename="uploads/a.txt", media_type="text/plain")
    buf[:] = b"MUTATED-000000000000000000"
    assert b_in.content == b"Invoice Number: SECRET-999"
    assert b_in.filename == "a.txt"
    assert "SECRET-999" not in repr(b_in)
    assert "size_bytes=26" in repr(b_in)

    mv_in = DocumentInput.from_bytes(memoryview(b"hello world"))
    assert mv_in.content == b"hello world"

    # 3. from_text redacts raw text from repr
    t_in = DocumentInput.from_text("TOP-SECRET-TEXT", filename=r"C:\dir\note.txt")
    assert t_in.kind == DocumentInputKind.TEXT
    assert t_in.filename == "note.txt"
    assert "TOP-SECRET-TEXT" not in repr(t_in)
    assert "size_chars=15" in repr(t_in)

    # 4. from_stream accepts readable BinaryIO
    stream = io.BytesIO(b"hello")
    s_in = DocumentInput.from_stream(stream, filename="s3/bucket/file.txt")
    assert s_in.kind == DocumentInputKind.STREAM
    assert s_in.stream is stream
    assert s_in.filename == "file.txt"


def test_document_input_eager_validation_rejects_malformed_inputs() -> None:
    # Blank document_id
    with pytest.raises(ConfigurationError):
        DocumentInput.from_text("ok", document_id="   ")
    # Null byte in filename or path
    with pytest.raises(ConfigurationError):
        DocumentInput.from_bytes(b"ok", filename="bad\x00.txt")
    with pytest.raises(ConfigurationError):
        DocumentInput.from_path("bad\x00path.txt")
    with pytest.raises(ConfigurationError):
        DocumentInput.from_path("   ")
    # Wrong payload types
    with pytest.raises(ConfigurationError):
        DocumentInput.from_bytes("not-bytes")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        DocumentInput.from_text(b"not-str")  # type: ignore[arg-type]
    # Incompatible media_type for from_text
    with pytest.raises(ConfigurationError):
        DocumentInput.from_text("hello", media_type="application/pdf")
    # Text-mode, closed, or non-readable streams
    with pytest.raises(ConfigurationError):
        DocumentInput.from_stream(io.StringIO("text"))  # type: ignore[arg-type]
    closed = io.BytesIO(b"abc")
    closed.close()
    with pytest.raises(ConfigurationError):
        DocumentInput.from_stream(closed)
    with pytest.raises(ConfigurationError):
        DocumentInput.from_stream(object())  # type: ignore[arg-type]
    # Direct constructor with mixed payloads or invalid kind
    with pytest.raises(ConfigurationError):
        DocumentInput(kind="bytes", content=b"a")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        DocumentInput(kind=DocumentInputKind.BYTES, content=b"a", text="conflict")


def test_read_stream_bounded_enforces_exact_cap_and_isolates_stream_errors() -> None:
    # Normal read preserves exact bytes and does not close caller stream
    ok_stream = io.BytesIO(b"hello world")
    data, err = read_stream_bounded(ok_stream, max_bytes=11)
    assert err is None
    assert data == b"hello world"
    assert not ok_stream.closed

    # Infinite stream terminates after at most max_bytes + 1 bytes
    inf = _CountingInfiniteStream(chunk_size=1024)
    data_inf, err_inf = read_stream_bounded(inf, max_bytes=2048)
    assert data_inf is None
    assert err_inf is not None
    assert err_inf.code == "resource_limit_exceeded"
    assert inf.total_bytes_yielded == 2049

    # Stream raising OSError -> invalid_input
    data_io, err_io = read_stream_bounded(_FailingStream(), max_bytes=1024)  # type: ignore[arg-type]
    assert data_io is None
    assert err_io is not None
    assert err_io.code == "invalid_input"

    # Stream returning str -> invalid_input
    data_txt, err_txt = read_stream_bounded(_TextReturningStream(), max_bytes=1024)  # type: ignore[arg-type]
    assert data_txt is None
    assert err_txt is not None
    assert err_txt.code == "invalid_input"
