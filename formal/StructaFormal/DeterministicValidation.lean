/-
  Structa Formal Assurance Layer — Deterministic Schema Validation Semantics.
  Authoritative Python source: `src/structa_maestro/validation/validator.py` (`DeterministicValidator`).

  Explicit Verification Boundaries:
  - Regular-expression execution (`pattern`) is parameterized via `PatternMatcher := String → String → Bool`
    (no regex engine is formalized in Lean).
  - Cross-field validation rules (`validation/cross_field.py`) are out of scope for this increment;
    record-level `VALID ↔ ∀ field VALID` holds for single-field/structural validation (`rules = ()`).
-/

import StructaFormal.SchemaModels
import StructaFormal.ValidationInvariants

namespace StructaFormal

/-- Abstract pure pattern matcher `(fieldPath → candidateValue → Bool)`. -/
abbrev PatternMatcher := String → String → Bool

/-- Default unconstrained pattern matcher used when no regex oracle is supplied. -/
def defaultPatternMatcher : PatternMatcher := fun _ _ => true

/-- Single record input mapping field names to `(FieldPresence, RuntimeValue)`. -/
abbrev RecordFieldInput := List (String × (FieldPresence × RuntimeValue))

/-- Construct a validation-stage `ERROR` diagnostic guaranteed to satisfy `ValidDiagnosticIssue`. -/
def mkValidationError
    (code message : String) (recordId : Option String) (fieldPath : Option String) : DiagnosticIssue :=
  {
    code := if code == "" then "validation_error" else code,
    severity := .error,
    stage := .validation,
    message := if message == "" then "Validation failed." else message,
    recordId := recordId,
    fieldPath := fieldPath
  }

theorem mkValidationError_is_valid_validation_error
    (code message : String) (recordId fieldPath : Option String) :
    let e := mkValidationError code message recordId fieldPath
    ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
  refine ⟨⟨?_, ?_⟩, rfl, rfl⟩
  · dsimp only [mkValidationError]
    by_cases hc : code = "" <;> simp [hc]
  · dsimp only [mkValidationError]
    by_cases hm : message = "" <;> simp [hm]

private def lookupFieldInput (fields : RecordFieldInput) (name : String) :
    FieldPresence × RuntimeValue :=
  match fields.find? (fun kv => kv.1 == name) with
  | some (_, pv) => pv
  | none => (.missing, .nullVal)

private def lookupObjectEntry (entries : List (String × RuntimeValue)) (name : String) :
    Option RuntimeValue :=
  match entries.find? (fun kv => kv.1 == name) with
  | some (_, v) => some v
  | none => none

private def insertSortedStr (x : String) : List String → List String
  | [] => [x]
  | y :: ys =>
    if x == y then y :: ys
    else if x < y then x :: y :: ys
    else y :: insertSortedStr x ys

/-- Deterministic lexicographic sort + deduplication matching Python's `sorted(set(...))`. -/
def sortUniqueStrings (xs : List String) : List String :=
  xs.foldl (fun acc s => insertSortedStr s acc) []

private def allowedCheckErrors
    (c : FieldConstraints) (v : RuntimeValue) (recordId fieldPath : String) :
    List DiagnosticIssue :=
  match c.allowedValues with
  | none => []
  | some allowed =>
    if allowed.any (fun cand => runtimeValueEq v cand) then
      []
    else
      [mkValidationError "constraint_violation"
        s!"Field '{fieldPath}' value is not in allowed_values."
        (some recordId) (some fieldPath)]

private def checkNumericBounds
    (c : FieldConstraints) (q : Rat) (recordId fieldPath : String) :
    List DiagnosticIssue :=
  let minErr := match c.minValue with
    | some lo =>
      if decide (q < lo) then
        [mkValidationError "constraint_violation"
          s!"Field '{fieldPath}' is below min_value."
          (some recordId) (some fieldPath)]
      else []
    | none => []
  let maxErr := match c.maxValue with
    | some hi =>
      if decide (hi < q) then
        [mkValidationError "constraint_violation"
          s!"Field '{fieldPath}' exceeds max_value."
          (some recordId) (some fieldPath)]
      else []
    | none => []
  minErr ++ maxErr

mutual
  /-- Pure structural formalization of `DeterministicValidator._validate_value` (`validator.py:204-522`). -/
  def validateValue
      (pm : PatternMatcher)
      (spec : FieldSpec)
      (presence : FieldPresence)
      (value : RuntimeValue)
      (recordId fieldPath : String) : List DiagnosticIssue :=
    match spec with
    | .mk _ ft req nul c props items aaf =>
      match presence with
      | .missing =>
        if req then
          [mkValidationError "missing_required"
            s!"Required field '{fieldPath}' is missing."
            (some recordId) (some fieldPath)]
        else
          []
      | .present =>
        match value with
        | .nullVal =>
          if nul then
            []
          else
            [mkValidationError "disallowed_null"
              s!"Field '{fieldPath}' does not allow null values."
              (some recordId) (some fieldPath)]
        | _ =>
          match ft with
          | .string =>
            match value with
            | .strVal s =>
              let len : Int := Int.ofNat s.length
              let e1 := match c.minLength with
                | some lo =>
                  if decide (len < lo) then
                    [mkValidationError "constraint_violation"
                      s!"Field '{fieldPath}' length is below min_length."
                      (some recordId) (some fieldPath)]
                  else []
                | none => []
              let e2 := match c.maxLength with
                | some hi =>
                  if decide (hi < len) then
                    [mkValidationError "constraint_violation"
                      s!"Field '{fieldPath}' length exceeds max_length."
                      (some recordId) (some fieldPath)]
                  else []
                | none => []
              let e3 := match c.pattern with
                | some _ =>
                  if pm fieldPath s then []
                  else
                    [mkValidationError "constraint_violation"
                      s!"Field '{fieldPath}' does not match required pattern."
                      (some recordId) (some fieldPath)]
                | none => []
              e1 ++ e2 ++ e3 ++ allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected string."
                (some recordId) (some fieldPath)]
          | .integer =>
            match value with
            | .intVal n =>
              checkNumericBounds c (n : Rat) recordId fieldPath ++
                allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected integer."
                (some recordId) (some fieldPath)]
          | .decimal =>
            match value with
            | .decVal q =>
              checkNumericBounds c q recordId fieldPath ++
                allowedCheckErrors c value recordId fieldPath
            | .intVal n =>
              checkNumericBounds c (n : Rat) recordId fieldPath ++
                allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected finite decimal."
                (some recordId) (some fieldPath)]
          | .boolean =>
            match value with
            | .boolVal _ =>
              allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected boolean."
                (some recordId) (some fieldPath)]
          | .date =>
            match value with
            | .dateVal _ =>
              allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected date."
                (some recordId) (some fieldPath)]
          | .array =>
            match value with
            | .arrayVal elems =>
              let len : Int := Int.ofNat elems.length
              let e1 := match c.minItems with
                | some lo =>
                  if decide (len < lo) then
                    [mkValidationError "constraint_violation"
                      s!"Field '{fieldPath}' has fewer than min_items."
                      (some recordId) (some fieldPath)]
                  else []
                | none => []
              let e2 := match c.maxItems with
                | some hi =>
                  if decide (hi < len) then
                    [mkValidationError "constraint_violation"
                      s!"Field '{fieldPath}' exceeds max_items."
                      (some recordId) (some fieldPath)]
                  else []
                | none => []
              let elemErrs := match items with
                | none => []
                | some itemSpec => validateArrayElems pm itemSpec elems 0 recordId fieldPath
              e1 ++ e2 ++ elemErrs ++ allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected array."
                (some recordId) (some fieldPath)]
          | .object =>
            match value with
            | .objectVal entries =>
              let propErrs := match props with
                | none => []
                | some declaredProps => validateObjectProps pm declaredProps entries recordId fieldPath
              let declaredKeys := (props.getD []).map Prod.fst
              let extraErrs :=
                if aaf then []
                else
                  let extraKeys :=
                    sortUniqueStrings ((entries.map Prod.fst).filter (fun k => !declaredKeys.contains k))
                  extraKeys.map fun ek =>
                    mkValidationError "unexpected_field"
                      s!"Unexpected nested field '{fieldPath}.{ek}' is not declared in schema."
                      (some recordId) (some s!"{fieldPath}.{ek}")
              propErrs ++ extraErrs ++ allowedCheckErrors c value recordId fieldPath
            | _ =>
              [mkValidationError "type_mismatch"
                s!"Field '{fieldPath}' expected object."
                (some recordId) (some fieldPath)]
  termination_by (sizeOf spec, 0)

  def validateArrayElems
      (pm : PatternMatcher)
      (itemSpec : FieldSpec)
      (elems : List RuntimeValue)
      (idx : Nat)
      (recordId fieldPath : String) : List DiagnosticIssue :=
    match elems with
    | [] => []
    | elem :: rest =>
      validateValue pm itemSpec .present elem recordId s!"{fieldPath}[{idx}]" ++
        validateArrayElems pm itemSpec rest (idx + 1) recordId fieldPath
  termination_by (sizeOf itemSpec, elems.length + 1)

  def validateObjectProps
      (pm : PatternMatcher)
      (props : List (String × FieldSpec))
      (entries : List (String × RuntimeValue))
      (recordId fieldPath : String) : List DiagnosticIssue :=
    match props with
    | [] => []
    | (propName, propSpec) :: rest =>
      let (pPres, pVal) :=
        match lookupObjectEntry entries propName with
        | some v => (.present, v)
        | none => (.missing, .nullVal)
      validateValue pm propSpec pPres pVal recordId s!"{fieldPath}.{propName}" ++
        validateObjectProps pm rest entries recordId fieldPath
  termination_by (sizeOf props, 0)
end

/-- Pure formalization of `DeterministicValidator.validate_record` (`validator.py:82-152`,
    single-field/structural validation with `schema.rules = ()`). -/
def validateRecord
    (pm : PatternMatcher)
    (fields : RecordFieldInput)
    (schema : ExtractionSchema)
    (recordId : String) : ValidationOutcome × List (String × ValidationOutcome) :=
  let declaredOutcomes : List (String × ValidationOutcome) :=
    schema.fields.map fun (fname, spec) =>
      let (pres, val) := lookupFieldInput fields fname
      let errs := validateValue pm spec pres val recordId fname
      (fname, makeValidationOutcome errs)
  let declaredKeys := schema.fields.map Prod.fst
  let extraNames :=
    sortUniqueStrings ((fields.map Prod.fst).filter (fun k => !declaredKeys.contains k))
  let extraOutcomes : List (String × ValidationOutcome) :=
    extraNames.map fun extraName =>
      let (pres, _) := lookupFieldInput fields extraName
      match pres with
      | .missing => (extraName, { status := .valid, errors := [] })
      | .present =>
        if schema.allowAdditionalFields then
          (extraName, { status := .valid, errors := [] })
        else
          let err := mkValidationError "unexpected_field"
            s!"Unexpected field '{extraName}' is not declared in schema."
            (some recordId) (some extraName)
          (extraName, { status := .invalid, errors := [err] })
  let allFieldOutcomes := declaredOutcomes ++ extraOutcomes
  let allErrors := allFieldOutcomes.flatMap (fun kv => kv.2.errors)
  (makeValidationOutcome allErrors, allFieldOutcomes)

/-- Pure formalization of `DeterministicValidator.validate_result_cardinality` (`validator.py:154-202`). -/
def validateResultCardinality
    (recordOutcomes : List ValidationOutcome)
    (schema : ExtractionSchema)
    (hasRecords failedBeforeValidation : Bool) : ValidationOutcome :=
  if failedBeforeValidation && !hasRecords then
    { status := .notValidated, errors := [] }
  else
    let cardErrors : List DiagnosticIssue :=
      match schema.cardinality with
      | .single =>
        match recordOutcomes with
        | [] =>
          [mkValidationError "missing_record"
            "Single-record schema expected 1 record, but 0 records were extracted."
            none none]
        | [_] => []
        | _ :: _ :: _ =>
          [mkValidationError "cardinality_violation"
            "Single-record schema expected 1 record, but multiple records were extracted."
            none none]
      | .multiple => []
    let topErrors := cardErrors ++ recordOutcomes.flatMap (fun o => o.errors)
    makeValidationOutcome topErrors

-- =============================================================================
-- Soundness helpers & Machine-Checked Theorems (19 required theorems)
-- =============================================================================

private theorem allowedCheckErrors_valid
    (c : FieldConstraints) (v : RuntimeValue) (rid fp : String) :
    ∀ e ∈ allowedCheckErrors c v rid fp,
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
  unfold allowedCheckErrors
  cases c.allowedValues with
  | none => simp
  | some allowed =>
    by_cases h : allowed.any (fun cand => runtimeValueEq v cand) = true
    · simp [h]
    · simp only [h, Bool.false_eq_true, ↓reduceIte]
      intro e he
      simp only [List.mem_singleton] at he
      subst he
      exact mkValidationError_is_valid_validation_error _ _ _ _

private theorem checkNumericBounds_valid
    (c : FieldConstraints) (q : Rat) (rid fp : String) :
    ∀ e ∈ checkNumericBounds c q rid fp,
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
  unfold checkNumericBounds
  intro e he
  simp only [List.mem_append] at he
  cases he with
  | inl h1 =>
    cases hlo : c.minValue with
    | none => simp [hlo] at h1
    | some lo =>
      simp only [hlo] at h1
      split at h1
      · simp only [List.mem_singleton] at h1
        subst h1
        exact mkValidationError_is_valid_validation_error _ _ _ _
      · simp at h1
  | inr h2 =>
    cases hhi : c.maxValue with
    | none => simp [hhi] at h2
    | some hi =>
      simp only [hhi] at h2
      split at h2
      · simp only [List.mem_singleton] at h2
        subst h2
        exact mkValidationError_is_valid_validation_error _ _ _ _
      · simp at h2

private theorem makeValidationOutcome_errors (errs : List DiagnosticIssue) :
    (makeValidationOutcome errs).errors = errs := by
  cases errs <;> rfl

private theorem makeValidationOutcome_valid (errs : List DiagnosticIssue)
    (herrs : ∀ e ∈ errs, ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation) :
    ValidValidationOutcome (makeValidationOutcome errs) := by
  cases errs with
  | nil => simp [makeValidationOutcome, ValidValidationOutcome]
  | cons e es =>
    simp only [makeValidationOutcome, List.isEmpty_cons, Bool.false_eq_true, ↓reduceIte,
      ValidValidationOutcome]
    refine ⟨herrs, by intro h; exact absurd rfl h⟩

mutual
  /-- Every diagnostic produced by `validateValue` is a valid validation-stage `ERROR`. -/
  theorem validate_value_errors_are_valid_diagnostics
      (pm : PatternMatcher) (spec : FieldSpec) (pres : FieldPresence)
      (v : RuntimeValue) (rid fp : String) (e : DiagnosticIssue)
      (he : e ∈ validateValue pm spec pres v rid fp) :
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
    cases spec with
    | mk name ft req nul c props items aaf =>
      cases pres with
      | missing =>
        unfold validateValue at he
        by_cases hreq : req = true
        · simp only [hreq, ↓reduceIte, List.mem_singleton] at he
          subst he
          exact mkValidationError_is_valid_validation_error _ _ _ _
        · simp [hreq] at he
      | present =>
        cases v with
        | nullVal =>
          unfold validateValue at he
          by_cases hnul : nul = true
          · simp [hnul] at he
          · simp only [hnul, Bool.false_eq_true, ↓reduceIte, List.mem_singleton] at he
            subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | strVal s =>
          cases ft with
          | string =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            rcases he with ((h1 | h2) | h3) | h4
            · cases hlo : c.minLength with
              | none => simp [hlo] at h1
              | some lo =>
                simp only [hlo] at h1; split at h1
                · simp only [List.mem_singleton] at h1; subst h1
                  exact mkValidationError_is_valid_validation_error _ _ _ _
                · simp at h1
            · cases hhi : c.maxLength with
              | none => simp [hhi] at h2
              | some hi =>
                simp only [hhi] at h2; split at h2
                · simp only [List.mem_singleton] at h2; subst h2
                  exact mkValidationError_is_valid_validation_error _ _ _ _
                · simp at h2
            · cases hpat : c.pattern with
              | none => simp [hpat] at h3
              | some pat =>
                simp only [hpat] at h3; split at h3
                · simp at h3
                · simp only [List.mem_singleton] at h3; subst h3
                  exact mkValidationError_is_valid_validation_error _ _ _ _
            · exact allowedCheckErrors_valid c (.strVal s) rid fp e h4
          | integer | decimal | boolean | date | array | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | intVal n =>
          cases ft with
          | integer =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            cases he with
            | inl h1 => exact checkNumericBounds_valid c (n : Rat) rid fp e h1
            | inr h2 => exact allowedCheckErrors_valid c (.intVal n) rid fp e h2
          | decimal =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            cases he with
            | inl h1 => exact checkNumericBounds_valid c (n : Rat) rid fp e h1
            | inr h2 => exact allowedCheckErrors_valid c (.intVal n) rid fp e h2
          | string | boolean | date | array | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | decVal q =>
          cases ft with
          | decimal =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            cases he with
            | inl h1 => exact checkNumericBounds_valid c q rid fp e h1
            | inr h2 => exact allowedCheckErrors_valid c (.decVal q) rid fp e h2
          | string | integer | boolean | date | array | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | nonFiniteDecVal =>
          cases ft <;> (
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _)
        | boolVal b =>
          cases ft with
          | boolean =>
            unfold validateValue at he
            exact allowedCheckErrors_valid c (.boolVal b) rid fp e he
          | string | integer | decimal | date | array | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | dateVal iso =>
          cases ft with
          | date =>
            unfold validateValue at he
            exact allowedCheckErrors_valid c (.dateVal iso) rid fp e he
          | string | integer | decimal | boolean | array | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | datetimeVal =>
          cases ft <;> (
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _)
        | arrayVal elems =>
          cases ft with
          | array =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            rcases he with ((h1 | h2) | h3) | h4
            · cases hlo : c.minItems with
              | none => simp [hlo] at h1
              | some lo =>
                simp only [hlo] at h1; split at h1
                · simp only [List.mem_singleton] at h1; subst h1
                  exact mkValidationError_is_valid_validation_error _ _ _ _
                · simp at h1
            · cases hhi : c.maxItems with
              | none => simp [hhi] at h2
              | some hi =>
                simp only [hhi] at h2; split at h2
                · simp only [List.mem_singleton] at h2; subst h2
                  exact mkValidationError_is_valid_validation_error _ _ _ _
                · simp at h2
            · cases items with
              | none => simp at h3
              | some itemSpec =>
                exact validateArrayElems_valid pm itemSpec elems 0 rid fp e h3
            · exact allowedCheckErrors_valid c (.arrayVal elems) rid fp e h4
          | string | integer | decimal | boolean | date | object =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | objectVal entries =>
          cases ft with
          | object =>
            unfold validateValue at he
            simp only [List.mem_append] at he
            rcases he with (h1 | h2) | h3
            · cases props with
              | none => simp at h1
              | some declaredProps =>
                exact validateObjectProps_valid pm declaredProps entries rid fp e h1
            · by_cases haaf : aaf = true
              · simp [haaf] at h2
              · simp only [haaf, Bool.false_eq_true, ↓reduceIte, List.mem_map] at h2
                rcases h2 with ⟨_, _, rfl⟩
                exact mkValidationError_is_valid_validation_error _ _ _ _
            · exact allowedCheckErrors_valid c (.objectVal entries) rid fp e h3
          | string | integer | decimal | boolean | date | array =>
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
        | otherVal =>
          cases ft <;> (
            unfold validateValue at he
            simp only [List.mem_singleton] at he; subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _)
  termination_by (sizeOf spec, 0)

  theorem validateArrayElems_valid
      (pm : PatternMatcher) (itemSpec : FieldSpec) (elems : List RuntimeValue)
      (idx : Nat) (rid fp : String) (e : DiagnosticIssue)
      (he : e ∈ validateArrayElems pm itemSpec elems idx rid fp) :
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
    cases elems with
    | nil => simp [validateArrayElems] at he
    | cons elem rest =>
      unfold validateArrayElems at he
      simp only [List.mem_append] at he
      cases he with
      | inl h1 => exact validate_value_errors_are_valid_diagnostics pm itemSpec .present elem rid _ e h1
      | inr h2 => exact validateArrayElems_valid pm itemSpec rest (idx + 1) rid fp e h2
  termination_by (sizeOf itemSpec, elems.length + 1)

  theorem validateObjectProps_valid
      (pm : PatternMatcher) (props : List (String × FieldSpec))
      (entries : List (String × RuntimeValue)) (rid fp : String) (e : DiagnosticIssue)
      (he : e ∈ validateObjectProps pm props entries rid fp) :
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
    cases props with
    | nil => simp [validateObjectProps] at he
    | cons kv rest =>
      cases kv with
      | mk propName propSpec =>
        unfold validateObjectProps at he
        simp only [List.mem_append] at he
        cases he with
        | inl h1 =>
          exact validate_value_errors_are_valid_diagnostics pm propSpec _ _ rid _ e h1
        | inr h2 =>
          exact validateObjectProps_valid pm rest entries rid fp e h2
  termination_by (sizeOf props, 0)
end

/-- Validation is strictly deterministic: equal inputs always produce equal validation outcomes. -/
theorem validation_is_deterministic
    (pm1 pm2 : PatternMatcher) (f1 f2 : RecordFieldInput)
    (s1 s2 : ExtractionSchema) (r1 r2 : String)
    (hpm : pm1 = pm2) (hf : f1 = f2) (hs : s1 = s2) (hr : r1 = r2) :
    validateRecord pm1 f1 s1 r1 = validateRecord pm2 f2 s2 r2 := by
  subst hpm; subst hf; subst hs; subst hr; rfl

/-- Every record-level and field-level `ValidationOutcome` emitted by `validateRecord` satisfies
    Contract v1.0's `ValidValidationOutcome` invariant. -/
theorem validate_record_outcomes_satisfy_contract
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) :
    let res := validateRecord pm fields schema recordId
    ValidValidationOutcome res.1 ∧ (∀ kv ∈ res.2, ValidValidationOutcome kv.2) := by
  have hFields : ∀ kv ∈ (validateRecord pm fields schema recordId).2, ValidValidationOutcome kv.2 := by
    intro kv hkv
    unfold validateRecord at hkv
    dsimp only at hkv
    simp only [List.mem_append, List.mem_map] at hkv
    cases hkv with
    | inl hdecl =>
      rcases hdecl with ⟨⟨fname, spec⟩, _, rfl⟩
      apply makeValidationOutcome_valid
      intro e he
      exact validate_value_errors_are_valid_diagnostics pm spec _ _ recordId fname e he
    | inr hextra =>
      rcases hextra with ⟨extraName, _, rfl⟩
      cases lookupFieldInput fields extraName with
      | mk pres val =>
        cases pres with
        | missing => simp [ValidValidationOutcome]
        | present =>
          by_cases haaf : schema.allowAdditionalFields = true
          · simp [haaf, ValidValidationOutcome]
          · simp only [haaf, Bool.false_eq_true, ↓reduceIte, ValidValidationOutcome]
            refine ⟨?_, by intro h; exact absurd rfl h⟩
            intro e he
            simp only [List.mem_singleton] at he
            subst he
            exact mkValidationError_is_valid_validation_error _ _ _ _
  refine ⟨?_, hFields⟩
  unfold validateRecord
  dsimp only
  apply makeValidationOutcome_valid
  intro e he
  simp only [List.mem_flatMap] at he
  rcases he with ⟨kv, hkv, herr⟩
  exact (hFields kv hkv).1 e herr

/-- For single-field/structural validation (`schema.rules = ()`), record-level `VALID` holds iff
    every field outcome has `status = VALID`. -/
theorem validate_record_valid_iff_all_fields_valid
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) :
    let res := validateRecord pm fields schema recordId
    res.1.status = .valid ↔ (∀ kv ∈ res.2, kv.2.status = .valid) := by
  have hFieldStatus : ∀ kv ∈ (validateRecord pm fields schema recordId).2,
      kv.2.status = .valid ↔ kv.2.errors = [] := by
    intro kv hkv
    unfold validateRecord at hkv
    dsimp only at hkv
    simp only [List.mem_append, List.mem_map] at hkv
    cases hkv with
    | inl hdecl =>
      rcases hdecl with ⟨⟨fname, spec⟩, _, rfl⟩
      dsimp only
      rw [makeValidationOutcome_errors]
      exact validator_valid_iff_empty_errors _
    | inr hextra =>
      rcases hextra with ⟨extraName, _, rfl⟩
      cases lookupFieldInput fields extraName with
      | mk pres val =>
        cases pres with
        | missing => simp
        | present =>
          by_cases haaf : schema.allowAdditionalFields = true
          · simp [haaf]
          · simp [haaf]
  dsimp only
  have hRec : (validateRecord pm fields schema recordId).1.status = .valid ↔
      ((validateRecord pm fields schema recordId).2.flatMap (fun kv => kv.2.errors)) = [] :=
    validator_valid_iff_empty_errors _
  rw [hRec]
  constructor
  · intro hflat kv hkv
    apply (hFieldStatus kv hkv).mpr
    cases herrs : kv.2.errors with
    | nil => rfl
    | cons e es =>
      have hmem : e ∈ (validateRecord pm fields schema recordId).2.flatMap (fun p => p.2.errors) := by
        rw [List.mem_flatMap]
        exact ⟨kv, hkv, by simp [herrs]⟩
      rw [hflat] at hmem
      cases hmem
  · intro hall
    cases hflat : (validateRecord pm fields schema recordId).2.flatMap (fun p => p.2.errors) with
    | nil => rfl
    | cons e es =>
      have hmem : e ∈ (validateRecord pm fields schema recordId).2.flatMap (fun p => p.2.errors) := by
        simp [hflat]
      rw [List.mem_flatMap] at hmem
      rcases hmem with ⟨kv, hkv, herr⟩
      have hemp := (hFieldStatus kv hkv).mp (hall kv hkv)
      rw [hemp] at herr
      cases herr

/-- `MISSING` on a `required = true` field yields a single `missing_required` error (`INVALID`). -/
theorem missing_required_field_is_invalid
    (pm : PatternMatcher) (spec : FieldSpec) (v : RuntimeValue) (rid fp : String)
    (hreq : spec.required = true) :
    let errs := validateValue pm spec .missing v rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "missing_required") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.required] at hreq
    subst hreq
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- `MISSING` on a `required = false` field yields `[]` (`VALID`). -/
theorem missing_optional_field_is_valid
    (pm : PatternMatcher) (spec : FieldSpec) (v : RuntimeValue) (rid fp : String)
    (hreq : spec.required = false) :
    let errs := validateValue pm spec .missing v rid fp
    errs = [] ∧ (makeValidationOutcome errs).status = .valid := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.required] at hreq
    subst hreq
    simp [validateValue, makeValidationOutcome]

/-- `PRESENT` + `nullVal` on a `nullable = false` field yields `disallowed_null` (`INVALID`). -/
theorem present_null_disallowed_is_invalid
    (pm : PatternMatcher) (spec : FieldSpec) (rid fp : String)
    (hnul : spec.nullable = false) :
    let errs := validateValue pm spec .present .nullVal rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "disallowed_null") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.nullable] at hnul
    subst hnul
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- `PRESENT` + `nullVal` on a `nullable = true` field yields `[]` (`VALID`). -/
theorem present_null_allowed_is_valid
    (pm : PatternMatcher) (spec : FieldSpec) (rid fp : String)
    (hnul : spec.nullable = true) :
    let errs := validateValue pm spec .present .nullVal rid fp
    errs = [] ∧ (makeValidationOutcome errs).status = .valid := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.nullable] at hnul
    subst hnul
    simp [validateValue, makeValidationOutcome]

/-- Strict non-coercion: strings (such as `"42"`) are never coerced to `INTEGER`. -/
theorem no_coercion_string_for_integer
    (pm : PatternMatcher) (spec : FieldSpec) (s rid fp : String)
    (hft : spec.fieldType = .integer) :
    let errs := validateValue pm spec .present (.strVal s) rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "type_mismatch") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.fieldType] at hft
    subst hft
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- Strict non-coercion: booleans are never coerced to `INTEGER`. -/
theorem no_coercion_bool_for_integer
    (pm : PatternMatcher) (spec : FieldSpec) (b : Bool) (rid fp : String)
    (hft : spec.fieldType = .integer) :
    let errs := validateValue pm spec .present (.boolVal b) rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "type_mismatch") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.fieldType] at hft
    subst hft
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- Strict non-coercion: booleans are never coerced to `DECIMAL`. -/
theorem no_coercion_bool_for_decimal
    (pm : PatternMatcher) (spec : FieldSpec) (b : Bool) (rid fp : String)
    (hft : spec.fieldType = .decimal) :
    let errs := validateValue pm spec .present (.boolVal b) rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "type_mismatch") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.fieldType] at hft
    subst hft
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- Strict non-coercion: `datetime` is never coerced to `DATE`. -/
theorem no_coercion_datetime_for_date
    (pm : PatternMatcher) (spec : FieldSpec) (rid fp : String)
    (hft : spec.fieldType = .date) :
    let errs := validateValue pm spec .present .datetimeVal rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "type_mismatch") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.fieldType] at hft
    subst hft
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- Strict non-coercion: non-finite decimals (`NaN`/`±inf`) fail `DECIMAL` with `type_mismatch`. -/
theorem no_coercion_nonfinite_for_decimal
    (pm : PatternMatcher) (spec : FieldSpec) (rid fp : String)
    (hft : spec.fieldType = .decimal) :
    let errs := validateValue pm spec .present .nonFiniteDecVal rid fp
    (makeValidationOutcome errs).status = .invalid ∧
    (∃ e, errs = [e] ∧ e.code = "type_mismatch") := by
  cases spec with
  | mk name ft req nul c props items aaf =>
    simp only [FieldSpec.fieldType] at hft
    subst hft
    simp [validateValue, makeValidationOutcome, mkValidationError]

/-- Authoritative fidelity (`validator.py:336`): exact `INTEGER` values are accepted for
    unconstrained `DECIMAL` fields. -/
theorem integer_accepted_for_unconstrained_decimal
    (pm : PatternMatcher) (name : String) (req nul aaf : Bool) (n : Int) (rid fp : String) :
    let spec := FieldSpec.mk name .decimal req nul {} none none aaf
    validateValue pm spec .present (.intVal n) rid fp = [] := by
  simp [validateValue, checkNumericBounds, allowedCheckErrors]

/-- `allowed_values` equality keeps booleans strictly distinct from integers while preserving
    exact numeric equality between `intVal` and `decVal`. -/
theorem allowed_values_separates_bool_and_int :
    runtimeValueEq (.boolVal true) (.intVal 1) = false ∧
    runtimeValueEq (.boolVal false) (.intVal 0) = false ∧
    runtimeValueEq (.intVal 10) (.decVal (10 : Rat)) = true := by
  exact ⟨rfl, rfl, rfl⟩

private theorem mem_insertSortedStr (x y : String) (xs : List String) :
    y ∈ insertSortedStr x xs ↔ y = x ∨ y ∈ xs := by
  induction xs with
  | nil => simp [insertSortedStr]
  | cons z zs ih =>
    unfold insertSortedStr
    split
    · next heq =>
      have hxz : x = z := beq_iff_eq.mp heq
      subst hxz
      simp
    · split
      · simp
      · simp [ih, or_left_comm]

private theorem mem_sortUniqueStrings (y : String) (xs : List String) :
    y ∈ sortUniqueStrings xs ↔ y ∈ xs := by
  unfold sortUniqueStrings
  suffices ∀ acc : List String, y ∈ xs.foldl (fun a s => insertSortedStr s a) acc ↔ y ∈ acc ∨ y ∈ xs by
    simpa using this []
  induction xs with
  | nil => intro acc; simp
  | cons x rest ih =>
    intro acc
    simp only [List.foldl_cons, ih, mem_insertSortedStr, List.mem_cons, or_left_comm, or_assoc]

/-- When `allowAdditionalFields = false`, any `PRESENT` undeclared field yields `unexpected_field`
    and makes the record `INVALID`. -/
theorem unexpected_extra_field_rejected_when_disallowed
    (pm : PatternMatcher) (fields : RecordFieldInput) (schema : ExtractionSchema)
    (recordId extraName : String) (v : RuntimeValue)
    (hdisallowed : schema.allowAdditionalFields = false)
    (hundecl : (schema.fields.map Prod.fst).contains extraName = false)
    (hmem : extraName ∈ fields.map Prod.fst)
    (hpres : (lookupFieldInput fields extraName).1 = .present)
    (_hv : (lookupFieldInput fields extraName).2 = v) :
    let res := validateRecord pm fields schema recordId
    res.1.status = .invalid ∧
    (∃ e ∈ res.1.errors, e.code = "unexpected_field" ∧ e.fieldPath = some extraName) := by
  have hin : extraName ∈
      sortUniqueStrings ((fields.map Prod.fst).filter (fun k => !(schema.fields.map Prod.fst).contains k)) := by
    rw [mem_sortUniqueStrings, List.mem_filter]
    exact ⟨hmem, by rw [hundecl]; rfl⟩
  have hLookup : lookupFieldInput fields extraName = (.present, v) := by
    cases hl : lookupFieldInput fields extraName with
    | mk p val =>
      rw [hl] at hpres _hv
      simp only at hpres _hv
      subst hpres; subst _hv; rfl
  let targetErr := mkValidationError "unexpected_field"
    s!"Unexpected field '{extraName}' is not declared in schema."
    (some recordId) (some extraName)
  have herrInFlat : targetErr ∈ (validateRecord pm fields schema recordId).2.flatMap (fun kv => kv.2.errors) := by
    rw [List.mem_flatMap]
    refine ⟨(extraName, { status := .invalid, errors := [targetErr] }), ?_, List.mem_cons_self⟩
    unfold validateRecord
    dsimp only
    rw [List.mem_append]
    right
    rw [List.mem_map]
    refine ⟨extraName, hin, ?_⟩
    simp [hLookup, hdisallowed, targetErr]
  have hNonEmpty : (validateRecord pm fields schema recordId).2.flatMap (fun kv => kv.2.errors) ≠ [] := by
    intro hemp
    rw [hemp] at herrInFlat
    cases herrInFlat
  refine ⟨?_, ?_⟩
  · exact (validator_invalid_iff_nonempty_errors _).mpr hNonEmpty
  · refine ⟨targetErr, ?_, rfl, rfl⟩
    unfold validateRecord at herrInFlat ⊢
    dsimp only at herrInFlat ⊢
    rw [makeValidationOutcome_errors]
    exact herrInFlat

/-- When `allowAdditionalFields = true` or an undeclared field has `presence = MISSING`, no
    `unexpected_field` error is emitted for it (its field outcome is `VALID`). -/
theorem extra_field_allowed_when_opted_in_or_missing
    (pm : PatternMatcher) (fields : RecordFieldInput) (schema : ExtractionSchema)
    (recordId extraName : String)
    (hundecl : (schema.fields.map Prod.fst).contains extraName = false)
    (hmem : extraName ∈ fields.map Prod.fst)
    (hcond : schema.allowAdditionalFields = true ∨ (lookupFieldInput fields extraName).1 = .missing) :
    (extraName, { status := .valid, errors := [] }) ∈ (validateRecord pm fields schema recordId).2 := by
  have hin : extraName ∈
      sortUniqueStrings ((fields.map Prod.fst).filter (fun k => !(schema.fields.map Prod.fst).contains k)) := by
    rw [mem_sortUniqueStrings, List.mem_filter]
    exact ⟨hmem, by rw [hundecl]; rfl⟩
  unfold validateRecord
  dsimp only
  rw [List.mem_append]
  right
  rw [List.mem_map]
  refine ⟨extraName, hin, ?_⟩
  cases hcond with
  | inl haaf =>
    cases hl : lookupFieldInput fields extraName with
    | mk pres val =>
      cases pres <;> simp [haaf]
  | inr hmiss =>
    cases hl : lookupFieldInput fields extraName with
    | mk pres val =>
      rw [hl] at hmiss
      simp only at hmiss
      subst hmiss
      rfl

/-- `SINGLE` cardinality validation semantics: `0` records yields `missing_record` (`INVALID`),
    `>= 2` records yields `cardinality_violation` (`INVALID`), and `1` valid record yields `VALID`. -/
theorem cardinality_single_semantics (schema : ExtractionSchema) (hsingle : schema.cardinality = .single) :
    (let o0 := validateResultCardinality [] schema false false;
      o0.status = .invalid ∧ (∃ e ∈ o0.errors, e.code = "missing_record")) ∧
    (∀ r1 r2 rest,
      let oMany := validateResultCardinality (r1 :: r2 :: rest) schema true false;
      oMany.status = .invalid ∧ (∃ e ∈ oMany.errors, e.code = "cardinality_violation")) ∧
    (∀ r, r.errors = [] →
      (validateResultCardinality [r] schema true false).status = .valid) := by
  refine ⟨?_, ?_, ?_⟩
  · unfold validateResultCardinality
    simp [hsingle, makeValidationOutcome, mkValidationError]
  · intro r1 r2 rest
    unfold validateResultCardinality
    simp [hsingle, makeValidationOutcome, mkValidationError]
  · intro r hr
    unfold validateResultCardinality
    simp [hsingle, hr, makeValidationOutcome]

/-- `MULTIPLE` cardinality allows 0 or more valid records (`VALID` iff all records have empty errors),
    and pre-validation failure with 0 records always yields `NOT_VALIDATED` (`errors = []`). -/
theorem cardinality_multiple_and_prevalidation_semantics (schema : ExtractionSchema) :
    (schema.cardinality = .multiple →
      (validateResultCardinality [] schema false false).status = .valid ∧
      (∀ recs hasRecs,
        (validateResultCardinality recs schema hasRecs false).status = .valid ↔
          ∀ r ∈ recs, r.errors = [])) ∧
    (∀ recs,
      validateResultCardinality recs schema false true =
        { status := .notValidated, errors := [] }) := by
  refine ⟨?_, ?_⟩
  · intro hmult
    refine ⟨?_, ?_⟩
    · unfold validateResultCardinality
      simp [hmult, makeValidationOutcome]
    · intro recs hasRecs
      unfold validateResultCardinality
      simp only [hmult, Bool.false_and, Bool.false_eq_true, ↓reduceIte, List.nil_append,
        validator_valid_iff_empty_errors]
      constructor
      · intro hflat r hr
        cases herrs : r.errors with
        | nil => rfl
        | cons e es =>
          have hmem : e ∈ recs.flatMap (fun o => o.errors) := by
            rw [List.mem_flatMap]
            exact ⟨r, hr, by simp [herrs]⟩
          rw [hflat] at hmem
          cases hmem
      · intro hall
        cases hflat : recs.flatMap (fun o => o.errors) with
        | nil => rfl
        | cons e es =>
          have hmem : e ∈ recs.flatMap (fun o => o.errors) := by simp [hflat]
          rw [List.mem_flatMap] at hmem
          rcases hmem with ⟨r, hr, herr⟩
          rw [hall r hr] at herr
          cases herr
  · intro recs
    rfl

end StructaFormal
