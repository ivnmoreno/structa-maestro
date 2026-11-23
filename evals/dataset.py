"""Golden dataset manifest loader, pre-flight integrity validator, and subset filter."""

from __future__ import annotations

import json
from pathlib import Path

from evals.models import (
    ChallengeProfile,
    DocumentCategory,
    EvaluationFilter,
    GoldenCase,
    GoldenDatasetManifest,
)
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.exceptions import SchemaDefinitionError
from structa_maestro.schema.models import ExtractionSchema

DEFAULT_MANIFEST_PATH = Path(__file__).parent / "dataset" / "manifest.json"
REQUIRED_FULL_CORPUS_SIZE = 50


def load_dataset(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    *,
    validate_full_corpus: bool = True,
) -> GoldenDatasetManifest:
    """Load the versioned golden manifest (resolving split category files) and validate it."""
    root_path = Path(manifest_path)
    if not root_path.exists():
        raise ValueError(f"Golden dataset manifest not found: {root_path}")

    raw = json.loads(root_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Golden dataset manifest root must be a JSON object.")

    cases_raw: list[object] = []
    if "category_files" in raw:
        cat_files = raw["category_files"]
        if not isinstance(cat_files, list) or not cat_files:
            raise ValueError("Manifest 'category_files' must be a non-empty list of paths.")
        for rel in cat_files:
            cat_path = root_path.parent / str(rel)
            if not cat_path.exists():
                raise ValueError(f"Missing category manifest file: {cat_path}")
            cat_data = json.loads(cat_path.read_text(encoding="utf-8"))
            if isinstance(cat_data, dict) and isinstance(cat_data.get("cases"), list):
                cases_raw.extend(cat_data["cases"])
            elif isinstance(cat_data, list):
                cases_raw.extend(cat_data)
            else:
                raise ValueError(f"Invalid category manifest structure in {cat_path.name}")
    elif isinstance(raw.get("cases"), list):
        cases_raw.extend(raw["cases"])
    else:
        raise ValueError("Manifest must declare either 'category_files' or 'cases'.")

    manifest = GoldenDatasetManifest.model_validate(
        {
            "dataset_version": raw.get("dataset_version"),
            "description": raw.get("description"),
            "cases": cases_raw,
        }
    )
    validate_dataset(manifest, require_full_corpus=validate_full_corpus)
    return manifest


def validate_dataset(
    manifest: GoldenDatasetManifest,
    *,
    require_full_corpus: bool = True,
) -> None:
    """Verify unique IDs, valid extraction schemas, and full-corpus coverage invariants."""
    seen_ids: set[str] = set()
    categories_present: set[DocumentCategory] = set()
    formats_present: set[DocumentFormat] = set()
    profiles_present: set[ChallengeProfile] = set()

    for case in manifest.cases:
        if case.case_id in seen_ids:
            raise ValueError(f"Duplicate golden case_id detected: {case.case_id!r}")
        seen_ids.add(case.case_id)

        try:
            ExtractionSchema.from_dict(case.schema_definition)
        except SchemaDefinitionError as exc:
            raise ValueError(
                f"Golden case {case.case_id!r} has an invalid schema_definition: {exc}"
            ) from exc

        categories_present.add(case.category)
        formats_present.add(case.format)
        profiles_present.update(case.challenge_profiles)

    if require_full_corpus:
        if len(manifest.cases) != REQUIRED_FULL_CORPUS_SIZE:
            raise ValueError(
                f"Full golden corpus requires exactly {REQUIRED_FULL_CORPUS_SIZE} cases, "
                f"found {len(manifest.cases)}."
            )
        missing_cats = set(DocumentCategory) - categories_present
        if missing_cats:
            raise ValueError(
                f"Golden corpus missing categories: {sorted(c.value for c in missing_cats)}"
            )
        missing_fmts = set(DocumentFormat) - formats_present
        if missing_fmts:
            raise ValueError(
                f"Golden corpus missing formats: {sorted(f.value for f in missing_fmts)}"
            )
        missing_profiles = set(ChallengeProfile) - profiles_present
        if missing_profiles:
            raise ValueError(
                f"Golden corpus missing challenge profiles: "
                f"{sorted(p.value for p in missing_profiles)}"
            )


def filter_cases(
    manifest: GoldenDatasetManifest,
    filters: EvaluationFilter | None = None,
) -> list[GoldenCase]:
    """Return golden cases matching filters; raise ValueError if selection is empty."""
    if filters is None or not filters.is_filtered:
        return list(manifest.cases)

    selected: list[GoldenCase] = []
    cat_set = set(filters.categories)
    fmt_set = set(filters.formats)
    prof_set = set(filters.profiles)
    id_set = set(filters.case_ids)

    for case in manifest.cases:
        if cat_set and case.category not in cat_set:
            continue
        if fmt_set and case.format not in fmt_set:
            continue
        if prof_set and not (set(case.challenge_profiles) & prof_set):
            continue
        if id_set and case.case_id not in id_set:
            continue
        selected.append(case)

    if not selected:
        raise ValueError("Active evaluation filters matched 0 golden cases.")
    return selected
