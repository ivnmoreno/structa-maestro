"""Implementing a custom ExtractionStrategy behind structa-maestro's strategy protocol."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from structa_maestro import (
    ExtractionSchema,
    FieldPresence,
    PreparedDocument,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
    extract,
)


class RegexKeyValueStrategy:
    """Minimal custom strategy demonstrating protocol compliance without canonical coupling."""

    @property
    def name(self) -> str:
        return "regex-key-value-demo"

    def extract_raw(
        self,
        document: PreparedDocument,
        schema: ExtractionSchema,
    ) -> StrategyOutput:
        fields: dict[str, ProposedField] = {}
        for fname in schema.fields:
            match = re.search(
                rf"^{re.escape(fname)}:\s*(.+)$", document.full_text, flags=re.I | re.M
            )
            if match:
                fields[fname] = ProposedField(
                    presence=FieldPresence.PRESENT,
                    value=match.group(1).strip(),
                    confidence=0.9,
                    confidence_method="regex-exact-label",
                    evidence=(ProposedEvidence(quote=match.group(0).strip()),),
                )
            else:
                fields[fname] = ProposedField(presence=FieldPresence.MISSING, value=None)
        return StrategyOutput(records=(ProposedRecord(fields=fields),))


def main() -> None:
    schema = {
        "schema_id": "memo-schema",
        "schema_version": "1.0",
        "fields": {
            "author": {"type": "string", "required": True},
            "topic": {"type": "string", "required": True},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        doc_path = Path(tmp) / "memo.txt"
        doc_path.write_text(
            "author: Dr. Ada Lovelace\ntopic: Analytical Engine Notes\n", encoding="utf-8"
        )
        result = extract(doc_path, schema, strategy=RegexKeyValueStrategy())
        print(result.to_json(indent=2))


if __name__ == "__main__":
    main()
