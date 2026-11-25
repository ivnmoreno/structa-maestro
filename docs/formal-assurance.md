# Formal Assurance Layer (`formal/`)

`structa-maestro` includes an independent **Lean 4** formal specification and theorem-proving layer under `formal/` that formally specifies selected invariants of **Canonical Output Record Contract v1.0** and complements them with an independent two-layer Python-to-Lean conformance suite.

> **Architectural Principle**: Structa uses deterministic processing and empirical testing to establish observed implementation behavior, while formal verification establishes mathematical properties of explicitly defined domain models. Both contribute to assurance, and neither is misrepresented as proof of the factual correctness of extracted information.

---

## 1. Architectural Position & Runtime Isolation

- **Python remains the sole runtime language**: `structa_maestro` has **zero** Lean runtime dependencies, **zero** FFI bridges, and **zero** Lean background services.
- **Zero consumer impact**: Ordinary users installing `structa-maestro` from PyPI wheels or source distributions never need Lean or Lake installed; `formal/` is strictly excluded from both built wheels and source distributions (`sdist`).
- **Standalone Lake project**: `formal/` is pinned to `leanprover/lean4:v4.36.0` (`formal/lean-toolchain`) with **zero external Lean dependencies** (no Mathlib), strict `warningAsError = true` compilation, a programmatic `Lean.collectAxioms` theorem audit (`lake test`), and independent kernel checking (`lake env leanchecker StructaFormal`).
- **Contract v1.0 unchanged**: The formal layer faithfully mirrors the authoritative Python implementation (`contracts/models.py`, `contracts/enums.py`, `validation/validator.py`, `confidence/normalizer.py`, `provenance/associator.py`, `builder/result_builder.py`) without altering Contract v1.0 schemas or runtime APIs.

---

## 2. Formal Domain Model (`formal/StructaFormal/`)

| Module | Authoritative Python Source | Formalized Scope |
| --- | --- | --- |
| `Enums.lean` | `contracts/enums.py` | 7 Contract v1.0 sum types (`FieldType`, `ProcessingStatus`, `ValidationStatus`, `FieldPresence`, `EvidenceRelationship`, `IssueSeverity`, `PipelineStage`) + string codecs |
| `Models.lean` | `contracts/models.py` | All 10 Contract v1.0 structures (`SourceMetadata`, `SchemaMetadata`, `SourceLocation`, `ProvenanceEvidence`, `ConfidenceAssessment`, `DiagnosticIssue`, `ValidationOutcome`, `ExtractedField`, `ExtractedRecord`, `ExtractionResult`), `Valid* : T → Prop`, decidable `isValid* : T → Bool`, and `isValid*_iff` soundness/completeness bridges |
| `FieldInvariants.lean` | `contracts/models.py` | Missing-has-no-value, missing vs. explicit-null separation, present null/non-null support, presence-validation independence |
| `ValidationInvariants.lean` | `contracts/models.py`, `validation/validator.py` | Structural `ValidationOutcome` invariants (faithfully permitting `ValidationOutcome(INVALID, [])` per `models.py`) + `DeterministicValidator` outcome construction (`INVALID ↔ errors ≠ []`, `VALID ↔ errors = []`, `NOT_VALIDATED` on pre-validation empty failures) |
| `ConfidenceInvariants.lean` | `contracts/models.py`, `confidence/normalizer.py` | `"0-1"` exact rational `[0, 1]` bounds, explicit unavailability, calibration distinction, `ConfidenceNormalizer` uncalibrated/unavailable guarantees, independence from validation & provenance |
| `ProvenanceInvariants.lean` | `contracts/models.py`, `provenance/associator.py` | Source-document binding when `source` is present, `SourceLocation` bounds/span-ordering/independent optionality, `UNVERIFIED` vs. verified separation, identity-vs-location separation, `ProvenanceAssociator` foreign-doc rejection & unlocated classification |
| `IdentityInvariants.lean` | `contracts/models.py` | Explicit `"1.0"` contract version, pairwise unique `record_id`s, non-empty identifiers, extraction-vs-schema identity separation |
| `StatusAssembly.lean` | `contracts/models.py`, `builder/result_builder.py` | Processing-vs-validation independence + exact `ResultBuilder` assembly rules (`build_failed`, `PARTIAL` never promoted to `COMPLETED`, `strategy_output.failed=True` with preserved records, record-level `FAILED` independence) |
| `Oracle.lean` / `OracleMain.lean` | Dev/CI conformance only | Standalone JSONL CLI (`structa-formal-oracle`) evaluating Lean's actual decidable `isValid*` and assembly functions on Python-generated Contract v1.0 test vectors |
| `AxiomAuditMain.lean` | Lake `testDriver` | Programmatically verifies all 46 named theorems exist in `StructaFormal` and use ONLY Lean's standard core axioms (`propext`, `Classical.choice`, `Quot.sound`) with zero `sorryAx` |

---

## 3. Assurance Classification Matrix

Structa explicitly distinguishes **four independent assurance levels** and never conflates them:

| Property / Area | Lean 4 Model Proof | Python-to-Lean Conformance (Layer 1 & 2) | Conventional / Golden Tests | Status / Boundary |
| --- | --- | --- | --- | --- |
| Missing field cannot hold non-null value; missing ≠ explicit null | **Proved** (`FieldInvariants.lean`) | **Verified** (deterministic + Hypothesis + Lean oracle) | `tests/contract/` | Complete within Contract v1.0 model |
| Structural `ValidationOutcome` rules & `DeterministicValidator` outcome rules (`INVALID ↔ errors ≠ []`) | **Proved** (`ValidationInvariants.lean`) | **Verified** (Layer 1 + Layer 2) | `tests/unit/test_validation.py` | Structural `INVALID + []` faithfully preserved |
| Confidence `"0-1"` bounds (`[0, 1]`), explicit unavailability, `ConfidenceNormalizer` `calibrated = False` | **Proved** (`ConfidenceInvariants.lean`, exact `Rat`) | **Verified** (including IEEE-754 `NaN`/`±inf`/bool/rounding) | `tests/unit/test_confidence.py` | IEEE-754 float-vs-`Rat` boundary tested empirically |
| Provenance document binding, `SourceLocation` bounds/ordering/optionality, `UNVERIFIED` separation, `ProvenanceAssociator` rules | **Proved** (`ProvenanceInvariants.lean`) | **Verified** (Layer 1 + Layer 2) | `tests/unit/test_provenance.py` | Structural integrity only (not semantic quote truth) |
| Contract `"1.0"`, unique `record_id`, schema vs. extraction identity | **Proved** (`IdentityInvariants.lean`) | **Verified** (Layer 1 + Layer 2) | `tests/contract/` | Complete within Contract v1.0 model |
| Processing vs. validation status independence & `ResultBuilder` status assembly | **Proved** (`StatusAssembly.lean`) | **Verified** (exhaustive 64-case result + 16-case record matrices) | `tests/unit/test_result_builder.py` | Complete for status-assembly flags |
| JSON round-trip serialization, Pydantic v2 binding, CLI, async/batch, telemetry | Out of scope | Out of scope | `tests/contract/`, `tests/property/`, `tests/integration/` | Covered by Python test suites |
| Document parsers (TXT/PDF/DOCX/HTML/Markdown/EML) | **Explicitly out of scope** | N/A | `tests/document_processing/`, `tests/security/` | Not formally verified |
| LLM strategies, external providers, extraction/factual accuracy, regexes, full cross-field execution, calibration algorithms | **Explicitly out of scope** | N/A | `tests/integration/`, `evals/` (50-case golden suite) | Empirical testing & evaluation only |

---

## 4. Explicit Assumptions & Limitations

1. **Formal model vs. Python runtime**: A Lean 4 proof establishes mathematical truth about the Lean specification in `formal/StructaFormal/`, *not* automatic equivalence of Python/Pydantic execution. Empirical correspondence is established by the two-layer conformance suite (`tests/conformance/`).
2. **Exact rational vs. IEEE-754 floating-point confidence**: `ConfidenceInvariants.lean` models finite confidence values using Lean's exact rational numbers (`Rat`) and represents `NaN`/`±inf`/non-numeric inputs explicitly via `ConfidenceInput`. Python's `ConfidenceNormalizer` rounds valid finite floats via `round(numeric, 6)` and operates on IEEE-754 floats; `tests/conformance/` exercises IEEE-754 edge cases (`0.0`, `1.0`, `NaN`, `+inf`, `-inf`, booleans, out-of-range floats) directly against Python and the Lean oracle.
3. **Abstract field values**: `ExtractedField.value` is modeled as `FieldValue.nullValue | FieldValue.nonNull reprTag` because Contract v1.0's structural field invariant depends solely on null-vs-non-null distinction, not arbitrary dynamic Python object types.
4. **No semantic truth claims**: Proving that a provenance entry references `source.document_id` and has valid `char_start ≤ char_end` never proves that the extracted value is factually true in the real world.

---

## 5. Local Verification & CI Enforcement

### Build & Check Lean 4 Proofs Locally

Install [`elan`](https://github.com/leanprover/elan) once; it automatically selects the pinned toolchain in `formal/lean-toolchain`:

```bash
cd formal
lake build
lake test
lake env leanchecker StructaFormal
```

### Run Two-Layer Python-to-Lean Conformance Tests

```bash
# Layer 1 (always-on pure Python + Hypothesis; runs Layer 2 automatically if lake is available)
pytest tests/conformance/ -v

# Mandatory CI mode (fails if Lean oracle cannot be executed)
STRUCTA_REQUIRE_LEAN_ORACLE=1 pytest tests/conformance/ -v
```

### Continuous Integration & Release Gates

- **Pull Requests & `main`**: `.github/workflows/formal-assurance.yml` installs the pinned Lean toolchain, runs `lake build` (`warningAsError = true`), runs `lake test` (`structa-axiom-audit`), runs `lake env leanchecker StructaFormal`, and runs `STRUCTA_REQUIRE_LEAN_ORACLE=1 pytest tests/conformance/ -v`.
- **Release Qualification**: `.github/workflows/release.yml` gates PyPI publishing on both `qualify` and `formal-assurance` passing.
