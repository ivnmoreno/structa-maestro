/-
  Structa Formal Assurance Layer — ExtractionSchema Well-Formedness Invariants.
  Authoritative Python source: `src/structa_maestro/schema/models.py`.
-/

import StructaFormal.SchemaModels

namespace StructaFormal

/-- Every well-formed `FieldConstraints` enforces ordering, non-negativity, and non-empty enums. -/
theorem schema_constraints_ordering_and_nonneg
    (c : FieldConstraints) (hvalid : ValidFieldConstraints c) :
    (∀ lo hi, c.minValue = some lo → c.maxValue = some hi → lo ≤ hi) ∧
    (∀ n, c.minLength = some n → n ≥ 0) ∧
    (∀ n, c.maxLength = some n → n ≥ 0) ∧
    (∀ lo hi, c.minLength = some lo → c.maxLength = some hi → lo ≤ hi) ∧
    (∀ n, c.minItems = some n → n ≥ 0) ∧
    (∀ n, c.maxItems = some n → n ≥ 0) ∧
    (∀ lo hi, c.minItems = some lo → c.maxItems = some hi → lo ≤ hi) ∧
    (∀ xs, c.allowedValues = some xs → xs ≠ []) :=
  hvalid

/-- Numeric value bounds (`minValue`/`maxValue`) are permitted only on `INTEGER` or `DECIMAL`. -/
theorem schema_numeric_constraints_require_numeric_type
    (sp : FieldSpec) (hvalid : ValidFieldSpec sp)
    (hnum : sp.constraints.minValue.isSome = true ∨ sp.constraints.maxValue.isSome = true) :
    sp.fieldType = .integer ∨ sp.fieldType = .decimal := by
  cases sp with
  | mk name ft req nul c props items aaf =>
    unfold ValidFieldSpec at hvalid
    exact hvalid.2.2.1.1 hnum

/-- String constraints (`minLength`/`maxLength`/`pattern`) are permitted only on `STRING`. -/
theorem schema_string_constraints_require_string_type
    (sp : FieldSpec) (hvalid : ValidFieldSpec sp)
    (hstr : sp.constraints.minLength.isSome = true ∨
      sp.constraints.maxLength.isSome = true ∨
      sp.constraints.pattern.isSome = true) :
    sp.fieldType = .string := by
  cases sp with
  | mk name ft req nul c props items aaf =>
    unfold ValidFieldSpec at hvalid
    exact hvalid.2.2.1.2.1 hstr

/-- Array item bounds (`minItems`/`maxItems`) are permitted only on `ARRAY`. -/
theorem schema_array_constraints_require_array_type
    (sp : FieldSpec) (hvalid : ValidFieldSpec sp)
    (harr : sp.constraints.minItems.isSome = true ∨ sp.constraints.maxItems.isSome = true) :
    sp.fieldType = .array := by
  cases sp with
  | mk name ft req nul c props items aaf =>
    unfold ValidFieldSpec at hvalid
    exact hvalid.2.2.1.2.2 harr

/-- Well-formed `OBJECT` fields require non-empty, uniquely-keyed, matching, valid `properties`,
    while non-`OBJECT` fields forbid `properties`. -/
theorem schema_object_requires_nonempty_matching_properties
    (sp : FieldSpec) (hvalid : ValidFieldSpec sp) :
    (sp.fieldType = .object →
      ∃ ps, sp.properties = some ps ∧ ps ≠ [] ∧
        (ps.map Prod.fst).Pairwise (· ≠ ·) ∧
        (∀ kv ∈ ps, kv.2.name = kv.1 ∧ ValidFieldSpec kv.2)) ∧
    (sp.fieldType ≠ .object → sp.properties = none) := by
  cases sp with
  | mk name ft req nul c props items aaf =>
    unfold ValidFieldSpec at hvalid
    have hObj := hvalid.2.2.2.1
    constructor
    · intro hft
      simp only [FieldSpec.fieldType, FieldSpec.properties] at hft ⊢
      simp only [hft, ↓reduceIte] at hObj
      cases props with
      | none => exact False.elim hObj
      | some ps =>
        refine ⟨ps, rfl, hObj.1, hObj.2.1, (validFieldSpecProps_iff_forall ps).mp hObj.2.2⟩
    · intro hne
      simp only [FieldSpec.fieldType, FieldSpec.properties] at hne ⊢
      simp only [hne, ↓reduceIte] at hObj
      cases props with
      | none => rfl
      | some _ => simp at hObj

/-- Well-formed `ARRAY` fields require valid `items`, while non-`ARRAY` fields forbid `items`. -/
theorem schema_array_requires_valid_items
    (sp : FieldSpec) (hvalid : ValidFieldSpec sp) :
    (sp.fieldType = .array → ∃ it, sp.items = some it ∧ ValidFieldSpec it) ∧
    (sp.fieldType ≠ .array → sp.items = none) := by
  cases sp with
  | mk name ft req nul c props items aaf =>
    unfold ValidFieldSpec at hvalid
    have hArr := hvalid.2.2.2.2
    constructor
    · intro hft
      simp only [FieldSpec.fieldType, FieldSpec.items] at hft ⊢
      simp only [hft, ↓reduceIte] at hArr
      cases items with
      | none => exact False.elim hArr
      | some it => exact ⟨it, rfl, hArr⟩
    · intro hne
      simp only [FieldSpec.fieldType, FieldSpec.items] at hne ⊢
      simp only [hne, ↓reduceIte] at hArr
      cases items with
      | none => rfl
      | some _ => simp at hArr

/-- Every well-formed `ExtractionSchema` has non-blank identities, `maxDepth >= 1`, at least one
    field, pairwise unique field keys matching `spec.name`, and nesting depth bounded by `maxDepth`. -/
theorem schema_nonempty_unique_fields_and_identities
    (s : ExtractionSchema) (hvalid : ValidExtractionSchema s) :
    isNonBlank s.schemaId = true ∧
    isNonBlank s.schemaVersion = true ∧
    s.maxDepth ≥ 1 ∧
    s.fields ≠ [] ∧
    (s.fields.map Prod.fst).Pairwise (· ≠ ·) ∧
    (∀ kv ∈ s.fields,
      kv.2.name = kv.1 ∧ ValidFieldSpec kv.2 ∧ fieldDepth kv.2 ≤ s.maxDepth.toNat) :=
  hvalid

/-- `ExtractionCardinality.single` and `ExtractionCardinality.multiple` are distinct. -/
theorem schema_cardinality_mutually_distinct :
    ExtractionCardinality.single ≠ ExtractionCardinality.multiple := by
  intro h
  cases h

/-- Well-formed schemas exist with both `allowAdditionalFields = false` and `true` and are distinct. -/
theorem schema_allow_additional_fields_distinguishable :
    ∃ sClosed sOpen : ExtractionSchema,
      ValidExtractionSchema sClosed ∧
      ValidExtractionSchema sOpen ∧
      sClosed.allowAdditionalFields = false ∧
      sOpen.allowAdditionalFields = true ∧
      sClosed.allowAdditionalFields ≠ sOpen.allowAdditionalFields := by
  let fSpec : FieldSpec := FieldSpec.mk "title" .string true false {} none none false
  let sClosed : ExtractionSchema := {
    schemaId := "s1",
    schemaVersion := "1.0",
    fields := [("title", fSpec)],
    cardinality := .single,
    allowAdditionalFields := false,
    maxDepth := 16
  }
  let sOpen : ExtractionSchema := { sClosed with allowAdditionalFields := true }
  refine ⟨sClosed, sOpen, ?_, ?_, rfl, rfl, by decide⟩
  · exact (isValidExtractionSchema_iff sClosed).mp (by decide)
  · exact (isValidExtractionSchema_iff sOpen).mp (by decide)

/-- Incompatible constraints and missing composite definitions are provably rejected. -/
theorem schema_incompatible_constraints_rejected :
    ¬ ValidFieldSpec (FieldSpec.mk "flag" .boolean true false { minValue := some (1 : Rat) } none none false) ∧
    ¬ ValidFieldSpec (FieldSpec.mk "n" .integer true false { minLength := some 1 } none none false) ∧
    ¬ ValidFieldSpec (FieldSpec.mk "obj" .object true false {} none none false) ∧
    ¬ ValidFieldSpec (FieldSpec.mk "arr" .array true false {} none none false) := by
  refine ⟨?_, ?_, ?_, ?_⟩
  · intro h
    have hnum := schema_numeric_constraints_require_numeric_type _ h (Or.inl rfl)
    cases hnum with
    | inl h1 => cases h1
    | inr h2 => cases h2
  · intro h
    have hstr := schema_string_constraints_require_string_type _ h (Or.inl rfl)
    cases hstr
  · intro h
    have hobj := (schema_object_requires_nonempty_matching_properties _ h).1 rfl
    rcases hobj with ⟨_, hprops, _⟩
    cases hprops
  · intro h
    have harr := (schema_array_requires_valid_items _ h).1 rfl
    rcases harr with ⟨_, hitems, _⟩
    cases hitems

end StructaFormal
