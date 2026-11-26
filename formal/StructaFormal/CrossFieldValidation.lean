/-
  Structa Formal Assurance Layer — Deterministic Cross-Field & Conditional Validation Semantics.
  Authoritative Python source:
    - `src/structa_maestro/validation/cross_field.py`
    - `src/structa_maestro/validation/validator.py` (`DeterministicValidator.validate_record`)
-/

import StructaFormal.CrossFieldInvariants
import StructaFormal.DeterministicValidation

namespace StructaFormal

/-- Exact rational absolute value matching Python's `abs(computed - target_dec)` on `Decimal`. -/
def absRat (q : Rat) : Rat :=
  if q < 0 then -q else q

/-- Strict non-coercing numeric extraction matching `_to_exact_decimal` (`cross_field.py:36-46`). -/
def toExactRat : RuntimeValue → Option Rat
  | .intVal n => some (n : Rat)
  | .decVal q => some q
  | _ => none

/-- Strict non-coercing type validity check matching `_is_valid_value_for_spec` (`cross_field.py:49-68`). -/
def isValidValueForSpec (v : RuntimeValue) (spec : FieldSpec) : Bool :=
  match spec.fieldType, v with
  | .string, .strVal _ => true
  | .integer, .intVal _ => true
  | .decimal, .intVal _ => true
  | .decimal, .decVal _ => true
  | .boolean, .boolVal _ => true
  | .date, .dateVal _ => true
  | .array, .arrayVal _ => true
  | .object, .objectVal _ => true
  | _, _ => false

/-- Construct a validation-stage diagnostic for a cross-field rule with explicit `RuleSeverity`. -/
def mkCrossFieldIssue
    (code message : String) (sev : RuleSeverity) (recordId fieldPath : String) : DiagnosticIssue :=
  {
    code := if code == "" then "cross_field_error" else code,
    severity := match sev with | .error => .error | .warning => .warning,
    stage := .validation,
    message := if message == "" then "Cross-field validation rule failed." else message,
    recordId := some recordId,
    fieldPath := some fieldPath
  }

theorem mkCrossFieldIssue_valid
    (code message : String) (sev : RuleSeverity) (recordId fieldPath : String) :
    let d := mkCrossFieldIssue code message sev recordId fieldPath
    ValidDiagnosticIssue d ∧
    d.stage = .validation ∧
    (sev = .error → d.severity = .error) ∧
    (sev = .warning → d.severity = .warning) := by
  refine ⟨⟨?_, ?_⟩, rfl, ?_, ?_⟩
  · dsimp only [mkCrossFieldIssue]
    by_cases hc : code = "" <;> simp [hc]
  · dsimp only [mkCrossFieldIssue]
    by_cases hm : message = "" <;> simp [hm]
  · intro h; subst h; rfl
  · intro h; subst h; rfl

private def lookupFieldInputCF (fields : RecordFieldInput) (name : String) :
    FieldPresence × RuntimeValue :=
  match fields.find? (fun kv => kv.1 == name) with
  | some (_, pv) => pv
  | none => (.missing, .nullVal)

/-- Pure traversal of a nested object value along dot-separated path segments. -/
def resolveObjectEntries (entries : List (String × RuntimeValue)) :
    List String → FieldPresence × RuntimeValue
  | [] => (.missing, .nullVal)
  | [seg] =>
    match entries.find? (fun kv => kv.1 == seg) with
    | some (_, v) => (.present, v)
    | none => (.missing, .nullVal)
  | seg :: seg2 :: rest =>
    match entries.find? (fun kv => kv.1 == seg) with
    | some (_, .objectVal sub) => resolveObjectEntries sub (seg2 :: rest)
    | _ => (.missing, .nullVal)

/-- Pure path resolution against record fields matching `_resolve_record_path` (`cross_field.py:71-94`). -/
def resolveRecordPath
    (fields : RecordFieldInput) (schema : ExtractionSchema) (path : String) :
    FieldPresence × RuntimeValue × Option FieldSpec :=
  let spec? := resolveSchemaFieldSpec schema.fields path
  match splitPathSegments path with
  | [] => (.missing, .nullVal, spec?)
  | [top] =>
    let (p, v) := lookupFieldInputCF fields top
    (p, v, spec?)
  | top :: seg2 :: rest =>
    match lookupFieldInputCF fields top with
    | (.present, .objectVal entries) =>
      let (p, v) := resolveObjectEntries entries (seg2 :: rest)
      (p, v, spec?)
    | _ => (.missing, .nullVal, spec?)

/-- Strict typed comparison matching `_compare_typed_values` (`cross_field.py:134-162`). -/
def compareTypedValues
    (lv : RuntimeValue) (lt : FieldType) (rv : RuntimeValue) (rt : FieldType)
    (op : ComparisonOperator) : Bool :=
  if isNumericType lt && isNumericType rt then
    match toExactRat lv, toExactRat rv with
    | some lq, some rq =>
      match op with
      | .eq => lq == rq
      | .ne => lq != rq
      | .lt => decide (lq < rq)
      | .lte => decide (lq ≤ rq)
      | .gt => decide (rq < lq)
      | .gte => decide (rq ≤ lq)
    | _, _ => true
  else
    match lv, rv with
    | .strVal ls, .strVal rs =>
      match op with
      | .eq => ls == rs
      | .ne => ls != rs
      | .lt => decide (ls < rs)
      | .lte => decide (ls ≤ rs)
      | .gt => decide (rs < ls)
      | .gte => decide (rs ≤ ls)
    | .dateVal ld, .dateVal rd =>
      match op with
      | .eq => ld == rd
      | .ne => ld != rd
      | .lt => decide (ld < rd)
      | .lte => decide (ld ≤ rd)
      | .gt => decide (rd < ld)
      | .gte => decide (rd ≤ ld)
    | .boolVal lb, .boolVal rb =>
      match op with
      | .eq => lb == rb
      | .ne => lb != rb
      | _ => true
    | _, _ => true

/-- Pure evaluation of `ComparisonRule` matching `_eval_comparison` (`cross_field.py:165-199`). -/
def evalComparisonRule
    (rule : ComparisonRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) : Option DiagnosticIssue :=
  let (lp, lv, lspec?) := resolveRecordPath fields schema rule.leftField
  let (rp, rv, rspec?) := resolveRecordPath fields schema rule.rightField
  match lspec?, rspec? with
  | some lspec, some rspec =>
    if lp == .missing || rp == .missing ||
       !isValidValueForSpec lv lspec || !isValidValueForSpec rv rspec then
      none
    else if compareTypedValues lv lspec.fieldType rv rspec.fieldType rule.operator then
      none
    else
      let msg := rule.message.getD
        s!"Cross-field rule '{rule.ruleId}' failed: '{rule.leftField}' ({rule.operator.toString}) '{rule.rightField}' did not hold."
      some (mkCrossFieldIssue "cross_field_comparison_failed" msg rule.severity recordId rule.leftField)
  | _, _ => none

/-- Sum numeric elements of an array-of-objects term (`itemField = some itemPath`). -/
def sumObjectArrayElems
    (props : List (String × FieldSpec)) (itemPath : String) :
    List RuntimeValue → Option Rat
  | [] => some 0
  | elem :: rest =>
    match elem with
    | .objectVal entries =>
      match resolveSchemaFieldSpec props itemPath with
      | none => none
      | some espec =>
        let (ep, ev) := resolveObjectEntries entries (splitPathSegments itemPath)
        if ep == .missing || !isValidValueForSpec ev espec then
          none
        else
          match toExactRat ev, sumObjectArrayElems props itemPath rest with
          | some q, some acc => some (q + acc)
          | _, _ => none
    | _ => none

/-- Sum numeric elements of a primitive numeric array term (`itemField = none`). -/
def sumPrimitiveArrayElems (itemSpec : FieldSpec) : List RuntimeValue → Option Rat
  | [] => some 0
  | elem :: rest =>
    if !isValidValueForSpec elem itemSpec then
      none
    else
      match toExactRat elem, sumPrimitiveArrayElems itemSpec rest with
      | some q, some acc => some (q + acc)
      | _, _ => none

/-- Pure evaluation of `ArithmeticTerm` matching `_eval_arithmetic_term` (`cross_field.py:202-253`). -/
def evalArithmeticTerm
    (term : ArithmeticTerm) (fields : RecordFieldInput) (schema : ExtractionSchema) : Option Rat :=
  let (pres, val, spec?) := resolveRecordPath fields schema term.field
  match spec? with
  | none => none
  | some spec =>
    if pres == .missing || (match val with | .nullVal => true | _ => false) then
      if term.treatMissingAsZero then some 0 else none
    else if spec.fieldType == .array then
      match val with
      | .arrayVal elems =>
        let sum? : Option Rat :=
          match term.itemField with
          | some itemPath =>
            match spec.items with
            | some itemSpec =>
              match itemSpec.properties with
              | some props =>
                if props.isEmpty then none else sumObjectArrayElems props itemPath elems
              | none => none
            | none => none
          | none =>
            match spec.items with
            | some itemSpec => sumPrimitiveArrayElems itemSpec elems
            | none => none
        sum?.map (fun total => total * (term.sign : Rat))
      | _ => none
    else
      if !isValidValueForSpec val spec then
        none
      else
        (toExactRat val).map (fun q => q * (term.sign : Rat))

/-- Sum all terms of an `ArithmeticRule`; returns `none` if any term skips cleanly. -/
def sumArithmeticTerms
    (fields : RecordFieldInput) (schema : ExtractionSchema) : List ArithmeticTerm → Option Rat
  | [] => some 0
  | t :: rest =>
    match evalArithmeticTerm t fields schema, sumArithmeticTerms fields schema rest with
    | some q, some acc => some (q + acc)
    | _, _ => none

/-- Pure evaluation of `ArithmeticRule` matching `_eval_arithmetic` (`cross_field.py:256-292`). -/
def evalArithmeticRule
    (rule : ArithmeticRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) : Option DiagnosticIssue :=
  let (tp, tv, tspec?) := resolveRecordPath fields schema rule.targetField
  match tspec? with
  | none => none
  | some tspec =>
    if tp == .missing || !isValidValueForSpec tv tspec then
      none
    else
      match toExactRat tv, sumArithmeticTerms fields schema rule.terms with
      | some targetQ, some computedQ =>
        if decide (absRat (computedQ - targetQ) ≤ rule.tolerance) then
          none
        else
          let msg := rule.message.getD
            s!"Cross-field arithmetic rule '{rule.ruleId}' failed for '{rule.targetField}'."
          some (mkCrossFieldIssue "cross_field_arithmetic_mismatch" msg rule.severity recordId rule.targetField)
      | _, _ => none

/-- Strict equality matching `_values_equal_strict` (`cross_field.py:295-302`). -/
def valuesEqualStrict (actual expected : RuntimeValue) (ft : FieldType) : Bool :=
  if isNumericType ft then
    match toExactRat actual, toExactRat expected with
    | some aq, some eqQ => aq == eqQ
    | _, _ => false
  else
    runtimeValueEq actual expected

/-- Pure evaluation of `FieldCondition` matching `_eval_condition` (`cross_field.py:305-331`). -/
def evalFieldCondition
    (cond : FieldCondition) (fields : RecordFieldInput) (schema : ExtractionSchema) : Bool :=
  let (pres, val, spec?) := resolveRecordPath fields schema cond.field
  match cond.operator with
  | .isMissing => pres == .missing
  | .isPresent =>
    pres == .present && (match val with | .nullVal => false | _ => true)
  | .equals =>
    match spec?, cond.value with
    | some spec, some expected =>
      if pres == .missing || !isValidValueForSpec val spec then
        false
      else
        valuesEqualStrict val expected spec.fieldType
    | _, _ => false
  | .notEquals =>
    match spec?, cond.value with
    | some spec, some expected =>
      if pres == .missing || !isValidValueForSpec val spec then
        false
      else
        !valuesEqualStrict val expected spec.fieldType
    | _, _ => false
  | .inValues =>
    match spec?, cond.values with
    | some spec, some allowed =>
      if pres == .missing || !isValidValueForSpec val spec then
        false
      else
        allowed.any (fun item => valuesEqualStrict val item spec.fieldType)
    | _, _ => false

/-- Pure evaluation of `ConditionalPresenceRule` matching `_eval_conditional_presence` (`cross_field.py:334-382`). -/
def evalConditionalPresenceRule
    (rule : ConditionalPresenceRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) : List DiagnosticIssue :=
  if !evalFieldCondition rule.when fields schema then
    []
  else
    rule.targetFields.filterMap fun targetPath =>
      let (tp, tv, tspec?) := resolveRecordPath fields schema targetPath
      let isNonNull : Bool := match tv with | .nullVal => false | _ => true
      match rule.kind with
      | .requiredIf =>
        let targetNullable : Bool := match tspec? with | some ts => ts.nullable | none => false
        let isSatisfied : Bool := tp == .present && (isNonNull || targetNullable)
        if isSatisfied then
          none
        else
          let msg := rule.message.getD
            s!"Conditional rule '{rule.ruleId}' failed: field '{targetPath}' is required."
          some (mkCrossFieldIssue "conditional_required_missing" msg rule.severity recordId targetPath)
      | .forbiddenIf =>
        if tp == .present && isNonNull then
          let msg := rule.message.getD
            s!"Conditional rule '{rule.ruleId}' failed: field '{targetPath}' is forbidden."
          some (mkCrossFieldIssue "conditional_forbidden_present" msg rule.severity recordId targetPath)
        else
          none

/-- Pure evaluation of `GroupPresenceRule` matching `_eval_group_presence` (`cross_field.py:385-427`). -/
def evalGroupPresenceRule
    (rule : GroupPresenceRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) : Option DiagnosticIssue :=
  let presentFields : List String :=
    rule.fields.filter fun fpath =>
      let (fp, fv, _) := resolveRecordPath fields schema fpath
      fp == .present && (match fv with | .nullVal => false | _ => true)
  match rule.kind with
  | .mutuallyExclusive =>
    match presentFields with
    | first :: _ :: _ =>
      let msg := rule.message.getD
        s!"Group rule '{rule.ruleId}' failed: fields are mutually exclusive."
      some (mkCrossFieldIssue "mutually_exclusive_fields" msg rule.severity recordId first)
    | _ => none
  | .atLeastOneOf =>
    match presentFields, rule.fields with
    | [], first :: _ =>
      let msg := rule.message.getD
        s!"Group rule '{rule.ruleId}' failed: at least one field must be present."
      some (mkCrossFieldIssue "missing_required_group" msg rule.severity recordId first)
    | _, _ => none

/-- Evaluate a single `ValidationRule`, returning its emitted diagnostics in order. -/
def evalSingleRule
    (rule : ValidationRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) : List DiagnosticIssue :=
  match rule with
  | .comparison r => (evalComparisonRule r fields schema recordId).toList
  | .arithmetic r => (evalArithmeticRule r fields schema recordId).toList
  | .conditionalPresence r => evalConditionalPresenceRule r fields schema recordId
  | .groupPresence r => (evalGroupPresenceRule r fields schema recordId).toList

/-- Pure evaluation of all declared rules in order, partitioned into `(errors, warnings)`
    matching `evaluate_cross_field_rules` (`cross_field.py:430-463`). -/
def evaluateCrossFieldRules
    (fields : RecordFieldInput) (schema : ExtractionSchema)
    (rules : List ValidationRule) (recordId : String) :
    List DiagnosticIssue × List DiagnosticIssue :=
  match rules with
  | [] => ([], [])
  | rule :: rest =>
    let emitted := evalSingleRule rule fields schema recordId
    let ruleErrs := emitted.filter (fun d => d.severity == .error)
    let ruleWarns := emitted.filter (fun d => d.severity != .error)
    let (restErrs, restWarns) := evaluateCrossFieldRules fields schema rest recordId
    (ruleErrs ++ restErrs, ruleWarns ++ restWarns)

/-- Integrated deterministic record validation combining single-field/structural validation
    (`validateRecord`) with declarative cross-field rules (`evaluateCrossFieldRules`),
    matching `DeterministicValidator.validate_record` (`validator.py:82-152`). -/
def validateRecordWithRules
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (rules : List ValidationRule) (recordId : String) :
    ValidationOutcome × List (String × ValidationOutcome) × List DiagnosticIssue :=
  let (baseOutcome, fieldOutcomes) := validateRecord pm fields schema recordId
  let (cfErrors, cfWarnings) := evaluateCrossFieldRules fields schema rules recordId
  let allErrors := baseOutcome.errors ++ cfErrors
  (makeValidationOutcome allErrors, fieldOutcomes, cfWarnings)

-- =============================================================================
-- Soundness Helpers & Machine-Checked Theorems (18 required theorems)
-- =============================================================================

private theorem evalSingleRule_valid
    (rule : ValidationRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) :
    ∀ d ∈ evalSingleRule rule fields schema recordId,
      ValidDiagnosticIssue d ∧ d.stage = .validation ∧
      (rule.severity = .error → d.severity = .error) ∧
      (rule.severity = .warning → d.severity = .warning) := by
  intro d hd
  cases rule with
  | comparison r =>
    unfold evalSingleRule evalComparisonRule at hd
    dsimp only at hd
    cases hl : resolveRecordPath fields schema r.leftField with
    | mk lp restL =>
      cases restL with
      | mk lv lspec? =>
        cases hr : resolveRecordPath fields schema r.rightField with
        | mk rp restR =>
          cases restR with
          | mk rv rspec? =>
            rw [hl, hr] at hd
            cases lspec? with
            | none => simp at hd
            | some lspec =>
              cases rspec? with
              | none => simp at hd
              | some rspec =>
                dsimp only at hd
                split at hd
                · simp at hd
                · split at hd
                  · simp at hd
                  · simp only [Option.toList_some, List.mem_singleton] at hd
                    subst hd
                    exact mkCrossFieldIssue_valid _ _ _ _ _
  | arithmetic r =>
    unfold evalSingleRule evalArithmeticRule at hd
    dsimp only at hd
    cases ht : resolveRecordPath fields schema r.targetField with
    | mk tp restT =>
      cases restT with
      | mk tv tspec? =>
        rw [ht] at hd
        cases tspec? with
        | none => simp at hd
        | some tspec =>
          dsimp only at hd
          split at hd
          · simp at hd
          · split at hd
            · split at hd
              · simp at hd
              · simp only [Option.toList_some, List.mem_singleton] at hd
                subst hd
                exact mkCrossFieldIssue_valid _ _ _ _ _
            · simp at hd
  | conditionalPresence r =>
    unfold evalSingleRule evalConditionalPresenceRule at hd
    dsimp only at hd
    split at hd
    · simp at hd
    · rw [List.mem_filterMap] at hd
      rcases hd with ⟨targetPath, _, hmap⟩
      cases hkind : r.kind with
      | requiredIf =>
        rw [hkind] at hmap
        dsimp only at hmap
        by_cases hsat :
            ((resolveRecordPath fields schema targetPath).1 == .present &&
              ((match (resolveRecordPath fields schema targetPath).2.1 with | .nullVal => false | _ => true) ||
                match (resolveRecordPath fields schema targetPath).2.2 with | some ts => ts.nullable | none => false)) = true
        · simp [hsat] at hmap
        · simp only [hsat, Bool.false_eq_true, ↓reduceIte, Option.some.injEq] at hmap
          subst hmap
          exact mkCrossFieldIssue_valid _ _ _ _ _
      | forbiddenIf =>
        rw [hkind] at hmap
        dsimp only at hmap
        split at hmap <;> split at hmap
        · cases hmap; exact mkCrossFieldIssue_valid _ _ _ _ _
        · cases hmap
        · cases hmap; exact mkCrossFieldIssue_valid _ _ _ _ _
        · cases hmap
  | groupPresence r =>
    unfold evalSingleRule evalGroupPresenceRule at hd
    dsimp only at hd
    cases hkind : r.kind with
    | mutuallyExclusive =>
      rw [hkind] at hd
      dsimp only at hd
      split at hd
      · simp only [Option.toList_some, List.mem_singleton] at hd
        subst hd
        exact mkCrossFieldIssue_valid _ _ _ _ _
      · simp at hd
    | atLeastOneOf =>
      rw [hkind] at hd
      dsimp only at hd
      split at hd
      · simp only [Option.toList_some, List.mem_singleton] at hd
        subst hd
        exact mkCrossFieldIssue_valid _ _ _ _ _
      · simp at hd

/-- Cross-field rule evaluation and integrated record validation are strictly deterministic. -/
theorem cross_field_evaluation_is_deterministic
    (pm1 pm2 : PatternMatcher) (f1 f2 : RecordFieldInput)
    (s1 s2 : ExtractionSchema) (rs1 rs2 : List ValidationRule) (r1 r2 : String)
    (hpm : pm1 = pm2) (hf : f1 = f2) (hs : s1 = s2) (hrs : rs1 = rs2) (hr : r1 = r2) :
    evaluateCrossFieldRules f1 s1 rs1 r1 = evaluateCrossFieldRules f2 s2 rs2 r2 ∧
    validateRecordWithRules pm1 f1 s1 rs1 r1 = validateRecordWithRules pm2 f2 s2 rs2 r2 := by
  subst hpm; subst hf; subst hs; subst hrs; subst hr
  exact ⟨rfl, rfl⟩

/-- Every error returned by `evaluateCrossFieldRules` is a valid validation-stage `ERROR`. -/
theorem cross_field_errors_are_validation_stage_errors
    (fields : RecordFieldInput) (schema : ExtractionSchema)
    (rules : List ValidationRule) (recordId : String) :
    ∀ e ∈ (evaluateCrossFieldRules fields schema rules recordId).1,
      ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation := by
  induction rules with
  | nil =>
    intro e he
    simp [evaluateCrossFieldRules] at he
  | cons rule rest ih =>
    intro e he
    unfold evaluateCrossFieldRules at he
    dsimp only at he
    rw [List.mem_append] at he
    cases he with
    | inl h1 =>
      rw [List.mem_filter] at h1
      have hv := evalSingleRule_valid rule fields schema recordId e h1.1
      exact ⟨hv.1, beq_iff_eq.mp h1.2, hv.2.1⟩
    | inr h2 =>
      exact ih e h2

/-- Every warning returned by `evaluateCrossFieldRules` is a valid validation-stage `WARNING`. -/
theorem cross_field_warnings_are_validation_stage_warnings
    (fields : RecordFieldInput) (schema : ExtractionSchema)
    (rules : List ValidationRule) (recordId : String) :
    ∀ w ∈ (evaluateCrossFieldRules fields schema rules recordId).2,
      ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .validation := by
  induction rules with
  | nil =>
    intro w hw
    simp [evaluateCrossFieldRules] at hw
  | cons rule rest ih =>
    intro w hw
    unfold evaluateCrossFieldRules at hw
    dsimp only at hw
    rw [List.mem_append] at hw
    cases hw with
    | inl h1 =>
      rw [List.mem_filter] at h1
      have hv := evalSingleRule_valid rule fields schema recordId w h1.1
      have hsev : w.severity = .warning := by
        cases hs : w.severity with
        | error => simp [hs] at h1
        | warning => rfl
      exact ⟨hv.1, hsev, hv.2.1⟩
    | inr h2 =>
      exact ih w h2

private theorem makeValidationOutcome_valid_cf (errs : List DiagnosticIssue)
    (herrs : ∀ e ∈ errs, ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation) :
    ValidValidationOutcome (makeValidationOutcome errs) := by
  cases errs with
  | nil => simp [makeValidationOutcome, ValidValidationOutcome]
  | cons e es =>
    simp only [makeValidationOutcome, List.isEmpty_cons, Bool.false_eq_true, ↓reduceIte,
      ValidValidationOutcome]
    refine ⟨herrs, by intro h; exact absurd rfl h⟩

private theorem makeValidationOutcome_errors_cf (errs : List DiagnosticIssue) :
    (makeValidationOutcome errs).errors = errs := by
  cases errs <;> rfl

/-- Integrated `validateRecordWithRules` always produces Contract v1.0-valid record & field
    `ValidationOutcome`s and validation-stage `WARNING` diagnostics. -/
theorem validate_record_with_rules_outcomes_satisfy_contract
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (rules : List ValidationRule) (recordId : String) :
    let res := validateRecordWithRules pm fields schema rules recordId
    ValidValidationOutcome res.1 ∧
    (∀ kv ∈ res.2.1, ValidValidationOutcome kv.2) ∧
    (∀ w ∈ res.2.2, ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .validation) := by
  have hbase := validate_record_outcomes_satisfy_contract pm fields schema recordId
  refine ⟨?_, hbase.2, cross_field_warnings_are_validation_stage_warnings fields schema rules recordId⟩
  unfold validateRecordWithRules
  dsimp only
  apply makeValidationOutcome_valid_cf
  intro e he
  rw [List.mem_append] at he
  cases he with
  | inl h1 =>
    exact hbase.1.1 e h1
  | inr h2 =>
    exact cross_field_errors_are_validation_stage_errors fields schema rules recordId e h2

/-- Backward compatibility: when `rules = []`, `validateRecordWithRules` reduces identically to
    Increment 2's `validateRecord` with empty warnings. -/
theorem validate_record_with_rules_empty_reduces_to_validate_record
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) :
    let base := validateRecord pm fields schema recordId
    validateRecordWithRules pm fields schema [] recordId = (base.1, base.2, []) := by
  unfold validateRecordWithRules evaluateCrossFieldRules
  dsimp only
  rw [List.append_nil]
  have hmk : makeValidationOutcome (validateRecord pm fields schema recordId).1.errors =
      (validateRecord pm fields schema recordId).1 := by
    unfold validateRecord
    dsimp only
    rw [makeValidationOutcome_errors_cf]
  rw [hmk]

/-- Integrated record validity: record-level `VALID` holds iff all field outcomes are `VALID`
    AND cross-field rule errors are empty (warnings never affect record validity). -/
theorem validate_record_with_rules_valid_iff_fields_and_rule_errors_valid
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (rules : List ValidationRule) (recordId : String) :
    let res := validateRecordWithRules pm fields schema rules recordId
    let cfErrs := (evaluateCrossFieldRules fields schema rules recordId).1
    res.1.status = .valid ↔ ((∀ kv ∈ res.2.1, kv.2.status = .valid) ∧ cfErrs = []) := by
  dsimp only
  unfold validateRecordWithRules
  dsimp only
  rw [validator_valid_iff_empty_errors, List.append_eq_nil_iff]
  have hbaseValid := validate_record_valid_iff_all_fields_valid pm fields schema recordId
  dsimp only at hbaseValid
  have hbaseErr : (validateRecord pm fields schema recordId).1.errors = [] ↔
      (validateRecord pm fields schema recordId).1.status = .valid := by
    unfold validateRecord
    dsimp only
    rw [makeValidationOutcome_errors_cf]
    exact (validator_valid_iff_empty_errors _).symm
  rw [hbaseErr, hbaseValid]

/-- Any cross-field `ERROR` diagnostic forces record-level `ValidationStatus.invalid`, even when
    every individual field outcome has `status = VALID`. -/
theorem rule_error_forces_record_invalid_even_when_all_fields_valid
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (rules : List ValidationRule) (recordId : String)
    (hcfErr : (evaluateCrossFieldRules fields schema rules recordId).1 ≠ []) :
    (validateRecordWithRules pm fields schema rules recordId).1.status = .invalid := by
  unfold validateRecordWithRules
  dsimp only
  apply (validator_invalid_iff_nonempty_errors _).mpr
  intro happ
  have hnil := (List.append_eq_nil_iff.mp happ).2
  exact hcfErr hnil

private theorem evaluateCrossFieldRules_all_warnings_no_errors
    (fields : RecordFieldInput) (schema : ExtractionSchema)
    (rules : List ValidationRule) (recordId : String)
    (hwarnOnly : ∀ r ∈ rules, r.severity = .warning) :
    (evaluateCrossFieldRules fields schema rules recordId).1 = [] := by
  induction rules with
  | nil => rfl
  | cons rule rest ih =>
    unfold evaluateCrossFieldRules
    dsimp only
    have hrest : (evaluateCrossFieldRules fields schema rest recordId).1 = [] :=
      ih (fun r hr => hwarnOnly r (List.mem_cons_of_mem _ hr))
    rw [hrest, List.append_nil]
    have hrw : rule.severity = .warning := hwarnOnly rule List.mem_cons_self
    cases hfilt : (evalSingleRule rule fields schema recordId).filter (fun d => d.severity == .error) with
    | nil => rfl
    | cons d ds =>
      have hmem : d ∈ (evalSingleRule rule fields schema recordId).filter (fun d => d.severity == .error) := by
        simp [hfilt]
      rw [List.mem_filter] at hmem
      have hsevWarn := (evalSingleRule_valid rule fields schema recordId d hmem.1).2.2.2 hrw
      rw [hsevWarn] at hmem
      cases hmem.2

/-- Warning-severity rules never emit errors into `ValidationOutcome.errors` and never flip an
    otherwise `VALID` record to `INVALID`. -/
theorem rule_warnings_never_enter_errors_or_invalidate_valid_record
    (pm : PatternMatcher) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (rules : List ValidationRule) (recordId : String)
    (hwarnOnly : ∀ r ∈ rules, r.severity = .warning) :
    (evaluateCrossFieldRules fields schema rules recordId).1 = [] ∧
    ((validateRecord pm fields schema recordId).1.status = .valid →
      (validateRecordWithRules pm fields schema rules recordId).1.status = .valid) := by
  have hnoErr := evaluateCrossFieldRules_all_warnings_no_errors fields schema rules recordId hwarnOnly
  refine ⟨hnoErr, ?_⟩
  intro hvalid
  have hiff := validate_record_with_rules_valid_iff_fields_and_rule_errors_valid pm fields schema rules recordId
  dsimp only at hiff
  rw [hiff]
  exact ⟨(validate_record_valid_iff_all_fields_valid pm fields schema recordId).mp hvalid, hnoErr⟩

/-- `ComparisonRule` skips cleanly (`none`) whenever either operand is `MISSING` or `nullVal`. -/
theorem comparison_rule_skips_when_operand_missing_or_null
    (rule : ComparisonRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String)
    (hskip : (resolveRecordPath fields schema rule.leftField).1 = .missing ∨
             (resolveRecordPath fields schema rule.leftField).2.1 = .nullVal ∨
             (resolveRecordPath fields schema rule.rightField).1 = .missing ∨
             (resolveRecordPath fields schema rule.rightField).2.1 = .nullVal) :
    evalComparisonRule rule fields schema recordId = none := by
  unfold evalComparisonRule
  cases hl : resolveRecordPath fields schema rule.leftField with
  | mk lp restL =>
    cases restL with
    | mk lv lspec? =>
      cases hr : resolveRecordPath fields schema rule.rightField with
      | mk rp restR =>
        cases restR with
        | mk rv rspec? =>
          rw [hl, hr] at hskip
          dsimp only at hskip ⊢
          cases lspec? with
          | none => rfl
          | some lspec =>
            cases rspec? with
            | none => rfl
            | some rspec =>
              rcases hskip with h | h | h | h
              · subst h; simp
              · subst h; cases lspec.fieldType <;> simp [isValidValueForSpec]
              · subst h; simp
              · subst h; cases rspec.fieldType <;> simp [isValidValueForSpec]

/-- Strict non-coercion in `ComparisonRule`: a `boolVal` on a numeric field is never coerced and
    triggers a clean skip (`none`). -/
theorem comparison_rule_no_coercion_bool_for_numeric
    (rule : ComparisonRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String) (b : Bool) (lspec : FieldSpec)
    (hres : resolveRecordPath fields schema rule.leftField = (.present, .boolVal b, some lspec))
    (hnum : lspec.fieldType = .integer ∨ lspec.fieldType = .decimal) :
    evalComparisonRule rule fields schema recordId = none := by
  unfold evalComparisonRule
  rw [hres]
  cases resolveRecordPath fields schema rule.rightField with
  | mk rp restR =>
    cases restR with
    | mk rv rspec? =>
      cases rspec? with
      | none => rfl
      | some rspec =>
        cases hnum with
        | inl hi => simp [isValidValueForSpec, hi]
        | inr hd => simp [isValidValueForSpec, hd]

/-- Exact rational arithmetic soundness: when the computed sum matches the target within
    `tolerance`, `evalArithmeticRule` passes (`none`). -/
theorem arithmetic_rule_exact_balance_within_tolerance_passes
    (rule : ArithmeticRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String)
    (tspec : FieldSpec) (tv : RuntimeValue) (targetQ computedQ : Rat)
    (hres : resolveRecordPath fields schema rule.targetField = (.present, tv, some tspec))
    (hval : isValidValueForSpec tv tspec = true)
    (htq : toExactRat tv = some targetQ)
    (hcq : sumArithmeticTerms fields schema rule.terms = some computedQ)
    (hbal : absRat (computedQ - targetQ) ≤ rule.tolerance) :
    evalArithmeticRule rule fields schema recordId = none := by
  unfold evalArithmeticRule
  rw [hres]
  simp [hval, htq, hcq, hbal]

/-- Exact rational arithmetic soundness: when `|computed - target| > tolerance`, `evalArithmeticRule`
    emits a `cross_field_arithmetic_mismatch` diagnostic on `rule.targetField`. -/
theorem arithmetic_rule_deviation_exceeding_tolerance_fails
    (rule : ArithmeticRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String)
    (tspec : FieldSpec) (tv : RuntimeValue) (targetQ computedQ : Rat)
    (hres : resolveRecordPath fields schema rule.targetField = (.present, tv, some tspec))
    (hval : isValidValueForSpec tv tspec = true)
    (htq : toExactRat tv = some targetQ)
    (hcq : sumArithmeticTerms fields schema rule.terms = some computedQ)
    (hfail : ¬ (absRat (computedQ - targetQ) ≤ rule.tolerance)) :
    ∃ d, evalArithmeticRule rule fields schema recordId = some d ∧
      d.code = "cross_field_arithmetic_mismatch" ∧
      d.fieldPath = some rule.targetField := by
  unfold evalArithmeticRule
  rw [hres]
  simp [hval, htq, hcq, hfail, mkCrossFieldIssue]

/-- `ArithmeticTerm.treatMissingAsZero`: missing or null resolved term yields `some 0` when
    `treatMissingAsZero = true`, and skips (`none`) when `treatMissingAsZero = false`. -/
theorem arithmetic_rule_treat_missing_as_zero_vs_skip
    (term : ArithmeticTerm) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (spec : FieldSpec) (pres : FieldPresence) (val : RuntimeValue)
    (hres : resolveRecordPath fields schema term.field = (pres, val, some spec))
    (hmissOrNull : pres = .missing ∨ val = .nullVal) :
    (term.treatMissingAsZero = true → evalArithmeticTerm term fields schema = some 0) ∧
    (term.treatMissingAsZero = false → evalArithmeticTerm term fields schema = none) := by
  constructor
  · intro ht
    unfold evalArithmeticTerm
    rw [hres]
    cases hmissOrNull with
    | inl hm => simp [hm, ht]
    | inr hn => simp [hn, ht]
  · intro hf
    unfold evalArithmeticTerm
    rw [hres]
    cases hmissOrNull with
    | inl hm => simp [hm, hf]
    | inr hn => simp [hn, hf]

/-- Strict non-coercion in `ArithmeticRule`: `boolVal` or `nonFiniteDecVal` as target never
    coerces and skips cleanly (`none`). -/
theorem arithmetic_rule_no_coercion_bool_or_nonfinite_skips
    (rule : ArithmeticRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String)
    (tspec : FieldSpec) (tv : RuntimeValue)
    (hres : resolveRecordPath fields schema rule.targetField = (.present, tv, some tspec))
    (hbad : (∃ b, tv = .boolVal b) ∨ tv = .nonFiniteDecVal) :
    evalArithmeticRule rule fields schema recordId = none := by
  unfold evalArithmeticRule
  rw [hres]
  cases hbad with
  | inl hb =>
    rcases hb with ⟨b, rfl⟩
    cases tspec.fieldType <;> simp [isValidValueForSpec, toExactRat]
  | inr hnf =>
    subst hnf
    cases tspec.fieldType <;> simp [isValidValueForSpec]

/-- A `ConditionalPresenceRule` is completely inert (`[]`) whenever its `when` condition is false. -/
theorem conditional_rule_inert_when_condition_false
    (rule : ConditionalPresenceRule) (fields : RecordFieldInput)
    (schema : ExtractionSchema) (recordId : String)
    (hfalse : evalFieldCondition rule.when fields schema = false) :
    evalConditionalPresenceRule rule fields schema recordId = [] := by
  unfold evalConditionalPresenceRule
  simp [hfalse]

/-- In `FieldCondition`, `IS_MISSING` holds for `FieldPresence.missing` and is strictly false
    for an explicit `FieldPresence.present` + `nullVal` (preserving missing vs. null separation). -/
theorem conditional_is_missing_distinct_from_present_null
    (field : String) (fields : RecordFieldInput) (schema : ExtractionSchema) (spec? : Option FieldSpec) :
    (resolveRecordPath fields schema field = (.missing, .nullVal, spec?) →
      evalFieldCondition { field := field, operator := .isMissing } fields schema = true) ∧
    (resolveRecordPath fields schema field = (.present, .nullVal, spec?) →
      evalFieldCondition { field := field, operator := .isMissing } fields schema = false) := by
  constructor
  · intro h
    unfold evalFieldCondition
    rw [h]
    rfl
  · intro h
    unfold evalFieldCondition
    rw [h]
    rfl

/-- `ConditionalPresenceRule` semantics: when the condition holds on a single target,
    `requiredIf` accepts `PRESENT + null` iff `nullable = true` (emitting `conditional_required_missing`
    on `MISSING` or disallowed `null`), while `forbiddenIf` rejects `PRESENT` non-null with
    `conditional_forbidden_present` and accepts `PRESENT + null`. -/
theorem conditional_required_if_honors_nullable_and_forbidden_if_semantics
    (ruleId targetPath : String) (whenCond : FieldCondition)
    (sev : RuleSeverity) (msg : Option String)
    (fields : RecordFieldInput) (schema : ExtractionSchema) (recordId : String)
    (tspec : FieldSpec)
    (hwhen : evalFieldCondition whenCond fields schema = true) :
    (resolveRecordPath fields schema targetPath = (.present, .nullVal, some tspec) →
      tspec.nullable = true →
      evalConditionalPresenceRule
        { ruleId := ruleId, kind := .requiredIf, when := whenCond, targetFields := [targetPath],
          severity := sev, message := msg }
        fields schema recordId = []) ∧
    (resolveRecordPath fields schema targetPath = (.missing, .nullVal, some tspec) →
      ∃ d, evalConditionalPresenceRule
        { ruleId := ruleId, kind := .requiredIf, when := whenCond, targetFields := [targetPath],
          severity := sev, message := msg }
        fields schema recordId = [d] ∧ d.code = "conditional_required_missing") ∧
    (∀ s, resolveRecordPath fields schema targetPath = (.present, .strVal s, some tspec) →
      ∃ d, evalConditionalPresenceRule
        { ruleId := ruleId, kind := .forbiddenIf, when := whenCond, targetFields := [targetPath],
          severity := sev, message := msg }
        fields schema recordId = [d] ∧ d.code = "conditional_forbidden_present") ∧
    (resolveRecordPath fields schema targetPath = (.present, .nullVal, some tspec) →
      evalConditionalPresenceRule
        { ruleId := ruleId, kind := .forbiddenIf, when := whenCond, targetFields := [targetPath],
          severity := sev, message := msg }
        fields schema recordId = []) := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · intro hres hnul
    unfold evalConditionalPresenceRule
    simp [hwhen, hres, hnul]
  · intro hres
    unfold evalConditionalPresenceRule
    simp [hwhen, hres, mkCrossFieldIssue]
  · intro s hres
    unfold evalConditionalPresenceRule
    simp [hwhen, hres, mkCrossFieldIssue]
  · intro hres
    unfold evalConditionalPresenceRule
    simp [hwhen, hres]

/-- `GroupPresenceRule` semantics on a 2-field group `[f1, f2]`:
    - `mutuallyExclusive` emits `mutually_exclusive_fields` on `f1` when both are present non-null,
      and passes when only one is present non-null.
    - `atLeastOneOf` emits `missing_required_group` on `f1` when both are missing, and passes
      when at least one is present non-null. -/
theorem group_presence_mutually_exclusive_and_at_least_one_semantics
    (ruleId f1 f2 : String) (sev : RuleSeverity) (msg : Option String)
    (fields : RecordFieldInput) (schema : ExtractionSchema) (recordId : String)
    (s1 s2 : String) (sp1 sp2 : Option FieldSpec) :
    (resolveRecordPath fields schema f1 = (.present, .strVal s1, sp1) →
      resolveRecordPath fields schema f2 = (.present, .strVal s2, sp2) →
      ∃ d, evalGroupPresenceRule
        { ruleId := ruleId, kind := .mutuallyExclusive, fields := [f1, f2], severity := sev, message := msg }
        fields schema recordId = some d ∧
        d.code = "mutually_exclusive_fields" ∧ d.fieldPath = some f1) ∧
    (resolveRecordPath fields schema f1 = (.present, .strVal s1, sp1) →
      resolveRecordPath fields schema f2 = (.missing, .nullVal, sp2) →
      evalGroupPresenceRule
        { ruleId := ruleId, kind := .mutuallyExclusive, fields := [f1, f2], severity := sev, message := msg }
        fields schema recordId = none) ∧
    (resolveRecordPath fields schema f1 = (.missing, .nullVal, sp1) →
      resolveRecordPath fields schema f2 = (.missing, .nullVal, sp2) →
      ∃ d, evalGroupPresenceRule
        { ruleId := ruleId, kind := .atLeastOneOf, fields := [f1, f2], severity := sev, message := msg }
        fields schema recordId = some d ∧
        d.code = "missing_required_group" ∧ d.fieldPath = some f1) ∧
    (resolveRecordPath fields schema f1 = (.present, .strVal s1, sp1) →
      resolveRecordPath fields schema f2 = (.missing, .nullVal, sp2) →
      evalGroupPresenceRule
        { ruleId := ruleId, kind := .atLeastOneOf, fields := [f1, f2], severity := sev, message := msg }
        fields schema recordId = none) := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · intro h1 h2
    unfold evalGroupPresenceRule
    simp [h1, h2, mkCrossFieldIssue]
  · intro h1 h2
    unfold evalGroupPresenceRule
    simp [h1, h2]
  · intro h1 h2
    unfold evalGroupPresenceRule
    simp [h1, h2, mkCrossFieldIssue]
  · intro h1 h2
    unfold evalGroupPresenceRule
    simp [h1, h2]

end StructaFormal
