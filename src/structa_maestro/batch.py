"""Stateless, in-process batch document extraction models, scheduler, and directory discovery."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from structa_maestro.config import DocumentLimits
from structa_maestro.contracts.enums import (
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    DiagnosticIssue,
    ExtractionResult,
    SchemaMetadata,
    SourceMetadata,
)
from structa_maestro.documents.inputs import DocumentInput, DocumentInputKind
from structa_maestro.documents.loader import DocumentLoadResult, read_stream_bounded
from structa_maestro.exceptions import ConfigurationError
from structa_maestro.telemetry import BatchProgress

MAX_BATCH_WORKERS = 32
SUPPORTED_BATCH_EXTENSIONS: frozenset[str] = frozenset({".txt", ".pdf", ".docx"})


@dataclass(frozen=True, slots=True)
class BatchDocumentInput:
    """Immutable per-document batch input pairing a file path with an optional document ID."""

    path: Path
    document_id: str | None = None

    def __post_init__(self) -> None:
        raw_path: Any = self.path
        if isinstance(raw_path, str):
            if not raw_path.strip():
                raise ConfigurationError("BatchDocumentInput.path must be a non-empty file path.")
            object.__setattr__(self, "path", Path(raw_path))
        elif isinstance(raw_path, Path):
            if not str(raw_path).strip():
                raise ConfigurationError("BatchDocumentInput.path must be a non-empty file path.")
        else:
            raise ConfigurationError("BatchDocumentInput.path must be a str or pathlib.Path.")

        if self.document_id is not None:
            if not isinstance(self.document_id, str) or not self.document_id.strip():
                raise ConfigurationError(
                    "BatchDocumentInput.document_id must be a non-empty string or None."
                )
            object.__setattr__(self, "document_id", self.document_id.strip())


@dataclass(frozen=True, slots=True)
class _PreparedBatchItem:
    """Internal normalized batch item holding a DocumentInput and optional pre-captured error."""

    document_input: DocumentInput
    preloaded_error: DocumentLoadResult | None = None


@dataclass(frozen=True, slots=True)
class BatchOptions:
    """Immutable in-process batch execution options validated eagerly on construction."""

    max_workers: int = 1
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_workers, bool)
            or not isinstance(self.max_workers, int)
            or self.max_workers < 1
            or self.max_workers > MAX_BATCH_WORKERS
        ):
            raise ConfigurationError(
                f"BatchOptions.max_workers must be an integer between 1 and {MAX_BATCH_WORKERS}."
            )
        if not isinstance(self.fail_fast, bool):
            raise ConfigurationError("BatchOptions.fail_fast must be a boolean.")


@dataclass(frozen=True, slots=True)
class BatchSummary:
    """Immutable aggregate processing and validation statistics for a batch extraction run."""

    total_documents: int
    processed_documents: int
    skipped_documents: int
    completed_count: int
    partial_count: int
    failed_count: int
    valid_count: int
    invalid_count: int
    not_validated_count: int
    aborted_early: bool
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of the batch summary."""
        return {
            "total_documents": self.total_documents,
            "processed_documents": self.processed_documents,
            "skipped_documents": self.skipped_documents,
            "completed_count": self.completed_count,
            "partial_count": self.partial_count,
            "failed_count": self.failed_count,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "not_validated_count": self.not_validated_count,
            "aborted_early": self.aborted_early,
            "elapsed_ms": self.elapsed_ms,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the batch summary to JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class BatchExtractionResult:
    """Immutable ordered batch container holding per-document Contract v1.0 results and summary."""

    batch_id: str
    schema_metadata: SchemaMetadata
    results: tuple[ExtractionResult, ...]
    summary: BatchSummary

    def to_jsonl(self) -> str:
        """Serialize processed Canonical Output Record Contract v1.0 results as JSON Lines."""
        if not self.results:
            return ""
        return "\n".join(res.to_json(indent=None) for res in self.results) + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe batch envelope preserving Contract v1.0 for every item."""
        return {
            "batch_id": self.batch_id,
            "schema": {
                "schema_id": self.schema_metadata.schema_id,
                "schema_version": self.schema_metadata.schema_version,
            },
            "summary": self.summary.to_dict(),
            "results": [res.to_dict() for res in self.results],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the batch envelope to JSON."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def normalize_batch_inputs(
    documents: Sequence[str | Path | BatchDocumentInput | DocumentInput],
) -> tuple[DocumentInput, ...]:
    """Eagerly validate and normalize batch input descriptors before opening any file or stream."""
    if isinstance(documents, (str, bytes, bytearray)) or not isinstance(documents, Sequence):
        raise ConfigurationError(
            "Batch documents must be a non-empty sequence of paths, BatchDocumentInput, "
            "or DocumentInput items."
        )
    if len(documents) == 0:
        raise ConfigurationError("Batch documents collection cannot be empty.")

    normalized: list[DocumentInput] = []
    seen_doc_ids: set[str] = set()
    for idx, item in enumerate(documents):
        if isinstance(item, DocumentInput):
            desc = item
        elif isinstance(item, BatchDocumentInput):
            desc = DocumentInput.from_path(item.path, document_id=item.document_id)
        elif isinstance(item, (str, Path)):
            if isinstance(item, str) and not item.strip():
                raise ConfigurationError(f"Batch document at index {idx} cannot be blank.")
            desc = DocumentInput.from_path(item)
        else:
            raise ConfigurationError(
                f"Batch document at index {idx} must be str, pathlib.Path, "
                "BatchDocumentInput, or DocumentInput."
            )

        if desc.document_id is not None:
            if desc.document_id in seen_doc_ids:
                raise ConfigurationError(
                    f"Duplicate caller-supplied document_id in batch: {desc.document_id!r}"
                )
            seen_doc_ids.add(desc.document_id)
        normalized.append(desc)

    return tuple(normalized)


def prepare_batch_items(
    inputs: tuple[DocumentInput, ...],
    *,
    limits: DocumentLimits,
    max_workers: int,
) -> tuple[_PreparedBatchItem, ...]:
    """Prepare normalized batch items, safely pre-materializing streams for concurrent workers."""
    if max_workers <= 1:
        return tuple(_PreparedBatchItem(document_input=item) for item in inputs)

    prepared: list[_PreparedBatchItem] = []
    for item in inputs:
        if item.kind != DocumentInputKind.STREAM:
            prepared.append(_PreparedBatchItem(document_input=item))
            continue

        if item.stream is None:
            raise ConfigurationError("STREAM DocumentInput is missing a binary stream.")

        raw_bytes, read_err = read_stream_bounded(item.stream, max_bytes=limits.max_file_bytes)
        if read_err is not None or raw_bytes is None:
            doc_id = item.document_id or str(uuid4())
            err_issue = read_err or DiagnosticIssue(
                code="invalid_input",
                severity=IssueSeverity.ERROR,
                stage=PipelineStage.INPUT,
                message="Failed to read input binary stream.",
            )
            failed_load = DocumentLoadResult(
                source=SourceMetadata(
                    document_id=doc_id,
                    filename=item.filename,
                    media_type=None,
                ),
                errors=[err_issue],
            )
            prepared.append(_PreparedBatchItem(document_input=item, preloaded_error=failed_load))
        else:
            snapshot = DocumentInput.from_bytes(
                raw_bytes,
                filename=item.filename,
                document_id=item.document_id,
                media_type=item.media_type,
            )
            prepared.append(_PreparedBatchItem(document_input=snapshot))

    return tuple(prepared)


def _compute_summary(
    *,
    total_documents: int,
    results: Sequence[ExtractionResult],
    aborted_early: bool,
    elapsed_ms: float,
) -> BatchSummary:
    completed = sum(1 for r in results if r.status == ProcessingStatus.COMPLETED)
    partial = sum(1 for r in results if r.status == ProcessingStatus.PARTIAL)
    failed = sum(1 for r in results if r.status == ProcessingStatus.FAILED)

    valid = sum(1 for r in results if r.validation.status == ValidationStatus.VALID)
    invalid = sum(1 for r in results if r.validation.status == ValidationStatus.INVALID)
    not_validated = sum(1 for r in results if r.validation.status == ValidationStatus.NOT_VALIDATED)

    processed = len(results)
    return BatchSummary(
        total_documents=total_documents,
        processed_documents=processed,
        skipped_documents=total_documents - processed,
        completed_count=completed,
        partial_count=partial,
        failed_count=failed,
        valid_count=valid,
        invalid_count=invalid,
        not_validated_count=not_validated,
        aborted_early=aborted_early,
        elapsed_ms=round(max(0.0, elapsed_ms), 3),
    )


def run_batch_extraction(
    *,
    inputs: tuple[_PreparedBatchItem, ...],
    schema_metadata: SchemaMetadata,
    options: BatchOptions,
    extract_one: Callable[[_PreparedBatchItem], ExtractionResult],
    batch_id: str | None = None,
    on_item_complete: Callable[[int, ExtractionResult, BatchProgress], None] | None = None,
) -> BatchExtractionResult:
    """Execute stateless sequential or bounded concurrent batch extraction in exact input order."""
    started = time.perf_counter()
    resolved_batch_id = batch_id if batch_id is not None else str(uuid4())
    total = len(inputs)
    aborted_early = False
    processed_count = 0
    completed_count = 0
    partial_count = 0
    failed_count = 0

    def _record_progress(idx: int, res: ExtractionResult, *, aborted_now: bool) -> None:
        nonlocal processed_count, completed_count, partial_count, failed_count
        processed_count += 1
        if res.status == ProcessingStatus.COMPLETED:
            completed_count += 1
        elif res.status == ProcessingStatus.PARTIAL:
            partial_count += 1
        elif res.status == ProcessingStatus.FAILED:
            failed_count += 1
        if on_item_complete is not None:
            on_item_complete(
                idx,
                res,
                BatchProgress(
                    batch_id=resolved_batch_id,
                    total_documents=total,
                    processed_documents=processed_count,
                    skipped_documents=0,
                    item_index=idx,
                    completed_count=completed_count,
                    partial_count=partial_count,
                    failed_count=failed_count,
                    aborted_early=aborted_now,
                ),
            )

    if options.max_workers == 1:
        ordered_results: list[ExtractionResult] = []
        for idx, doc_input in enumerate(inputs):
            res = extract_one(doc_input)
            ordered_results.append(res)
            will_abort = (
                options.fail_fast and res.status == ProcessingStatus.FAILED and idx < total - 1
            )
            _record_progress(idx, res, aborted_now=will_abort)
            if will_abort:
                aborted_early = True
                break
        final_results = tuple(ordered_results)
    else:
        completed_by_idx: dict[int, ExtractionResult] = {}
        worker_count = min(options.max_workers, total)
        next_idx = 0
        stop_submitting = False

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            in_flight: dict[Future[ExtractionResult], int] = {}

            while next_idx < total and len(in_flight) < worker_count:
                fut = pool.submit(extract_one, inputs[next_idx])
                in_flight[fut] = next_idx
                next_idx += 1

            while in_flight:
                done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                for fut in done:
                    idx = in_flight.pop(fut)
                    res = fut.result()
                    completed_by_idx[idx] = res
                    if options.fail_fast and res.status == ProcessingStatus.FAILED:
                        stop_submitting = True
                    _record_progress(
                        idx,
                        res,
                        aborted_now=stop_submitting and next_idx < total,
                    )

                while not stop_submitting and next_idx < total and len(in_flight) < worker_count:
                    fut = pool.submit(extract_one, inputs[next_idx])
                    in_flight[fut] = next_idx
                    next_idx += 1

        aborted_early = stop_submitting and len(completed_by_idx) < total
        final_results = tuple(completed_by_idx[i] for i in sorted(completed_by_idx))

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    summary = _compute_summary(
        total_documents=total,
        results=final_results,
        aborted_early=aborted_early,
        elapsed_ms=elapsed_ms,
    )
    return BatchExtractionResult(
        batch_id=resolved_batch_id,
        schema_metadata=schema_metadata,
        results=final_results,
        summary=summary,
    )


async def run_async_batch_extraction(
    *,
    inputs: tuple[_PreparedBatchItem, ...],
    schema_metadata: SchemaMetadata,
    options: BatchOptions,
    extract_one: Callable[[_PreparedBatchItem], Coroutine[Any, Any, ExtractionResult]],
    batch_id: str | None = None,
    aon_item_complete: (
        Callable[[int, ExtractionResult, BatchProgress], Coroutine[Any, Any, None]] | None
    ) = None,
) -> BatchExtractionResult:
    """Execute stateless sequential or bounded concurrent asyncio batch extraction in order."""
    started = time.perf_counter()
    resolved_batch_id = batch_id if batch_id is not None else str(uuid4())
    total = len(inputs)
    aborted_early = False
    processed_count = 0
    completed_count = 0
    partial_count = 0
    failed_count = 0

    async def _arecord_progress(idx: int, res: ExtractionResult, *, aborted_now: bool) -> None:
        nonlocal processed_count, completed_count, partial_count, failed_count
        processed_count += 1
        if res.status == ProcessingStatus.COMPLETED:
            completed_count += 1
        elif res.status == ProcessingStatus.PARTIAL:
            partial_count += 1
        elif res.status == ProcessingStatus.FAILED:
            failed_count += 1
        if aon_item_complete is not None:
            await aon_item_complete(
                idx,
                res,
                BatchProgress(
                    batch_id=resolved_batch_id,
                    total_documents=total,
                    processed_documents=processed_count,
                    skipped_documents=0,
                    item_index=idx,
                    completed_count=completed_count,
                    partial_count=partial_count,
                    failed_count=failed_count,
                    aborted_early=aborted_now,
                ),
            )

    if options.max_workers == 1:
        ordered_results: list[ExtractionResult] = []
        for idx, item in enumerate(inputs):
            res = await extract_one(item)
            ordered_results.append(res)
            will_abort = (
                options.fail_fast and res.status == ProcessingStatus.FAILED and idx < total - 1
            )
            await _arecord_progress(idx, res, aborted_now=will_abort)
            if will_abort:
                aborted_early = True
                break
        final_results = tuple(ordered_results)
    else:
        completed_by_idx: dict[int, ExtractionResult] = {}
        worker_count = min(options.max_workers, total)
        next_idx = 0
        stop_submitting = False
        in_flight: dict[asyncio.Task[ExtractionResult], int] = {}

        try:
            while next_idx < total and len(in_flight) < worker_count:
                task = asyncio.create_task(extract_one(inputs[next_idx]))
                in_flight[task] = next_idx
                next_idx += 1

            while in_flight:
                done, _ = await asyncio.wait(
                    in_flight.keys(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    idx = in_flight.pop(task)
                    res = task.result()
                    completed_by_idx[idx] = res
                    if options.fail_fast and res.status == ProcessingStatus.FAILED:
                        stop_submitting = True
                    await _arecord_progress(
                        idx,
                        res,
                        aborted_now=stop_submitting and next_idx < total,
                    )

                while not stop_submitting and next_idx < total and len(in_flight) < worker_count:
                    task = asyncio.create_task(extract_one(inputs[next_idx]))
                    in_flight[task] = next_idx
                    next_idx += 1
        except BaseException:
            # Cancel and cleanly await all active child tasks before propagating CancelledError
            pending_tasks = list(in_flight.keys())
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            raise

        aborted_early = stop_submitting and len(completed_by_idx) < total
        final_results = tuple(completed_by_idx[i] for i in sorted(completed_by_idx))

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    summary = _compute_summary(
        total_documents=total,
        results=final_results,
        aborted_early=aborted_early,
        elapsed_ms=elapsed_ms,
    )
    return BatchExtractionResult(
        batch_id=resolved_batch_id,
        schema_metadata=schema_metadata,
        results=final_results,
        summary=summary,
    )


def discover_batch_documents(
    inputs: Sequence[str | Path],
    *,
    recursive: bool = False,
) -> list[Path]:
    """Deterministically resolve CLI file and directory inputs without symlink cycles.

    - Existing directories are scanned lexicographically (non-recursively by default, or
      recursively with ``followlinks=False``), ignoring hidden dotfiles/directories and broken
      symlinks, and keeping files with supported extensions (``.txt``, ``.pdf``, ``.docx``).
    - Explicitly named file paths (including nonexistent paths) are preserved in caller order.
    """
    discovered: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            dir_files: list[Path] = []
            if recursive:
                for root_str, dirnames, filenames in os.walk(path, followlinks=False):
                    # Prune hidden directories & symlinked subdirectories in deterministic order
                    root_path = Path(root_str)
                    dirnames[:] = sorted(
                        d
                        for d in dirnames
                        if not d.startswith(".") and not (root_path / d).is_symlink()
                    )
                    for fname in sorted(filenames):
                        if fname.startswith("."):
                            continue
                        candidate = root_path / fname
                        if candidate.is_symlink() and not candidate.exists():
                            continue
                        if (
                            candidate.is_file()
                            and candidate.suffix.lower() in SUPPORTED_BATCH_EXTENSIONS
                        ):
                            dir_files.append(candidate)
            else:
                for entry in sorted(path.iterdir(), key=lambda p: p.name):
                    if entry.name.startswith("."):
                        continue
                    if entry.is_symlink() and not entry.exists():
                        continue
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_BATCH_EXTENSIONS:
                        dir_files.append(entry)
            dir_files.sort(key=lambda p: p.as_posix())
            discovered.extend(dir_files)
        else:
            discovered.append(path)
    return discovered
