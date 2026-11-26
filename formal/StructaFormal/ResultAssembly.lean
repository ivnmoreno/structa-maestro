/-
  Structa Formal Assurance Layer — Full ResultBuilder Assembly & Strategy-to-Contract Firewall Soundness.
  Authoritative Python sources:
    - `src/structa_maestro/builder/result_builder.py` (`ResultBuilder`)
    - `src/structa_maestro/confidence/normalizer.py` (`ConfidenceNormalizer`)
    - `src/structa_maestro/strategies/base.py` (`ProposedField`, `ProposedRecord`, `StrategyOutput`)
-/

import StructaFormal.ConfidenceInvariants
import StructaFormal.CrossFieldValidation
import StructaFormal.ProvenanceAssociation
import StructaFormal.StatusAssembly

namespace StructaFormal

-- =============================================================================
-- Deterministic `record-NNN` Formatting & Injectivity
-- =============================================================================

private def digitChar (d : Nat) : Char := Char.ofNat ('0'.toNat + d)
private def charDigit (c : Char) : Nat := c.toNat - '0'.toNat

private theorem charDigit_digitChar (d : Nat) (hd : d < 10) : charDigit (digitChar d) = d := by
  have h : d = 0 ∨ d = 1 ∨ d = 2 ∨ d = 3 ∨ d = 4 ∨ d = 5 ∨ d = 6 ∨ d = 7 ∨ d = 8 ∨ d = 9 := by omega
  rcases h with rfl | rfl | rfl | rfl | rfl | rfl | rfl | rfl | rfl | rfl <;> rfl

private def natToRevDigitsFuel : Nat → Nat → List Char
  | 0, n => [digitChar (n % 10)]
  | fuel + 1, n =>
    if n < 10 then [digitChar n]
    else digitChar (n % 10) :: natToRevDigitsFuel fuel (n / 10)

private def natToRevDigits (n : Nat) : List Char :=
  natToRevDigitsFuel n n

private def foldDigits (acc : Nat) : List Char → Nat
  | [] => acc
  | c :: rest => foldDigits (acc * 10 + charDigit c) rest

private theorem foldDigits_append (acc : Nat) (xs ys : List Char) :
    foldDigits acc (xs ++ ys) = foldDigits (foldDigits acc xs) ys := by
  induction xs generalizing acc with
  | nil => rfl
  | cons c cs ih => exact ih (acc * 10 + charDigit c)

private theorem foldDigits_zero_cons (ds : List Char) :
    foldDigits 0 ('0' :: ds) = foldDigits 0 ds := rfl

private theorem foldDigits_single (acc : Nat) (c : Char) :
    foldDigits acc [c] = acc * 10 + charDigit c := rfl

private theorem foldDigits_natToRevDigitsFuel :
    ∀ fuel n : Nat, n ≤ fuel → foldDigits 0 (natToRevDigitsFuel fuel n).reverse = n
  | 0, n, hle => by
    have h0 : n = 0 := by omega
    subst h0; rfl
  | fuel + 1, n, hle => by
    unfold natToRevDigitsFuel
    by_cases h : n < 10
    · simp only [h, ↓reduceIte, List.reverse_cons, List.reverse_nil, List.nil_append,
        foldDigits_single, Nat.zero_mul, Nat.zero_add]
      exact charDigit_digitChar n h
    · simp only [h, ↓reduceIte, List.reverse_cons, foldDigits_append, foldDigits_single]
      rw [foldDigits_natToRevDigitsFuel fuel (n / 10) (by omega),
        charDigit_digitChar (n % 10) (Nat.mod_lt n (by decide))]
      omega

private theorem foldDigits_natToDigits (n : Nat) :
    foldDigits 0 (natToRevDigits n).reverse = n :=
  foldDigits_natToRevDigitsFuel n n (Nat.le_refl n)

private def recordPrefixChars : List Char := ['r', 'e', 'c', 'o', 'r', 'd', '-']

/-- Deterministic 1-indexed zero-padded record identifier matching `f"record-{idx:03d}"`. -/
def formatRecordId (idx : Nat) : String :=
  let digits := (natToRevDigits idx).reverse
  let padded :=
    if idx < 10 then ['0', '0'] ++ digits
    else if idx < 100 then ['0'] ++ digits
    else digits
  String.ofList (recordPrefixChars ++ padded)

private def parseRecordIdNum (s : String) : Nat :=
  foldDigits 0 (s.toList.drop 7)

private theorem parseRecordIdNum_formatRecordId (idx : Nat) :
    parseRecordIdNum (formatRecordId idx) = idx := by
  unfold parseRecordIdNum formatRecordId
  simp only [String.toList_ofList]
  have hdrop : ∀ xs : List Char, (recordPrefixChars ++ xs).drop 7 = xs := by
    intro xs; rfl
  rw [hdrop]
  by_cases h1 : idx < 10
  · simp only [h1, ↓reduceIte, List.cons_append, List.nil_append]
    rw [foldDigits_zero_cons, foldDigits_zero_cons]
    exact foldDigits_natToDigits idx
  · by_cases h2 : idx < 100
    · simp only [h1, h2, ↓reduceIte, List.cons_append, List.nil_append]
      rw [foldDigits_zero_cons]
      exact foldDigits_natToDigits idx
    · simp only [h1, h2, ↓reduceIte]
      exact foldDigits_natToDigits idx

-- =============================================================================
-- Untrusted Strategy DTOs Crossing the Strategy-to-Contract Firewall
-- =============================================================================

/-- Strategy-proposed field input (`ProposedField` in `strategies/base.py:30-41`) plus explicit
    provenance-fault flag modeling exception isolation (`result_builder.py:254-266`). -/
structure ProposedFieldInput where
  presence : FieldPresence := .present
  value : RuntimeValue := .nullVal
  confidence : ConfidenceInput := .missing
  confidenceScale : String := "0-1"
  confidenceMethod : Option String := none
  evidence : List ProposedEvidenceInput := []
  ambiguous : Bool := false
  provenanceFailed : Bool := false
  warnings : List DiagnosticIssue := []
  errors : List DiagnosticIssue := []
  deriving Repr

/-- Strategy-proposed record input (`ProposedRecord` in `strategies/base.py:45-52`). -/
structure ProposedRecordInput where
  fields : List (String × ProposedFieldInput) := []
  partialFlag : Bool := false
  failed : Bool := false
  errors : List DiagnosticIssue := []
  warnings : List DiagnosticIssue := []
  deriving Repr

/-- Strategy output crossing the firewall (`StrategyOutput` in `strategies/base.py:56-65`). -/
structure StrategyOutputInput where
  records : List ProposedRecordInput := []
  partialFlag : Bool := false
  failed : Bool := false
  errors : List DiagnosticIssue := []
  warnings : List DiagnosticIssue := []
  defaultConfidenceMethod : String := "extraction-assessment"
  deriving Repr

-- =============================================================================
-- Pure Assembly Functions Matching `ResultBuilder` (`result_builder.py`)
-- =============================================================================

/-- Convert a runtime value into Contract v1.0's abstract `FieldValue` preserving null vs. non-null. -/
def runtimeToFieldValue : RuntimeValue → FieldValue
  | .nullVal => .nullValue
  | _ => .nonNull "value"

/-- Construct a `DiagnosticIssue` guaranteed to satisfy `ValidDiagnosticIssue`. -/
def mkSafeDiag
    (code : String) (sev : IssueSeverity) (stage : PipelineStage) (msg : String)
    (recordId fieldPath : Option String := none) : DiagnosticIssue :=
  {
    code := if code == "" then "diagnostic" else code,
    severity := sev,
    stage := stage,
    message := if msg == "" then "Diagnostic issue." else msg,
    recordId := recordId,
    fieldPath := fieldPath
  }

private theorem mkSafeDiag_valid
    (code : String) (sev : IssueSeverity) (stage : PipelineStage) (msg : String)
    (recordId fieldPath : Option String) :
    ValidDiagnosticIssue (mkSafeDiag code sev stage msg recordId fieldPath) := by
  refine ⟨?_, ?_⟩
  · dsimp only [mkSafeDiag]; by_cases hc : code = "" <;> simp [hc]
  · dsimp only [mkSafeDiag]; by_cases hm : msg = "" <;> simp [hm]

/-- Confidence normalization returning extraction warnings matching `ConfidenceNormalizer.normalize`. -/
def normalizeConfidenceWarnings
    (input : ConfidenceInput) (rawScale : String) (recordId fieldName : String) :
    List DiagnosticIssue :=
  let scale := cleanScale rawScale
  match input with
  | .missing => []
  | .nonNumeric =>
    [mkSafeDiag "invalid_confidence_value" .warning .extraction
      s!"Non-numeric confidence hint for field '{fieldName}' was ignored."
      (some recordId) (some fieldName)]
  | .nonFinite =>
    [mkSafeDiag "invalid_confidence_range" .warning .extraction
      s!"Non-finite confidence score for field '{fieldName}' discarded."
      (some recordId) (some fieldName)]
  | .finite v =>
    if scale == "0-1" && !(decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat))) then
      [mkSafeDiag "invalid_confidence_range" .warning .extraction
        s!"Confidence value for field '{fieldName}' fell outside declared scale '0-1'."
        (some recordId) (some fieldName)]
    else []

private theorem normalizeConfidenceWarnings_valid
    (input : ConfidenceInput) (rawScale recordId fieldName : String) :
    ∀ w ∈ normalizeConfidenceWarnings input rawScale recordId fieldName,
      ValidDiagnosticIssue w := by
  unfold normalizeConfidenceWarnings
  cases input with
  | missing => simp
  | nonNumeric =>
    intro w hw; simp only [List.mem_singleton] at hw; subst hw
    exact mkSafeDiag_valid _ _ _ _ _ _
  | nonFinite =>
    intro w hw; simp only [List.mem_singleton] at hw; subst hw
    exact mkSafeDiag_valid _ _ _ _ _ _
  | finite v =>
    intro w hw
    simp at hw
    rw [hw.2]
    exact mkSafeDiag_valid _ _ _ _ _ _

private def lookupProposedField (fields : List (String × ProposedFieldInput)) (name : String) :
    Option ProposedFieldInput :=
  (fields.find? (fun kv => kv.1 == name)).map Prod.snd

private def lookupFieldOutcome
    (outcomes : List (String × ValidationOutcome)) (name : String) : ValidationOutcome :=
  match outcomes.find? (fun kv => kv.1 == name) with
  | some (_, o) => o
  | none => { status := .valid, errors := [] }

/-- Build the ordered field name list matching `result_builder.py:174-176`. -/
def orderedRecordFieldNames (schema : ExtractionSchema) (rec : ProposedRecordInput) : List String :=
  let declaredKeys := schema.fields.map Prod.fst
  let extraKeys :=
    sortUniqueStrings ((rec.fields.map Prod.fst).filter (fun k => !declaredKeys.contains k))
  declaredKeys ++ extraKeys

/-- Build the sanitized `RecordFieldInput` passed to `DeterministicValidator` (`result_builder.py:178-183`). -/
def buildValidationInput (schema : ExtractionSchema) (rec : ProposedRecordInput) : RecordFieldInput :=
  (orderedRecordFieldNames schema rec).map fun fname =>
    match lookupProposedField rec.fields fname with
    | none => (fname, (.missing, .nullVal))
    | some pf =>
      match pf.presence with
      | .missing => (fname, (.missing, .nullVal))
      | .present => (fname, (.present, pf.value))

/-- Assemble a single `ExtractedField` behind the Strategy-to-Contract Firewall (`result_builder.py:195-277`). -/
def assembleField
    (docId : String)
    (segments : List DocumentSegment)
    (schema : ExtractionSchema)
    (defaultConfMethod : String)
    (maxExcerptChars : Nat)
    (recordId : String)
    (fieldOutcomes : List (String × ValidationOutcome))
    (rec : ProposedRecordInput)
    (fname : String) : ExtractedField :=
  let vtype := (schema.fields.find? (fun kv => kv.1 == fname)).map (fun kv => kv.2.fieldType)
  let fieldValOutcome := lookupFieldOutcome fieldOutcomes fname
  match lookupProposedField rec.fields fname with
  | none =>
    {
      presence := .missing,
      value := .nullValue,
      valueType := vtype,
      confidence := none,
      provenance := [],
      validation := fieldValOutcome,
      errors := [],
      warnings := []
    }
  | some pf =>
    let pres := pf.presence
    let sanitizedVal : FieldValue :=
      match pres with
      | .missing => .nullValue
      | .present => runtimeToFieldValue pf.value
    let ambigWarns : List DiagnosticIssue :=
      if pf.ambiguous then
        [mkSafeDiag "ambiguous_source_value" .warning .extraction
          s!"Source document contains ambiguous candidates for '{fname}'."
          (some recordId) (some fname)]
      else []
    let conf := normalizeConfidence pf.confidence pf.confidenceScale pf.confidenceMethod defaultConfMethod
    let confWarns := normalizeConfidenceWarnings pf.confidence pf.confidenceScale recordId fname
    let (provEntries, provErrs, provWarns) :=
      if pf.provenanceFailed then
        ([],
         [mkSafeDiag "provenance_association_failed" .error .provenance
           s!"Provenance association failed for field '{fname}'."
           (some recordId) (some fname)],
         [])
      else
        let (evs, pws) :=
          associateEvidence docId segments fname pres recordId maxExcerptChars pf.evidence
        (evs, [], pws)
    {
      presence := pres,
      value := sanitizedVal,
      valueType := vtype,
      confidence := conf,
      provenance := provEntries,
      validation := fieldValOutcome,
      errors := pf.errors ++ provErrs,
      warnings := pf.warnings ++ ambigWarns ++ confWarns ++ provWarns
    }

/-- Pure formalization of `ResultBuilder._build_record` (`result_builder.py:160-293`). -/
def buildRecord
    (pm : PatternMatcher)
    (docId : String)
    (segments : List DocumentSegment)
    (schema : ExtractionSchema)
    (rules : List ValidationRule)
    (defaultConfMethod : String)
    (maxExcerptChars : Nat)
    (recordId : String)
    (rec : ProposedRecordInput) : ExtractedRecord :=
  let valInput := buildValidationInput schema rec
  let (recOutcome, fieldOutcomes, cfWarnings) :=
    validateRecordWithRules pm valInput schema rules recordId
  let fieldNames := orderedRecordFieldNames schema rec
  let assembledFields : List (String × ExtractedField) :=
    fieldNames.map fun fname =>
      (fname, assembleField docId segments schema defaultConfMethod maxExcerptChars
        recordId fieldOutcomes rec fname)
  let anyFieldErrors := assembledFields.any (fun kv => !kv.2.errors.isEmpty)
  let recStatus := assembleRecordStatus rec.failed rec.partialFlag (!rec.errors.isEmpty) anyFieldErrors
  {
    recordId := recordId,
    status := recStatus,
    fields := assembledFields,
    validation := recOutcome,
    errors := rec.errors,
    warnings := rec.warnings ++ cfWarnings
  }

/-- Build all proposed records sequentially assigning `record-001`, `record-002`, ... -/
def buildRecordsFrom
    (pm : PatternMatcher)
    (docId : String)
    (segments : List DocumentSegment)
    (schema : ExtractionSchema)
    (rules : List ValidationRule)
    (defaultConfMethod : String)
    (maxExcerptChars : Nat)
    (startIdx : Nat) : List ProposedRecordInput → List ExtractedRecord
  | [] => []
  | r :: rest =>
    buildRecord pm docId segments schema rules defaultConfMethod maxExcerptChars
      (formatRecordId startIdx) r ::
    buildRecordsFrom pm docId segments schema rules defaultConfMethod maxExcerptChars
      (startIdx + 1) rest

/-- Pure formalization of `ResultBuilder.build` (`result_builder.py:72-158`). -/
def buildResult
    (pm : PatternMatcher)
    (extractionId : String)
    (source : SourceMetadata)
    (segments : List DocumentSegment)
    (schema : ExtractionSchema)
    (rules : List ValidationRule)
    (documentWarnings : List DiagnosticIssue)
    (strategyOutput : StrategyOutputInput)
    (maxExcerptChars : Nat := 500) : ExtractionResult :=
  let resultErrors := strategyOutput.errors
  let baseWarnings := documentWarnings ++ strategyOutput.warnings
  let schemaMeta : SchemaMetadata := {
    schemaId := schema.schemaId,
    schemaVersion := schema.schemaVersion
  }
  if strategyOutput.failed && strategyOutput.records.isEmpty then
    let finalErrors :=
      if resultErrors.isEmpty then
        [{
          code := "extraction_failed",
          severity := .error,
          stage := .extraction,
          message := "Extraction strategy failed without returning any records."
        }]
      else resultErrors
    assembleBuildFailed extractionId schemaMeta (some source) finalErrors baseWarnings
  else
    let builtRecords :=
      buildRecordsFrom pm source.documentId segments schema rules
        strategyOutput.defaultConfidenceMethod maxExcerptChars 1 strategyOutput.records
    let anyRecNotCompleted := builtRecords.any (fun r => r.status != .completed)
    let propagatedWarns :=
      builtRecords.flatMap (fun r => r.warnings.filter (fun w => w.stage == .validation))
    let overallValidation :=
      validateResultCardinality (builtRecords.map ExtractedRecord.validation) schema
        (!builtRecords.isEmpty) strategyOutput.failed
    let overallStatus :=
      assembleOverallStatus strategyOutput.failed strategyOutput.partialFlag
        (!resultErrors.isEmpty) (!builtRecords.isEmpty) anyRecNotCompleted
    {
      contractVersion := contractVersionV1,
      extractionId := extractionId,
      status := overallStatus,
      source := some source,
      schemaMetadata := schemaMeta,
      records := builtRecords,
      validation := overallValidation,
      errors := resultErrors,
      warnings := baseWarnings ++ propagatedWarns
    }

-- =============================================================================
-- Soundness Helpers & Machine-Checked Theorems (13 required theorems)
-- =============================================================================

/-- `formatRecordId` is injective on all `Nat` and always produces a non-empty string. -/
theorem format_record_id_injective_and_nonempty :
    (∀ i j : Nat, formatRecordId i = formatRecordId j → i = j) ∧
    (∀ i : Nat, formatRecordId i ≠ "") := by
  constructor
  · intro i j h
    have hp := congrArg parseRecordIdNum h
    simpa [parseRecordIdNum_formatRecordId] using hp
  · intro i h
    have hl := congrArg (fun s => s.toList.length) h
    unfold formatRecordId at hl
    simp [recordPrefixChars] at hl

private theorem buildRecordsFrom_recordIds
    (pm : PatternMatcher) (docId : String) (segments : List DocumentSegment)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (defaultConfMethod : String) (maxExcerptChars : Nat)
    (startIdx : Nat) (recs : List ProposedRecordInput) :
    ∀ r ∈ buildRecordsFrom pm docId segments schema rules defaultConfMethod maxExcerptChars startIdx recs,
      ∃ k ≥ startIdx, r.recordId = formatRecordId k := by
  induction recs generalizing startIdx with
  | nil => intro r hr; simp [buildRecordsFrom] at hr
  | cons x xs ih =>
    intro r hr
    simp only [buildRecordsFrom, List.mem_cons] at hr
    cases hr with
    | inl heq =>
      subst heq
      exact ⟨startIdx, Nat.le_refl _, rfl⟩
    | inr hmem =>
      rcases ih (startIdx + 1) r hmem with ⟨k, hk, hid⟩
      exact ⟨k, by omega, hid⟩

private theorem buildRecordsFrom_pairwise_unique
    (pm : PatternMatcher) (docId : String) (segments : List DocumentSegment)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (defaultConfMethod : String) (maxExcerptChars : Nat)
    (startIdx : Nat) (recs : List ProposedRecordInput) :
    ((buildRecordsFrom pm docId segments schema rules defaultConfMethod maxExcerptChars startIdx recs).map
      ExtractedRecord.recordId).Pairwise (· ≠ ·) := by
  induction recs generalizing startIdx with
  | nil => simp [buildRecordsFrom]
  | cons x xs ih =>
    simp only [buildRecordsFrom, List.map_cons, List.pairwise_cons]
    refine ⟨?_, ih (startIdx + 1)⟩
    intro rid hrid
    rw [List.mem_map] at hrid
    rcases hrid with ⟨r, hr, rfl⟩
    rcases buildRecordsFrom_recordIds pm docId segments schema rules defaultConfMethod
      maxExcerptChars (startIdx + 1) xs r hr with ⟨k, hk, hid⟩
    intro heq
    rw [hid] at heq
    have hidx : startIdx = k := format_record_id_injective_and_nonempty.1 startIdx k heq
    omega

/-- All record identifiers generated by `buildResult` are pairwise unique. -/
theorem build_result_record_ids_pairwise_unique
    (pm : PatternMatcher) (extractionId : String) (source : SourceMetadata)
    (segments : List DocumentSegment) (schema : ExtractionSchema) (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (out : StrategyOutputInput) (maxChars : Nat) :
    ((buildResult pm extractionId source segments schema rules docWarns out maxChars).records.map
      ExtractedRecord.recordId).Pairwise (· ≠ ·) := by
  unfold buildResult
  by_cases hfail : (out.failed && out.records.isEmpty) = true
  · simp [hfail, assembleBuildFailed]
  · simp only [hfail, Bool.false_eq_true, ↓reduceIte]
    exact buildRecordsFrom_pairwise_unique pm source.documentId segments schema rules
      out.defaultConfidenceMethod maxChars 1 out.records

/-- Strategy-to-Contract Firewall sanitization: any proposed field with `FieldPresence.missing`
    is unconditionally assembled with `value = FieldValue.nullValue`, regardless of what raw
    `RuntimeValue` the untrusted strategy proposed. -/
theorem build_record_sanitizes_missing_field_value
    (docId : String) (segments : List DocumentSegment) (schema : ExtractionSchema)
    (defaultConfMethod : String) (maxChars : Nat) (recordId : String)
    (fieldOutcomes : List (String × ValidationOutcome))
    (rec : ProposedRecordInput) (fname : String) (pf : ProposedFieldInput)
    (hlookup : lookupProposedField rec.fields fname = some pf)
    (hmiss : pf.presence = .missing) :
    let f := assembleField docId segments schema defaultConfMethod maxChars recordId fieldOutcomes rec fname
    f.presence = .missing ∧ f.value = .nullValue := by
  unfold assembleField
  simp [hlookup, hmiss]

/-- When `pf.ambiguous = true`, `assembleField` emits an `ambiguous_source_value` warning
    (`WARNING`, `EXTRACTION`). -/
theorem build_record_ambiguous_emits_extraction_warning
    (docId : String) (segments : List DocumentSegment) (schema : ExtractionSchema)
    (defaultConfMethod : String) (maxChars : Nat) (recordId : String)
    (fieldOutcomes : List (String × ValidationOutcome))
    (rec : ProposedRecordInput) (fname : String) (pf : ProposedFieldInput)
    (hlookup : lookupProposedField rec.fields fname = some pf)
    (hambig : pf.ambiguous = true) :
    ∃ w ∈ (assembleField docId segments schema defaultConfMethod maxChars recordId fieldOutcomes rec fname).warnings,
      w.code = "ambiguous_source_value" ∧ w.severity = .warning ∧ w.stage = .extraction := by
  unfold assembleField
  simp only [hlookup, hambig, ↓reduceIte]
  refine ⟨mkSafeDiag "ambiguous_source_value" .warning .extraction
      s!"Source document contains ambiguous candidates for '{fname}'." (some recordId) (some fname),
    by simp, rfl, rfl, rfl⟩

/-- Provenance fault isolation: when `pf.provenanceFailed = true`, the field receives empty
    provenance and a `provenance_association_failed` (`ERROR`, `PROVENANCE`) diagnostic,
    and if the record did not fail completely, its status becomes `PARTIAL` (never `COMPLETED`). -/
theorem build_record_isolates_provenance_failure
    (docId : String) (segments : List DocumentSegment) (schema : ExtractionSchema)
    (defaultConfMethod : String) (maxChars : Nat) (recordId : String)
    (fieldOutcomes : List (String × ValidationOutcome))
    (rec : ProposedRecordInput) (fname : String) (pf : ProposedFieldInput)
    (hlookup : lookupProposedField rec.fields fname = some pf)
    (hfail : pf.provenanceFailed = true) :
    let f := assembleField docId segments schema defaultConfMethod maxChars recordId fieldOutcomes rec fname
    f.provenance = [] ∧
    (∃ e ∈ f.errors, e.code = "provenance_association_failed" ∧ e.severity = .error ∧ e.stage = .provenance) ∧
    (rec.failed = false → assembleRecordStatus rec.failed rec.partialFlag (!rec.errors.isEmpty) true = .partialStatus) := by
  refine ⟨?_, ?_, ?_⟩
  · unfold assembleField; simp [hlookup, hfail]
  · unfold assembleField
    simp only [hlookup, hfail, ↓reduceIte]
    refine ⟨mkSafeDiag "provenance_association_failed" .error .provenance
        s!"Provenance association failed for field '{fname}'." (some recordId) (some fname),
      by simp, rfl, rfl, rfl⟩
  · intro hnotfail
    simp [assembleRecordStatus, hnotfail]

private theorem lookupFieldOutcome_valid
    (outcomes : List (String × ValidationOutcome))
    (hvalid : ∀ kv ∈ outcomes, ValidValidationOutcome kv.2) (name : String) :
    ValidValidationOutcome (lookupFieldOutcome outcomes name) := by
  unfold lookupFieldOutcome
  cases hf : outcomes.find? (fun kv => kv.1 == name) with
  | none => simp [ValidValidationOutcome]
  | some kv =>
    have hmem : kv ∈ outcomes := List.mem_of_find?_eq_some hf
    exact hvalid kv hmem

private theorem assembleField_valid_and_bound
    (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (defaultConfMethod : String) (maxChars : Nat) (recordId : String)
    (fieldOutcomes : List (String × ValidationOutcome))
    (hfo : ∀ kv ∈ fieldOutcomes, ValidValidationOutcome kv.2)
    (rec : ProposedRecordInput) (fname : String)
    (hpfValid : ∀ kv ∈ rec.fields,
      (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
      (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce)) :
    let f := assembleField docId segments schema defaultConfMethod maxChars recordId fieldOutcomes rec fname
    ValidExtractedField f ∧ (∀ ev ∈ f.provenance, ev.sourceDocumentId = docId) := by
  unfold assembleField
  cases hfind : rec.fields.find? (fun kv => kv.1 == fname) with
  | none =>
    simp only [lookupProposedField, hfind, Option.map_none]
    refine ⟨⟨(by intro _; rfl), (by intro _ h; cases h), (by intro _ h; cases h),
      lookupFieldOutcome_valid fieldOutcomes hfo fname, (by simp), (by simp)⟩, (by simp)⟩
  | some kv =>
    have hkvMem : kv ∈ rec.fields := List.mem_of_find?_eq_some hfind
    have ⟨herrsV, hwarnsV, hspansV⟩ := hpfValid kv hkvMem
    simp only [lookupProposedField, hfind, Option.map_some]
    constructor
    · refine ⟨?_, ?_, ?_, lookupFieldOutcome_valid fieldOutcomes hfo fname, ?_, ?_⟩
      · intro hmiss; dsimp only at hmiss ⊢; rw [hmiss]
      · intro c hc
        exact normalized_confidence_valid_contract kv.2.confidence kv.2.confidenceScale
          kv.2.confidenceMethod defaultConfMethod c hc
      · intro ev hev
        by_cases hpfail : kv.2.provenanceFailed = true
        · simp [hpfail] at hev
        · simp only [hpfail, Bool.false_eq_true, ↓reduceIte] at hev
          exact (associate_evidence_all_entries_valid_and_bound docId hdoc segments hsegs fname
            kv.2.presence recordId maxChars kv.2.evidence hspansV ev hev).1
      · intro e he
        by_cases hpfail : kv.2.provenanceFailed = true
        · simp only [hpfail, ↓reduceIte, List.mem_append, List.mem_singleton] at he
          cases he with
          | inl h1 => exact herrsV e h1
          | inr h2 => subst h2; exact mkSafeDiag_valid _ _ _ _ _ _
        · simp only [hpfail, Bool.false_eq_true, ↓reduceIte, List.append_nil] at he
          exact herrsV e he
      · intro w hw
        simp only [List.mem_append] at hw
        rcases hw with ((h1 | h2) | h3) | h4
        · exact hwarnsV w h1
        · by_cases hamb : kv.2.ambiguous = true
          · simp only [hamb, ↓reduceIte, List.mem_singleton] at h2
            subst h2; exact mkSafeDiag_valid _ _ _ _ _ _
          · simp [hamb] at h2
        · exact normalizeConfidenceWarnings_valid kv.2.confidence kv.2.confidenceScale recordId fname w h3
        · by_cases hpfail : kv.2.provenanceFailed = true
          · simp [hpfail] at h4
          · simp only [hpfail, Bool.false_eq_true, ↓reduceIte] at h4
            exact (associate_evidence_all_warnings_are_provenance_warnings docId segments fname
              kv.2.presence recordId maxChars kv.2.evidence w h4).1
    · intro ev hev
      by_cases hpfail : kv.2.provenanceFailed = true
      · simp [hpfail] at hev
      · simp only [hpfail, Bool.false_eq_true, ↓reduceIte] at hev
        exact (associate_evidence_all_entries_valid_and_bound docId hdoc segments hsegs fname
          kv.2.presence recordId maxChars kv.2.evidence hspansV ev hev).2

/-- Every field assembled by `buildRecord` satisfies `ValidExtractedField` and binds all
    provenance entries to `docId`. -/
theorem build_record_fields_all_valid_contract
    (pm : PatternMatcher) (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (defaultConfMethod : String) (maxChars : Nat) (recordId : String)
    (rec : ProposedRecordInput)
    (hpfValid : ∀ kv ∈ rec.fields,
      (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
      (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce)) :
    ∀ kv ∈ (buildRecord pm docId segments schema rules defaultConfMethod maxChars recordId rec).fields,
      ValidExtractedField kv.2 ∧ (∀ ev ∈ kv.2.provenance, ev.sourceDocumentId = docId) := by
  intro kv hkv
  unfold buildRecord at hkv
  dsimp only at hkv
  rw [List.mem_map] at hkv
  rcases hkv with ⟨fname, _, rfl⟩
  have hvalContract := validate_record_with_rules_outcomes_satisfy_contract pm
    (buildValidationInput schema rec) schema rules recordId
  exact assembleField_valid_and_bound docId hdoc segments hsegs schema defaultConfMethod maxChars
    recordId (validateRecordWithRules pm (buildValidationInput schema rec) schema rules recordId).2.1
    hvalContract.2.1 rec fname hpfValid

/-- Every record assembled by `buildRecord` satisfies Contract v1.0's `ValidExtractedRecord`. -/
theorem build_record_is_valid_extracted_record
    (pm : PatternMatcher) (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (defaultConfMethod : String) (maxChars : Nat) (recordId : String) (hrid : recordId ≠ "")
    (rec : ProposedRecordInput)
    (hrecErrs : ∀ e ∈ rec.errors, ValidDiagnosticIssue e)
    (hrecWarns : ∀ w ∈ rec.warnings, ValidDiagnosticIssue w)
    (hpfValid : ∀ kv ∈ rec.fields,
      (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
      (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce)) :
    ValidExtractedRecord
      (buildRecord pm docId segments schema rules defaultConfMethod maxChars recordId rec) := by
  have hvalContract := validate_record_with_rules_outcomes_satisfy_contract pm
    (buildValidationInput schema rec) schema rules recordId
  have hfields := build_record_fields_all_valid_contract pm docId hdoc segments hsegs schema rules
    defaultConfMethod maxChars recordId rec hpfValid
  refine ⟨hrid, ?_, hvalContract.1, hrecErrs, ?_⟩
  · intro kv hkv
    exact (hfields kv hkv).1
  · intro w hw
    unfold buildRecord at hw
    dsimp only at hw
    rw [List.mem_append] at hw
    cases hw with
    | inl h1 => exact hrecWarns w h1
    | inr h2 => exact (hvalContract.2.2 w h2).1

/-- When `strategyOutput.failed = true` and `records = []` with no explicit errors, `buildResult`
    synthesizes `extraction_failed` (`ERROR`, `EXTRACTION`) and returns `FAILED` + `NOT_VALIDATED`. -/
theorem build_result_empty_failed_synthesizes_extraction_failed
    (pm : PatternMatcher) (extractionId : String) (source : SourceMetadata)
    (segments : List DocumentSegment) (schema : ExtractionSchema) (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (out : StrategyOutputInput) (maxChars : Nat)
    (hfail : out.failed = true) (hempty : out.records = []) (hnoErr : out.errors = []) :
    let res := buildResult pm extractionId source segments schema rules docWarns out maxChars
    res.status = .failed ∧
    res.records = [] ∧
    res.validation.status = .notValidated ∧
    (∃ e, res.errors = [e] ∧ e.code = "extraction_failed" ∧ e.severity = .error ∧ e.stage = .extraction) := by
  unfold buildResult
  simp [hfail, hempty, hnoErr, assembleBuildFailed]

/-- When `strategyOutput.failed = true` while preserving extracted records (`records ≠ []`),
    `buildResult` sets overall `status = PARTIAL` and runs validation (`status ≠ NOT_VALIDATED`). -/
theorem build_result_failed_with_records_is_partial_and_validated
    (pm : PatternMatcher) (extractionId : String) (source : SourceMetadata)
    (segments : List DocumentSegment) (schema : ExtractionSchema) (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (out : StrategyOutputInput) (maxChars : Nat)
    (hfail : out.failed = true) (hnonempty : out.records ≠ []) :
    let res := buildResult pm extractionId source segments schema rules docWarns out maxChars
    res.status = .partialStatus ∧ res.validation.status ≠ .notValidated := by
  cases hrecs : out.records with
  | nil => exact absurd hrecs hnonempty
  | cons r rs =>
    unfold buildResult
    simp only [hfail, hrecs, List.isEmpty_cons, Bool.and_false, Bool.false_eq_true, ↓reduceIte,
      buildRecordsFrom, Bool.not_false]
    constructor
    · simp [assembleOverallStatus]
    · unfold validateResultCardinality
      simp only [Bool.not_true, Bool.and_false, Bool.false_eq_true, ↓reduceIte]
      have hne : ∀ xs : List DiagnosticIssue, (makeValidationOutcome xs).status ≠ .notValidated := by
        intro xs; cases xs <;> simp [makeValidationOutcome]
      exact hne _

/-- Warning propagation discipline: any warning appended to `result.warnings` beyond
    `documentWarnings ++ strategyOutput.warnings` originates from a record with
    `stage = PipelineStage.validation` (non-validation record warnings stay record-scoped). -/
theorem build_result_propagates_only_validation_record_warnings
    (pm : PatternMatcher) (extractionId : String) (source : SourceMetadata)
    (segments : List DocumentSegment) (schema : ExtractionSchema) (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (out : StrategyOutputInput) (maxChars : Nat) :
    ∀ w ∈ (buildResult pm extractionId source segments schema rules docWarns out maxChars).warnings,
      w ∈ docWarns ∨ w ∈ out.warnings ∨ w.stage = .validation := by
  intro w hw
  unfold buildResult at hw
  by_cases hfail : (out.failed && out.records.isEmpty) = true
  · simp only [hfail, ↓reduceIte, assembleBuildFailed, List.mem_append] at hw
    cases hw with
    | inl h1 => exact Or.inl h1
    | inr h2 => exact Or.inr (Or.inl h2)
  · simp only [hfail, Bool.false_eq_true, ↓reduceIte, List.mem_append] at hw
    rcases hw with (h1 | h2) | h3
    · exact Or.inl h1
    · exact Or.inr (Or.inl h2)
    · rw [List.mem_flatMap] at h3
      rcases h3 with ⟨r, _, hwf⟩
      rw [List.mem_filter] at hwf
      exact Or.inr (Or.inr (beq_iff_eq.mp hwf.2))

private theorem buildRecordsFrom_all_valid_and_bound
    (pm : PatternMatcher) (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (defaultConfMethod : String) (maxChars : Nat)
    (startIdx : Nat) (recs : List ProposedRecordInput)
    (hrecs : ∀ r ∈ recs,
      (∀ e ∈ r.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ r.warnings, ValidDiagnosticIssue w) ∧
      (∀ kv ∈ r.fields,
        (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
        (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
        (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce))) :
    ∀ r ∈ buildRecordsFrom pm docId segments schema rules defaultConfMethod maxChars startIdx recs,
      ValidExtractedRecord r ∧
      (∀ kv ∈ r.fields, ∀ ev ∈ kv.2.provenance, ev.sourceDocumentId = docId) := by
  induction recs generalizing startIdx with
  | nil => intro r hr; simp [buildRecordsFrom] at hr
  | cons x xs ih =>
    intro r hr
    simp only [buildRecordsFrom, List.mem_cons] at hr
    have ⟨herrs, hwarns, hpfs⟩ := hrecs x List.mem_cons_self
    cases hr with
    | inl heq =>
      subst heq
      refine ⟨build_record_is_valid_extracted_record pm docId hdoc segments hsegs schema rules
        defaultConfMethod maxChars (formatRecordId startIdx)
        (format_record_id_injective_and_nonempty.2 startIdx) x herrs hwarns hpfs, ?_⟩
      intro kv hkv
      exact (build_record_fields_all_valid_contract pm docId hdoc segments hsegs schema rules
        defaultConfMethod maxChars (formatRecordId startIdx) x hpfs kv hkv).2
    | inr hmem =>
      exact ih (startIdx + 1) (fun y hy => hrecs y (List.mem_cons_of_mem _ hy)) r hmem

/-- Every provenance entry across all records and fields assembled by `buildResult` is bound to
    `source.documentId`. -/
theorem build_result_preserves_provenance_document_binding
    (pm : PatternMatcher) (extractionId : String) (source : SourceMetadata) (hdoc : source.documentId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (out : StrategyOutputInput) (maxChars : Nat)
    (hrecs : ∀ r ∈ out.records,
      (∀ e ∈ r.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ r.warnings, ValidDiagnosticIssue w) ∧
      (∀ kv ∈ r.fields,
        (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
        (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
        (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce))) :
    let res := buildResult pm extractionId source segments schema rules docWarns out maxChars
    ProvenanceBoundToSource res.source res.records := by
  unfold buildResult
  by_cases hfail : (out.failed && out.records.isEmpty) = true
  · simp [hfail, assembleBuildFailed, ProvenanceBoundToSource]
  · simp only [hfail, Bool.false_eq_true, ↓reduceIte, ProvenanceBoundToSource]
    intro s hs r hr kv hkv ev hev
    cases hs
    exact (buildRecordsFrom_all_valid_and_bound pm source.documentId hdoc segments hsegs schema rules
      out.defaultConfidenceMethod maxChars 1 out.records hrecs r hr).2 kv hkv ev hev

/-- Full `ResultBuilder.build` assembly is strictly deterministic. -/
theorem build_result_is_deterministic
    (pm1 pm2 : PatternMatcher) (e1 e2 : String) (s1 s2 : SourceMetadata)
    (seg1 seg2 : List DocumentSegment) (sc1 sc2 : ExtractionSchema)
    (ru1 ru2 : List ValidationRule) (dw1 dw2 : List DiagnosticIssue)
    (o1 o2 : StrategyOutputInput) (m1 m2 : Nat)
    (hpm : pm1 = pm2) (he : e1 = e2) (hs : s1 = s2) (hseg : seg1 = seg2)
    (hsc : sc1 = sc2) (hru : ru1 = ru2) (hdw : dw1 = dw2) (ho : o1 = o2) (hm : m1 = m2) :
    buildResult pm1 e1 s1 seg1 sc1 ru1 dw1 o1 m1 =
    buildResult pm2 e2 s2 seg2 sc2 ru2 dw2 o2 m2 := by
  subst hpm; subst he; subst hs; subst hseg; subst hsc; subst hru; subst hdw; subst ho; subst hm
  rfl

private theorem isNonBlank_ne_empty (s : String) (h : isNonBlank s = true) : s ≠ "" := by
  intro hemp
  subst hemp
  simp [isNonBlank] at h

private theorem makeValidationOutcome_valid_ra (errs : List DiagnosticIssue)
    (herrs : ∀ e ∈ errs, ValidDiagnosticIssue e ∧ e.severity = .error ∧ e.stage = .validation) :
    ValidValidationOutcome (makeValidationOutcome errs) := by
  cases errs with
  | nil => simp [makeValidationOutcome, ValidValidationOutcome]
  | cons e es =>
    simp only [makeValidationOutcome, List.isEmpty_cons, Bool.false_eq_true, ↓reduceIte,
      ValidValidationOutcome]
    exact ⟨herrs, by intro h; exact absurd rfl h⟩

private theorem validateResultCardinality_valid
    (recs : List ValidationOutcome)
    (hrecs : ∀ o ∈ recs, ValidValidationOutcome o)
    (schema : ExtractionSchema) (hasRecs failedBefore : Bool) :
    ValidValidationOutcome (validateResultCardinality recs schema hasRecs failedBefore) := by
  unfold validateResultCardinality
  by_cases hpre : (failedBefore && !hasRecs) = true
  · simp [hpre, ValidValidationOutcome]
  · simp only [hpre, Bool.false_eq_true, ↓reduceIte]
    apply makeValidationOutcome_valid_ra
    intro e he
    rw [List.mem_append] at he
    cases he with
    | inl hcard =>
      cases hsc : schema.cardinality with
      | multiple => rw [hsc] at hcard; simp at hcard
      | single =>
        rw [hsc] at hcard
        cases hr : recs with
        | nil =>
          rw [hr] at hcard; simp only [List.mem_singleton] at hcard; subst hcard
          exact mkValidationError_is_valid_validation_error _ _ _ _
        | cons r1 rest =>
          rw [hr] at hcard
          cases rest with
          | nil => simp at hcard
          | cons r2 tail =>
            simp only [List.mem_singleton] at hcard; subst hcard
            exact mkValidationError_is_valid_validation_error _ _ _ _
    | inr hflat =>
      rw [List.mem_flatMap] at hflat
      rcases hflat with ⟨o, ho, heo⟩
      exact (hrecs o ho).1 e heo

/-- End-to-end Strategy-to-Contract Firewall Soundness Theorem:
    Assembling any untrusted `StrategyOutputInput` through `buildResult` ALWAYS yields a
    Contract v1.0-valid `ExtractionResult` (`ValidExtractionResult`) whenever `extractionId`,
    `source.documentId`, `schema`, `segments`, and input diagnostics/spans are valid. -/
theorem build_result_satisfies_valid_extraction_result
    (pm : PatternMatcher) (extractionId : String) (hext : extractionId ≠ "")
    (source : SourceMetadata) (hdoc : source.documentId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (schema : ExtractionSchema) (hschema : ValidExtractionSchema schema)
    (rules : List ValidationRule)
    (docWarns : List DiagnosticIssue) (hdw : ∀ w ∈ docWarns, ValidDiagnosticIssue w)
    (out : StrategyOutputInput)
    (houtErrs : ∀ e ∈ out.errors, ValidDiagnosticIssue e)
    (houtWarns : ∀ w ∈ out.warnings, ValidDiagnosticIssue w)
    (hrecs : ∀ r ∈ out.records,
      (∀ e ∈ r.errors, ValidDiagnosticIssue e) ∧
      (∀ w ∈ r.warnings, ValidDiagnosticIssue w) ∧
      (∀ kv ∈ r.fields,
        (∀ e ∈ kv.2.errors, ValidDiagnosticIssue e) ∧
        (∀ w ∈ kv.2.warnings, ValidDiagnosticIssue w) ∧
        (∀ it ∈ kv.2.evidence, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce)))
    (maxChars : Nat) :
    ValidExtractionResult
      (buildResult pm extractionId source segments schema rules docWarns out maxChars) := by
  have hsid : schema.schemaId ≠ "" := isNonBlank_ne_empty schema.schemaId hschema.1
  have hsver : schema.schemaVersion ≠ "" := isNonBlank_ne_empty schema.schemaVersion hschema.2.1
  have huniq := build_result_record_ids_pairwise_unique pm extractionId source segments schema rules
    docWarns out maxChars
  have hbound := build_result_preserves_provenance_document_binding pm extractionId source hdoc
    segments hsegs schema rules docWarns out maxChars hrecs
  unfold buildResult at huniq hbound ⊢
  by_cases hfail : (out.failed && out.records.isEmpty) = true
  · simp only [hfail, ↓reduceIte, assembleBuildFailed, ValidExtractionResult, ValidSchemaMetadata]
    refine ⟨trivial, hext, ?_, ⟨hsid, hsver⟩, (by simp), (by simp), ?_, (by simp [ValidValidationOutcome]), ?_, ?_⟩
    · intro s hs; cases hs; exact hdoc
    · simp [ProvenanceBoundToSource]
    · intro e he
      by_cases herrEmpty : out.errors.isEmpty = true
      · simp only [herrEmpty, ↓reduceIte, List.mem_singleton] at he
        subst he; exact ⟨by decide, by decide⟩
      · simp only [herrEmpty, Bool.false_eq_true, ↓reduceIte] at he
        exact houtErrs e he
    · intro w hw
      rw [List.mem_append] at hw
      cases hw with
      | inl h1 => exact hdw w h1
      | inr h2 => exact houtWarns w h2
  · simp only [hfail, Bool.false_eq_true, ↓reduceIte] at huniq hbound ⊢
    have hbuilt := buildRecordsFrom_all_valid_and_bound pm source.documentId hdoc segments hsegs
      schema rules out.defaultConfidenceMethod maxChars 1 out.records hrecs
    refine ⟨rfl, hext, ?_, ⟨hsid, hsver⟩, huniq, ?_, hbound, ?_, houtErrs, ?_⟩
    · intro s hs; cases hs; exact hdoc
    · intro r hr; exact (hbuilt r hr).1
    · apply validateResultCardinality_valid
      intro o ho
      rw [List.mem_map] at ho
      rcases ho with ⟨r, hr, rfl⟩
      exact (hbuilt r hr).1.2.2.1
    · intro w hw
      simp only [List.mem_append] at hw
      rcases hw with (h1 | h2) | h3
      · exact hdw w h1
      · exact houtWarns w h2
      · rw [List.mem_flatMap] at h3
        rcases h3 with ⟨r, hr, hwf⟩
        rw [List.mem_filter] at hwf
        exact (hbuilt r hr).1.2.2.2.2 w hwf.1

end StructaFormal
