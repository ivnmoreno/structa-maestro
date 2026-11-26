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
import StructaFormal.SchemaModels
import StructaFormal.DeterministicValidation
import StructaFormal.CrossFieldModels
import StructaFormal.CrossFieldValidation
import StructaFormal.ProvenanceAssociation
import StructaFormal.ResultAssembly

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

private def jsonToRat (j : Json) : Except String Rat :=
  match j with
  | .num n => .ok (jsonNumberToRat n)
  | .str s =>
    match Json.parse s with
    | .ok (.num n) => .ok (jsonNumberToRat n)
    | _ => .error s!"expected decimal number string, got '{s}'"
  | _ => .error "expected number or numeric string"

private def getOptRat (obj : Json) (k : String) : Except String (Option Rat) :=
  match getOptField obj k with
  | none => .ok none
  | some j => some <$> jsonToRat j

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

private def orderByKeys {α : Type} (entries : List (String × α)) (order? : Option (List String)) :
    List (String × α) :=
  match order? with
  | none => entries
  | some keys =>
    let ordered := keys.filterMap fun k => entries.find? (fun kv => kv.1 == k)
    let remaining := entries.filter fun kv => !keys.contains kv.1
    ordered ++ remaining

partial def parseRuntimeValue (j : Json) : Except String RuntimeValue := do
  let kind ← getReqStr j "kind"
  match kind with
  | "null" => pure .nullVal
  | "string" => pure (.strVal (← getReqStr j "value"))
  | "integer" =>
    let v ← j.getObjVal? "value"
    pure (.intVal (← v.getInt?))
  | "decimal" =>
    let v ← j.getObjVal? "value"
    pure (.decVal (← jsonToRat v))
  | "non_finite_decimal" => pure .nonFiniteDecVal
  | "boolean" => pure (.boolVal (← getReqBool j "value"))
  | "date" => pure (.dateVal (← getReqStr j "value"))
  | "datetime" => pure .datetimeVal
  | "array" =>
    match ← j.getObjVal? "value" with
    | .arr elems => pure (.arrayVal (← elems.toList.mapM parseRuntimeValue))
    | _ => .error "expected array for RuntimeValue 'array'"
  | "object" =>
    match ← j.getObjVal? "value" with
    | .obj kvs =>
      let pairs ← kvs.toList.mapM fun (k, vj) => do
        pure (k, ← parseRuntimeValue vj)
      pure (.objectVal pairs)
    | _ => .error "expected object for RuntimeValue 'object'"
  | "other" => pure .otherVal
  | other => .error s!"unknown RuntimeValue kind: '{other}'"

def parseFieldConstraints (j : Json) : Except String FieldConstraints := do
  let minVal ← getOptRat j "min_value"
  let maxVal ← getOptRat j "max_value"
  let minLen ← getOptInt j "min_length"
  let maxLen ← getOptInt j "max_length"
  let minIt ← getOptInt j "min_items"
  let maxIt ← getOptInt j "max_items"
  let pat ← getOptStr j "pattern"
  let allowed ← match getOptField j "allowed_values" with
    | none => pure none
    | some (.arr elems) => some <$> elems.toList.mapM parseRuntimeValue
    | some _ => .error "expected array or null for 'allowed_values'"
  pure {
    minValue := minVal,
    maxValue := maxVal,
    minLength := minLen,
    maxLength := maxLen,
    minItems := minIt,
    maxItems := maxIt,
    pattern := pat,
    allowedValues := allowed
  }

partial def parseFieldSpec (j : Json) : Except String FieldSpec := do
  let name ← getReqStr j "name"
  let ftStr ← getReqStr j "field_type"
  let ft ← parseEnum "FieldType" FieldType.fromString? ftStr
  let req ← getOptBool j "required" true
  let nul ← getOptBool j "nullable" false
  let constraints ← match getOptField j "constraints" with
    | none => pure ({} : FieldConstraints)
    | some cj => parseFieldConstraints cj
  let propOrder ← match getOptField j "property_order" with
    | none => pure none
    | some (.arr elems) => some <$> elems.toList.mapM (fun e => e.getStr?)
    | some _ => .error "expected string array for 'property_order'"
  let props ← match getOptField j "properties" with
    | none => pure none
    | some (.obj kvs) =>
      let parsed ← kvs.toList.mapM fun (k, vj) => do
        pure (k, ← parseFieldSpec vj)
      pure (some (orderByKeys parsed propOrder))
    | some _ => .error "expected object or null for 'properties'"
  let items ← match getOptField j "items" with
    | none => pure none
    | some ij => some <$> parseFieldSpec ij
  let aaf ← getOptBool j "allow_additional_fields" false
  pure (.mk name ft req nul constraints props items aaf)

def parseExtractionSchema (j : Json) : Except String ExtractionSchema := do
  let sid ← getReqStr j "schema_id"
  let sver ← getReqStr j "schema_version"
  let cardStr ← match getOptStr j "cardinality" with
    | .ok (some s) => pure s
    | .ok none => pure "single"
    | .error e => .error e
  let card ← parseEnum "ExtractionCardinality" ExtractionCardinality.fromString? cardStr
  let aaf ← getOptBool j "allow_additional_fields" false
  let maxDepth ← match getOptInt j "max_depth" with
    | .ok (some n) => pure n
    | .ok none => pure 16
    | .error e => .error e
  let fieldOrder ← match getOptField j "field_order" with
    | none => pure none
    | some (.arr elems) => some <$> elems.toList.mapM (fun e => e.getStr?)
    | some _ => .error "expected string array for 'field_order'"
  let fields ← match ← j.getObjVal? "fields" with
    | .obj kvs =>
      let parsed ← kvs.toList.mapM fun (k, vj) => do
        pure (k, ← parseFieldSpec vj)
      pure (orderByKeys parsed fieldOrder)
    | _ => .error "expected object for 'fields'"
  pure {
    schemaId := sid,
    schemaVersion := sver,
    fields := fields,
    cardinality := card,
    allowAdditionalFields := aaf,
    maxDepth := maxDepth
  }

def parseRecordFieldInput (j : Json) : Except String RecordFieldInput := do
  match j with
  | .obj kvs =>
    kvs.toList.mapM fun (k, vj) => do
      let presStr ← getReqStr vj "presence"
      let pres ← parseEnum "FieldPresence" FieldPresence.fromString? presStr
      let valJson ← vj.getObjVal? "value"
      let val ← parseRuntimeValue valJson
      pure (k, (pres, val))
  | _ => .error "expected object for 'fields'"

private def parseRuleSeverityOpt (j : Json) : Except String RuleSeverity := do
  match ← getOptStr j "severity" with
  | none => pure .error
  | some s => parseEnum "RuleSeverity" RuleSeverity.fromString? s

def parseArithmeticTerm (j : Json) : Except String ArithmeticTerm := do
  let field ← getReqStr j "field"
  let sign ← match getOptField j "sign" with
    | none => pure (1 : Int)
    | some sj => sj.getInt?
  let itemField ← getOptStr j "item_field"
  let tmaz ← getOptBool j "treat_missing_as_zero" false
  pure { field := field, sign := sign, itemField := itemField, treatMissingAsZero := tmaz }

def parseFieldCondition (j : Json) : Except String FieldCondition := do
  let field ← getReqStr j "field"
  let opStr ← getReqStr j "operator"
  let op ← parseEnum "ConditionOperator" ConditionOperator.fromString? opStr
  let val ← match getOptField j "value" with
    | none => pure none
    | some vj => some <$> parseRuntimeValue vj
  let vals ← match getOptField j "values" with
    | none => pure none
    | some (.arr elems) => some <$> elems.toList.mapM parseRuntimeValue
    | some _ => .error "expected array or null for 'values'"
  pure { field := field, operator := op, value := val, values := vals }

def parseValidationRule (j : Json) : Except String ValidationRule := do
  let rtype ← getReqStr j "rule_type"
  let ruleId ← getReqStr j "rule_id"
  let sev ← parseRuleSeverityOpt j
  let msg ← getOptStr j "message"
  match rtype with
  | "comparison" =>
    let leftField ← getReqStr j "left_field"
    let opStr ← getReqStr j "operator"
    let op ← parseEnum "ComparisonOperator" ComparisonOperator.fromString? opStr
    let rightField ← getReqStr j "right_field"
    pure (.comparison {
      ruleId := ruleId, leftField := leftField, operator := op,
      rightField := rightField, severity := sev, message := msg
    })
  | "arithmetic" =>
    let terms ← match getOptField j "terms" with
      | none => pure []
      | some (.arr elems) => elems.toList.mapM parseArithmeticTerm
      | some _ => .error "expected array for 'terms'"
    let targetField ← getReqStr j "target_field"
    let tol ← match ← getOptRat j "tolerance" with
      | none => pure (0 : Rat)
      | some q => pure q
    pure (.arithmetic {
      ruleId := ruleId, terms := terms, targetField := targetField,
      tolerance := tol, severity := sev, message := msg
    })
  | "conditional_presence" =>
    let kindStr ← getReqStr j "kind"
    let kind ← parseEnum "ConditionalPresenceKind" ConditionalPresenceKind.fromString? kindStr
    let whenJson ← j.getObjVal? "when"
    let whenCond ← parseFieldCondition whenJson
    let targetFields ← match getOptField j "target_fields" with
      | none => pure []
      | some (.arr elems) => elems.toList.mapM (fun e => e.getStr?)
      | some _ => .error "expected string array for 'target_fields'"
    pure (.conditionalPresence {
      ruleId := ruleId, kind := kind, when := whenCond,
      targetFields := targetFields, severity := sev, message := msg
    })
  | "group_presence" =>
    let kindStr ← getReqStr j "kind"
    let kind ← parseEnum "GroupPresenceKind" GroupPresenceKind.fromString? kindStr
    let fields ← match getOptField j "fields" with
      | none => pure []
      | some (.arr elems) => elems.toList.mapM (fun e => e.getStr?)
      | some _ => .error "expected string array for 'fields'"
    pure (.groupPresence {
      ruleId := ruleId, kind := kind, fields := fields, severity := sev, message := msg
    })
  | other => .error s!"unknown rule_type: '{other}'"

def parseSchemaRules (j : Json) : Except String (List ValidationRule) :=
  match getOptField j "rules" with
  | none => .ok []
  | some (.arr elems) => elems.toList.mapM parseValidationRule
  | some _ => .error "expected array or null for 'rules'"

def parseDocumentSegment (j : Json) : Except String DocumentSegment := do
  let cs ← (← j.getObjVal? "char_start").getInt?
  let ce ← (← j.getObjVal? "char_end").getInt?
  let page ← getOptInt j "page"
  let para ← getOptInt j "paragraph"
  let ls ← getOptInt j "line_start"
  let le ← getOptInt j "line_end"
  let heading ← getOptStr j "heading"
  pure {
    charStart := cs, charEnd := ce, page := page, paragraph := para,
    lineStart := ls, lineEnd := le, heading := heading
  }

def parseProposedEvidenceInput (j : Json) : Except String ProposedEvidenceInput := do
  let quote ← getReqStr j "quote"
  let srcDoc ← getOptStr j "source_document_id"
  let rel ← match ← getOptStr j "relationship" with
    | some s => pure s
    | none => pure "direct"
  let span ← match getOptField j "matched_span" with
    | none => pure none
    | some (.arr elems) =>
      match elems.toList with
      | [a, b] => pure (some (← a.getInt?, ← b.getInt?))
      | _ => .error "expected 2-element array for 'matched_span'"
    | some _ => .error "expected array or null for 'matched_span'"
  let matchedText ← match ← getOptStr j "matched_text" with
    | some s => pure s
    | none => pure ""
  let scalarSup ← getOptBool j "scalar_supported" true
  pure {
    quote := quote,
    sourceDocumentId := srcDoc,
    relationship := rel,
    matchedSpan := span,
    matchedText := matchedText,
    scalarSupported := scalarSup
  }

def parseConfidenceInputObj (j : Json) : Except String ConfidenceInput := do
  let inputKind ← getReqStr j "input_kind"
  match inputKind with
  | "missing" => pure .missing
  | "non_numeric" => pure .nonNumeric
  | "non_finite" => pure .nonFinite
  | "finite" =>
    match ← getOptRat j "value" with
    | some q => pure (.finite q)
    | none => .error "finite input_kind requires numeric 'value'"
  | other => .error s!"unknown input_kind: '{other}'"

def parseProposedFieldInput (j : Json) : Except String ProposedFieldInput := do
  let presStr ← getReqStr j "presence"
  let pres ← parseEnum "FieldPresence" FieldPresence.fromString? presStr
  let val ← match getOptField j "value" with
    | none => pure .nullVal
    | some vj => parseRuntimeValue vj
  let conf ← match getOptField j "confidence_input" with
    | none => pure .missing
    | some cj => parseConfidenceInputObj cj
  let cScale ← match ← getOptStr j "confidence_scale" with
    | some s => pure s
    | none => pure "0-1"
  let cMethod ← getOptStr j "confidence_method"
  let ev ← parseList j "evidence" parseProposedEvidenceInput
  let ambig ← getOptBool j "ambiguous" false
  let provFailed ← getOptBool j "provenance_failed" false
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  pure {
    presence := pres,
    value := val,
    confidence := conf,
    confidenceScale := cScale,
    confidenceMethod := cMethod,
    evidence := ev,
    ambiguous := ambig,
    provenanceFailed := provFailed,
    warnings := warns,
    errors := errs
  }

def parseProposedRecordInput (j : Json) : Except String ProposedRecordInput := do
  let fields ← match getOptField j "fields" with
    | none => pure []
    | some (.obj kvs) =>
      kvs.toList.mapM fun (k, vj) => do
        pure (k, ← parseProposedFieldInput vj)
    | some _ => .error "expected object for 'fields'"
  let part ← getOptBool j "partial" false
  let failed ← getOptBool j "failed" false
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  pure {
    fields := fields, partialFlag := part, failed := failed,
    errors := errs, warnings := warns
  }

def parseStrategyOutputInput (j : Json) : Except String StrategyOutputInput := do
  let recs ← parseList j "records" parseProposedRecordInput
  let part ← getOptBool j "partial" false
  let failed ← getOptBool j "failed" false
  let errs ← parseList j "errors" parseDiagnosticIssue
  let warns ← parseList j "warnings" parseDiagnosticIssue
  let dcm ← match ← getOptStr j "default_confidence_method" with
    | some s => pure s
    | none => pure "extraction-assessment"
  pure {
    records := recs, partialFlag := part, failed := failed,
    errors := errs, warnings := warns, defaultConfidenceMethod := dcm
  }

private def optIntToJson : Option Int → Json
  | none => Json.null
  | some n => Json.num n

private def optStrToJson : Option String → Json
  | none => Json.null
  | some s => Json.str s

def locationToJson (loc : SourceLocation) : Json :=
  Json.mkObj [
    ("page", optIntToJson loc.page),
    ("paragraph", optIntToJson loc.paragraph),
    ("line_start", optIntToJson loc.lineStart),
    ("line_end", optIntToJson loc.lineEnd),
    ("char_start", optIntToJson loc.charStart),
    ("char_end", optIntToJson loc.charEnd),
    ("section", optStrToJson loc.sectionName)
  ]

def provenanceEvidenceToJson (ev : ProvenanceEvidence) : Json :=
  Json.mkObj [
    ("source_document_id", Json.str ev.sourceDocumentId),
    ("relationship", Json.str ev.relationship.toString),
    ("evidence", Json.str ev.evidence),
    ("truncated", Json.bool ev.truncated),
    ("location", match ev.location with | none => Json.null | some loc => locationToJson loc)
  ]

private def fieldPresenceToString : FieldPresence → String
  | .present => "present"
  | .missing => "missing"

private def diagPairToJson (d : DiagnosticIssue) : Json :=
  Json.mkObj [
    ("field_path", optStrToJson d.fieldPath),
    ("code", Json.str d.code)
  ]

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
  | "schema_well_formed" =>
    match parseExtractionSchema payload, parseSchemaRules payload with
    | .ok schema, .ok rules =>
      pure (Json.mkObj [("ok", true), ("valid", isValidSchemaWithRules schema rules)])
    | _, _ => pure (Json.mkObj [("ok", true), ("valid", false)])
  | "validate_record" =>
    let schemaJson ← payload.getObjVal? "schema"
    let schema ← parseExtractionSchema schemaJson
    let rules ← parseSchemaRules schemaJson
    let recordId ← match getOptStr payload "record_id" with
      | .ok (some s) => pure s
      | .ok none => pure "record-001"
      | .error e => .error e
    let fieldsJson ← payload.getObjVal? "fields"
    let fields ← parseRecordFieldInput fieldsJson
    let patternMap : List (String × Bool) ← match getOptField payload "pattern_results" with
      | none => pure []
      | some (.obj kvs) =>
        kvs.toList.mapM fun (k, vj) => do
          pure (k, ← vj.getBool?)
      | some _ => .error "expected object or null for 'pattern_results'"
    let pm : PatternMatcher := fun fp _ =>
      match patternMap.find? (fun kv => kv.1 == fp) with
      | some (_, matched) => matched
      | none => true
    let (recOutcome, fieldOutcomes, ruleWarnings) :=
      validateRecordWithRules pm fields schema rules recordId
    let errorsJson : List Json := recOutcome.errors.map fun e =>
      Json.mkObj [
        ("field_path", match e.fieldPath with | some s => Json.str s | none => Json.null),
        ("code", Json.str e.code)
      ]
    let warningsJson : List Json := ruleWarnings.map fun w =>
      Json.mkObj [
        ("field_path", match w.fieldPath with | some s => Json.str s | none => Json.null),
        ("code", Json.str w.code)
      ]
    let fieldStatusesJson : Json :=
      Json.mkObj (fieldOutcomes.map fun (k, o) => (k, Json.str o.status.toString))
    pure (Json.mkObj [
      ("ok", true),
      ("status", recOutcome.status.toString),
      ("errors", Json.arr errorsJson.toArray),
      ("warnings", Json.arr warningsJson.toArray),
      ("field_statuses", fieldStatusesJson)
    ])
  | "validate_result_cardinality" =>
    let cardStr ← getReqStr payload "cardinality"
    let card ← parseEnum "ExtractionCardinality" ExtractionCardinality.fromString? cardStr
    let counts ← parseList payload "record_error_counts" (fun j => j.getNat?)
    let hasRecs ← getReqBool payload "has_records"
    let failedBefore ← getReqBool payload "failed_before_validation"
    let recOutcomes : List ValidationOutcome := counts.map fun n =>
      makeValidationOutcome (List.replicate n dummyValidationError)
    let schema : ExtractionSchema := {
      schemaId := "cardinality-check",
      schemaVersion := "1.0",
      fields := [],
      cardinality := card
    }
    let out := validateResultCardinality recOutcomes schema hasRecs failedBefore
    let codesJson : List Json := out.errors.map (fun e => Json.str e.code)
    pure (Json.mkObj [
      ("ok", true),
      ("status", out.status.toString),
      ("codes", Json.arr codesJson.toArray)
    ])
  | "location_from_span" =>
    let segs ← parseList payload "segments" parseDocumentSegment
    let cs ← (← payload.getObjVal? "char_start").getInt?
    let ce ← (← payload.getObjVal? "char_end").getInt?
    let loc := locationFromSpan segs cs ce
    pure (Json.mkObj [("ok", true), ("location", locationToJson loc)])
  | "associate_provenance" =>
    let docId ← getReqStr payload "doc_id"
    let segs ← parseList payload "segments" parseDocumentSegment
    let fieldName ← getReqStr payload "field_name"
    let presStr ← getReqStr payload "presence"
    let pres ← parseEnum "FieldPresence" FieldPresence.fromString? presStr
    let recordId ← match ← getOptStr payload "record_id" with
      | some s => pure s
      | none => pure "record-001"
    let maxChars ← match getOptField payload "max_excerpt_chars" with
      | none => pure 500
      | some mj => mj.getNat?
    let items ← parseList payload "proposed_evidence" parseProposedEvidenceInput
    let (evList, warns) := associateEvidence docId segs fieldName pres recordId maxChars items
    pure (Json.mkObj [
      ("ok", true),
      ("evidence", Json.arr (evList.map provenanceEvidenceToJson).toArray),
      ("warning_codes", Json.arr (warns.map (fun w => Json.str w.code)).toArray)
    ])
  | "build_result" =>
    let extractionId ← getReqStr payload "extraction_id"
    let sourceJson ← payload.getObjVal? "source"
    let source ← parseSourceMetadata sourceJson
    let segs ← parseList payload "segments" parseDocumentSegment
    let schemaJson ← payload.getObjVal? "schema"
    let schema ← parseExtractionSchema schemaJson
    let rules ← parseSchemaRules schemaJson
    let docWarns ← parseList payload "document_warnings" parseDiagnosticIssue
    let stratJson ← payload.getObjVal? "strategy_output"
    let stratOut ← parseStrategyOutputInput stratJson
    let patternMap : List (String × Bool) ← match getOptField payload "pattern_results" with
      | none => pure []
      | some (.obj kvs) =>
        kvs.toList.mapM fun (k, vj) => do
          pure (k, ← vj.getBool?)
      | some _ => .error "expected object or null for 'pattern_results'"
    let pm : PatternMatcher := fun fp _ =>
      match patternMap.find? (fun kv => kv.1 == fp) with
      | some (_, matched) => matched
      | none => true
    let maxChars ← match getOptField payload "max_excerpt_chars" with
      | none => pure 500
      | some mj => mj.getNat?
    let res := buildResult pm extractionId source segs schema rules docWarns stratOut maxChars
    let recordsJson : List Json := res.records.map fun rec =>
      let fieldsJson : Json := Json.mkObj (rec.fields.map fun (fname, f) =>
        (fname, Json.mkObj [
          ("presence", Json.str (fieldPresenceToString f.presence)),
          ("is_null", Json.bool (f.value == .nullValue)),
          ("validation_status", Json.str f.validation.status.toString),
          ("provenance_relationships",
            Json.arr (f.provenance.map (fun ev => Json.str ev.relationship.toString)).toArray),
          ("error_codes", Json.arr (f.errors.map (fun e => Json.str e.code)).toArray),
          ("warning_codes", Json.arr (f.warnings.map (fun w => Json.str w.code)).toArray)
        ]))
      Json.mkObj [
        ("record_id", Json.str rec.recordId),
        ("status", Json.str rec.status.toString),
        ("validation_status", Json.str rec.validation.status.toString),
        ("validation_errors", Json.arr (rec.validation.errors.map diagPairToJson).toArray),
        ("warning_codes", Json.arr (rec.warnings.map (fun w => Json.str w.code)).toArray),
        ("fields", fieldsJson)
      ]
    pure (Json.mkObj [
      ("ok", true),
      ("valid_contract", Json.bool (isValidExtractionResult res)),
      ("status", Json.str res.status.toString),
      ("validation_status", Json.str res.validation.status.toString),
      ("validation_errors", Json.arr (res.validation.errors.map diagPairToJson).toArray),
      ("error_codes", Json.arr (res.errors.map (fun e => Json.str e.code)).toArray),
      ("warning_codes", Json.arr (res.warnings.map (fun w => Json.str w.code)).toArray),
      ("records", Json.arr recordsJson.toArray)
    ])
  | other => .error s!"unknown oracle kind: '{other}'"

end StructaFormal
