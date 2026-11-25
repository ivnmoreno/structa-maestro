# Development Guide — structa-maestro

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Mandatory Quality Checks (CI Parity)

Run these checks locally before opening a pull request:

```bash
ruff format --check .
ruff check .
ruff check --select S src/ evals/
mypy src/ evals/
pytest --cov=structa_maestro --cov-report=term-missing --cov-fail-under=80
pytest tests/evaluation/ -v
```

### Formal Assurance Checks (Lean 4 + Two-Layer Conformance)

With [`elan`](https://github.com/leanprover/elan) installed (which activates the pinned toolchain in `formal/lean-toolchain` automatically):

```bash
cd formal && lake build && lake test
cd .. && STRUCTA_REQUIRE_LEAN_ORACLE=1 pytest tests/conformance/ -v
```

See [docs/formal-assurance.md](formal-assurance.md) for the assurance matrix, formal model scope, and proof boundaries.

## Golden Evaluation Harness (`python -m evals`)

The repository-internal `evals/` package (excluded from published wheel and sdist artifacts) hosts a 50-case synthetic golden evaluation corpus spanning 8 business document categories, 3 input formats (`TXT`, `PDF`, `DOCX`), and 10 challenge profiles.

### Offline Deterministic CI Gate

```bash
# Validate dataset manifest integrity (50 cases, 8 categories, 3 formats, 10 profiles)
python -m evals validate-dataset

# Run full 50-case offline deterministic evaluation against committed baseline
python -m evals run \
  --strategy deterministic \
  --baseline evals/baselines/deterministic_v2.0.0.json \
  --output-json /tmp/eval-report.json \
  --output-md /tmp/eval-summary.md
```

### Filtering, Repeated Trials & Differential Comparison

```bash
# Run a targeted subset (category / format / profile / case IDs)
python -m evals run --category invoices --format pdf

# Run repeated trials to compute mean/min/max metric distributions
python -m evals run --strategy deterministic --trials 3 --output-json /tmp/trials.json

# Compare two saved strategy reports side-by-side
python -m evals diff evals/baselines/deterministic_v2.0.0.json /tmp/trials.json
```

### On-Demand Live LLM Benchmarking (Never Runs in Default CI)

```bash
export STRUCTA_MAESTRO_API_KEY="your-api-key"
python -m evals run \
  --strategy llm \
  --model gpt-4o-mini \
  --structured-output-mode strict_with_fallback \
  --max-retries 2 \
  --max-input-chars 120000 \
  --overflow-policy reject \
  --trials 3 \
  --baseline evals/baselines/deterministic_v2.0.0.json \
  --output-json /tmp/live-benchmark.json \
  --output-md /tmp/live-benchmark.md
```

Secrets such as API keys are read strictly from environment variables and redacted from all reports and error diagnostics.

## Packaging & Clean Install Verification

```bash
python -m build
twine check dist/*
```

Verify both `dist/*.whl` and `dist/*.tar.gz` install cleanly inside isolated virtual environments using declared runtime dependencies only.

## Branch Protection & CI Workflows

GitHub Actions workflows in `.github/workflows/` enforce rules `CI-001`–`CI-020` documented in [docs/TESTING.md](TESTING.md):
- `ci.yml`: formatting, linting, security lint, mypy, pytest (`>=80%` coverage), build, `twine check`, isolated wheel/sdist install smoke tests.
- `formal-assurance.yml`: pinned Lean 4 toolchain (`formal/lean-toolchain`), `lake build` (`warningAsError=true`), kernel proof replay + programmatic `structa-axiom-audit` (`lake test`), and Layer 1 + Layer 2 Python-to-Lean conformance (`STRUCTA_REQUIRE_LEAN_ORACLE=1 pytest tests/conformance/ -v`).
- `security.yml`: `pip-audit`, Ruff security lint, secret scanning.
- `evaluation.yml`: 50-case golden dataset integrity validation, pytest evaluation suite, and offline baseline regression gate across 8 document categories, `TXT`/`PDF`/`DOCX`, and 10 challenge profiles.
- `compatibility.yml`: Python `3.11`, `3.12`, and `3.13` test & packaging matrix.
- `release.yml`: version-tag release qualification (`qualify` + `formal-assurance`) and guarded publishing.
