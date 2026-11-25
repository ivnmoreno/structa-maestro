# Structa Formal Assurance Layer (`formal/`)

Independent **Lean 4** formal specification and theorem-proving project for Structa's **Canonical Output Record Contract v1.0**, **Schema Well-Formedness**, and **Deterministic Validation Semantics**.

> **Zero runtime dependency**: `formal/` is strictly a development-time and CI-time assurance layer. It is excluded from `structa-maestro` Python wheel and source distributions and is never imported or invoked at runtime by `structa_maestro`.

## Pinned Toolchain

- Toolchain file: `formal/lean-toolchain` (`leanprover/lean4:v4.36.0`)
- Build system: Lake (`formal/lakefile.toml`, zero external Lean dependencies, `warningAsError = true`)

## Build & Verify Locally

Install [`elan`](https://github.com/leanprover/elan) (which automatically activates the toolchain in `formal/lean-toolchain`), then run:

```bash
cd formal
lake build
lake test
```

- `lake build` compiles `StructaFormal`, `structa-formal-oracle`, and `structa-axiom-audit` with `warningAsError = true` (any `sorry` or warning fails the build).
- `lake test` runs `structa-axiom-audit`, which replays every declaration in `StructaFormal` from scratch against a clean `Init` kernel environment (`Lean.Kernel.Environment.replay`) and programmatically audits all **78** required theorems (46 Contract v1.0 + 32 Schema & Deterministic Validation) via `Lean.collectAxioms` to confirm they depend only on Lean's standard core axioms (`propext`, `Classical.choice`, `Quot.sound`) with zero `sorryAx`.

## Run Two-Layer Python Conformance Tests

From the repository root (with `.venv` active):

```bash
# Layer 1 (Python-only, runs everywhere) + Layer 2 (automatically runs when lake is on PATH)
pytest tests/conformance/ -v

# Mandatory CI mode (fails if the Lean oracle cannot be executed)
STRUCTA_REQUIRE_LEAN_ORACLE=1 pytest tests/conformance/ -v
```

See [docs/formal-assurance.md](../docs/formal-assurance.md) for the complete assurance matrix, formal model overview, assumptions, and explicit non-goals.
