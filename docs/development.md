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
ruff check --select S src/
mypy src/
pytest --cov=structa_maestro --cov-report=term-missing --cov-fail-under=80
pytest tests/evaluation/ -v
```

## Packaging & Clean Install Verification

```bash
python -m build
twine check dist/*
```

Verify both `dist/*.whl` and `dist/*.tar.gz` install cleanly inside isolated virtual environments using declared runtime dependencies only.

## Branch Protection & CI Workflows

GitHub Actions workflows in `.github/workflows/` enforce rules `CI-001`–`CI-020` documented in [docs/TESTING.md](TESTING.md):
- `ci.yml`: formatting, linting, security lint, mypy, pytest (`>=80%` coverage), build, `twine check`, isolated wheel/sdist install smoke tests.
- `security.yml`: `pip-audit`, Ruff security lint, secret scanning.
- `evaluation.yml`: golden dataset regression suite across 5 document categories and `TXT`/`PDF`/`DOCX`.
- `compatibility.yml`: Python `3.11`, `3.12`, and `3.13` test & packaging matrix.
- `release.yml`: version-tag release qualification and guarded publishing.
