/-
  Structa Formal Assurance Layer — Dev/CI Conformance JSONL Oracle Evaluator.
  Evaluates the exact decidable Lean predicates (`isValid*` and assembly functions) on JSON
  vectors supplied by the Python conformance test suite.
-/

import Lean.Data.Json
import StructaFormal.Enums
import StructaFormal.Models
import StructaFormal.ValidationInvariants
import StructaFormal.ConfidenceInvariants
import StructaFormal.ProvenanceInvariants
import StructaFormal.StatusAssembly

namespace StructaFormal

open Lean

/-- Lossless conversion from `Lean.JsonNumber` (`mantissa * 10^(-exponent)`) to exact `Rat`. -/
def jsonNumberToRat (n : JsonNumber) : Rat :=
  mkRat n.mantissa (10 ^ n.exponent)

private def getOptField (obj : Json) (k : String) : Option Json :=
  match obj.getObjVal? k with
  | .ok .null => none
  | .ok v => some v
  | .error _ => none

private def getOptStr (obj : Json) (k : String) : Except String (Option String) :=
  match getOptField obj k with
  | none => .ok none
  | some j => some <$> j.getStr?

private def getReqStr (obj : Json) (k : String) : Except String String := do
  let v ← obj.getObjVal? k
  v.getStr?

private def getOptInt (obj : Json) (k : String) : Except String (Option Int) :=
  match getOptField obj k with
  | none => .ok none
  | some j => some <$> j.getInt?

private def getReqBool (obj : Json) (k : String) : Except String Bool := do
  let v ← obj.getObjVal? k
  v.getBool?

private def getOptBool (obj : Json) (k : String) (default : Bool) : Except String Bool :=
  match getOptField obj k with
  | none => .ok default
  | some j => j.getBool?

private def getOptRat (obj : Json) (k : String) : Except String (Option Rat) :=
  match getOptField obj k with
  | none => .ok none
  | some (.num n) => .ok (some (jsonNumberToRat n))
  | some _ => .error s!"expected number or null for '{k}'"

private def parseEnum {α : Type} (label : String) (fn : String → Option α) (s : String) :
    Except String α :=
  match fn s with
  | some v => .ok v
  | none => .error s!"invalid {label}: '{s}'"

def parseSourceMetadata (j : Json) : Except String SourceMetadata := do
  let docId ← getReqStr j "document_id"
  let filename ← getOptStr j "filename"
  let mediaType ← getOptStr j "media_type"
  pure { documentId := docId, filename := filename, mediaType := mediaType }

def parseSchemaMetadata (j : Json) : Except String SchemaMetadata := do
  let sid ← getReqStr j "schema_id"
  let sver ← getReqStr j "schema_version"
  pure { schemaId := sid, schemaVersion := sver }

def parseSourceLocation (j : Json) : Except String SourceLocation := do
  let page ← getOptInt j "page"
  let paragraph ← getOptInt j "paragraph"
  let lineStart ← getOptInt j "line_start"
  let lineEnd ← getOptInt j "line_end"
  let charStart ← getOptInt j "char_start"
  let charEnd ← getOptInt j "char_end"
  let sectionName ← getOptStr j "section"
  pure {
    page := page,
    paragraph := paragraph,
    lineStart := lineStart,
    lineEnd := lineEnd,
    charStart := charStart,
    charEnd := charEnd,
    sectionName := sectionName
  }

def parseProvenanceEvidence (j : Json) : Except String ProvenanceEvidence := do
  let docId ← getReqStr j "source_document_id"
  let loc ← match getOptField j "location" with
    | none => pure none
    | some lj => some <$> parseSourceLocation lj
  let evidence ← match getOptStr j "evidence" with
    | .ok (some s) => pure s
    | .ok none => pure ""
    | .error e => .error e
  let relStr ← getReqStr j "relationship"
  let rel ← parseEnum "EvidenceRelationship" EvidenceRelationship.fromString? relStr
  let truncated ← getOptBool j "truncated" false
  pure {
    sourceDocumentId := docId,
    location := loc,
    evidence := evidence,
    relationship := rel,
    truncated := truncated
  }

def parseConfidenceAssessment (j : Json) : Except String ConfidenceAssessment := do
  let val ← getOptRat j "value"
  let scale ← match getOptStr j "scale" with
    | .ok (some s) => pure s
    | .ok none => pure "0-1"
    | .error e => .error e
  let method ← match getOptStr j "method" with
    | .ok (some s) => pure s
    | .ok none => pure "extraction-assessment"
    | .error e => .error e
  let calibrated ← getOptBool j "calibrated" false
  pure { value := val, scale := scale, method := method, calibrated := calibrated }

def parseDiagnosticIssue (j : Json) : Except String DiagnosticIssue := do
  let code ← getReqStr j "code"
  let sevStr ← getReqStr j "severity"
  let sev ← parseEnum "IssueSeverity" IssueSeverity.fromString? sevStr
  let stageStr ← getReqStr j "stage"
  let stage ← parseEnum "PipelineStage" PipelineStage.fromString? stageStr
  let msg ← getReqStr j "message"
  let recordId ← getOptStr j "record_id"
  let fieldPath ← getOptStr j "field_path"
  let expected ← getOptStr j "expected"
  let actual ← getOptStr j "actual"
  pure {
    code := code,
    severity := sev,
    stage := stage,
    message := msg,
    recordId := recordId,
    fieldPath := fieldPath,
    expected := expected,
    actual := actual
  }

private def parseList {α : Type} (j : Json) (k : String) (p : Json → Except String α) :
    Except String (List α) :=
  match getOptField j k with
  | none => .ok []
  | some (.arr elems) => elems.toList.mapM p
  | some _ => .error s!"expected array for '{k}'"

def parseValidationOutcome (j : Json) : Except String ValidationOutcome := do
  let stStr ← getReqStr j "status"
  let st ← parseEnum "ValidationStatus" ValidationStatus.fromString? stStr
  let errs ← parseList j "errors" parseDiagnosticIssue
  pure { status := st, errors := errs }

def parseExtractedField (j : Json) : Except String ExtractedField := do
  let presStr ← getReqStr j "presence"
  let pres ← parseEnum "FieldPresence" FieldPresence.fromString? presStr
  let val : FieldValue :=
    match j.getObjVal? "value" with
    | .ok .null => .nullValue
    | .ok other => .nonNull other.compress
    | .error _ => .nullValue
  let vtype ← match getOptStr j "value_type" with
    | .ok none => pure none
    | .ok (some s) => some <$> parseEnum "FieldType" FieldType.fromString? s
    | .error e => .error e
  let conf ← match getOptField j "confidence" with
    | none => pure none
    | some cj => some <$> parseConfidenceAssessment cj
  let prov ← parseList j "provenance" parseProvenanceEvidence
  let valObj ← j.getObjVal? "validation"
  let validation ← parseValidationOutcome valObj
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  pure {
    presence := pres,
    value := val,
    valueType := vtype,
    confidence := conf,
    provenance := prov,
    validation := validation,
    errors := errs,
    warnings := warns
  }

def parseExtractedRecord (j : Json) : Except String ExtractedRecord := do
  let rid ← getReqStr j "record_id"
  let stStr ← getReqStr j "status"
  let st ← parseEnum "ProcessingStatus" ProcessingStatus.fromString? stStr
  let fields ← match getOptField j "fields" with
    | none => pure []
    | some (.obj kvs) =>
      kvs.toList.mapM fun (k, vj) => do
        let f ← parseExtractedField vj
        pure (k, f)
    | some _ => .error "expected object for 'fields'"
  let valObj ← j.getObjVal? "validation"
  let validation ← parseValidationOutcome valObj
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  pure {
    recordId := rid,
    status := st,
    fields := fields,
    validation := validation,
    errors := errs,
    warnings := warns
  }

def parseExtractionResult (j : Json) : Except String ExtractionResult := do
  let cv ← match getOptStr j "contract_version" with
    | .ok (some s) => pure s
    | .ok none => pure contractVersionV1
    | .error e => .error e
  let eid ← getReqStr j "extraction_id"
  let stStr ← getReqStr j "status"
  let st ← parseEnum "ProcessingStatus" ProcessingStatus.fromString? stStr
  let src ← match getOptField j "source" with
    | none => pure none
    | some sj => some <$> parseSourceMetadata sj
  let schemaJson ← match j.getObjVal? "schema" with
    | .ok sj => pure sj
    | .error _ => j.getObjVal? "schema_metadata"
  let schemaMeta ← parseSchemaMetadata schemaJson
  let recs ← parseList j "records" parseExtractedRecord
  let valObj ← j.getObjVal? "validation"
  let validation ← parseValidationOutcome valObj
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  pure {
    contractVersion := cv,
    extractionId := eid,
    status := st,
    source := src,
    schemaMetadata := schemaMeta,
    records := recs,
    validation := validation,
    errors := errs,
    warnings := warns
  }

private def dummyValidationError : DiagnosticIssue :=
  { code := "err", severity := .error, stage := .validation, message := "validation error" }

/-- Evaluate one JSONL oracle request object per `contracts/formal-oracle-protocol.md` §1. -/
def evaluateOracleRequest (req : Json) : Except String Json := do
  let kind ← getReqStr req "kind"
  let payload ← req.getObjVal? "payload"
  match kind with
  | "source_location" =>
    match parseSourceLocation payload with
    | .ok loc => pure (Json.mkObj [("ok", true), ("valid", isValidSourceLocation loc)])
    | .error _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "confidence_assessment" =>
    match parseConfidenceAssessment payload with
    | .ok c => pure (Json.mkObj [("ok", true), ("valid", isValidConfidenceAssessment c)])
    | .error _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "validation_outcome" =>
    match parseValidationOutcome payload with
    | .ok o => pure (Json.mkObj [("ok", true), ("valid", isValidValidationOutcome o)])
    | .error _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "extracted_field" =>
    match parseExtractedField payload with
    | .ok f => pure (Json.mkObj [("ok", true), ("valid", isValidExtractedField f)])
    | .error _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "extraction_result" =>
    match parseExtractionResult payload with
    | .ok res => pure (Json.mkObj [("ok", true), ("valid", isValidExtractionResult res)])
    | .error _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "result_builder_status" =>
    let strategyFailed ← getReqBool payload "strategy_failed"
    let strategyPartial ← getReqBool payload "strategy_partial"
    let hasResultErrors ← getReqBool payload "has_result_errors"
    let hasRecords ← getReqBool payload "has_records"
    let anyRecordNotCompleted ← getReqBool payload "any_record_not_completed"
    let hasValidationErrors ← getReqBool payload "has_validation_errors"
    let ps := assembleOverallStatus strategyFailed strategyPartial hasResultErrors
      hasRecords anyRecordNotCompleted
    let topErrs := if hasValidationErrors then [dummyValidationError] else []
    let vo := makeResultCardinalityOutcome strategyFailed hasRecords topErrs
    pure (Json.mkObj [
      ("ok", true),
      ("processing_status", ps.toString),
      ("validation_status", vo.status.toString)
    ])
  | "record_builder_status" =>
    let recordFailed ← getReqBool payload "record_failed"
    let recordPartial ← getReqBool payload "record_partial"
    let hasRecordErrors ← getReqBool payload "has_record_errors"
    let hasFieldErrors ← getReqBool payload "has_field_errors"
    let ps := assembleRecordStatus recordFailed recordPartial hasRecordErrors hasFieldErrors
    pure (Json.mkObj [("ok", true), ("processing_status", ps.toString)])
  | "confidence_normalizer" =>
    let inputKind ← getReqStr payload "input_kind"
    let rawScale ← match getOptStr payload "scale" with
      | .ok (some s) => pure s
      | .ok none => pure "0-1"
      | .error e => .error e
    let rawMethod ← getOptStr payload "method"
    let defaultMethod ← match getOptStr payload "default_method" with
      | .ok (some s) => pure s
      | .ok none => pure "extraction-assessment"
      | .error e => .error e
    let cinput ← match inputKind with
      | "missing" => pure ConfidenceInput.missing
      | "non_numeric" => pure ConfidenceInput.nonNumeric
      | "non_finite" => pure ConfidenceInput.nonFinite
      | "finite" =>
        match ← getOptRat payload "value" with
        | some q => pure (ConfidenceInput.finite q)
        | none => .error "finite input_kind requires numeric 'value'"
      | other => .error s!"unknown input_kind: '{other}'"
    let res := normalizeConfidence cinput rawScale rawMethod defaultMethod
    let confJson : Json := match res with
      | none => Json.null
      | some c =>
        let valJson : Json := match c.value with
          | none => Json.null
          | some q => Json.mkObj [("num", Json.num q.num), ("den", Json.num (Int.ofNat q.den))]
        Json.mkObj [
          ("value", valJson),
          ("scale", c.scale),
          ("method", c.method),
          ("calibrated", c.calibrated)
        ]
    pure (Json.mkObj [("ok", true), ("confidence", confJson)])
  | "provenance_associator" =>
    let docId ← getReqStr payload "doc_id"
    let proposedDocId ← getOptStr payload "proposed_doc_id"
    let hasLocation ← getReqBool payload "has_location"
    let reqRelStr ← getReqStr payload "requested_relationship"
    let reqRel ← parseEnum "EvidenceRelationship" EvidenceRelationship.fromString? reqRelStr
    let presentField ← getReqBool payload "present_field"
    let scalarSupported ← getReqBool payload "scalar_supported"
    let obs : ProposedEvidenceObservation := {
      proposedDocId := proposedDocId,
      locatedSpan := if hasLocation then some { charStart := some 0, charEnd := some 5 } else none,
      requestedRelationship := reqRel,
      presentField := presentField,
      scalarSupported := scalarSupported,
      excerpt := "excerpt",
      truncated := false
    }
    match associateOne docId obs with
    | none =>
      pure (Json.mkObj [
        ("ok", true),
        ("attached", false),
        ("relationship", Json.null),
        ("has_location", false)
      ])
    | some ev =>
      pure (Json.mkObj [
        ("ok", true),
        ("attached", true),
        ("relationship", ev.relationship.toString),
        ("has_location", ev.location.isSome)
      ])
  | other => .error s!"unknown oracle kind: '{other}'"

end StructaFormal
