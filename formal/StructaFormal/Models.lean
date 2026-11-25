/-
  Structa Formal Assurance Layer — Canonical Output Record Contract v1.0 Domain Models.
  Authoritative Python source: `src/structa_maestro/contracts/models.py`.

  Every model defines:
  - A propositional invariant `Valid* : T → Prop` used in machine-checked theorems.
  - A decidable boolean predicate `isValid* : T → Bool` used by the conformance oracle.
  - A soundness/completeness bridge `isValid*_iff : isValid* x = true ↔ Valid* x`.
-/

import StructaFormal.Enums

namespace StructaFormal

/-- Fixed canonical contract version (`CONTRACT_VERSION = "1.0"`). -/
def contractVersionV1 : String := "1.0"

/-- Public source document identity (`SourceMetadata`). -/
structure SourceMetadata where
  documentId : String
  filename : Option String := none
  mediaType : Option String := none
  deriving DecidableEq, Repr

def ValidSourceMetadata (s : SourceMetadata) : Prop :=
  s.documentId ≠ ""

def isValidSourceMetadata (s : SourceMetadata) : Bool :=
  s.documentId != ""

theorem isValidSourceMetadata_iff (s : SourceMetadata) :
    isValidSourceMetadata s = true ↔ ValidSourceMetadata s := by
  simp [isValidSourceMetadata, ValidSourceMetadata]

/-- Public schema identity (`SchemaMetadata`). -/
structure SchemaMetadata where
  schemaId : String
  schemaVersion : String
  deriving DecidableEq, Repr

def ValidSchemaMetadata (s : SchemaMetadata) : Prop :=
  s.schemaId ≠ "" ∧ s.schemaVersion ≠ ""

def isValidSchemaMetadata (s : SchemaMetadata) : Bool :=
  (s.schemaId != "") && (s.schemaVersion != "")

theorem isValidSchemaMetadata_iff (s : SchemaMetadata) :
    isValidSchemaMetadata s = true ↔ ValidSchemaMetadata s := by
  simp [isValidSchemaMetadata, ValidSchemaMetadata]

/-- Optional granular location within the originating document (`SourceLocation`).
    `sectionName` corresponds to the JSON/Python field `section` (`section` is a Lean keyword). -/
structure SourceLocation where
  page : Option Int := none
  paragraph : Option Int := none
  lineStart : Option Int := none
  lineEnd : Option Int := none
  charStart : Option Int := none
  charEnd : Option Int := none
  sectionName : Option String := none
  deriving DecidableEq, Repr

/-- Structural validity invariant for `SourceLocation` matching `contracts/models.py`. -/
def ValidSourceLocation (loc : SourceLocation) : Prop :=
  (∀ p, loc.page = some p → p ≥ 1) ∧
  (∀ p, loc.paragraph = some p → p ≥ 1) ∧
  (∀ s, loc.lineStart = some s → s ≥ 1) ∧
  (∀ e, loc.lineEnd = some e → e ≥ 1) ∧
  (∀ s, loc.charStart = some s → s ≥ 0) ∧
  (∀ e, loc.charEnd = some e → e ≥ 0) ∧
  (∀ s e, loc.lineStart = some s → loc.lineEnd = some e → s ≤ e) ∧
  (∀ s e, loc.charStart = some s → loc.charEnd = some e → s ≤ e)

private def optGe (x : Option Int) (bound : Int) : Bool :=
  match x with
  | none => true
  | some v => decide (v ≥ bound)

private def optSpanLe (lo hi : Option Int) : Bool :=
  match lo, hi with
  | some s, some e => decide (s ≤ e)
  | _, _ => true

private theorem optGe_iff (x : Option Int) (bound : Int) :
    optGe x bound = true ↔ (∀ v, x = some v → v ≥ bound) := by
  cases x <;> simp [optGe]

private theorem optSpanLe_iff (lo hi : Option Int) :
    optSpanLe lo hi = true ↔ (∀ s e, lo = some s → hi = some e → s ≤ e) := by
  cases lo <;> cases hi <;> simp [optSpanLe]

def isValidSourceLocation (loc : SourceLocation) : Bool :=
  optGe loc.page 1 &&
  optGe loc.paragraph 1 &&
  optGe loc.lineStart 1 &&
  optGe loc.lineEnd 1 &&
  optGe loc.charStart 0 &&
  optGe loc.charEnd 0 &&
  optSpanLe loc.lineStart loc.lineEnd &&
  optSpanLe loc.charStart loc.charEnd

theorem isValidSourceLocation_iff (loc : SourceLocation) :
    isValidSourceLocation loc = true ↔ ValidSourceLocation loc := by
  simp [isValidSourceLocation, ValidSourceLocation, optGe_iff, optSpanLe_iff, and_assoc]

/-- Supporting source evidence linked to an extracted field (`ProvenanceEvidence`). -/
structure ProvenanceEvidence where
  sourceDocumentId : String
  location : Option SourceLocation := none
  evidence : String := ""
  relationship : EvidenceRelationship
  truncated : Bool := false
  deriving DecidableEq, Repr

def ValidProvenanceEvidence (ev : ProvenanceEvidence) : Prop :=
  ev.sourceDocumentId ≠ "" ∧
  (∀ loc, ev.location = some loc → ValidSourceLocation loc)

def isValidProvenanceEvidence (ev : ProvenanceEvidence) : Bool :=
  (ev.sourceDocumentId != "") &&
  match ev.location with
  | none => true
  | some loc => isValidSourceLocation loc

theorem isValidProvenanceEvidence_iff (ev : ProvenanceEvidence) :
    isValidProvenanceEvidence ev = true ↔ ValidProvenanceEvidence ev := by
  cases hloc : ev.location with
  | none =>
    simp [isValidProvenanceEvidence, ValidProvenanceEvidence, hloc]
  | some loc =>
    simp [isValidProvenanceEvidence, ValidProvenanceEvidence, hloc, isValidSourceLocation_iff]

/-- Explicit reliability assessment independent of validation (`ConfidenceAssessment`). -/
structure ConfidenceAssessment where
  value : Option Rat := none
  scale : String := "0-1"
  method : String := "extraction-assessment"
  calibrated : Bool := false
  deriving DecidableEq, Repr

def ValidConfidenceAssessment (c : ConfidenceAssessment) : Prop :=
  c.scale ≠ "" ∧
  c.method ≠ "" ∧
  (c.scale = "0-1" → ∀ v, c.value = some v → (0 : Rat) ≤ v ∧ v ≤ (1 : Rat))

def isValidConfidenceAssessment (c : ConfidenceAssessment) : Bool :=
  (c.scale != "") &&
  (c.method != "") &&
  if c.scale == "0-1" then
    match c.value with
    | none => true
    | some v => decide ((0 : Rat) ≤ v ∧ v ≤ (1 : Rat))
  else
    true

theorem isValidConfidenceAssessment_iff (c : ConfidenceAssessment) :
    isValidConfidenceAssessment c = true ↔ ValidConfidenceAssessment c := by
  unfold isValidConfidenceAssessment ValidConfidenceAssessment
  by_cases hs : c.scale = "0-1"
  · cases c.value with
    | none => simp [hs]
    | some v => simp [hs]
  · simp [hs]

/-- Structured, privacy-safe error or warning (`DiagnosticIssue`). -/
structure DiagnosticIssue where
  code : String
  severity : IssueSeverity
  stage : PipelineStage
  message : String
  recordId : Option String := none
  fieldPath : Option String := none
  expected : Option String := none
  actual : Option String := none
  deriving DecidableEq, Repr

def ValidDiagnosticIssue (d : DiagnosticIssue) : Prop :=
  d.code ≠ "" ∧ d.message ≠ ""

def isValidDiagnosticIssue (d : DiagnosticIssue) : Bool :=
  (d.code != "") && (d.message != "")

theorem isValidDiagnosticIssue_iff (d : DiagnosticIssue) :
    isValidDiagnosticIssue d = true ↔ ValidDiagnosticIssue d := by
  simp [isValidDiagnosticIssue, ValidDiagnosticIssue]

/-- Independent structural and constraint validation result (`ValidationOutcome`). -/
structure ValidationOutcome where
  status : ValidationStatus
  errors : List DiagnosticIssue := []
  deriving DecidableEq, Repr

/--
  Structural invariant matching `ValidationOutcome._check_validation_errors`:
  - Every issue in `errors` is a valid diagnostic with `severity = .error` and `stage = .validation`.
  - If `status ≠ .invalid`, then `errors = []`.
  - Structurally permits `ValidationOutcome.mk .invalid []` (per `contracts/models.py`).
-/
def ValidValidationOutcome (o : ValidationOutcome) : Prop :=
  (∀ err ∈ o.errors,
    ValidDiagnosticIssue err ∧ err.severity = .error ∧ err.stage = .validation) ∧
  (o.status ≠ .invalid → o.errors = [])

def isValidValidationError (err : DiagnosticIssue) : Bool :=
  isValidDiagnosticIssue err &&
  (err.severity == .error) &&
  (err.stage == .validation)

private theorem isValidValidationError_iff (err : DiagnosticIssue) :
    isValidValidationError err = true ↔
      (ValidDiagnosticIssue err ∧ err.severity = .error ∧ err.stage = .validation) := by
  simp [isValidValidationError, isValidDiagnosticIssue_iff, and_assoc]

def isValidValidationOutcome (o : ValidationOutcome) : Bool :=
  o.errors.all isValidValidationError &&
  ((o.status == .invalid) || o.errors.isEmpty)

theorem isValidValidationOutcome_iff (o : ValidationOutcome) :
    isValidValidationOutcome o = true ↔ ValidValidationOutcome o := by
  unfold isValidValidationOutcome ValidValidationOutcome
  simp only [Bool.and_eq_true, Bool.or_eq_true, List.all_eq_true, isValidValidationError_iff,
    beq_iff_eq, List.isEmpty_iff]
  constructor
  · intro ⟨herrs, hstatus⟩
    refine ⟨herrs, ?_⟩
    intro hne
    cases hstatus with
    | inl heq => exact absurd heq hne
    | inr hempty => exact hempty
  · intro ⟨herrs, hstatus⟩
    refine ⟨herrs, ?_⟩
    by_cases hi : o.status = .invalid
    · exact Or.inl hi
    · exact Or.inr (hstatus hi)

/-- Abstract representation of an extracted field value distinguishing null from non-null. -/
inductive FieldValue where
  | nullValue
  | nonNull (reprTag : String)
  deriving DecidableEq, Repr

/-- Single extracted field with independent quality dimensions (`ExtractedField`). -/
structure ExtractedField where
  presence : FieldPresence
  value : FieldValue := .nullValue
  valueType : Option FieldType := none
  confidence : Option ConfidenceAssessment := none
  provenance : List ProvenanceEvidence := []
  validation : ValidationOutcome
  errors : List DiagnosticIssue := []
  warnings : List DiagnosticIssue := []
  deriving DecidableEq, Repr

def ValidExtractedField (f : ExtractedField) : Prop :=
  (f.presence = .missing → f.value = .nullValue) ∧
  (∀ c, f.confidence = some c → ValidConfidenceAssessment c) ∧
  (∀ ev ∈ f.provenance, ValidProvenanceEvidence ev) ∧
  ValidValidationOutcome f.validation ∧
  (∀ e ∈ f.errors, ValidDiagnosticIssue e) ∧
  (∀ w ∈ f.warnings, ValidDiagnosticIssue w)

private def optConfidenceValid (oc : Option ConfidenceAssessment) : Bool :=
  match oc with
  | none => true
  | some c => isValidConfidenceAssessment c

private theorem optConfidenceValid_iff (oc : Option ConfidenceAssessment) :
    optConfidenceValid oc = true ↔ (∀ c, oc = some c → ValidConfidenceAssessment c) := by
  cases oc with
  | none => simp [optConfidenceValid]
  | some c => simp [optConfidenceValid, isValidConfidenceAssessment_iff]

private def presenceValueValid (p : FieldPresence) (v : FieldValue) : Bool :=
  (p != .missing) || (v == .nullValue)

private theorem presenceValueValid_iff (p : FieldPresence) (v : FieldValue) :
    presenceValueValid p v = true ↔ (p = .missing → v = .nullValue) := by
  cases p <;> cases v <;> simp [presenceValueValid]

def isValidExtractedField (f : ExtractedField) : Bool :=
  presenceValueValid f.presence f.value &&
  optConfidenceValid f.confidence &&
  f.provenance.all isValidProvenanceEvidence &&
  isValidValidationOutcome f.validation &&
  f.errors.all isValidDiagnosticIssue &&
  f.warnings.all isValidDiagnosticIssue

theorem isValidExtractedField_iff (f : ExtractedField) :
    isValidExtractedField f = true ↔ ValidExtractedField f := by
  simp [isValidExtractedField, ValidExtractedField, presenceValueValid_iff,
    optConfidenceValid_iff, isValidProvenanceEvidence_iff, isValidValidationOutcome_iff,
    isValidDiagnosticIssue_iff, and_assoc]

/-- Single bounded structured record within an extraction result (`ExtractedRecord`). -/
structure ExtractedRecord where
  recordId : String
  status : ProcessingStatus
  fields : List (String × ExtractedField) := []
  validation : ValidationOutcome
  errors : List DiagnosticIssue := []
  warnings : List DiagnosticIssue := []
  deriving DecidableEq, Repr

def ValidExtractedRecord (r : ExtractedRecord) : Prop :=
  r.recordId ≠ "" ∧
  (∀ kv ∈ r.fields, ValidExtractedField kv.2) ∧
  ValidValidationOutcome r.validation ∧
  (∀ e ∈ r.errors, ValidDiagnosticIssue e) ∧
  (∀ w ∈ r.warnings, ValidDiagnosticIssue w)

def isValidExtractedRecord (r : ExtractedRecord) : Bool :=
  (r.recordId != "") &&
  r.fields.all (fun kv => isValidExtractedField kv.2) &&
  isValidValidationOutcome r.validation &&
  r.errors.all isValidDiagnosticIssue &&
  r.warnings.all isValidDiagnosticIssue

theorem isValidExtractedRecord_iff (r : ExtractedRecord) :
    isValidExtractedRecord r = true ↔ ValidExtractedRecord r := by
  simp [isValidExtractedRecord, ValidExtractedRecord, isValidExtractedField_iff,
    isValidValidationOutcome_iff, isValidDiagnosticIssue_iff, and_assoc]

/-- Decidable pairwise-uniqueness check matching `List.Pairwise (· ≠ ·)`. -/
def allDistinct : List String → Bool
  | [] => true
  | x :: xs => !(decide (x ∈ xs)) && allDistinct xs

theorem allDistinct_iff (xs : List String) :
    allDistinct xs = true ↔ xs.Pairwise (· ≠ ·) := by
  induction xs with
  | nil => simp [allDistinct]
  | cons x xs ih =>
    simp only [allDistinct, Bool.and_eq_true, Bool.not_eq_true', decide_eq_false_iff_not,
      ih, List.pairwise_cons]
    constructor
    · intro ⟨hnotmem, hpw⟩
      refine ⟨?_, hpw⟩
      intro y hy heq
      subst heq
      exact hnotmem hy
    · intro ⟨hne, hpw⟩
      refine ⟨?_, hpw⟩
      intro hmem
      exact hne x hmem rfl

/-- Top-level Canonical Output Record Contract v1.0 (`ExtractionResult`). -/
structure ExtractionResult where
  contractVersion : String := contractVersionV1
  extractionId : String
  status : ProcessingStatus
  source : Option SourceMetadata := none
  schemaMetadata : SchemaMetadata
  records : List ExtractedRecord := []
  validation : ValidationOutcome
  errors : List DiagnosticIssue := []
  warnings : List DiagnosticIssue := []
  deriving DecidableEq, Repr

def ProvenanceBoundToSource (source : Option SourceMetadata) (records : List ExtractedRecord) : Prop :=
  ∀ s, source = some s →
    ∀ r ∈ records, ∀ kv ∈ r.fields, ∀ ev ∈ kv.2.provenance,
      ev.sourceDocumentId = s.documentId

def isProvenanceBoundToSource (source : Option SourceMetadata) (records : List ExtractedRecord) : Bool :=
  match source with
  | none => true
  | some s =>
    records.all fun r =>
      r.fields.all fun kv =>
        kv.2.provenance.all fun ev =>
          ev.sourceDocumentId == s.documentId

theorem isProvenanceBoundToSource_iff (source : Option SourceMetadata) (records : List ExtractedRecord) :
    isProvenanceBoundToSource source records = true ↔ ProvenanceBoundToSource source records := by
  cases source with
  | none =>
    simp [isProvenanceBoundToSource, ProvenanceBoundToSource]
  | some s =>
    simp [isProvenanceBoundToSource, ProvenanceBoundToSource]

def ValidExtractionResult (res : ExtractionResult) : Prop :=
  res.contractVersion = contractVersionV1 ∧
  res.extractionId ≠ "" ∧
  (∀ s, res.source = some s → ValidSourceMetadata s) ∧
  ValidSchemaMetadata res.schemaMetadata ∧
  (res.records.map ExtractedRecord.recordId).Pairwise (· ≠ ·) ∧
  (∀ r ∈ res.records, ValidExtractedRecord r) ∧
  ProvenanceBoundToSource res.source res.records ∧
  ValidValidationOutcome res.validation ∧
  (∀ e ∈ res.errors, ValidDiagnosticIssue e) ∧
  (∀ w ∈ res.warnings, ValidDiagnosticIssue w)

private def optSourceValid (os : Option SourceMetadata) : Bool :=
  match os with
  | none => true
  | some s => isValidSourceMetadata s

private theorem optSourceValid_iff (os : Option SourceMetadata) :
    optSourceValid os = true ↔ (∀ s, os = some s → ValidSourceMetadata s) := by
  cases os with
  | none => simp [optSourceValid]
  | some s => simp [optSourceValid, isValidSourceMetadata_iff]

def isValidExtractionResult (res : ExtractionResult) : Bool :=
  (res.contractVersion == contractVersionV1) &&
  (res.extractionId != "") &&
  optSourceValid res.source &&
  isValidSchemaMetadata res.schemaMetadata &&
  allDistinct (res.records.map ExtractedRecord.recordId) &&
  res.records.all isValidExtractedRecord &&
  isProvenanceBoundToSource res.source res.records &&
  isValidValidationOutcome res.validation &&
  res.errors.all isValidDiagnosticIssue &&
  res.warnings.all isValidDiagnosticIssue

theorem isValidExtractionResult_iff (res : ExtractionResult) :
    isValidExtractionResult res = true ↔ ValidExtractionResult res := by
  simp [isValidExtractionResult, ValidExtractionResult, optSourceValid_iff,
    isValidSchemaMetadata_iff, allDistinct_iff, isValidExtractedRecord_iff,
    isProvenanceBoundToSource_iff, isValidValidationOutcome_iff, isValidDiagnosticIssue_iff,
    and_assoc]

end StructaFormal
