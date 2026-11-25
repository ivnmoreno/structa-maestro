/-
  Structa Formal Assurance Layer — Field Presence, Nullability & Validation Independence Invariants.
  Authoritative Python source: `src/structa_maestro/contracts/models.py` (`ExtractedField`).
-/

import StructaFormal.Models

namespace StructaFormal

/-- Missing fields cannot contain a non-null extracted value. -/
theorem missing_field_has_no_value (f : ExtractedField)
    (hvalid : ValidExtractedField f) (hmissing : f.presence = .missing) :
    f.value = .nullValue :=
  hvalid.1 hmissing

/-- A missing field (`MISSING`, null) and an explicitly null present field (`PRESENT`, null)
    are mathematically distinct valid states. -/
theorem missing_distinct_from_present_null :
    ∃ fMissing fPresentNull : ExtractedField,
      ValidExtractedField fMissing ∧
      ValidExtractedField fPresentNull ∧
      fMissing.presence = .missing ∧
      fMissing.value = .nullValue ∧
      fPresentNull.presence = .present ∧
      fPresentNull.value = .nullValue ∧
      fMissing ≠ fPresentNull := by
  let okVal : ValidationOutcome := { status := .valid, errors := [] }
  refine ⟨
    { presence := .missing, value := .nullValue, validation := okVal },
    { presence := .present, value := .nullValue, validation := okVal },
    ?_, ?_, rfl, rfl, rfl, rfl, ?_⟩
  · simp [ValidExtractedField, ValidValidationOutcome, okVal]
  · simp [ValidExtractedField, ValidValidationOutcome, okVal]
  · intro h
    cases h

/-- A present field may hold either an explicit null value or a non-null extracted value. -/
theorem present_allows_null_and_nonnull :
    (∃ fNull : ExtractedField,
      ValidExtractedField fNull ∧ fNull.presence = .present ∧ fNull.value = .nullValue) ∧
    (∃ fVal : ExtractedField,
      ValidExtractedField fVal ∧ fVal.presence = .present ∧ fVal.value = .nonNull "INV-2026-001") := by
  let okVal : ValidationOutcome := { status := .valid, errors := [] }
  constructor
  · refine ⟨{ presence := .present, value := .nullValue, validation := okVal }, ?_, rfl, rfl⟩
    simp [ValidExtractedField, ValidValidationOutcome, okVal]
  · refine ⟨{ presence := .present, value := .nonNull "INV-2026-001", validation := okVal },
      ?_, rfl, rfl⟩
    simp [ValidExtractedField, ValidValidationOutcome, okVal]

/-- Field presence (`PRESENT` vs. `MISSING`) is independent of `ValidationStatus`:
    every `(FieldPresence, ValidationStatus)` combination is realizable as a valid contract field. -/
theorem field_presence_independent_of_validation_status
    (p : FieldPresence) (vs : ValidationStatus) :
    ∃ f : ExtractedField,
      ValidExtractedField f ∧ f.presence = p ∧ f.validation.status = vs := by
  refine ⟨{ presence := p, value := .nullValue, validation := { status := vs, errors := [] } },
    ?_, rfl, rfl⟩
  simp [ValidExtractedField, ValidValidationOutcome]

end StructaFormal
