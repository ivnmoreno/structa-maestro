"""50-case golden evaluation parity test using hardened OpenAICompatibleLLMStrategy (T019)."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from evals.comparison import compare_against_baseline
from evals.dataset import DEFAULT_MANIFEST_PATH, load_dataset
from evals.models import RegressionThresholds
from evals.reference_strategy import ReferenceDeterministicStrategy
from evals.reporting import load_report
from evals.runner import EvaluationRunner
from structa_maestro import (
    OpenAICompatibleLLMStrategy,
    RetryPolicy,
)
from structa_maestro.contracts.enums import DocumentFormat
from structa_maestro.contracts.models import SourceMetadata
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.schema.models import ExtractionSchema

BASELINE_PATH = (
    Path(__file__).parent.parent.parent / "evals" / "baselines" / "deterministic_v2.0.0.json"
)
_UNTRUSTED_BLOCK_RE = re.compile(
    r'<untrusted_document nonce="[^"]+">\n(.*)\n</untrusted_document>',
    re.DOTALL,
)
_SCHEMA_BLOCK_RE = re.compile(
    r"Target Schema:\n(\{.*?\})\n\nCardinality Instruction:",
    re.DOTALL,
)


def _json_safe(val: Any) -> Any:
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, date):
        return val.isoformat()
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_json_safe(v) for v in val]
    return val


@pytest.mark.evaluation
def test_hardened_llm_strategy_50_case_golden_parity_with_faults_and_fallback() -> None:
    manifest = load_dataset(DEFAULT_MANIFEST_PATH, validate_full_corpus=True)
    baseline = load_report(BASELINE_PATH)
    ref_strategy = ReferenceDeterministicStrategy()

    schema_by_id = {
        case.schema_definition["schema_id"]: ExtractionSchema.from_dict(case.schema_definition)
        for case in manifest.cases
    }

    request_count = 0
    fallback_injected = 0
    retry_injected = 0
    fenced_injected = 0
    strict_requests_seen = 0

    def _simulated_openai_endpoint(req: httpx.Request) -> httpx.Response:
        nonlocal request_count, fallback_injected, retry_injected, fenced_injected
        nonlocal strict_requests_seen
        request_count += 1
        payload = json.loads(req.content.decode("utf-8"))
        rf = payload["response_format"]
        if rf["type"] == "json_schema":
            assert rf["json_schema"]["strict"] is True
            strict_requests_seen += 1

        user_msg = payload["messages"][1]["content"]
        doc_match = _UNTRUSTED_BLOCK_RE.search(user_msg)
        schema_match = _SCHEMA_BLOCK_RE.search(user_msg)
        assert doc_match is not None and schema_match is not None

        # Restore any neutralized delimiter tokens for the reference simulator
        raw_text = doc_match.group(1).replace("&lt;", "<")
        schema_id = json.loads(schema_match.group(1))["schema_id"]
        schema = schema_by_id[schema_id]

        # Inject strict-schema fallback on request #1, transient 503 on request #5
        if request_count == 1 and rf["type"] == "json_schema":
            fallback_injected += 1
            return httpx.Response(400, text="json_schema response_format is not supported")
        if request_count == 5:
            retry_injected += 1
            return httpx.Response(503, text="simulated transient outage")

        doc = PreparedDocument(
            source=SourceMetadata(
                document_id="sim-doc",
                filename="sim.txt",
                media_type="text/plain",
            ),
            format=DocumentFormat.TXT,
            full_text=raw_text,
            segments=(DocumentSegment(text=raw_text, char_start=0, char_end=len(raw_text)),),
        )
        out = ref_strategy.extract_raw(doc, schema)
        wire_records = []
        for rec in out.records:
            wire_fields: dict[str, Any] = {}
            for fname, pf in rec.fields.items():
                wire_fields[fname] = {
                    "presence": pf.presence.value,
                    "value": _json_safe(pf.value),
                    "confidence": pf.confidence,
                    "ambiguous": pf.ambiguous,
                    "evidence": [
                        {"quote": ev.quote, "relationship": ev.relationship} for ev in pf.evidence
                    ],
                }
            wire_records.append({"fields": wire_fields})

        content = json.dumps({"records": wire_records})
        if request_count == 9:
            fenced_injected += 1
            content = f"```json\n{content}\n```"

        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        )

    transport = httpx.MockTransport(_simulated_openai_endpoint)
    with httpx.Client(transport=transport) as client:
        strategy = OpenAICompatibleLLMStrategy(
            model="gpt-4o-mini-sim",
            api_key="sk-eval-sim-secret-123456",
            base_url="https://api.example.test/v1",
            http_client=client,
            retry_policy=RetryPolicy(max_retries=2, initial_backoff_seconds=0.01),
            sleep_fn=lambda _s: None,
        )
        report = EvaluationRunner(manifest).run(strategy=strategy)

    assert strict_requests_seen > 0
    assert fallback_injected == 1
    assert retry_injected == 1
    assert fenced_injected == 1

    comparison = compare_against_baseline(report, baseline, RegressionThresholds())
    assert comparison.passed, f"Golden LLM parity regression: {comparison.failure_reasons}"
    assert report.summary.total_cases == 50
    assert report.summary.hallucination_rate == 0.0
    assert report.summary.provenance_accuracy == 1.0
    assert report.summary.field_exact_match_accuracy == 1.0
