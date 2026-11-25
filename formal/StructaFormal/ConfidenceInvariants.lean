/-
  Structa Formal Assurance Layer — Confidence Assessment & ConfidenceNormalizer Invariants.
  Authoritative Python sources:
  - `src/structa_maestro/contracts/models.py` (`ConfidenceAssessment`)
  - `src/structa_maestro/confidence/normalizer.py` (`ConfidenceNormalizer`)
-/

import StructaFormal.Models

namespace StructaFormal

/-- Explicit input categories accepted by `ConfidenceNormalizer.normalize` (`normalizer.py`). -/
inductive ConfidenceInput where
  | missing
  | nonNumeric
  | nonFinite
  | finite (value : Rat)
  deriving DecidableEq, Repr

private def fallbackIfEmpty (s fallback : String) : String :=
  if s == "" then fallback else s

private theorem fallbackIfEmpty_nonempty (s fallback : String) (hf : fallback ≠ "") :
    fallbackIfEmpty s fallback ≠ "" := by
  unfold fallbackIfEmpty
  by_cases h : s = ""
  · simp [h, hf]
  · simp [h]

/-- Clean scale/method fallback helpers matching `ConfidenceNormalizer.normalize`. -/
def cleanScale (rawScale : String) : String :=
  fallbackIfEmpty rawScale.trimAscii.toString "0-1"

def cleanMethod (rawMethod : Option String) (defaultMethod : String) : String :=
  let fallback := fallbackIfEmpty defaultMethod "unknown"
  match rawMethod with
  | some m => fallbackIfEmpty m.trimAscii.toString fallback
  | none => fallback

/-- Pure formalization of `ConfidenceNormalizer.normalize` (`normalizer.py:23-117`). -/
def normalizeConfidence
    (input : ConfidenceInput)
    (rawScale : String := "0-1")
    (rawMethod : Option String := none)
    (defaultMethod : String := "extraction-assessment") : Option ConfidenceAssessment :=
  let scale := cleanScale rawScale
  let method := cleanMethod rawMethod defaultMethod
  match input with
  | .missing => none
  | .nonNumeric => some { value := none, scale := scale, method := method, calibrated := false }
  | .nonFinite => some { value := none, scale := scale, method := method, calibrated := false }
  | .finite v =>
    if scale == "0-1" && !(decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat))) then
      some { value := none, scale := scale, method := method, calibrated := false }
    else
      some { value := some v, scale := scale, method := method, calibrated := false }

private theorem cleanScale_nonempty (s : String) : cleanScale s ≠ "" :=
  fallbackIfEmpty_nonempty _ "0-1" (by decide)

private theorem cleanMethod_nonempty (m : Option String) (d : String) :
    cleanMethod m d ≠ "" := by
  unfold cleanMethod
  have hfb : fallbackIfEmpty d "unknown" ≠ "" := fallbackIfEmpty_nonempty d "unknown" (by decide)
  cases m with
  | none => exact hfb
  | some s => exact fallbackIfEmpty_nonempty _ _ hfb

/-- Any non-null confidence value on declared scale `"0-1"` lies within `[0, 1]`. -/
theorem confidence_zero_one_scale_within_bounds
    (c : ConfidenceAssessment) (hvalid : ValidConfidenceAssessment c)
    (hscale : c.scale = "0-1") (v : Rat) (hval : c.value = some v) :
    (0 : Rat) ≤ v ∧ v ≤ (1 : Rat) :=
  hvalid.2.2 hscale v hval

/-- Unavailable confidence is explicitly representable both as `ConfidenceAssessment.value = none`
    and as `ExtractedField.confidence = none`. -/
theorem confidence_unavailable_explicitly_representable :
    (∃ c : ConfidenceAssessment, ValidConfidenceAssessment c ∧ c.value = none) ∧
    (∃ f : ExtractedField, ValidExtractedField f ∧ f.confidence = none) := by
  let cNone : ConfidenceAssessment :=
    { value := none, scale := "0-1", method := "extraction-assessment", calibrated := false }
  let fNone : ExtractedField := {
    presence := .present,
    value := .nonNull "x",
    confidence := none,
    validation := { status := .valid, errors := [] }
  }
  constructor
  · refine ⟨cNone, ?_, rfl⟩
    simp [cNone, ValidConfidenceAssessment]
  · refine ⟨fNone, ?_, rfl⟩
    simp [fNone, ValidExtractedField, ValidValidationOutcome]

/-- Contract v1.0 explicitly distinguishes uncalibrated (`calibrated = false`) from calibrated
    (`calibrated = true`) assessments without imposing extra restrictions beyond the contract. -/
theorem confidence_calibrated_distinguishable :
    ∃ cUncal cCal : ConfidenceAssessment,
      ValidConfidenceAssessment cUncal ∧
      ValidConfidenceAssessment cCal ∧
      cUncal.calibrated = false ∧
      cCal.calibrated = true ∧
      cUncal ≠ cCal := by
  let cUncal : ConfidenceAssessment :=
    { value := some (1 : Rat), scale := "0-1", method := "llm", calibrated := false }
  let cCal : ConfidenceAssessment :=
    { value := some (1 : Rat), scale := "0-1", method := "platt", calibrated := true }
  refine ⟨cUncal, cCal, ?_, ?_, rfl, rfl, ?_⟩
  · refine ⟨by decide, by decide, ?_⟩
    intro _ v hv
    cases hv
    decide
  · refine ⟨by decide, by decide, ?_⟩
    intro _ v hv
    cases hv
    decide
  · intro h
    have hcal := congrArg ConfidenceAssessment.calibrated h
    cases hcal

/-- `ConfidenceNormalizer` never represents an uncalibrated assessment as calibrated. -/
theorem normalized_confidence_never_calibrated
    (input : ConfidenceInput) (rawScale : String) (rawMethod : Option String)
    (defaultMethod : String) (c : ConfidenceAssessment)
    (heq : normalizeConfidence input rawScale rawMethod defaultMethod = some c) :
    c.calibrated = false := by
  unfold normalizeConfidence at heq
  cases input with
  | missing => cases heq
  | nonNumeric =>
    cases heq
    rfl
  | nonFinite =>
    cases heq
    rfl
  | finite v =>
    by_cases hcond : (cleanScale rawScale == "0-1" && !(decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat)))) = true
    · simp only [hcond, ↓reduceIte, Option.some.injEq] at heq
      subst heq
      rfl
    · simp only [hcond, Bool.false_eq_true, ↓reduceIte, Option.some.injEq] at heq
      subst heq
      rfl

/-- Every assessment emitted by `ConfidenceNormalizer` satisfies `ValidConfidenceAssessment`. -/
theorem normalized_confidence_valid_contract
    (input : ConfidenceInput) (rawScale : String) (rawMethod : Option String)
    (defaultMethod : String) (c : ConfidenceAssessment)
    (heq : normalizeConfidence input rawScale rawMethod defaultMethod = some c) :
    ValidConfidenceAssessment c := by
  unfold normalizeConfidence at heq
  cases input with
  | missing => cases heq
  | nonNumeric =>
    cases heq
    exact ⟨cleanScale_nonempty rawScale, cleanMethod_nonempty rawMethod defaultMethod,
      by intro _ _ h; cases h⟩
  | nonFinite =>
    cases heq
    exact ⟨cleanScale_nonempty rawScale, cleanMethod_nonempty rawMethod defaultMethod,
      by intro _ _ h; cases h⟩
  | finite v =>
    by_cases hcond : (cleanScale rawScale == "0-1" && !(decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat)))) = true
    · simp only [hcond, ↓reduceIte, Option.some.injEq] at heq
      subst heq
      exact ⟨cleanScale_nonempty rawScale, cleanMethod_nonempty rawMethod defaultMethod,
        by intro _ _ h; cases h⟩
    · simp only [hcond, Bool.false_eq_true, ↓reduceIte, Option.some.injEq] at heq
      subst heq
      refine ⟨cleanScale_nonempty rawScale, cleanMethod_nonempty rawMethod defaultMethod, ?_⟩
      intro hscale w hw
      dsimp only at hscale
      cases hw
      by_cases hbounds : (0 : Rat) ≤ v ∧ v ≤ (1 : Rat)
      · exact hbounds
      · exfalso
        apply hcond
        simp [hscale, hbounds]

/-- Missing, non-numeric, non-finite, and out-of-range `"0-1"` confidence inputs are represented
    explicitly as unavailable (`none` or `ConfidenceAssessment.value = none`). -/
theorem normalized_missing_or_invalid_explicitly_unavailable
    (rawScale : String) (rawMethod : Option String) (defaultMethod : String) :
    (normalizeConfidence .missing rawScale rawMethod defaultMethod = none) ∧
    (∀ c, normalizeConfidence .nonNumeric rawScale rawMethod defaultMethod = some c → c.value = none) ∧
    (∀ c, normalizeConfidence .nonFinite rawScale rawMethod defaultMethod = some c → c.value = none) ∧
    (∀ v c, cleanScale rawScale = "0-1" → ¬((0 : Rat) ≤ v ∧ v ≤ (1 : Rat)) →
      normalizeConfidence (.finite v) rawScale rawMethod defaultMethod = some c → c.value = none) := by
  refine ⟨rfl, ?_, ?_, ?_⟩
  · intro c h; unfold normalizeConfidence at h; cases h; rfl
  · intro c h; unfold normalizeConfidence at h; cases h; rfl
  · intro v c hscale hout h
    unfold normalizeConfidence at h
    have hcond : (cleanScale rawScale == "0-1" && !(decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat)))) = true := by
      simp [hscale, hout]
    simp only [hcond, ↓reduceIte, Option.some.injEq] at h
    subst h
    rfl

/-- Confidence is mathematically independent of validation status and provenance presence. -/
theorem confidence_independent_of_validation_and_provenance
    (c : ConfidenceAssessment) (hc : ValidConfidenceAssessment c)
    (vs : ValidationStatus) (hasProv : Bool) :
    ∃ f : ExtractedField,
      ValidExtractedField f ∧
      f.confidence = some c ∧
      f.validation.status = vs ∧
      (f.provenance.isEmpty = !hasProv) := by
  let prov : List ProvenanceEvidence :=
    if hasProv then
      [{ sourceDocumentId := "doc-1", location := none, evidence := "snippet", relationship := .direct }]
    else
      []
  refine ⟨{
    presence := .present,
    value := .nonNull "v",
    confidence := some c,
    provenance := prov,
    validation := { status := vs, errors := [] }
  }, ?_, rfl, rfl, ?_⟩
  · cases hasProv <;>
      simp [prov, ValidExtractedField, ValidProvenanceEvidence, ValidValidationOutcome, hc]
  · cases hasProv <;> simp [prov]

end StructaFormal
