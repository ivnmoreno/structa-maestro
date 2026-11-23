"""Golden dataset manifest integrity and on-disk materialization tests (T008)."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.dataset import DEFAULT_MANIFEST_PATH, filter_cases, load_dataset, validate_dataset
from evals.materializer import materialize_document
from evals.models import (
    ChallengeProfile,
    DocumentCategory,
    DocumentRecipe,
    EvaluationFilter,
    GoldenDatasetManifest,
    MaterializationKind,
)
from structa_maestro.contracts.enums import DocumentFormat


@pytest.mark.evaluation
def test_full_golden_corpus_has_50_cases_8_categories_3_formats_10_profiles() -> None:
    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=True)
    assert manifest.dataset_version == "2.0.0"
    assert len(manifest.cases) == 50

    categories = {c.category for c in manifest.cases}
    formats = {c.format for c in manifest.cases}
    profiles = {p for c in manifest.cases for p in c.challenge_profiles}

    assert categories == set(DocumentCategory)
    assert formats == set(DocumentFormat)
    assert profiles == set(ChallengeProfile)

    # Every MaterializationKind is exercised across the 50 cases
    kinds = {c.document.kind for c in manifest.cases}
    assert kinds == set(MaterializationKind)


@pytest.mark.evaluation
def test_every_golden_case_materializes_cleanly_on_disk(tmp_path: Path) -> None:
    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=True)
    for case in manifest.cases:
        out_path = materialize_document(case.document, tmp_path / case.case_id)
        assert out_path.exists()
        assert out_path.is_file()


@pytest.mark.evaluation
def test_dataset_validation_rejects_duplicates_and_empty_filter_raises() -> None:
    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=True)
    dup_manifest = GoldenDatasetManifest(
        dataset_version="2.0.0",
        description="Duplicate test",
        cases=[manifest.cases[0], manifest.cases[0]],
    )
    with pytest.raises(ValueError, match="Duplicate golden case_id"):
        validate_dataset(dup_manifest, require_full_corpus=False)

    with pytest.raises(ValueError, match="matched 0 golden cases"):
        filter_cases(manifest, EvaluationFilter(case_ids=["nonexistent-case-999"]))

    with pytest.raises(ValueError, match="safe basename"):
        DocumentRecipe(kind=MaterializationKind.TXT, filename="../escape.txt", lines=["x"])
