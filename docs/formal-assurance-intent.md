# Formal Assurance Layer — Architectural Intent

**Project:** Structa  
**Capability:** Formal Assurance Layer  
**Technology:** Lean 4  
**Status:** Proposed Architectural Extension  
**Parent Intent:** [`intent.md`](intent.md)  
**Architecture Reference:** [`architecture.md`](architecture.md)  
**Primary Objective:** Establish machine-checked formal specifications of selected data integrity, provenance, validation, and processing invariants.

---

## 1. Purpose

Introduce Lean 4 as an independent formal specification and theorem-proving layer within Structa.

The purpose is to formalize selected properties of Structa's domain model and canonical output contracts, and to establish machine-checked proofs that the corresponding mathematical specifications satisfy explicitly defined invariants.

Structa transforms unstructured documents into structured, validated, machine-readable records.

The reliability of this process depends on preserving distinctions between extracted values, field presence, confidence, provenance, validation outcomes, and processing status.

The Formal Assurance Layer will establish precise mathematical specifications for these distinctions and their associated invariants.

It complements the existing Python implementation, Pydantic contracts, deterministic validation, contract tests, property-based tests, integration tests, and regression tests.

It does not replace them.

## 2. Architectural Position

Python remains the primary implementation and runtime language.

Lean 4 is used exclusively for:

- Formal specification of selected domain concepts.
- Mathematical modeling of canonical contract invariants.
- Machine-checked proofs of explicitly stated properties.
- Formal modeling of selected validation and provenance semantics.
- Reference specifications for independent Python conformance testing.
- Documenting assumptions, guarantees, and verification limitations.

Lean must not become a runtime dependency of the Python package.

Ordinary Structa consumers must not need Lean installed to install, import, or operate the library.

The formal project must be independently buildable using Lean's Lake build system.

The formal specification must remain separate from the production extraction pipeline.

No runtime FFI, Lean service, or mandatory language interoperability layer is required.

## 3. Relationship with Existing Architecture

The Formal Assurance Layer extends the existing architecture without replacing or restructuring its established responsibilities.

The following architectural boundaries remain authoritative:

### 3.1 Strategy-to-Contract Firewall

Extraction strategies produce internal `StrategyOutput` objects.

They must not construct public `ExtractionResult` objects directly.

Provider-specific response structures must not leak into the canonical output contract.

Formal verification must preserve this separation.

### 3.2 Canonical Output Record Contract

Canonical Output Record Contract v1.0 remains the authoritative public result model.

The formal specification must reflect the existing contract's semantics, including:

- `ExtractionResult`
- `ExtractedRecord`
- `ExtractedField`
- `ValidationOutcome`
- `DiagnosticIssue`
- `ConfidenceAssessment`
- `ProvenanceEvidence`
- `SourceLocation`
- `SourceMetadata`
- `SchemaMetadata`

The formal model must not silently introduce a new contract version or change existing serialization semantics.

### 3.3 Independent Quality Dimensions

The following dimensions must remain independent:

- Extracted value.
- Field presence.
- Confidence.
- Provenance.
- Validation.
- Processing status.

Formal specifications must preserve these distinctions.

In particular:

- Confidence is not validation.
- Provenance is not confidence.
- Validation is not proof of factual correctness.
- Processing completion is not schema validity.
- Field presence is not equivalent to whether a value is null.

### 3.4 Deterministic Validation

The existing deterministic validator remains the authoritative production implementation.

Lean will formalize selected validation semantics and their mathematical properties.

It must not replace the Python validator or introduce different validation behavior.

## 4. Initial Formalization Scope

The first implementation increment focuses on the Canonical Output Record Contract v1.0.

The initial formalization should establish the mathematical representation and selected invariants of the following domain concepts.

### 4.1 Field Presence and Values

Formalize:

- Present fields.
- Missing fields.
- Explicitly null values.
- Non-null extracted values.

The model must preserve the distinction between:

- A field that was not found.
- A field that was found with an explicit null value.
- A field that was found with a non-null value.

Required property:

A field with `FieldPresence.MISSING` cannot contain a non-null extracted value.

Do not assume that a present field must contain a non-null value.

### 4.2 Validation Outcomes

Formalize:

- `VALID`
- `INVALID`
- `NOT_VALIDATED`

Represent validation diagnostics with their severity and originating pipeline stage.

Required properties:

- Errors contained in a `ValidationOutcome` must have ERROR severity.
- Errors contained in a `ValidationOutcome` must originate from the validation stage.
- A non-INVALID validation outcome cannot contain validation errors.
- VALID, INVALID, and NOT_VALIDATED remain distinct states.

The formal specification must reflect the existing implementation rather than assume that INVALID necessarily implies a non-empty error collection.

### 4.3 Confidence Assessment

Formalize the relationship between:

- Confidence value.
- Confidence scale.
- Assessment method.
- Calibration indicator.

Required properties:

- A non-null confidence value using the declared `0-1` scale must lie within the permitted interval.
- An unavailable confidence value must remain representable.
- Confidence must remain independent of validation and provenance.

Do not treat confidence as a calibrated probability unless calibration has actually been established.

Do not impose additional calibration restrictions beyond the existing contract.

### 4.4 Provenance and Source Identity

Formalize:

- Source document identity.
- Evidence identity.
- Evidence relationship.
- Optional source location.
- Source location boundaries.

The evidence relationship must preserve the existing categories:

- DIRECT
- INFERRED
- CONFLICTING
- UNVERIFIED

Required properties:

- When source metadata is available, provenance entries must reference the originating document.
- Character and line spans must respect the existing ordering constraints.
- Optional location components must remain independently representable.
- Evidence relationship must not be confused with confidence.

The formal specification must distinguish structural provenance invariants from semantic claims about whether evidence actually supports an extracted value.

Formal verification of source identity or span boundaries does not establish semantic truth.

### 4.5 Record and Result Identity

Formalize selected invariants concerning:

- Extraction identity.
- Record identity.
- Schema identity.
- Contract version.
- Record collection integrity.

Required properties:

- Record identifiers must be unique within an extraction result.
- Contract identity must remain explicit.
- Schema identity must remain distinguishable from extraction identity.
- Records must remain independently identifiable.

### 4.6 Processing and Validation Status

Formalize the distinction between processing status and validation status.

Processing status:

- COMPLETED
- PARTIAL
- FAILED

Validation status:

- VALID
- INVALID
- NOT_VALIDATED

Required properties:

- Processing status and validation status are independent dimensions.
- Successful processing does not imply valid schema validation.
- Partial processing does not automatically imply invalid schema validation.
- Failed processing does not automatically imply invalid schema validation.
- A result that has not undergone validation must not be represented as successfully validated.

The formal model must reflect the actual `ResultBuilder` status-assembly rules, including their dependence on strategy failures, partial processing, processing errors, and record-level outcomes.

## 5. Formal Verification Boundaries

Lean proofs establish properties of the formal specification.

They do not automatically establish correctness of the Python implementation.

Therefore, formal verification must be complemented by independent Python-to-Lean conformance testing.

Conformance testing must:

- Compare Python contract behavior against the formal reference model.
- Cover valid and invalid contract states.
- Exercise boundary conditions.
- Include representative processing and validation combinations.
- Detect semantic divergence.
- Run automatically in CI.

The verification architecture must explicitly distinguish:

1. Machine-checked mathematical proofs.
2. Python implementation conformance tests.
3. Conventional unit and integration tests.
4. End-to-end extraction evaluations.

No claim of complete Python implementation verification may be made without an independently established proof of implementation correspondence.

## 6. Future Formalization Extensions

The following areas are candidates for subsequent formalization.

They are not commitments of the initial implementation increment.

### 6.1 Schema Well-Formedness

Potential future work:

- Field-type and constraint compatibility.
- Required and optional field semantics.
- Numeric and string constraint consistency.
- Array and object constraint consistency.
- Nested schema invariants.
- Schema cardinality properties.

### 6.2 Deterministic Validation

Potential future work:

- Field-level validation semantics.
- Cross-field rule semantics.
- Conditional validation.
- Unexpected field handling.
- Exact decimal arithmetic properties.
- Validation determinism.

### 6.3 Provenance Association

Potential future work:

- Evidence-to-document association.
- Source-span mapping.
- Quote matching properties.
- Evidence relationship classification.
- Direct-value conflict detection.
- Provenance integrity across multiple records.

### 6.4 Confidence Normalization

Potential future work:

- Numeric range enforcement.
- Non-finite input handling.
- Normalization determinism.
- Scale interpretation.
- Preservation of calibration semantics.

### 6.5 Result Assembly

Potential future work:

- Record-level status aggregation.
- Result-level status aggregation.
- Partial-result consistency.
- Diagnostic propagation.
- Multi-record cardinality semantics.

Each extension requires a separate feasibility assessment and independently testable specification.

## 7. Engineering Requirements

### 7.1 Formal Project

- Maintain an independent Lean 4 project under `formal/`.
- Use Lake for project management and builds.
- Pin the Lean toolchain.
- Keep specifications modular and domain-oriented.
- Avoid unnecessary external Lean dependencies.
- Ensure all required proofs are reproducibly checkable.

### 7.2 Specification Quality

- Use explicit mathematical definitions.
- Make assumptions visible.
- Use meaningful theorem statements.
- Avoid axioms or assumptions that trivialize required proofs.
- Document proof boundaries.
- Preserve the existing Python contract semantics.
- Do not introduce requirements solely to simplify proofs.

### 7.3 Python Integration

- Preserve the existing Python package architecture.
- Keep Lean outside runtime dependencies.
- Use Python reference-model conformance tests.
- Reuse existing contract and property-based test infrastructure where appropriate.
- Avoid unnecessary duplication of production business logic.

### 7.4 CI/CD

The formal project must have an independent CI verification job.

CI must:

1. Activate the pinned Lean toolchain.
2. Build the formal project.
3. Check all required proofs.
4. Run the Python conformance suite.
5. Reject failed mandatory verification stages.

Formal verification must become part of the repository's release acceptance process.

## 8. Non-Goals

The Formal Assurance Layer must not:

- Replace Python with Lean.
- Introduce a Lean runtime service.
- Require Lean for ordinary package consumers.
- Replace Pydantic or the existing validation engine.
- Replace conventional testing.
- Attempt to prove LLM extraction accuracy.
- Attempt to prove the semantic truth of arbitrary extracted values.
- Claim that a source quotation necessarily supports an extracted value merely because it exists in the source document.
- Formally verify PDF, DOCX, HTML, Markdown, or EML parsing in the initial implementation.
- Attempt to formally verify external model providers.
- Introduce a new public output contract solely for formal verification.
- Claim complete verification of the Python implementation.

## 9. Documentation Requirements

The project must document:

- The purpose of formal verification in Structa.
- The formal domain model.
- The relationship between Lean and Python.
- The properties covered by machine-checked proofs.
- The properties covered only by conformance tests.
- Assumptions and limitations.
- How to build and verify the Lean project.
- How to execute Python-to-Lean conformance tests.
- How formal verification participates in CI/CD.

The documentation must distinguish formal guarantees from empirical observations and operational assumptions.

## 10. Definition of Success

The initial Formal Assurance Layer is successful when:

1. A standalone Lean 4 project exists within the repository.
2. The Canonical Output Record Contract v1.0 has an explicit formal domain model.
3. Selected field, validation, confidence, provenance, identity, and status invariants have machine-checked proofs.
4. The proofs preserve existing Python contract semantics.
5. Python conformance tests establish agreement with the formal reference model for the tested cases.
6. CI independently enforces Lean compilation, proof checking, and conformance testing.
7. Existing Python APIs and runtime behavior remain unchanged.
8. Ordinary Structa consumers require no additional Lean installation.
9. Proof assumptions and limitations are documented.

## 11. Architectural Principle

Structa must distinguish between extracting information, representing information, validating information, and establishing the provenance of information.

The Formal Assurance Layer exists to make selected guarantees about these operations mathematically explicit and machine-checkable.

It strengthens the reliability of Structa's contracts without overstating what formal verification can establish about the underlying extraction process.

**Architectural Principle:**

Structa uses deterministic processing and empirical testing to establish observed implementation behavior, while formal verification establishes mathematical properties of explicitly defined domain models. Both contribute to assurance, and neither is misrepresented as proof of the factual correctness of extracted information.