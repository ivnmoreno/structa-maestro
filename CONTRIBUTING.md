# Contributing to `structa-maestro`

Thank you for your interest in contributing to **`structa-maestro`** (`structa_maestro`)! We welcome bug reports, documentation improvements, test coverage additions, and well-scoped feature pull requests.

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating. For security vulnerabilities, **do not** open a public issue — follow our private disclosure process in [SECURITY.md](SECURITY.md).

---

## Project Principles & Scope

Before proposing major changes, please review the repository's core engineering principles:

1. **Library-First Identity**: `structa-maestro` is a reusable Python library (`structa_maestro`) with an optional thin CLI (`structa-maestro`). Core extraction must remain stateless and in-process without requiring a database, web UI, background daemon, or external orchestrator.
2. **Canonical Output Record Contract (`v1.0`) Stability**: Every extraction returns an immutable, strategy-independent `ExtractionResult` with lossless JSON round-trip semantics. Provider-specific response structures must never leak into public outputs.
3. **Separation of Quality Dimensions**: Extracted values, confidence, provenance, validation, and processing status are modeled independently. Never silently coerce invalid types or fabricate missing values.
4. **Controlled Dependencies**: Keep runtime dependencies minimal (`pydantic`, `pypdf`, `python-docx`, `httpx`). Any new runtime dependency requires strong written justification.
5. **Security & Data Minimization**: Treat all documents as untrusted input. Never log or expose raw document text, local host directory paths, or API credentials in defaults, errors, or tracebacks.

---

## Local Development Setup

Requires **Python 3.11, 3.12, or 3.13**.

```bash
git clone https://github.com/ivnmoreno/structa-maestro.git
cd structa-maestro
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

---

## Branch & Pull Request Workflow

1. **Never commit directly to `main`**: Create a descriptive feature or fix branch off `main` (e.g., `feat/<short-name>`, `fix/<short-name>`, or `docs/<short-name>`).
2. **Use Conventional Commits**: Follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) with a concise subject line (`<= 72` characters). This repository is configured with Commitizen (`cz_conventional_commits`).
3. **Keep Documentation Synchronized**: Any change to public APIs, CLI flags, schemas, limits, or security behavior must update the relevant docs (`README.md`, `docs/api.md`, `docs/architecture.md`, `docs/configuration.md`, `CHANGELOG.md`) and examples in the same pull request.
4. **Ensure CI Passes**: Every PR targeting `main` must pass all mandatory GitHub Actions workflows (`ci.yml`, `security.yml`, `evaluation.yml`, and `compatibility.yml`).

---

## Running Local Quality Checks (CI Parity)

Run the full local qualification gate before opening or updating a pull request:

```bash
# Formatting & linting
ruff format --check .
ruff check .
ruff check --select S src/ evals/

# Strict static type checking
mypy src/ evals/

# Full test suite (>= 80% branch coverage required)
pytest --cov=structa_maestro --cov-report=term-missing --cov-fail-under=80

# Golden evaluation corpus integrity & zero-regression gate (50 offline cases)
pytest tests/evaluation/ -v
python -m evals validate-dataset
python -m evals run --strategy deterministic --baseline evals/baselines/deterministic_v2.0.0.json

# Packaging validation (confirm evals/ is excluded from wheel and sdist)
python -m build
twine check dist/*
```

See [docs/development.md](docs/development.md) and [docs/TESTING.md](docs/TESTING.md) for detailed test architecture, golden evaluation filtering, and live benchmarking options.

---

## Reporting Bugs & Requesting Features

- **Bug Reports**: Include your Python version, `structa-maestro` version, a minimal reproducible example (using synthetic/redacted input — never real confidential documents or API keys), expected behavior, and actual behavior.
- **Feature Proposals**: Open a GitHub issue describing the concrete problem, why it fits `structa-maestro`'s stateless extraction scope, and any impact on public APIs or Contract `v1.0`.
- **Security Issues**: Report privately via [SECURITY.md](SECURITY.md) — never disclose unpatched vulnerabilities in public issues or pull requests.

---

## Pull Request Checklist

- [ ] Changes are on a dedicated branch targeting `main`
- [ ] All unit, integration, contract, security, and evaluation tests pass (`>= 80%` coverage)
- [ ] `ruff format --check .`, `ruff check .`, `ruff check --select S src/ evals/`, and `mypy src/ evals/` pass cleanly
- [ ] Zero regressions on the 50-case offline golden baseline (`evals/baselines/deterministic_v2.0.0.json`)
- [ ] No API keys, credentials, or real confidential documents included in code, fixtures, or tests
- [ ] Documentation (`README.md`, `docs/`, `CHANGELOG.md`) and runnable examples updated where applicable
