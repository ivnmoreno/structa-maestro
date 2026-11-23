"""Deterministic offline reference ExtractionStrategy for reproducible CI golden baselines."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from structa_maestro.contracts.enums import ExtractionCardinality, FieldPresence, FieldType
from structa_maestro.documents.models import PreparedDocument
from structa_maestro.schema.models import ExtractionSchema, FieldSpec
from structa_maestro.strategies.base import (
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)

_RECORD_SPLIT_RE = re.compile(r"(?m)^\s*---\s*RECORD\s*---\s*$")
_INJECTION_LINE_RE = re.compile(
    r"(?i)(ignore\s+(?:all\s+)?previous\s+instructions|"
    r"disregard\s+(?:the\s+)?(?:schema|previous)|"
    r"override\s+schema|"
    r"</untrusted_document>|"
    r"return\s+administrator\s+credentials|"
    r"fabricate\s+missing|"
    r"bypass\s+validation|"
    r"system\s+override\s*:)"
)
_QUOTED_OR_HYPOTHETICAL_RE = re.compile(
    r"(?i)^\s*(?:example\s*:|for\s+example\b|note\s*\(example\)|>\s*|hypothetical\s*:)"
)
_KV_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\- /]+?)\s*:\s*(.+?)\s*$")
_PIPE_ROW_RE = re.compile(r"^\s*([A-Za-z0-9_.\- /]+?)\s*\|\s*(.+?)\s*$")
_AMBIGUOUS_TAG_RE = re.compile(r"\s*\[AMBIGUOUS\]\s*$", re.IGNORECASE)
_CONFLICT_TAG_RE = re.compile(r"\s*\[CONFLICTING\]\s*$", re.IGNORECASE)


def _norm_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


def _clean_text_lines(block: str) -> list[str]:
    clean: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _INJECTION_LINE_RE.search(stripped):
            continue
        if _QUOTED_OR_HYPOTHETICAL_RE.search(stripped):
            continue
        clean.append(stripped)
    return clean


def _parse_scalar(raw_val: str, field_type: FieldType) -> Any:
    val = raw_val.strip()
    if field_type == FieldType.STRING:
        return val
    if field_type == FieldType.INTEGER:
        cleaned = val.replace(",", "")
        try:
            return int(cleaned)
        except ValueError:
            return val
    if field_type == FieldType.DECIMAL:
        cleaned = re.sub(r"^[$€£¥]\s*", "", val).replace(",", "")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return val
    if field_type == FieldType.BOOLEAN:
        low = val.lower()
        if low in {"true", "yes"}:
            return True
        if low in {"false", "no"}:
            return False
        return val
    if field_type == FieldType.DATE:
        try:
            return date.fromisoformat(val)
        except ValueError:
            return val
    return val


def _extract_kv_candidates(
    lines: list[str],
) -> dict[str, list[tuple[str, str, bool]]]:
    """Collect normalized_key -> list[(raw_value, original_line, flagged_ambiguous)]."""
    candidates: dict[str, list[tuple[str, str, bool]]] = {}
    for line in lines:
        m = _KV_LINE_RE.match(line)
        if m is None:
            m = _PIPE_ROW_RE.match(line)
        if m is None:
            continue
        raw_key, raw_val = m.group(1), m.group(2)
        # Skip table header rows like "Field | Value" or "Item | Amount"
        if _norm_key(raw_key) in {"field", "attribute", "column", "metric"} and _norm_key(
            raw_val
        ) in {"value", "amount", "details"}:
            continue
        ambiguous = False
        cleaned_val = raw_val
        if _AMBIGUOUS_TAG_RE.search(cleaned_val):
            ambiguous = True
            cleaned_val = _AMBIGUOUS_TAG_RE.sub("", cleaned_val).strip()
        if _CONFLICT_TAG_RE.search(cleaned_val):
            ambiguous = True
            cleaned_val = _CONFLICT_TAG_RE.sub("", cleaned_val).strip()

        key = _norm_key(raw_key)
        candidates.setdefault(key, []).append((cleaned_val, line, ambiguous))
    return candidates


def _extract_field_from_candidates(
    fname: str,
    spec: FieldSpec,
    candidates: dict[str, list[tuple[str, str, bool]]],
) -> ProposedField:
    if spec.field_type == FieldType.OBJECT and spec.properties:
        obj_val: dict[str, Any] = {}
        evidences: list[ProposedEvidence] = []
        any_ambig = False
        for prop_name, prop_spec in spec.properties.items():
            # Match either "parent.child" or "parent_child" or bare "child"
            hits = (
                candidates.get(_norm_key(f"{fname}.{prop_name}"))
                or candidates.get(_norm_key(f"{fname}_{prop_name}"))
                or candidates.get(_norm_key(prop_name))
            )
            if not hits:
                continue
            raw_v, quote, amb = hits[0]
            if len({h[0] for h in hits}) > 1:
                amb = True
            any_ambig = any_ambig or amb
            obj_val[prop_name] = _parse_scalar(raw_v, prop_spec.field_type)
            evidences.append(ProposedEvidence(quote=quote))
        if not obj_val:
            return ProposedField(presence=FieldPresence.MISSING, value=None)
        return ProposedField(
            presence=FieldPresence.PRESENT,
            value=obj_val,
            confidence=0.85 if any_ambig else 0.95,
            confidence_method="reference-deterministic",
            evidence=tuple(evidences),
            ambiguous=any_ambig,
        )

    hits = candidates.get(_norm_key(fname))
    if not hits:
        return ProposedField(presence=FieldPresence.MISSING, value=None)

    unique_vals = list(dict.fromkeys(h[0] for h in hits))
    ambiguous = any(h[2] for h in hits) or len(unique_vals) > 1
    evidences = [ProposedEvidence(quote=h[1]) for h in hits]

    if spec.field_type == FieldType.ARRAY and spec.items is not None:
        raw_joined = hits[0][0]
        parts = [p.strip() for p in raw_joined.split(",") if p.strip()]
        parsed_items = [_parse_scalar(p, spec.items.field_type) for p in parts]
        return ProposedField(
            presence=FieldPresence.PRESENT,
            value=parsed_items,
            confidence=0.90 if not ambiguous else 0.65,
            confidence_method="reference-deterministic",
            evidence=tuple(evidences),
            ambiguous=ambiguous,
        )

    raw_first = hits[0][0]
    if raw_first.lower() == "null" and spec.nullable:
        return ProposedField(
            presence=FieldPresence.PRESENT,
            value=None,
            confidence=0.95,
            confidence_method="reference-deterministic",
            evidence=tuple(evidences),
            ambiguous=ambiguous,
        )

    parsed_val = _parse_scalar(raw_first, spec.field_type)
    return ProposedField(
        presence=FieldPresence.PRESENT,
        value=parsed_val,
        confidence=0.55 if ambiguous else 0.95,
        confidence_method="reference-deterministic",
        evidence=tuple(evidences),
        ambiguous=ambiguous,
    )


class ReferenceDeterministicStrategy:
    """Offline deterministic ExtractionStrategy for reproducible 50-case golden CI baselines."""

    def __init__(self, *, name: str = "reference-deterministic") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def extract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        text = document.full_text
        if schema.cardinality == ExtractionCardinality.MULTIPLE and _RECORD_SPLIT_RE.search(text):
            raw_blocks = [b for b in _RECORD_SPLIT_RE.split(text) if b.strip()]
        else:
            raw_blocks = [text]

        records: list[ProposedRecord] = []
        for block in raw_blocks:
            lines = _clean_text_lines(block)
            candidates = _extract_kv_candidates(lines)
            fields: dict[str, ProposedField] = {}
            any_present = False
            for fname, fspec in schema.fields.items():
                pf = _extract_field_from_candidates(fname, fspec, candidates)
                fields[fname] = pf
                if pf.presence == FieldPresence.PRESENT:
                    any_present = True
            if schema.cardinality == ExtractionCardinality.MULTIPLE and not any_present:
                continue
            records.append(ProposedRecord(fields=fields))

        return StrategyOutput(
            records=tuple(records),
            default_confidence_method="reference-deterministic",
        )
