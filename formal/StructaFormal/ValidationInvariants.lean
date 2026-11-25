/-
  Structa Formal Assurance Layer — Validation Outcome & DeterministicValidator Invariants.
  Authoritative Python sources:
  - `src/structa_maestro/contracts/models.py` (`ValidationOutcome`)
  - `src/structa_maestro/validation/validator.py` (`DeterministicValidator`)
-/

import StructaFormal.Models

namespace StructaFormal

/-- Formalization of `DeterministicValidator` outcome construction (`validator.py:103-106, 144-147`). -/
def makeValidationOutcome (errs : List DiagnosticIssue) : ValidationOutcome :=
  if errs.isEmpty then
    { status := .valid, errors := [] }
  else
    { status := .invalid, errors := errs }

/-- Formalization of `DeterministicValidator.validate_result_cardinality` (`validator.py:154-202`). -/
def makeResultCardinalityOutcome
    (failedBeforeValidation hasRecords : Bool)
    (topErrors : List DiagnosticIssue) : ValidationOutcome :=
  if failedBeforeValidation && !hasRecords then
    { status := .notValidated, errors := [] }
  else
    makeValidationOutcome topErrors

/-- `VALID`, `INVALID`, and `NOT_VALIDATED` are mutually distinct states. -/
theorem validation_status_mutually_distinct :
    ValidationStatus.valid ≠ ValidationStatus.invalid ∧
    ValidationStatus.valid ≠ ValidationStatus.notValidated ∧
    ValidationStatus.invalid ≠ ValidationStatus.notValidated := by
  refine ⟨?_, ?_, ?_⟩ <;> intro h <;> cases h

/-- Every error inside a valid `ValidationOutcome` has `ERROR` severity and `VALIDATION` stage. -/
theorem validation_outcome_errors_are_validation_stage_errors
    (o : ValidationOutcome) (hvalid : ValidValidationOutcome o)
    (err : DiagnosticIssue) (hmem : err ∈ o.errors) :
    err.severity = .error ∧ err.stage = .validation := by
  have h := hvalid.1 err hmem
  exact ⟨h.2.1, h.2.2⟩

/-- A `VALID` outcome contains no validation errors. -/
theorem valid_outcome_has_no_errors
    (o : ValidationOutcome) (hvalid : ValidValidationOutcome o)
    (hstatus : o.status = .valid) :
    o.errors = [] := by
  apply hvalid.2
  rw [hstatus]
  intro h
  cases h

/-- A `NOT_VALIDATED` outcome contains no validation errors. -/
theorem not_validated_outcome_has_no_errors
    (o : ValidationOutcome) (hvalid : ValidValidationOutcome o)
    (hstatus : o.status = .notValidated) :
    o.errors = [] := by
  apply hvalid.2
  rw [hstatus]
  intro h
  cases h

/-- Structural fidelity: Contract v1.0 (`contracts/models.py`) permits `ValidationOutcome(INVALID, [])`
    without rejecting an empty error collection at the structural model layer. -/
theorem structural_invalid_allows_empty_errors :
    ValidValidationOutcome { status := .invalid, errors := [] } := by
  simp [ValidValidationOutcome]

/-- Every `INVALID` outcome constructed by `DeterministicValidator` contains at least one error,
    and conversely a non-empty error list always yields `INVALID`. -/
theorem validator_invalid_iff_nonempty_errors (errs : List DiagnosticIssue) :
    (makeValidationOutcome errs).status = .invalid ↔ errs ≠ [] := by
  cases errs with
  | nil =>
    simp [makeValidationOutcome]
  | cons e es =>
    simp [makeValidationOutcome]

/-- Every `VALID` outcome constructed by `DeterministicValidator` contains zero errors,
    and conversely an empty error list yields `VALID`. -/
theorem validator_valid_iff_empty_errors (errs : List DiagnosticIssue) :
    (makeValidationOutcome errs).status = .valid ↔ errs = [] := by
  cases errs with
  | nil =>
    simp [makeValidationOutcome]
  | cons e es =>
    simp [makeValidationOutcome]

/-- Pre-validation failure with zero records always produces `NOT_VALIDATED` with empty errors. -/
theorem validator_prevalidation_failure_is_not_validated (topErrors : List DiagnosticIssue) :
    makeResultCardinalityOutcome true false topErrors =
      { status := .notValidated, errors := [] } := by
  rfl

end StructaFormal
