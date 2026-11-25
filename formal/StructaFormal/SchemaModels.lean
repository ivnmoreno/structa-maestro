/-
  Structa Formal Assurance Layer — ExtractionSchema, FieldSpec, FieldConstraints & RuntimeValue.
  Authoritative Python sources:
  - `src/structa_maestro/schema/models.py` (`FieldConstraints`, `FieldSpec`, `ExtractionSchema`)
  - `src/structa_maestro/validation/validator.py` (`_value_in_allowed` runtime value equality)
-/

import StructaFormal.Enums
import StructaFormal.Models

namespace StructaFormal

/-- Typed runtime value domain faithfully separating Python types for strict non-coercion. -/
inductive RuntimeValue where
  | nullVal
  | strVal (s : String)
  | intVal (n : Int)
  | decVal (q : Rat)
  | nonFiniteDecVal
  | boolVal (b : Bool)
  | dateVal (iso : String)
  | datetimeVal
  | arrayVal (elems : List RuntimeValue)
  | objectVal (entries : List (String × RuntimeValue))
  | otherVal
  deriving Repr

mutual
  /-- Value equality matching `validator.py:_value_in_allowed`:
      `boolVal` never equals `intVal`/`decVal`; `intVal n` equals `decVal q` iff `(n : Rat) = q`. -/
  def runtimeValueEq : RuntimeValue → RuntimeValue → Bool
    | .nullVal, .nullVal => true
    | .strVal a, .strVal b => a == b
    | .intVal a, .intVal b => a == b
    | .intVal a, .decVal b => (a : Rat) == b
    | .decVal a, .intVal b => a == (b : Rat)
    | .decVal a, .decVal b => a == b
    | .boolVal a, .boolVal b => a == b
    | .dateVal a, .dateVal b => a == b
    | .arrayVal xs, .arrayVal ys => runtimeValueListEq xs ys
    | .objectVal xs, .objectVal ys => runtimeValueMapEq xs ys
    | _, _ => false

  def runtimeValueListEq : List RuntimeValue → List RuntimeValue → Bool
    | [], [] => true
    | x :: xs, y :: ys => runtimeValueEq x y && runtimeValueListEq xs ys
    | _, _ => false

  def runtimeValueMapEq : List (String × RuntimeValue) → List (String × RuntimeValue) → Bool
    | [], [] => true
    | (k1, v1) :: xs, (k2, v2) :: ys =>
      (k1 == k2) && runtimeValueEq v1 v2 && runtimeValueMapEq xs ys
    | _, _ => false
end

/-- Optional single-field validation constraints (`FieldConstraints` in `schema/models.py`). -/
structure FieldConstraints where
  minValue : Option Rat := none
  maxValue : Option Rat := none
  minLength : Option Int := none
  maxLength : Option Int := none
  minItems : Option Int := none
  maxItems : Option Int := none
  pattern : Option String := none
  allowedValues : Option (List RuntimeValue) := none
  deriving Repr

def ValidFieldConstraints (c : FieldConstraints) : Prop :=
  (∀ lo hi, c.minValue = some lo → c.maxValue = some hi → lo ≤ hi) ∧
  (∀ n, c.minLength = some n → n ≥ 0) ∧
  (∀ n, c.maxLength = some n → n ≥ 0) ∧
  (∀ lo hi, c.minLength = some lo → c.maxLength = some hi → lo ≤ hi) ∧
  (∀ n, c.minItems = some n → n ≥ 0) ∧
  (∀ n, c.maxItems = some n → n ≥ 0) ∧
  (∀ lo hi, c.minItems = some lo → c.maxItems = some hi → lo ≤ hi) ∧
  (∀ xs, c.allowedValues = some xs → xs ≠ [])

private def optRatLe (lo hi : Option Rat) : Bool :=
  match lo, hi with
  | some a, some b => decide (a ≤ b)
  | _, _ => true

private theorem optRatLe_iff (lo hi : Option Rat) :
    optRatLe lo hi = true ↔ (∀ a b, lo = some a → hi = some b → a ≤ b) := by
  cases lo <;> cases hi <;> simp [optRatLe]

private def optIntGe0 (x : Option Int) : Bool :=
  match x with
  | none => true
  | some n => decide (n ≥ 0)

private theorem optIntGe0_iff (x : Option Int) :
    optIntGe0 x = true ↔ (∀ n, x = some n → n ≥ 0) := by
  cases x <;> simp [optIntGe0]

private def optIntLe (lo hi : Option Int) : Bool :=
  match lo, hi with
  | some a, some b => decide (a ≤ b)
  | _, _ => true

private theorem optIntLe_iff (lo hi : Option Int) :
    optIntLe lo hi = true ↔ (∀ a b, lo = some a → hi = some b → a ≤ b) := by
  cases lo <;> cases hi <;> simp [optIntLe]

private def optNonEmptyList {α : Type} (xs : Option (List α)) : Bool :=
  match xs with
  | none => true
  | some l => !l.isEmpty

private theorem optNonEmptyList_iff {α : Type} (xs : Option (List α)) :
    optNonEmptyList xs = true ↔ (∀ l, xs = some l → l ≠ []) := by
  cases xs with
  | none => simp [optNonEmptyList]
  | some l =>
    cases l <;> simp [optNonEmptyList]

def isValidFieldConstraints (c : FieldConstraints) : Bool :=
  optRatLe c.minValue c.maxValue &&
  optIntGe0 c.minLength &&
  optIntGe0 c.maxLength &&
  optIntLe c.minLength c.maxLength &&
  optIntGe0 c.minItems &&
  optIntGe0 c.maxItems &&
  optIntLe c.minItems c.maxItems &&
  optNonEmptyList c.allowedValues

theorem isValidFieldConstraints_iff (c : FieldConstraints) :
    isValidFieldConstraints c = true ↔ ValidFieldConstraints c := by
  simp [isValidFieldConstraints, ValidFieldConstraints, optRatLe_iff, optIntGe0_iff,
    optIntLe_iff, optNonEmptyList_iff, and_assoc]

/-- Recursive definition of a single schema field (`FieldSpec` in `schema/models.py`). -/
inductive FieldSpec where
  | mk
    (name : String)
    (fieldType : FieldType)
    (required : Bool)
    (nullable : Bool)
    (constraints : FieldConstraints)
    (properties : Option (List (String × FieldSpec)))
    (items : Option FieldSpec)
    (allowAdditionalFields : Bool)
  deriving Repr

def FieldSpec.name : FieldSpec → String
  | .mk n _ _ _ _ _ _ _ => n

def FieldSpec.fieldType : FieldSpec → FieldType
  | .mk _ ft _ _ _ _ _ _ => ft

def FieldSpec.required : FieldSpec → Bool
  | .mk _ _ req _ _ _ _ _ => req

def FieldSpec.nullable : FieldSpec → Bool
  | .mk _ _ _ nul _ _ _ _ => nul

def FieldSpec.constraints : FieldSpec → FieldConstraints
  | .mk _ _ _ _ c _ _ _ => c

def FieldSpec.properties : FieldSpec → Option (List (String × FieldSpec))
  | .mk _ _ _ _ _ props _ _ => props

def FieldSpec.items : FieldSpec → Option FieldSpec
  | .mk _ _ _ _ _ _ it _ => it

def FieldSpec.allowAdditionalFields : FieldSpec → Bool
  | .mk _ _ _ _ _ _ _ aaf => aaf

/-- Type-to-constraint compatibility matching `FieldSpec.__post_init__` (`schema/models.py:116-134`). -/
def TypeConstraintCompatible (ft : FieldType) (c : FieldConstraints) : Prop :=
  ((c.minValue.isSome = true ∨ c.maxValue.isSome = true) → (ft = .integer ∨ ft = .decimal)) ∧
  ((c.minLength.isSome = true ∨ c.maxLength.isSome = true ∨ c.pattern.isSome = true) → ft = .string) ∧
  ((c.minItems.isSome = true ∨ c.maxItems.isSome = true) → ft = .array)

def isTypeConstraintCompatible (ft : FieldType) (c : FieldConstraints) : Bool :=
  let numOk :=
    if c.minValue.isSome || c.maxValue.isSome then
      (ft == .integer) || (ft == .decimal)
    else
      true
  let strOk :=
    if c.minLength.isSome || c.maxLength.isSome || c.pattern.isSome then
      ft == .string
    else
      true
  let arrOk :=
    if c.minItems.isSome || c.maxItems.isSome then
      ft == .array
    else
      true
  numOk && strOk && arrOk

theorem isTypeConstraintCompatible_iff (ft : FieldType) (c : FieldConstraints) :
    isTypeConstraintCompatible ft c = true ↔ TypeConstraintCompatible ft c := by
  unfold isTypeConstraintCompatible TypeConstraintCompatible
  cases c.minValue <;> cases c.maxValue <;> cases c.minLength <;> cases c.maxLength <;>
    cases c.pattern <;> cases c.minItems <;> cases c.maxItems <;> simp [and_assoc]

mutual
  /-- Recursive nesting depth matching `_validate_depth` (`schema/models.py:248-269`), 1 for leaf. -/
  def fieldDepth : FieldSpec → Nat
    | .mk _ _ _ _ _ props items _ =>
      let propD := match props with
        | none => 0
        | some ps => fieldPropListDepth ps
      let itemD := match items with
        | none => 0
        | some it => fieldDepth it
      1 + max propD itemD

  def fieldPropListDepth : List (String × FieldSpec) → Nat
    | [] => 0
    | (_, sp) :: rest => max (fieldDepth sp) (fieldPropListDepth rest)
end

/-- Pure kernel-reducible non-blank string predicate matching Python `bool(s.strip())` on ASCII
    whitespace (`' '`, `'\t'`, `'\n'`, `'\r'`). -/
def isNonBlank (s : String) : Bool :=
  s.toList.any (fun c => !c.isWhitespace)

mutual
  /-- Propositional well-formedness invariant matching `FieldSpec.__post_init__`. -/
  def ValidFieldSpec : FieldSpec → Prop
    | .mk name ft _ _ c props items _ =>
      isNonBlank name = true ∧
      ValidFieldConstraints c ∧
      TypeConstraintCompatible ft c ∧
      (if ft = .object then
        match props with
        | none => False
        | some ps =>
          ps ≠ [] ∧ (ps.map Prod.fst).Pairwise (· ≠ ·) ∧ ValidFieldSpecProps ps
      else
        props.isNone = true) ∧
      (if ft = .array then
        match items with
        | none => False
        | some it => ValidFieldSpec it
      else
        items.isNone = true)

  def ValidFieldSpecProps : List (String × FieldSpec) → Prop
    | [] => True
    | (k, sp) :: rest => sp.name = k ∧ ValidFieldSpec sp ∧ ValidFieldSpecProps rest
end

theorem validFieldSpecProps_iff_forall (ps : List (String × FieldSpec)) :
    ValidFieldSpecProps ps ↔ (∀ kv ∈ ps, kv.2.name = kv.1 ∧ ValidFieldSpec kv.2) := by
  induction ps with
  | nil => simp [ValidFieldSpecProps]
  | cons kv rest ih =>
    cases kv with
    | mk k sp =>
      simp [ValidFieldSpecProps, ih, and_assoc]

mutual
  /-- Decidable well-formedness check for `FieldSpec`. -/
  def isValidFieldSpec : FieldSpec → Bool
    | .mk name ft _ _ c props items _ =>
      isNonBlank name &&
      isValidFieldConstraints c &&
      isTypeConstraintCompatible ft c &&
      (if ft == .object then
        match props with
        | none => false
        | some ps => !ps.isEmpty && allDistinct (ps.map Prod.fst) && isValidFieldSpecProps ps
      else
        props.isNone) &&
      (if ft == .array then
        match items with
        | none => false
        | some it => isValidFieldSpec it
      else
        items.isNone)

  def isValidFieldSpecProps : List (String × FieldSpec) → Bool
    | [] => true
    | (k, sp) :: rest => (sp.name == k) && isValidFieldSpec sp && isValidFieldSpecProps rest
end

mutual
  theorem isValidFieldSpec_iff : ∀ sp : FieldSpec,
      isValidFieldSpec sp = true ↔ ValidFieldSpec sp
    | .mk name ft req nul c props items aaf => by
      unfold isValidFieldSpec ValidFieldSpec
      have hProps :
          (if ft == .object then
            match props with
            | none => false
            | some ps => !ps.isEmpty && allDistinct (ps.map Prod.fst) && isValidFieldSpecProps ps
          else
            props.isNone) = true ↔
          (if ft = .object then
            match props with
            | none => False
            | some ps => ps ≠ [] ∧ (ps.map Prod.fst).Pairwise (· ≠ ·) ∧ ValidFieldSpecProps ps
          else
            props.isNone = true) := by
        by_cases hobj : ft = .object
        · subst hobj
          cases props with
          | none => simp
          | some ps =>
            simp only [beq_self_eq_true, ↓reduceIte, Bool.and_eq_true, Bool.not_eq_true',
              List.isEmpty_eq_false_iff, allDistinct_iff, isValidFieldSpecProps_iff, and_assoc]
        · simp [hobj]
      have hItems :
          (if ft == .array then
            match items with
            | none => false
            | some it => isValidFieldSpec it
          else
            items.isNone) = true ↔
          (if ft = .array then
            match items with
            | none => False
            | some it => ValidFieldSpec it
          else
            items.isNone = true) := by
        by_cases harr : ft = .array
        · subst harr
          cases items with
          | none => simp
          | some it =>
            simp [isValidFieldSpec_iff it]
        · simp [harr]
      simp only [Bool.and_eq_true, isValidFieldConstraints_iff,
        isTypeConstraintCompatible_iff, hProps, hItems, and_assoc]

  theorem isValidFieldSpecProps_iff : ∀ ps : List (String × FieldSpec),
      isValidFieldSpecProps ps = true ↔ ValidFieldSpecProps ps
    | [] => by simp [isValidFieldSpecProps, ValidFieldSpecProps]
    | (k, sp) :: rest => by
      simp [isValidFieldSpecProps, ValidFieldSpecProps, isValidFieldSpec_iff sp,
        isValidFieldSpecProps_iff rest, and_assoc]
end

/-- User-defined structured extraction schema (`ExtractionSchema` in `schema/models.py`). -/
structure ExtractionSchema where
  schemaId : String
  schemaVersion : String
  fields : List (String × FieldSpec)
  cardinality : ExtractionCardinality := .single
  allowAdditionalFields : Bool := false
  maxDepth : Int := 16
  deriving Repr

/-- Propositional well-formedness invariant matching `ExtractionSchema.__post_init__`. -/
def ValidExtractionSchema (s : ExtractionSchema) : Prop :=
  isNonBlank s.schemaId = true ∧
  isNonBlank s.schemaVersion = true ∧
  s.maxDepth ≥ 1 ∧
  s.fields ≠ [] ∧
  (s.fields.map Prod.fst).Pairwise (· ≠ ·) ∧
  (∀ kv ∈ s.fields,
    kv.2.name = kv.1 ∧ ValidFieldSpec kv.2 ∧ fieldDepth kv.2 ≤ s.maxDepth.toNat)

def isValidExtractionSchema (s : ExtractionSchema) : Bool :=
  isNonBlank s.schemaId &&
  isNonBlank s.schemaVersion &&
  decide (s.maxDepth ≥ 1) &&
  !s.fields.isEmpty &&
  allDistinct (s.fields.map Prod.fst) &&
  s.fields.all fun kv =>
    (kv.2.name == kv.1) && isValidFieldSpec kv.2 && decide (fieldDepth kv.2 ≤ s.maxDepth.toNat)

theorem isValidExtractionSchema_iff (s : ExtractionSchema) :
    isValidExtractionSchema s = true ↔ ValidExtractionSchema s := by
  simp [isValidExtractionSchema, ValidExtractionSchema, allDistinct_iff,
    isValidFieldSpec_iff, and_assoc]

end StructaFormal
