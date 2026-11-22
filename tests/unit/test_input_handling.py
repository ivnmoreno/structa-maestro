"""Input handling failure and edge-case tests covering IN-001 through IN-018."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from conftest import create_minimal_docx, create_minimal_pdf
from structa_maestro import (
    ConfigurationError,
    DeterministicTestStrategy,
    DocumentLimits,
    ProcessingStatus,
    SchemaDefinitionError,
    StrategyOutput,
    ValidationStatus,
    extract,
)


def _dummy_strategy() -> DeterministicTestStrategy:
    return DeterministicTestStrategy(StrategyOutput(records=()))


def test_in_001_to_009_and_014_supported_unsupported_missing_dir_empty_corrupt(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    strat = _dummy_strategy()

    # IN-004: Unsupported extension
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")
    r_unsup = extract(csv_file, invoice_schema_dict, strategy=strat)
    assert r_unsup.status == ProcessingStatus.FAILED
    assert r_unsup.validation.status == ValidationStatus.NOT_VALIDATED
    assert any(e.code == "unsupported_format" for e in r_unsup.errors)

    # IN-005 / IN-014: Zero-byte file
    zero = tmp_path / "zero.txt"
    zero.write_bytes(b"")
    r_zero = extract(zero, invoice_schema_dict, strategy=strat)
    assert r_zero.status == ProcessingStatus.FAILED
    assert any(e.code == "empty_document" for e in r_zero.errors)

    # IN-006: Nonexistent path
    r_missing = extract(tmp_path / "does_not_exist.txt", invoice_schema_dict, strategy=strat)
    assert r_missing.status == ProcessingStatus.FAILED
    assert any(e.code == "file_not_found" for e in r_missing.errors)

    # IN-007: Directory instead of file
    r_dir = extract(tmp_path, invoice_schema_dict, strategy=strat)
    assert r_dir.status == ProcessingStatus.FAILED
    assert any(e.code == "invalid_input" for e in r_dir.errors)

    # IN-008: Unreadable permissions (skip if running as root)
    if os.geteuid() != 0:
        locked = tmp_path / "locked.txt"
        locked.write_text("secret", encoding="utf-8")
        locked.chmod(0o000)
        try:
            r_perm = extract(locked, invoice_schema_dict, strategy=strat)
            assert r_perm.status == ProcessingStatus.FAILED
            assert any(e.code == "permission_denied" for e in r_perm.errors)
        finally:
            locked.chmod(0o600)

    # IN-009: Corrupted PDF
    bad_pdf = tmp_path / "corrupt.pdf"
    bad_pdf.write_bytes(b"%PDF-1.7\nGARBAGE")
    r_corrupt = extract(bad_pdf, invoice_schema_dict, strategy=strat)
    assert r_corrupt.status == ProcessingStatus.FAILED
    assert any(e.code == "corrupted_document" for e in r_corrupt.errors)


def test_in_010_to_018_unicode_spaces_misleading_extension_symlink_and_limits(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    strat = _dummy_strategy()

    # IN-011 & IN-012: Unicode filename with spaces
    uni = tmp_path / "Rechnung Müller 2026 (final).txt"
    uni.write_text("Invoice Number: INV-2026-001", encoding="utf-8")
    r_uni = extract(uni, invoice_schema_dict, strategy=strat)
    assert r_uni.source is not None
    assert r_uni.source.filename == uni.name
    assert r_uni.status == ProcessingStatus.COMPLETED

    # IN-013 & IN-018: PDF bytes named .txt -> detected by magic bytes, emits extension_mismatch
    misleading = create_minimal_pdf(tmp_path / "actually_pdf.txt", ["Invoice Number: INV-2026-001"])
    r_mis = extract(misleading, invoice_schema_dict, strategy=strat)
    assert r_mis.source is not None
    assert r_mis.source.media_type == "application/pdf"
    assert any(w.code == "extension_mismatch" for w in r_mis.warnings)

    # Misleading .pdf containing plain text -> rejected safely
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("Not actually a PDF", encoding="utf-8")
    r_fake_pdf = extract(fake_pdf, invoice_schema_dict, strategy=strat)
    assert r_fake_pdf.status == ProcessingStatus.FAILED
    assert any(e.code == "unsupported_format" for e in r_fake_pdf.errors)

    # IN-016: Symbolic link to valid DOCX
    real_docx = create_minimal_docx(tmp_path / "real.docx", paragraphs=["Invoice: INV-1"])
    link_docx = tmp_path / "link.docx"
    link_docx.symlink_to(real_docx)
    r_link = extract(link_docx, invoice_schema_dict, strategy=strat)
    assert r_link.status == ProcessingStatus.COMPLETED

    # Resource limit exceeded (raw file size)
    big = tmp_path / "big.txt"
    big.write_text("A" * 2048, encoding="utf-8")
    r_big = extract(
        big,
        invoice_schema_dict,
        strategy=strat,
        limits=DocumentLimits(max_file_bytes=512),
    )
    assert r_big.status == ProcessingStatus.FAILED
    assert any(e.code == "resource_limit_exceeded" for e in r_big.errors)


def test_invalid_schema_and_config_raise_pre_execution_errors(tmp_path: Path) -> None:
    doc = tmp_path / "a.txt"
    doc.write_text("hello", encoding="utf-8")
    with pytest.raises(SchemaDefinitionError):
        extract(doc, {"schema_id": "empty", "fields": {}}, strategy=_dummy_strategy())
    with pytest.raises(ConfigurationError):
        DocumentLimits(max_pages=0)
    with pytest.raises(ConfigurationError):
        DocumentLimits(max_file_bytes=-1)
