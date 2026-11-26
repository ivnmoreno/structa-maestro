/-
  Structa Formal Assurance Layer — Declarative Cross-Field & Conditional Rule Invariants.
  Authoritative Python source: `src/structa_maestro/schema/rules.py`.
-/

import StructaFormal.CrossFieldModels

namespace StructaFormal

/-- Every well-formed schema rule collection has pairwise unique, non-blank rule identifiers. -/
theorem schema_rules_unique_nonblank_ids
    (fields : List (String × FieldSpec)) (rules : List ValidationRule)
    (hvalid : ValidSchemaRules fields rules) :
    (rules.map ValidationRule.ruleId).Pairwise (· ≠ ·) ∧
    (∀ r ∈ rules, isNonBlank r.ruleId = true) := by
  refine ⟨hvalid.1, ?_⟩
  intro r hr
  have hv := hvalid.2 r hr
  cases r with
  | comparison c => exact hv.1
  | arithmetic a => exact hv.1
  | conditionalPresence cp => exact hv.1
  | groupPresence gp => exact hv.1

/-- Every well-formed `ComparisonRule` compares two distinct, resolvable, type-compatible fields. -/
theorem comparison_rule_distinct_compatible_fields
    (fields : List (String × FieldSpec)) (r : ComparisonRule)
    (hvalid : ValidComparisonRule fields r) :
    r.leftField ≠ r.rightField ∧
    ∃ lspec rspec,
      resolveSchemaFieldSpec fields r.leftField = some lspec ∧
      resolveSchemaFieldSpec fields r.rightField = some rspec ∧
      typesCompatibleForComparison lspec.fieldType rspec.fieldType r.operator = true := by
  refine ⟨hvalid.2.2.2.2.1, ?_⟩
  have hres := hvalid.2.2.2.2.2
  cases hl : resolveSchemaFieldSpec fields r.leftField with
  | none =>
    rw [hl] at hres
    exact False.elim hres
  | some lspec =>
    cases hr : resolveSchemaFieldSpec fields r.rightField with
    | none =>
      rw [hl, hr] at hres
      exact False.elim hres
    | some rspec =>
      rw [hl, hr] at hres
      exact ⟨lspec, rspec, rfl, rfl, hres⟩

/-- Ordered comparison operators (`lt`, `lte`, `gt`, `gte`) are always rejected on `BOOLEAN`. -/
theorem comparison_rule_forbids_ordered_boolean
    (lt rt : FieldType) (op : ComparisonOperator)
    (hord : isOrderedComparisonOp op = true)
    (hbool : lt = .boolean ∨ rt = .boolean) :
    typesCompatibleForComparison lt rt op = false := by
  cases hbool with
  | inl hl =>
    subst hl
    cases rt <;> cases op <;> simp_all [isOrderedComparisonOp, typesCompatibleForComparison,
      isScalarComparableType]
  | inr hr =>
    subst hr
    cases lt <;> cases op <;> simp_all [isOrderedComparisonOp, typesCompatibleForComparison,
      isScalarComparableType]

/-- Every well-formed `ArithmeticRule` has non-empty terms, unit signs (`±1`), and `tolerance >= 0`. -/
theorem arithmetic_rule_nonempty_unit_signs_nonneg_tol
    (fields : List (String × FieldSpec)) (r : ArithmeticRule)
    (hvalid : ValidArithmeticRule fields r) :
    r.terms ≠ [] ∧
    r.tolerance ≥ 0 ∧
    (∀ t ∈ r.terms, t.sign = 1 ∨ t.sign = -1) := by
  refine ⟨hvalid.2.2.1, hvalid.2.2.2.2.1, ?_⟩
  intro t ht
  exact (hvalid.2.2.2.2.2.2 t ht).2.1

/-- Every well-formed `ArithmeticRule` resolves `targetField` to a numeric type and every term
    to a numeric scalar, numeric primitive array, or array-of-objects numeric `itemField`. -/
theorem arithmetic_rule_target_and_terms_numeric
    (fields : List (String × FieldSpec)) (r : ArithmeticRule)
    (hvalid : ValidArithmeticRule fields r) :
    (∃ targetSpec,
      resolveSchemaFieldSpec fields r.targetField = some targetSpec ∧
      isNumericType targetSpec.fieldType = true) ∧
    (∀ t ∈ r.terms,
      ∃ termSpec, resolveSchemaFieldSpec fields t.field = some termSpec ∧
        match t.itemField with
        | some itemPath =>
          termSpec.fieldType = .array ∧
          ∃ itemSpec props nestedSpec,
            termSpec.items = some itemSpec ∧
            itemSpec.fieldType = .object ∧
            itemSpec.properties = some props ∧
            props ≠ [] ∧
            resolveSchemaFieldSpec props itemPath = some nestedSpec ∧
            isNumericType nestedSpec.fieldType = true
        | none =>
          if termSpec.fieldType = .array then
            ∃ itemSpec, termSpec.items = some itemSpec ∧ isNumericType itemSpec.fieldType = true
          else
            isNumericType termSpec.fieldType = true) := by
  constructor
  · have ht := hvalid.2.2.2.2.2.1
    cases hres : resolveSchemaFieldSpec fields r.targetField with
    | none => rw [hres] at ht; exact False.elim ht
    | some ts => rw [hres] at ht; exact ⟨ts, rfl, ht⟩
  · intro t ht
    have hv := (hvalid.2.2.2.2.2.2 t ht).2.2
    cases hres : resolveSchemaFieldSpec fields t.field with
    | none => rw [hres] at hv; exact False.elim hv
    | some termSpec =>
      rw [hres] at hv
      refine ⟨termSpec, rfl, ?_⟩
      cases hif : t.itemField with
      | none =>
        rw [hif] at hv
        by_cases harr : termSpec.fieldType = .array
        · simp only [harr, ↓reduceIte] at hv ⊢
          cases hit : termSpec.items with
          | none => rw [hit] at hv; exact False.elim hv
          | some itemSpec => rw [hit] at hv; exact ⟨itemSpec, rfl, hv⟩
        · simp only [harr, ↓reduceIte] at hv ⊢
          exact hv
      | some itemPath =>
        rw [hif] at hv
        refine ⟨hv.2.1, ?_⟩
        have hv2 := hv.2.2
        cases hit : termSpec.items with
        | none => rw [hit] at hv2; exact False.elim hv2
        | some itemSpec =>
          rw [hit] at hv2
          have hv3 := hv2.2
          cases hps : itemSpec.properties with
          | none => rw [hps] at hv3; exact False.elim hv3
          | some props =>
            rw [hps] at hv3
            have hv4 := hv3.2
            cases hns : resolveSchemaFieldSpec props itemPath with
            | none => rw [hns] at hv4; exact False.elim hv4
            | some nestedSpec =>
              rw [hns] at hv4
              exact ⟨itemSpec, props, nestedSpec, rfl, hv2.1, hps, hv3.1, hns, hv4⟩

/-- Every well-formed `FieldCondition` enforces operator-specific `value`/`values` consistency
    and literal compatibility with the resolved controlling field type. -/
theorem conditional_rule_condition_value_consistency
    (fields : List (String × FieldSpec)) (c : FieldCondition)
    (hvalid : ValidFieldCondition fields c) :
    ∃ condSpec, resolveSchemaFieldSpec fields c.field = some condSpec ∧
      ((c.operator = .isPresent ∨ c.operator = .isMissing) →
        c.value = none ∧ c.values = none) ∧
      ((c.operator = .equals ∨ c.operator = .notEquals) →
        c.values = none ∧ ∃ v, c.value = some v ∧ isLiteralCompatible v condSpec.fieldType = true) ∧
      (c.operator = .inValues →
        c.value = none ∧ ∃ vs, c.values = some vs ∧ vs ≠ [] ∧
          ∀ v ∈ vs, isLiteralCompatible v condSpec.fieldType = true) := by
  have hv := hvalid.2
  cases hres : resolveSchemaFieldSpec fields c.field with
  | none => rw [hres] at hv; exact False.elim hv
  | some condSpec =>
    rw [hres] at hv
    refine ⟨condSpec, rfl, ?_, ?_, ?_⟩
    · intro hop
      cases hop with
      | inl h =>
        rw [h] at hv
        exact ⟨Option.isNone_iff_eq_none.mp hv.1, Option.isNone_iff_eq_none.mp hv.2⟩
      | inr h =>
        rw [h] at hv
        exact ⟨Option.isNone_iff_eq_none.mp hv.1, Option.isNone_iff_eq_none.mp hv.2⟩
    · intro hop
      cases hop with
      | inl h =>
        rw [h] at hv
        refine ⟨Option.isNone_iff_eq_none.mp hv.1, ?_⟩
        cases hval : c.value with
        | none => rw [hval] at hv; exact False.elim hv.2
        | some v => rw [hval] at hv; exact ⟨v, rfl, hv.2⟩
      | inr h =>
        rw [h] at hv
        refine ⟨Option.isNone_iff_eq_none.mp hv.1, ?_⟩
        cases hval : c.value with
        | none => rw [hval] at hv; exact False.elim hv.2
        | some v => rw [hval] at hv; exact ⟨v, rfl, hv.2⟩
    · intro hop
      rw [hop] at hv
      refine ⟨Option.isNone_iff_eq_none.mp hv.1, ?_⟩
      cases hvals : c.values with
      | none => rw [hvals] at hv; exact False.elim hv.2
      | some vs => rw [hvals] at hv; exact ⟨vs, rfl, hv.2.1, hv.2.2⟩

/-- Every well-formed `ConditionalPresenceRule` declares non-empty, pairwise-unique resolvable
    `targetFields` disjoint from the controlling field `when.field`. -/
theorem conditional_rule_nonempty_unique_targets_disjoint_from_when
    (fields : List (String × FieldSpec)) (r : ConditionalPresenceRule)
    (hvalid : ValidConditionalPresenceRule fields r) :
    r.targetFields ≠ [] ∧
    r.targetFields.Pairwise (· ≠ ·) ∧
    ¬ (r.when.field ∈ r.targetFields) ∧
    (∀ tf ∈ r.targetFields, (resolveSchemaFieldSpec fields tf).isSome = true) := by
  refine ⟨hvalid.2.2.2.1, hvalid.2.2.2.2.1, hvalid.2.2.2.2.2.1, ?_⟩
  intro tf htf
  exact (hvalid.2.2.2.2.2.2 tf htf).2

/-- Every well-formed `GroupPresenceRule` declares at least two pairwise-distinct, resolvable fields. -/
theorem group_rule_at_least_two_distinct_resolvable_fields
    (fields : List (String × FieldSpec)) (r : GroupPresenceRule)
    (hvalid : ValidGroupPresenceRule fields r) :
    r.fields.length ≥ 2 ∧
    r.fields.Pairwise (· ≠ ·) ∧
    (∀ f ∈ r.fields, (resolveSchemaFieldSpec fields f).isSome = true) := by
  refine ⟨hvalid.2.2.1, hvalid.2.2.2.1, ?_⟩
  intro f hf
  exact (hvalid.2.2.2.2 f hf).2

/-- Concrete malformed cross-field rules (self-comparison, boolean ordered comparison, negative
    tolerance, self-targeting conditional rule, and single-field group rule) are provably rejected. -/
theorem invalid_cross_field_rules_rejected :
    let fields : List (String × FieldSpec) := [
      ("a", FieldSpec.mk "a" .integer true false {} none none false),
      ("b", FieldSpec.mk "b" .boolean true false {} none none false),
      ("c", FieldSpec.mk "c" .boolean true false {} none none false)
    ]
    ¬ ValidValidationRule fields (.comparison {
        ruleId := "r1", leftField := "a", operator := .lt, rightField := "a"
      }) ∧
    ¬ ValidValidationRule fields (.comparison {
        ruleId := "r2", leftField := "b", operator := .lt, rightField := "c"
      }) ∧
    ¬ ValidValidationRule fields (.arithmetic {
        ruleId := "r3", terms := [{ field := "a", sign := 1 }],
        targetField := "a", tolerance := (-1 : Rat)
      }) ∧
    ¬ ValidValidationRule fields (.conditionalPresence {
        ruleId := "r4", kind := .requiredIf,
        when := { field := "a", operator := .isPresent },
        targetFields := ["a"]
      }) ∧
    ¬ ValidValidationRule fields (.groupPresence {
        ruleId := "r5", kind := .mutuallyExclusive, fields := ["a"]
      }) := by
  refine ⟨?_, ?_, ?_, ?_, ?_⟩
  · intro h
    unfold ValidValidationRule at h
    exact (comparison_rule_distinct_compatible_fields _ _ h).1 rfl
  · intro h
    unfold ValidValidationRule at h
    have hcomp := (comparison_rule_distinct_compatible_fields _ _ h).2
    rcases hcomp with ⟨ls, rs, hls, hrs, hcompat⟩
    have hls' : ls = FieldSpec.mk "b" .boolean true false {} none none false := by
      have : resolveSchemaFieldSpec
          [("a", FieldSpec.mk "a" .integer true false {} none none false),
           ("b", FieldSpec.mk "b" .boolean true false {} none none false),
           ("c", FieldSpec.mk "c" .boolean true false {} none none false)] "b" =
          some (FieldSpec.mk "b" .boolean true false {} none none false) := rfl
      rw [this] at hls
      exact Option.some.inj hls.symm
    subst hls'
    change typesCompatibleForComparison .boolean rs.fieldType .lt = true at hcompat
    have hfalse := comparison_rule_forbids_ordered_boolean .boolean rs.fieldType .lt rfl (Or.inl rfl)
    rw [hfalse] at hcompat
    cases hcompat
  · intro h
    unfold ValidValidationRule at h
    have htol : (-1 : Rat) ≥ 0 := (arithmetic_rule_nonempty_unit_signs_nonneg_tol _ _ h).2.1
    exact absurd htol (by decide)
  · intro h
    unfold ValidValidationRule at h
    exact (conditional_rule_nonempty_unique_targets_disjoint_from_when _ _ h).2.2.1 List.mem_cons_self
  · intro h
    unfold ValidValidationRule at h
    have hlen : (["a"] : List String).length ≥ 2 :=
      (group_rule_at_least_two_distinct_resolvable_fields _ _ h).1
    exact absurd hlen (by decide)

end StructaFormal
