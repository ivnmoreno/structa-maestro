"""Unit tests for stateless batch models, validation, serialization & discovery (T003, T008)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from structa_maestro import (
    BatchDocumentInput,
    BatchOptions,
    ConfigurationError,
)
from structa_maestro.batch import (
    MAX_BATCH_WORKERS,
    discover_batch_documents,
    normalize_batch_inputs,
)


def test_batch_document_input_and_options_eager_validation() -> None:
    d1 = BatchDocumentInput(path="invoice.txt", document_id=" doc-1 ")
    assert d1.path == Path("invoice.txt")
    assert d1.document_id == "doc-1"

    d2 = BatchDocumentInput(path=Path("a.pdf"))
    assert d2.document_id is None

    for bad_path in ("", "   ", 123):
        with pytest.raises(ConfigurationError):
            BatchDocumentInput(path=bad_path)  # type: ignore[arg-type]

    for bad_id in ("", "   ", 42):
        with pytest.raises(ConfigurationError):
            BatchDocumentInput(path="a.txt", document_id=bad_id)  # type: ignore[arg-type]

    opts = BatchOptions()
    assert opts.max_workers == 1
    assert opts.fail_fast is False

    opts_max = BatchOptions(max_workers=MAX_BATCH_WORKERS, fail_fast=True)
    assert opts_max.max_workers == MAX_BATCH_WORKERS
    assert opts_max.fail_fast is True

    for bad_workers in (0, -1, MAX_BATCH_WORKERS + 1, True, False, "4"):
        with pytest.raises(ConfigurationError):
            BatchOptions(max_workers=bad_workers)  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError):
        BatchOptions(fail_fast="yes")  # type: ignore[arg-type]


def test_normalize_batch_inputs_rejects_empty_bare_str_and_duplicate_ids() -> None:
    with pytest.raises(ConfigurationError):
        normalize_batch_inputs([])
    with pytest.raises(ConfigurationError):
        normalize_batch_inputs("single_path.txt")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        normalize_batch_inputs([123])  # type: ignore[list-item]
    with pytest.raises(ConfigurationError, match="Duplicate"):
        normalize_batch_inputs(
            [
                BatchDocumentInput(path="a.txt", document_id="dup-1"),
                BatchDocumentInput(path="b.txt", document_id="dup-1"),
            ]
        )

    # Same file path twice without duplicate explicit IDs is allowed (positional correspondence)
    norm = normalize_batch_inputs(["a.txt", Path("a.txt"), BatchDocumentInput("b.pdf", "id-2")])
    assert len(norm) == 3
    assert norm[0].path == Path("a.txt")
    assert norm[1].path == Path("a.txt")
    assert norm[2].document_id == "id-2"


def test_discover_batch_documents_sorted_recursive_hidden_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    nested = root / "sub"
    hidden_dir = root / ".hidden"
    nested.mkdir(parents=True)
    hidden_dir.mkdir(parents=True)

    f_b = root / "b.TXT"
    f_a = root / "a.pdf"
    f_ign = root / "notes.md"
    f_dot = root / ".secret.txt"
    f_sub = nested / "c.docx"
    f_hid = hidden_dir / "ignored.txt"
    for f in (f_b, f_a, f_ign, f_dot, f_sub, f_hid):
        f.write_text("sample", encoding="utf-8")

    # Broken symlink must be skipped safely
    broken_link = root / "broken.txt"
    os.symlink(tmp_path / "does_not_exist.txt", broken_link)

    # Self-referencing directory symlink must not cause infinite recursion
    loop_link = nested / "loop"
    os.symlink(root, loop_link)

    non_rec = discover_batch_documents([root], recursive=False)
    assert non_rec == [f_a, f_b]

    rec = discover_batch_documents([root], recursive=True)
    assert rec == [f_a, f_b, f_sub]

    # Explicit nonexistent file path passes through in exact caller order
    missing = tmp_path / "missing.txt"
    mixed = discover_batch_documents([missing, root], recursive=False)
    assert mixed == [missing, f_a, f_b]
