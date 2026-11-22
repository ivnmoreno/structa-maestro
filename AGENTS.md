# Workflow & Git Guardrails

## 1. Protected `main` Branch, Branch-Per-Spec & CI-Gated PRs

- Treat `main` as protected against direct commits — all changes MUST land via a Pull Request (PR).
- Before starting any new specification (including `/speckit-specify`) or new feature work in this repository:
  1. Check the active Git branch (`git branch --show-current`).
  2. If currently on `main` (or `master`), create and switch to a new descriptive feature branch (e.g., `git checkout -b <NNN-short-name>` or `feat/<short-name>`) **before** creating spec files or editing code.
  3. Never commit or push directly to `main`; prepare changes on the feature branch for a Pull Request.
- **CI Gate Before Merge**:
  - All required CI checks MUST pass before any PR can be merged into `main`.
  - Before reporting an implementation as complete, run the repository's local CI-equivalent checks (`ruff format --check .`, `ruff check .`, `ruff check --select S src/`, `mypy src/`, `pytest --cov=structa_maestro --cov-fail-under=80`, `pytest tests/evaluation/`, and packaging checks when applicable) and fix all failures.
  - Never merge (or recommend merging) a PR while required CI checks are failing, skipped unexpectedly, or still pending; diagnose and fix CI failures on the feature branch first.

## 2. Commit Message Output After Implementation

- Whenever an implementation completes (including `/speckit-implement` or any substantive coding task), ALWAYS include a ready-to-paste commit message in a fenced `text` code block at the end of the response for the VS Code Source Control commit message box.
- Use Conventional Commits format (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) with a concise subject line (`<= 72` chars) and a short bulleted summary of key changes when warranted.
