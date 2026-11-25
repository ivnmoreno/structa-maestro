/-
  Structa Formal Assurance Layer — Processing vs. Validation Status & ResultBuilder Assembly Invariants.
  Authoritative Python sources:
  - `src/structa_maestro/contracts/models.py` (`ExtractedRecord`, `ExtractionResult`)
  - `src/structa_maestro/builder/result_builder.py` (`ResultBuilder`)
  - `src/structa_maestro/validation/validator.py` (`DeterministicValidator.validate_result_cardinality`)
-/

import StructaFormal.Models
import StructaFormal.ValidationInvariants

namespace StructaFormal

/-- Formalization of `ResultBuilder._build_record` status rules (`result_builder.py:279-284`). -/
def assembleRecordStatus
    (recordFailed recordPartial hasRecordErrors hasFieldErrors : Bool) : ProcessingStatus :=
  if recordFailed then
    .failed
  else if recordPartial || hasRecordErrors || hasFieldErrors then
    .partialStatus
  else
    .completed

/-- Formalization of `ResultBuilder.build` overall status rules (`result_builder.py:85-140`). -/
def assembleOverallStatus
    (strategyFailed strategyPartial hasResultErrors hasRecords anyRecordNotCompleted : Bool) :
    ProcessingStatus :=
  if strategyFailed && !hasRecords then
    .failed
  else if strategyFailed || strategyPartial || hasResultErrors || anyRecordNotCompleted then
    .partialStatus
  else
    .completed

/-- Formalization of `ResultBuilder.build_failed` (`result_builder.py:45-70`). -/
def assembleBuildFailed
    (extractionId : String)
    (schemaMeta : SchemaMetadata)
    (source : Option SourceMetadata)
    (errs : List DiagnosticIssue)
    (warns : List DiagnosticIssue := []) : ExtractionResult :=
  {
    contractVersion := contractVersionV1,
    extractionId := extractionId,
    status := .failed,
    source := source,
    schemaMetadata := schemaMeta,
    records := [],
    validation := { status := .notValidated, errors := [] },
    errors := errs,
    warnings := warns
  }

/-- Processing status and validation status are completely independent dimensions on Contract v1.0:
    every `(ProcessingStatus, ValidationStatus)` pair is realizable on a valid `ExtractionResult`. -/
theorem processing_and_validation_status_independent
    (ps : ProcessingStatus) (vs : ValidationStatus) :
    ∃ res : ExtractionResult,
      ValidExtractionResult res ∧ res.status = ps ∧ res.validation.status = vs := by
  refine ⟨{
    contractVersion := "1.0",
    extractionId := "ext-001",
    status := ps,
    source := none,
    schemaMetadata := { schemaId := "s", schemaVersion := "1.0" },
    records := [],
    validation := { status := vs, errors := [] }
  }, ?_, rfl, rfl⟩
  simp [ValidExtractionResult, contractVersionV1, ValidSchemaMetadata, ProvenanceBoundToSource,
    ValidValidationOutcome]

/-- Partial processing does not automatically imply invalid schema validation. -/
theorem partial_processing_does_not_imply_invalid :
    ∃ res : ExtractionResult,
      ValidExtractionResult res ∧
      res.status = .partialStatus ∧
      res.validation.status = .valid :=
  processing_and_validation_status_independent .partialStatus .valid

/-- Completed processing does not automatically imply valid schema validation. -/
theorem completed_processing_does_not_imply_valid :
    ∃ res : ExtractionResult,
      ValidExtractionResult res ∧
      res.status = .completed ∧
      res.validation.status = .invalid :=
  processing_and_validation_status_independent .completed .invalid

/-- Failed processing does not automatically imply invalid schema validation. -/
theorem failed_processing_does_not_imply_invalid :
    ∃ res : ExtractionResult,
      ValidExtractionResult res ∧
      res.status = .failed ∧
      res.validation.status = .notValidated :=
  processing_and_validation_status_independent .failed .notValidated

/-- Validation that never occurred (`NOT_VALIDATED`) is strictly distinguishable from `VALID`. -/
theorem not_validated_distinct_from_valid :
    ValidationStatus.notValidated ≠ ValidationStatus.valid := by
  intro h
  cases h

/-- `ResultBuilder.build_failed` always produces `status = FAILED`, `records = []`, and
    `validation = ValidationOutcome(NOT_VALIDATED, [])`. -/
theorem build_failed_is_failed_and_not_validated
    (extractionId : String) (schemaMeta : SchemaMetadata)
    (source : Option SourceMetadata) (errs warns : List DiagnosticIssue) :
    let res := assembleBuildFailed extractionId schemaMeta source errs warns
    res.status = .failed ∧
    res.records = [] ∧
    res.validation.status = .notValidated ∧
    res.validation.errors = [] := by
  exact ⟨rfl, rfl, rfl, rfl⟩

/-- Under `ResultBuilder.build`, any strategy failure, partial flag, processing error, or
    non-completed record prevents overall status from ever being promoted to `COMPLETED`. -/
theorem partial_or_errors_never_promoted_to_completed
    (strategyFailed strategyPartial hasResultErrors hasRecords anyRecordNotCompleted : Bool)
    (hissue : (strategyFailed || strategyPartial || hasResultErrors || anyRecordNotCompleted) = true) :
    assembleOverallStatus strategyFailed strategyPartial hasResultErrors hasRecords
      anyRecordNotCompleted ≠ .completed := by
  unfold assembleOverallStatus
  cases strategyFailed <;> cases strategyPartial <;> cases hasResultErrors <;>
    cases hasRecords <;> cases anyRecordNotCompleted <;> simp_all

/-- When a strategy marks `failed = true` while still returning extracted records, `ResultBuilder`
    preserves them with `status = PARTIAL` and runs validation (producing `VALID` or `INVALID`,
    never `NOT_VALIDATED`). -/
theorem failed_strategy_with_records_is_partial_and_validated
    (strategyPartial hasResultErrors anyRecordNotCompleted : Bool)
    (topErrors : List DiagnosticIssue) :
    assembleOverallStatus true strategyPartial hasResultErrors true anyRecordNotCompleted =
      .partialStatus ∧
    (makeResultCardinalityOutcome true true topErrors).status ≠ .notValidated := by
  constructor
  · simp [assembleOverallStatus]
  · unfold makeResultCardinalityOutcome makeValidationOutcome
    cases topErrors <;> simp

/-- Record-level `FAILED` processing status (`proposed_record.failed = true`) still permits both
    `VALID` and `INVALID` record validation outcomes. -/
theorem record_failed_status_independent_of_validation (vs : ValidationStatus) :
    ∃ rec : ExtractedRecord,
      ValidExtractedRecord rec ∧
      rec.status = assembleRecordStatus true false false false ∧
      rec.status = .failed ∧
      rec.validation.status = vs := by
  refine ⟨{
    recordId := "record-001",
    status := .failed,
    fields := [],
    validation := { status := vs, errors := [] }
  }, ?_, rfl, rfl, rfl⟩
  simp [ValidExtractedRecord, ValidValidationOutcome]

end StructaFormal
