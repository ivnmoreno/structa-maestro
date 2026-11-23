"""Deterministic on-disk golden document materializer for TXT, PDF, DOCX, and malformed files."""

from __future__ import annotations

import zipfile
from pathlib import Path

import docx

from evals.models import DocumentRecipe, MaterializationKind


def _write_minimal_pdf(path: Path, pages: list[str]) -> Path:
    """Create a deterministic minimal text-based PDF file using raw PDF objects."""

    def _pdf_escape(text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    page_obj_ids: list[int] = []
    next_id = 4
    page_entries: list[tuple[int, int, bytes]] = []

    effective_pages = pages if pages else [""]
    for page_text in effective_pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_obj_ids.append(page_id)
        lines = _pdf_escape(page_text).split("\n")
        stream_lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for idx, line in enumerate(lines):
            if idx == 0:
                stream_lines.append(f"({line}) Tj")
            else:
                stream_lines.append(f"T* ({line}) Tj")
        stream_lines.append("ET")
        stream_bytes = "\n".join(stream_lines).encode("latin-1", errors="replace")
        page_entries.append((page_id, content_id, stream_bytes))

    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for _page_id, content_id, stream_bytes in page_entries:
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream_bytes)).encode("ascii")
            + b" >>\nstream\n"
            + stream_bytes
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for obj_num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{obj_num} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def _write_docx(
    path: Path,
    *,
    paragraphs: list[str],
    headings: list[str],
    tables: list[list[list[str]]],
) -> Path:
    doc = docx.Document()
    for heading in headings:
        doc.add_heading(heading, level=1)
    for para in paragraphs:
        doc.add_paragraph(para)
    for table_rows in tables:
        if not table_rows:
            continue
        col_count = max(len(r) for r in table_rows)
        tbl = doc.add_table(rows=len(table_rows), cols=col_count)
        for r_idx, row in enumerate(table_rows):
            for c_idx, cell_val in enumerate(row):
                tbl.cell(r_idx, c_idx).text = cell_val
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def _write_zip_bomb_docx(path: Path, uncompressed_bytes: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>",
        )
        zf.writestr("word/padding.xml", "A" * uncompressed_bytes)
    return path


def materialize_document(recipe: DocumentRecipe, output_dir: Path) -> Path:
    """Materialize a declarative DocumentRecipe into a real file inside output_dir."""
    target = output_dir / recipe.filename
    target.parent.mkdir(parents=True, exist_ok=True)

    kind = recipe.kind
    if kind == MaterializationKind.TXT:
        content = "\n".join(recipe.lines)
        target.write_bytes(content.encode(recipe.encoding))
        return target

    if kind == MaterializationKind.PDF:
        pages = recipe.pages if recipe.pages else ["\n".join(recipe.lines)]
        return _write_minimal_pdf(target, pages)

    if kind == MaterializationKind.DOCX:
        return _write_docx(
            target,
            paragraphs=recipe.lines,
            headings=recipe.headings,
            tables=recipe.tables,
        )

    if kind == MaterializationKind.ZERO_BYTE:
        target.write_bytes(b"")
        return target

    if kind == MaterializationKind.WHITESPACE_ONLY:
        target.write_text("   \n\t  \n", encoding="utf-8")
        return target

    if kind == MaterializationKind.IMAGE_ONLY_PDF:
        return _write_minimal_pdf(target, [""])

    if kind == MaterializationKind.CORRUPT_PDF:
        target.write_bytes(b"%PDF-1.4\n%%CORRUPTED_OBJECT_STREAM%%EOF")
        return target

    if kind == MaterializationKind.CORRUPT_DOCX:
        target.write_bytes(b"PK\x03\x04corrupted_docx_archive_payload")
        return target

    if kind == MaterializationKind.ZIP_BOMB_DOCX:
        size = recipe.uncompressed_bytes or (55 * 1024 * 1024)
        return _write_zip_bomb_docx(target, size)

    if kind == MaterializationKind.MISLEADING_PDF_AS_TXT:
        pages = recipe.pages if recipe.pages else ["\n".join(recipe.lines)]
        return _write_minimal_pdf(target, pages)

    if kind == MaterializationKind.MISLEADING_TXT_AS_PDF:
        content = "\n".join(recipe.lines) or "Plain text masquerading as a PDF file."
        target.write_text(content, encoding="utf-8")
        return target

    raise ValueError(f"Unsupported materialization kind: {kind!r}")
