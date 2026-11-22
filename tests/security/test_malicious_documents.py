"""Malicious document security tests (ZIP bombs, corrupted containers, path traversal, limits)."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from conftest import create_zip_bomb_docx
from structa_maestro import (
    DeterministicTestStrategy,
    DocumentLimits,
    ProcessingStatus,
    StrategyOutput,
    extract,
)


@pytest.mark.security
def test_zip_bomb_docx_and_non_docx_zip_rejected_safely(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    strat = DeterministicTestStrategy(StrategyOutput(records=()))

    bomb = create_zip_bomb_docx(tmp_path / "bomb.docx", uncompressed_bytes=2 * 1024 * 1024)
    res_bomb = extract(
        bomb,
        invoice_schema_dict,
        strategy=strat,
        limits=DocumentLimits(max_decompressed_bytes=128 * 1024),
    )
    assert res_bomb.status == ProcessingStatus.FAILED
    assert any(e.code == "resource_limit_exceeded" for e in res_bomb.errors)

    # Regular ZIP masquerading as .docx without word/document.xml
    fake_zip = tmp_path / "archive.docx"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("evil.sh", "#!/bin/sh\necho pwned\n")
    res_zip = extract(fake_zip, invoice_schema_dict, strategy=strat)
    assert res_zip.status == ProcessingStatus.FAILED
    assert any(e.code == "unsupported_format" for e in res_zip.errors)


@pytest.mark.security
def test_path_traversal_null_byte_and_binary_payload_handled_safely(
    tmp_path: Path, invoice_schema_dict: dict[str, Any]
) -> None:
    strat = DeterministicTestStrategy(StrategyOutput(records=()))

    res_null = extract("invoice\x00.txt", invoice_schema_dict, strategy=strat)
    assert res_null.status == ProcessingStatus.FAILED
    assert any(e.code == "invalid_input" for e in res_null.errors)

    res_trav = extract(
        tmp_path / ".." / ".." / "nonexistent_secret.txt", invoice_schema_dict, strategy=strat
    )
    assert res_trav.status == ProcessingStatus.FAILED
    assert any(e.code == "file_not_found" for e in res_trav.errors)

    elf = tmp_path / "payload.txt"
    elf.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
    res_elf = extract(elf, invoice_schema_dict, strategy=strat)
    assert res_elf.status == ProcessingStatus.FAILED
    assert any(e.code == "unsupported_format" for e in res_elf.errors)
