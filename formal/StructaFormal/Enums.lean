/-
  Structa Formal Assurance Layer — Canonical Output Record Contract v1.0 Enumerations.
  Authoritative Python source: `src/structa_maestro/contracts/enums.py`.
-/

namespace StructaFormal

/-- Supported schema field data types (`FieldType` in `contracts/enums.py`). -/
inductive FieldType where
  | string
  | integer
  | decimal
  | boolean
  | date
  | array
  | object
  deriving DecidableEq, Repr

/-- Whether a schema targets a single record or multiple records (`ExtractionCardinality`). -/
inductive ExtractionCardinality where
  | single
  | multiple
  deriving DecidableEq, Repr

/-- Operational completion state of an extraction or record (`ProcessingStatus`). -/
inductive ProcessingStatus where
  | completed
  | partialStatus
  | failed
  deriving DecidableEq, Repr

/-- Schema validation outcome of a field, record, or overall result (`ValidationStatus`). -/
inductive ValidationStatus where
  | valid
  | invalid
  | notValidated
  deriving DecidableEq, Repr

/-- Distinguishes an absent/unfound field from an explicitly extracted null/value (`FieldPresence`). -/
inductive FieldPresence where
  | present
  | missing
  deriving DecidableEq, Repr

/-- Relationship between a cited source excerpt and an extracted field value (`EvidenceRelationship`). -/
inductive EvidenceRelationship where
  | direct
  | inferred
  | conflicting
  | unverified
  deriving DecidableEq, Repr

/-- Diagnostic severity separating blocking errors from non-invalidating warnings (`IssueSeverity`). -/
inductive IssueSeverity where
  | error
  | warning
  deriving DecidableEq, Repr

/-- Originating processing stage for classified diagnostic issues (`PipelineStage`). -/
inductive PipelineStage where
  | input
  | documentProcessing
  | schema
  | extraction
  | provenance
  | validation
  | serialization
  deriving DecidableEq, Repr

def FieldType.fromString? : String → Option FieldType
  | "string" => some .string
  | "integer" => some .integer
  | "decimal" => some .decimal
  | "boolean" => some .boolean
  | "date" => some .date
  | "array" => some .array
  | "object" => some .object
  | _ => none

def ExtractionCardinality.toString : ExtractionCardinality → String
  | .single => "single"
  | .multiple => "multiple"

def ExtractionCardinality.fromString? : String → Option ExtractionCardinality
  | "single" => some .single
  | "multiple" => some .multiple
  | _ => none

def ProcessingStatus.toString : ProcessingStatus → String
  | .completed => "completed"
  | .partialStatus => "partial"
  | .failed => "failed"

def ProcessingStatus.fromString? : String → Option ProcessingStatus
  | "completed" => some .completed
  | "partial" => some .partialStatus
  | "failed" => some .failed
  | _ => none

def ValidationStatus.toString : ValidationStatus → String
  | .valid => "valid"
  | .invalid => "invalid"
  | .notValidated => "not_validated"

def ValidationStatus.fromString? : String → Option ValidationStatus
  | "valid" => some .valid
  | "invalid" => some .invalid
  | "not_validated" => some .notValidated
  | _ => none

def FieldPresence.fromString? : String → Option FieldPresence
  | "present" => some .present
  | "missing" => some .missing
  | _ => none

def EvidenceRelationship.toString : EvidenceRelationship → String
  | .direct => "direct"
  | .inferred => "inferred"
  | .conflicting => "conflicting"
  | .unverified => "unverified"

def EvidenceRelationship.fromString? : String → Option EvidenceRelationship
  | "direct" => some .direct
  | "inferred" => some .inferred
  | "conflicting" => some .conflicting
  | "unverified" => some .unverified
  | _ => none

def IssueSeverity.fromString? : String → Option IssueSeverity
  | "error" => some .error
  | "warning" => some .warning
  | _ => none

def PipelineStage.fromString? : String → Option PipelineStage
  | "input" => some .input
  | "document_processing" => some .documentProcessing
  | "schema" => some .schema
  | "extraction" => some .extraction
  | "provenance" => some .provenance
  | "validation" => some .validation
  | "serialization" => some .serialization
  | _ => none

end StructaFormal
