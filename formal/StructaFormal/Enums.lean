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

/-- Comparison operators for declarative cross-field rules (`ComparisonOperator`). -/
inductive ComparisonOperator where
  | eq
  | ne
  | lt
  | lte
  | gt
  | gte
  deriving DecidableEq, Repr

/-- Predicate operators for controlling fields in conditional presence rules (`ConditionOperator`). -/
inductive ConditionOperator where
  | isPresent
  | isMissing
  | equals
  | notEquals
  | inValues
  deriving DecidableEq, Repr

/-- Diagnostic severity emitted when a cross-field rule is violated (`RuleSeverity`). -/
inductive RuleSeverity where
  | error
  | warning
  deriving DecidableEq, Repr

/-- Kinds of conditional presence rules (`ConditionalPresenceKind`). -/
inductive ConditionalPresenceKind where
  | requiredIf
  | forbiddenIf
  deriving DecidableEq, Repr

/-- Kinds of multi-field group presence rules (`GroupPresenceKind`). -/
inductive GroupPresenceKind where
  | mutuallyExclusive
  | atLeastOneOf
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

def ComparisonOperator.toString : ComparisonOperator → String
  | .eq => "eq"
  | .ne => "ne"
  | .lt => "lt"
  | .lte => "lte"
  | .gt => "gt"
  | .gte => "gte"

def ComparisonOperator.fromString? : String → Option ComparisonOperator
  | "eq" => some .eq
  | "ne" => some .ne
  | "lt" => some .lt
  | "lte" => some .lte
  | "gt" => some .gt
  | "gte" => some .gte
  | _ => none

def ConditionOperator.toString : ConditionOperator → String
  | .isPresent => "is_present"
  | .isMissing => "is_missing"
  | .equals => "equals"
  | .notEquals => "not_equals"
  | .inValues => "in"

def ConditionOperator.fromString? : String → Option ConditionOperator
  | "is_present" => some .isPresent
  | "is_missing" => some .isMissing
  | "equals" => some .equals
  | "not_equals" => some .notEquals
  | "in" => some .inValues
  | _ => none

def RuleSeverity.toString : RuleSeverity → String
  | .error => "error"
  | .warning => "warning"

def RuleSeverity.fromString? : String → Option RuleSeverity
  | "error" => some .error
  | "warning" => some .warning
  | _ => none

def ConditionalPresenceKind.toString : ConditionalPresenceKind → String
  | .requiredIf => "required_if"
  | .forbiddenIf => "forbidden_if"

def ConditionalPresenceKind.fromString? : String → Option ConditionalPresenceKind
  | "required_if" => some .requiredIf
  | "forbidden_if" => some .forbiddenIf
  | _ => none

def GroupPresenceKind.toString : GroupPresenceKind → String
  | .mutuallyExclusive => "mutually_exclusive"
  | .atLeastOneOf => "at_least_one_of"

def GroupPresenceKind.fromString? : String → Option GroupPresenceKind
  | "mutually_exclusive" => some .mutuallyExclusive
  | "at_least_one_of" => some .atLeastOneOf
  | _ => none

end StructaFormal
