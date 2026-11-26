/-
  Structa Formal Assurance Layer — Declarative Cross-Field & Conditional Validation Rule Models.
  Authoritative Python source: `src/structa_maestro/schema/rules.py`.
-/

import StructaFormal.SchemaModels

namespace StructaFormal

/-- Optional message must be non-blank when present (`_normalize_message` in `schema/rules.py:86-91`). -/
def optNonBlank : Option String → Bool
  | none => true
  | some s => isNonBlank s

def OptNonBlank : Option String → Prop
  | none => True
  | some s => isNonBlank s = true

theorem optNonBlank_iff (ms : Option String) :
    optNonBlank ms = true ↔ OptNonBlank ms := by
  cases ms <;> simp [optNonBlank, OptNonBlank]

private def splitCharsOnDot : List Char → List Char → List String
  | [], acc => [String.ofList acc.reverse]
  | '.' :: rest, acc => String.ofList acc.reverse :: splitCharsOnDot rest []
  | c :: rest, acc => splitCharsOnDot rest (c :: acc)

/-- Pure kernel-reducible split of a dot-separated field path into segments (`path.split(".")`). -/
def splitPathSegments (path : String) : List String :=
  splitCharsOnDot path.toList []

/-- Valid non-empty dot-separated path with every segment non-blank (`_normalize_path`). -/
def isValidFieldPath (path : String) : Bool :=
  isNonBlank path && (splitPathSegments path).all isNonBlank

/-- Structural schema path resolution matching `resolve_schema_field_spec` (`schema/rules.py:363-388`). -/
def resolveSchemaFieldSpecAux (fields : List (String × FieldSpec)) : List String → Option FieldSpec
  | [] => none
  | [seg] => (fields.find? (fun kv => kv.1 == seg)).map Prod.snd
  | seg :: seg2 :: rest =>
    match fields.find? (fun kv => kv.1 == seg) with
    | some (_, sp) =>
      if sp.fieldType == .object then
        match sp.properties with
        | some props =>
          if !props.isEmpty then
            resolveSchemaFieldSpecAux props (seg2 :: rest)
          else
            none
        | none => none
      else
        none
    | none => none

/-- Resolve a top-level or dot-separated nested object path against declared schema fields. -/
def resolveSchemaFieldSpec (fields : List (String × FieldSpec)) (path : String) : Option FieldSpec :=
  if isValidFieldPath path then
    resolveSchemaFieldSpecAux fields (splitPathSegments path)
  else
    none

/-- Numeric scalar types (`_NUMERIC_TYPES` in `schema/rules.py:18`). -/
def isNumericType (ft : FieldType) : Bool :=
  ft == .integer || ft == .decimal

/-- Scalar comparable types (`_SCALAR_COMPARABLE_TYPES` in `schema/rules.py:20-22`). -/
def isScalarComparableType (ft : FieldType) : Bool :=
  ft == .string || ft == .integer || ft == .decimal || ft == .boolean || ft == .date

/-- Ordered comparison operators (`_ORDERED_OPERATORS` in `schema/rules.py:19`). -/
def isOrderedComparisonOp (op : ComparisonOperator) : Bool :=
  op == .lt || op == .lte || op == .gt || op == .gte

/-- Type compatibility for `ComparisonRule` (`_types_compatible_for_comparison` in `schema/rules.py:391-407`). -/
def typesCompatibleForComparison (leftType rightType : FieldType) (op : ComparisonOperator) : Bool :=
  if !isScalarComparableType leftType || !isScalarComparableType rightType then
    false
  else if isOrderedComparisonOp op then
    if leftType == .boolean || rightType == .boolean then
      false
    else if isNumericType leftType && isNumericType rightType then
      true
    else
      leftType == rightType
  else
    if isNumericType leftType && isNumericType rightType then
      true
    else
      leftType == rightType

/-- Literal compatibility for `FieldCondition` (`_is_literal_compatible` in `schema/rules.py:410-424`). -/
def isLiteralCompatible (v : RuntimeValue) (ft : FieldType) : Bool :=
  match ft, v with
  | .string, .strVal _ => true
  | .boolean, .boolVal _ => true
  | .integer, .intVal _ => true
  | .decimal, .intVal _ => true
  | .decimal, .decVal _ => true
  | .date, .dateVal _ => true
  | _, _ => false

/-- Declarative field-to-field comparison rule (`ComparisonRule` in `schema/rules.py:105`). -/
structure ComparisonRule where
  ruleId : String
  leftField : String
  operator : ComparisonOperator
  rightField : String
  severity : RuleSeverity := .error
  message : Option String := none
  deriving Repr

/-- Signed numeric scalar or collection-sum operand (`ArithmeticTerm` in `schema/rules.py:144`). -/
structure ArithmeticTerm where
  field : String
  sign : Int := 1
  itemField : Option String := none
  treatMissingAsZero : Bool := false
  deriving Repr

/-- Declarative exact-decimal arithmetic balance rule (`ArithmeticRule` in `schema/rules.py:167`). -/
structure ArithmeticRule where
  ruleId : String
  terms : List ArithmeticTerm
  targetField : String
  tolerance : Rat := 0
  severity : RuleSeverity := .error
  message : Option String := none
  deriving Repr

/-- Declarative predicate over a controlling field (`FieldCondition` in `schema/rules.py:215`). -/
structure FieldCondition where
  field : String
  operator : ConditionOperator
  value : Option RuntimeValue := none
  values : Option (List RuntimeValue) := none
  deriving Repr

/-- Declarative `required_if` / `forbidden_if` rule (`ConditionalPresenceRule` in `schema/rules.py:269`). -/
structure ConditionalPresenceRule where
  ruleId : String
  kind : ConditionalPresenceKind
  when : FieldCondition
  targetFields : List String
  severity : RuleSeverity := .error
  message : Option String := none
  deriving Repr

/-- Declarative `mutually_exclusive` / `at_least_one_of` rule (`GroupPresenceRule` in `schema/rules.py:322`). -/
structure GroupPresenceRule where
  ruleId : String
  kind : GroupPresenceKind
  fields : List String
  severity : RuleSeverity := .error
  message : Option String := none
  deriving Repr

/-- Tagged sum of all four declarative intra-record rule families (`ValidationRule`). -/
inductive ValidationRule where
  | comparison (r : ComparisonRule)
  | arithmetic (r : ArithmeticRule)
  | conditionalPresence (r : ConditionalPresenceRule)
  | groupPresence (r : GroupPresenceRule)
  deriving Repr

def ValidationRule.ruleId : ValidationRule → String
  | .comparison r => r.ruleId
  | .arithmetic r => r.ruleId
  | .conditionalPresence r => r.ruleId
  | .groupPresence r => r.ruleId

def ValidationRule.severity : ValidationRule → RuleSeverity
  | .comparison r => r.severity
  | .arithmetic r => r.severity
  | .conditionalPresence r => r.severity
  | .groupPresence r => r.severity

-- =============================================================================
-- Propositional & Decidable Well-Formedness Predicates
-- =============================================================================

/-- Propositional well-formedness for a `ComparisonRule` against `fields`. -/
def ValidComparisonRule (fields : List (String × FieldSpec)) (r : ComparisonRule) : Prop :=
  isNonBlank r.ruleId = true ∧
  OptNonBlank r.message ∧
  isValidFieldPath r.leftField = true ∧
  isValidFieldPath r.rightField = true ∧
  r.leftField ≠ r.rightField ∧
  match resolveSchemaFieldSpec fields r.leftField, resolveSchemaFieldSpec fields r.rightField with
  | some lspec, some rspec =>
    typesCompatibleForComparison lspec.fieldType rspec.fieldType r.operator = true
  | _, _ => False

def isValidComparisonRule (fields : List (String × FieldSpec)) (r : ComparisonRule) : Bool :=
  isNonBlank r.ruleId &&
  optNonBlank r.message &&
  isValidFieldPath r.leftField &&
  isValidFieldPath r.rightField &&
  (r.leftField != r.rightField) &&
  match resolveSchemaFieldSpec fields r.leftField, resolveSchemaFieldSpec fields r.rightField with
  | some lspec, some rspec =>
    typesCompatibleForComparison lspec.fieldType rspec.fieldType r.operator
  | _, _ => false

theorem isValidComparisonRule_iff (fields : List (String × FieldSpec)) (r : ComparisonRule) :
    isValidComparisonRule fields r = true ↔ ValidComparisonRule fields r := by
  unfold isValidComparisonRule ValidComparisonRule
  cases resolveSchemaFieldSpec fields r.leftField <;>
    cases resolveSchemaFieldSpec fields r.rightField <;>
    simp [optNonBlank_iff, and_assoc]

/-- Propositional well-formedness for a single `ArithmeticTerm` against `fields`. -/
def ValidArithmeticTerm (fields : List (String × FieldSpec)) (t : ArithmeticTerm) : Prop :=
  isValidFieldPath t.field = true ∧
  (t.sign = 1 ∨ t.sign = -1) ∧
  match resolveSchemaFieldSpec fields t.field with
  | none => False
  | some termSpec =>
    match t.itemField with
    | some itemPath =>
      isValidFieldPath itemPath = true ∧
      termSpec.fieldType = .array ∧
      match termSpec.items with
      | none => False
      | some itemSpec =>
        itemSpec.fieldType = .object ∧
        match itemSpec.properties with
        | none => False
        | some props =>
          props ≠ [] ∧
          match resolveSchemaFieldSpec props itemPath with
          | none => False
          | some nestedSpec => isNumericType nestedSpec.fieldType = true
    | none =>
      if termSpec.fieldType = .array then
        match termSpec.items with
        | none => False
        | some itemSpec => isNumericType itemSpec.fieldType = true
      else
        isNumericType termSpec.fieldType = true

def isValidArithmeticTerm (fields : List (String × FieldSpec)) (t : ArithmeticTerm) : Bool :=
  isValidFieldPath t.field &&
  (t.sign == 1 || t.sign == -1) &&
  match resolveSchemaFieldSpec fields t.field with
  | none => false
  | some termSpec =>
    match t.itemField with
    | some itemPath =>
      isValidFieldPath itemPath &&
      (termSpec.fieldType == .array) &&
      match termSpec.items with
      | none => false
      | some itemSpec =>
        (itemSpec.fieldType == .object) &&
        match itemSpec.properties with
        | none => false
        | some props =>
          !props.isEmpty &&
          match resolveSchemaFieldSpec props itemPath with
          | none => false
          | some nestedSpec => isNumericType nestedSpec.fieldType
    | none =>
      if termSpec.fieldType == .array then
        match termSpec.items with
        | none => false
        | some itemSpec => isNumericType itemSpec.fieldType
      else
        isNumericType termSpec.fieldType

theorem isValidArithmeticTerm_iff (fields : List (String × FieldSpec)) (t : ArithmeticTerm) :
    isValidArithmeticTerm fields t = true ↔ ValidArithmeticTerm fields t := by
  unfold isValidArithmeticTerm ValidArithmeticTerm
  cases resolveSchemaFieldSpec fields t.field with
  | none => simp
  | some termSpec =>
    cases t.itemField with
    | none =>
      by_cases harr : termSpec.fieldType = .array
      · cases hitems : termSpec.items <;> simp [harr, hitems, and_assoc]
      · simp [harr, and_assoc]
    | some itemPath =>
      cases hitems : termSpec.items with
      | none => simp [hitems]
      | some itemSpec =>
        cases hprops : itemSpec.properties with
        | none => simp [hitems, hprops]
        | some props =>
          cases hns : resolveSchemaFieldSpec props itemPath <;> simp [hitems, hprops, hns, and_assoc]

/-- Propositional well-formedness for an `ArithmeticRule` against `fields`. -/
def ValidArithmeticRule (fields : List (String × FieldSpec)) (r : ArithmeticRule) : Prop :=
  isNonBlank r.ruleId = true ∧
  OptNonBlank r.message ∧
  r.terms ≠ [] ∧
  isValidFieldPath r.targetField = true ∧
  r.tolerance ≥ 0 ∧
  (match resolveSchemaFieldSpec fields r.targetField with
   | none => False
   | some targetSpec => isNumericType targetSpec.fieldType = true) ∧
  (∀ t ∈ r.terms, ValidArithmeticTerm fields t)

def isValidArithmeticRule (fields : List (String × FieldSpec)) (r : ArithmeticRule) : Bool :=
  isNonBlank r.ruleId &&
  optNonBlank r.message &&
  !r.terms.isEmpty &&
  isValidFieldPath r.targetField &&
  decide (r.tolerance ≥ 0) &&
  (match resolveSchemaFieldSpec fields r.targetField with
   | none => false
   | some targetSpec => isNumericType targetSpec.fieldType) &&
  r.terms.all (isValidArithmeticTerm fields)

theorem isValidArithmeticRule_iff (fields : List (String × FieldSpec)) (r : ArithmeticRule) :
    isValidArithmeticRule fields r = true ↔ ValidArithmeticRule fields r := by
  unfold isValidArithmeticRule ValidArithmeticRule
  cases resolveSchemaFieldSpec fields r.targetField <;>
    simp [optNonBlank_iff, isValidArithmeticTerm_iff, and_assoc]

/-- Propositional well-formedness for a `FieldCondition` against `fields`. -/
def ValidFieldCondition (fields : List (String × FieldSpec)) (c : FieldCondition) : Prop :=
  isValidFieldPath c.field = true ∧
  match resolveSchemaFieldSpec fields c.field with
  | none => False
  | some condSpec =>
    match c.operator with
    | .isPresent | .isMissing =>
      c.value.isNone = true ∧ c.values.isNone = true
    | .equals | .notEquals =>
      c.values.isNone = true ∧
      match c.value with
      | none => False
      | some v => isLiteralCompatible v condSpec.fieldType = true
    | .inValues =>
      c.value.isNone = true ∧
      match c.values with
      | none => False
      | some vs => vs ≠ [] ∧ (∀ v ∈ vs, isLiteralCompatible v condSpec.fieldType = true)

def isValidFieldCondition (fields : List (String × FieldSpec)) (c : FieldCondition) : Bool :=
  isValidFieldPath c.field &&
  match resolveSchemaFieldSpec fields c.field with
  | none => false
  | some condSpec =>
    match c.operator with
    | .isPresent | .isMissing =>
      c.value.isNone && c.values.isNone
    | .equals | .notEquals =>
      c.values.isNone &&
      match c.value with
      | none => false
      | some v => isLiteralCompatible v condSpec.fieldType
    | .inValues =>
      c.value.isNone &&
      match c.values with
      | none => false
      | some vs => !vs.isEmpty && vs.all (fun v => isLiteralCompatible v condSpec.fieldType)

theorem isValidFieldCondition_iff (fields : List (String × FieldSpec)) (c : FieldCondition) :
    isValidFieldCondition fields c = true ↔ ValidFieldCondition fields c := by
  unfold isValidFieldCondition ValidFieldCondition
  cases resolveSchemaFieldSpec fields c.field with
  | none => simp
  | some condSpec =>
    cases c.operator with
    | isPresent | isMissing => simp
    | equals | notEquals => cases c.value <;> simp
    | inValues => cases c.values <;> simp

/-- Propositional well-formedness for a `ConditionalPresenceRule` against `fields`. -/
def ValidConditionalPresenceRule
    (fields : List (String × FieldSpec)) (r : ConditionalPresenceRule) : Prop :=
  isNonBlank r.ruleId = true ∧
  OptNonBlank r.message ∧
  ValidFieldCondition fields r.when ∧
  r.targetFields ≠ [] ∧
  r.targetFields.Pairwise (· ≠ ·) ∧
  ¬ (r.when.field ∈ r.targetFields) ∧
  (∀ tf ∈ r.targetFields,
    isValidFieldPath tf = true ∧ (resolveSchemaFieldSpec fields tf).isSome = true)

def isValidConditionalPresenceRule
    (fields : List (String × FieldSpec)) (r : ConditionalPresenceRule) : Bool :=
  isNonBlank r.ruleId &&
  optNonBlank r.message &&
  isValidFieldCondition fields r.when &&
  !r.targetFields.isEmpty &&
  allDistinct r.targetFields &&
  !r.targetFields.contains r.when.field &&
  r.targetFields.all (fun tf => isValidFieldPath tf && (resolveSchemaFieldSpec fields tf).isSome)

theorem isValidConditionalPresenceRule_iff
    (fields : List (String × FieldSpec)) (r : ConditionalPresenceRule) :
    isValidConditionalPresenceRule fields r = true ↔ ValidConditionalPresenceRule fields r := by
  simp [isValidConditionalPresenceRule, ValidConditionalPresenceRule, optNonBlank_iff,
    isValidFieldCondition_iff, allDistinct_iff, and_assoc]

/-- Propositional well-formedness for a `GroupPresenceRule` against `fields`. -/
def ValidGroupPresenceRule (fields : List (String × FieldSpec)) (r : GroupPresenceRule) : Prop :=
  isNonBlank r.ruleId = true ∧
  OptNonBlank r.message ∧
  r.fields.length ≥ 2 ∧
  r.fields.Pairwise (· ≠ ·) ∧
  (∀ f ∈ r.fields, isValidFieldPath f = true ∧ (resolveSchemaFieldSpec fields f).isSome = true)

def isValidGroupPresenceRule (fields : List (String × FieldSpec)) (r : GroupPresenceRule) : Bool :=
  isNonBlank r.ruleId &&
  optNonBlank r.message &&
  decide (r.fields.length ≥ 2) &&
  allDistinct r.fields &&
  r.fields.all (fun f => isValidFieldPath f && (resolveSchemaFieldSpec fields f).isSome)

theorem isValidGroupPresenceRule_iff (fields : List (String × FieldSpec)) (r : GroupPresenceRule) :
    isValidGroupPresenceRule fields r = true ↔ ValidGroupPresenceRule fields r := by
  simp [isValidGroupPresenceRule, ValidGroupPresenceRule, optNonBlank_iff,
    allDistinct_iff, and_assoc]

/-- Propositional well-formedness for any single `ValidationRule` against `fields`. -/
def ValidValidationRule (fields : List (String × FieldSpec)) : ValidationRule → Prop
  | .comparison r => ValidComparisonRule fields r
  | .arithmetic r => ValidArithmeticRule fields r
  | .conditionalPresence r => ValidConditionalPresenceRule fields r
  | .groupPresence r => ValidGroupPresenceRule fields r

def isValidValidationRule (fields : List (String × FieldSpec)) : ValidationRule → Bool
  | .comparison r => isValidComparisonRule fields r
  | .arithmetic r => isValidArithmeticRule fields r
  | .conditionalPresence r => isValidConditionalPresenceRule fields r
  | .groupPresence r => isValidGroupPresenceRule fields r

theorem isValidValidationRule_iff (fields : List (String × FieldSpec)) (r : ValidationRule) :
    isValidValidationRule fields r = true ↔ ValidValidationRule fields r := by
  cases r with
  | comparison c => exact isValidComparisonRule_iff fields c
  | arithmetic a => exact isValidArithmeticRule_iff fields a
  | conditionalPresence cp => exact isValidConditionalPresenceRule_iff fields cp
  | groupPresence gp => exact isValidGroupPresenceRule_iff fields gp

/-- Schema-level rule collection well-formedness matching `validate_schema_rules` (`schema/rules.py:427`). -/
def ValidSchemaRules (fields : List (String × FieldSpec)) (rules : List ValidationRule) : Prop :=
  (rules.map ValidationRule.ruleId).Pairwise (· ≠ ·) ∧
  (∀ r ∈ rules, ValidValidationRule fields r)

def isValidSchemaRules (fields : List (String × FieldSpec)) (rules : List ValidationRule) : Bool :=
  allDistinct (rules.map ValidationRule.ruleId) &&
  rules.all (isValidValidationRule fields)

theorem isValidSchemaRules_iff (fields : List (String × FieldSpec)) (rules : List ValidationRule) :
    isValidSchemaRules fields rules = true ↔ ValidSchemaRules fields rules := by
  simp [isValidSchemaRules, ValidSchemaRules, allDistinct_iff, isValidValidationRule_iff]

/-- Combined schema + cross-field rules well-formedness (`ExtractionSchema` + `rules`). -/
def ValidSchemaWithRules (s : ExtractionSchema) (rules : List ValidationRule) : Prop :=
  ValidExtractionSchema s ∧ ValidSchemaRules s.fields rules

def isValidSchemaWithRules (s : ExtractionSchema) (rules : List ValidationRule) : Bool :=
  isValidExtractionSchema s && isValidSchemaRules s.fields rules

theorem isValidSchemaWithRules_iff (s : ExtractionSchema) (rules : List ValidationRule) :
    isValidSchemaWithRules s rules = true ↔ ValidSchemaWithRules s rules := by
  simp [isValidSchemaWithRules, ValidSchemaWithRules, isValidExtractionSchema_iff,
    isValidSchemaRules_iff]

end StructaFormal
