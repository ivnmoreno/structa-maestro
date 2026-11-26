/-
  Executable Lake testDriver (`structa-axiom-audit`) performing two kernel-level checks:
  1. Independent kernel proof replay (`Lean.Environment.replay`): replays every declaration and
     proof term in `StructaFormal` from scratch against a clean `Init` kernel environment.
  2. Programmatic axiom & theorem-inventory audit (`Lean.collectAxioms`): verifies all 108 required
     theorems exist in the replayed kernel environment and depend ONLY on Lean's standard core
     axioms (`propext`, `Classical.choice`, `Quot.sound`) with zero `sorryAx` and zero custom axioms.
-/

import Lean
import StructaFormal

open Lean

private def allowedAxioms : List Name :=
  [``propext, ``Classical.choice, ``Quot.sound]

/-- Complete registry of all 108 required machine-checked theorems
    (46 Contract v1.0 + 32 Schema & Validation + 30 Cross-Field & Conditional Rules). -/
private def requiredTheorems : List Name := [
  -- 1. Field Invariants (4)
  ``StructaFormal.missing_field_has_no_value,
  ``StructaFormal.missing_distinct_from_present_null,
  ``StructaFormal.present_allows_null_and_nonnull,
  ``StructaFormal.field_presence_independent_of_validation_status,
  -- 2. Validation Invariants (8)
  ``StructaFormal.validation_status_mutually_distinct,
  ``StructaFormal.validation_outcome_errors_are_validation_stage_errors,
  ``StructaFormal.valid_outcome_has_no_errors,
  ``StructaFormal.not_validated_outcome_has_no_errors,
  ``StructaFormal.structural_invalid_allows_empty_errors,
  ``StructaFormal.validator_invalid_iff_nonempty_errors,
  ``StructaFormal.validator_valid_iff_empty_errors,
  ``StructaFormal.validator_prevalidation_failure_is_not_validated,
  -- 3. Confidence Invariants (7)
  ``StructaFormal.confidence_zero_one_scale_within_bounds,
  ``StructaFormal.confidence_unavailable_explicitly_representable,
  ``StructaFormal.confidence_calibrated_distinguishable,
  ``StructaFormal.normalized_confidence_never_calibrated,
  ``StructaFormal.normalized_confidence_valid_contract,
  ``StructaFormal.normalized_missing_or_invalid_explicitly_unavailable,
  ``StructaFormal.confidence_independent_of_validation_and_provenance,
  -- 4. Provenance & Source Location Invariants (9)
  ``StructaFormal.provenance_bound_to_source_document,
  ``StructaFormal.source_location_bounds_and_ordering,
  ``StructaFormal.source_location_components_independently_optional,
  ``StructaFormal.unverified_distinct_from_verified_relationships,
  ``StructaFormal.evidence_identity_separate_from_location,
  ``StructaFormal.associator_rejects_foreign_document,
  ``StructaFormal.associator_unlocated_is_unverified_without_location,
  ``StructaFormal.associator_preserves_document_binding,
  ``StructaFormal.evidence_relationship_independent_of_confidence,
  -- 5. Identity Invariants (4)
  ``StructaFormal.result_contract_version_is_v1,
  ``StructaFormal.result_record_ids_unique,
  ``StructaFormal.schema_identity_distinguishable_from_extraction_identity,
  ``StructaFormal.result_identifiers_nonempty,
  -- 6. Processing & Validation Status / Result Assembly Invariants (9)
  ``StructaFormal.processing_and_validation_status_independent,
  ``StructaFormal.partial_processing_does_not_imply_invalid,
  ``StructaFormal.completed_processing_does_not_imply_valid,
  ``StructaFormal.failed_processing_does_not_imply_invalid,
  ``StructaFormal.not_validated_distinct_from_valid,
  ``StructaFormal.build_failed_is_failed_and_not_validated,
  ``StructaFormal.partial_or_errors_never_promoted_to_completed,
  ``StructaFormal.failed_strategy_with_records_is_partial_and_validated,
  ``StructaFormal.record_failed_status_independent_of_validation,
  -- 7. Contract v1.0 Predicate Soundness/Completeness Bridges (5)
  ``StructaFormal.isValidSourceLocation_iff,
  ``StructaFormal.isValidConfidenceAssessment_iff,
  ``StructaFormal.isValidValidationOutcome_iff,
  ``StructaFormal.isValidExtractedField_iff,
  ``StructaFormal.isValidExtractionResult_iff,
  -- 8. Schema Well-Formedness Predicate Bridges (3)
  ``StructaFormal.isValidFieldConstraints_iff,
  ``StructaFormal.isValidFieldSpec_iff,
  ``StructaFormal.isValidExtractionSchema_iff,
  -- 9. Schema Well-Formedness Invariants (10)
  ``StructaFormal.schema_constraints_ordering_and_nonneg,
  ``StructaFormal.schema_numeric_constraints_require_numeric_type,
  ``StructaFormal.schema_string_constraints_require_string_type,
  ``StructaFormal.schema_array_constraints_require_array_type,
  ``StructaFormal.schema_object_requires_nonempty_matching_properties,
  ``StructaFormal.schema_array_requires_valid_items,
  ``StructaFormal.schema_nonempty_unique_fields_and_identities,
  ``StructaFormal.schema_cardinality_mutually_distinct,
  ``StructaFormal.schema_allow_additional_fields_distinguishable,
  ``StructaFormal.schema_incompatible_constraints_rejected,
  -- 10. Deterministic Validation & Cardinality Semantics (19)
  ``StructaFormal.validation_is_deterministic,
  ``StructaFormal.validate_value_errors_are_valid_diagnostics,
  ``StructaFormal.validate_record_outcomes_satisfy_contract,
  ``StructaFormal.validate_record_valid_iff_all_fields_valid,
  ``StructaFormal.missing_required_field_is_invalid,
  ``StructaFormal.missing_optional_field_is_valid,
  ``StructaFormal.present_null_disallowed_is_invalid,
  ``StructaFormal.present_null_allowed_is_valid,
  ``StructaFormal.no_coercion_string_for_integer,
  ``StructaFormal.no_coercion_bool_for_integer,
  ``StructaFormal.no_coercion_bool_for_decimal,
  ``StructaFormal.no_coercion_datetime_for_date,
  ``StructaFormal.no_coercion_nonfinite_for_decimal,
  ``StructaFormal.integer_accepted_for_unconstrained_decimal,
  ``StructaFormal.allowed_values_separates_bool_and_int,
  ``StructaFormal.unexpected_extra_field_rejected_when_disallowed,
  ``StructaFormal.extra_field_allowed_when_opted_in_or_missing,
  ``StructaFormal.cardinality_single_semantics,
  ``StructaFormal.cardinality_multiple_and_prevalidation_semantics,
  -- 11. Cross-Field Rule Predicate Bridges (3)
  ``StructaFormal.isValidValidationRule_iff,
  ``StructaFormal.isValidSchemaRules_iff,
  ``StructaFormal.isValidSchemaWithRules_iff,
  -- 12. Cross-Field Rule Well-Formedness Invariants (9)
  ``StructaFormal.schema_rules_unique_nonblank_ids,
  ``StructaFormal.comparison_rule_distinct_compatible_fields,
  ``StructaFormal.comparison_rule_forbids_ordered_boolean,
  ``StructaFormal.arithmetic_rule_nonempty_unit_signs_nonneg_tol,
  ``StructaFormal.arithmetic_rule_target_and_terms_numeric,
  ``StructaFormal.conditional_rule_condition_value_consistency,
  ``StructaFormal.conditional_rule_nonempty_unique_targets_disjoint_from_when,
  ``StructaFormal.group_rule_at_least_two_distinct_resolvable_fields,
  ``StructaFormal.invalid_cross_field_rules_rejected,
  -- 13. Deterministic Cross-Field Evaluation, Exact Arithmetic & Severity Semantics (18)
  ``StructaFormal.cross_field_evaluation_is_deterministic,
  ``StructaFormal.cross_field_errors_are_validation_stage_errors,
  ``StructaFormal.cross_field_warnings_are_validation_stage_warnings,
  ``StructaFormal.validate_record_with_rules_outcomes_satisfy_contract,
  ``StructaFormal.validate_record_with_rules_empty_reduces_to_validate_record,
  ``StructaFormal.validate_record_with_rules_valid_iff_fields_and_rule_errors_valid,
  ``StructaFormal.rule_error_forces_record_invalid_even_when_all_fields_valid,
  ``StructaFormal.rule_warnings_never_enter_errors_or_invalidate_valid_record,
  ``StructaFormal.comparison_rule_skips_when_operand_missing_or_null,
  ``StructaFormal.comparison_rule_no_coercion_bool_for_numeric,
  ``StructaFormal.arithmetic_rule_exact_balance_within_tolerance_passes,
  ``StructaFormal.arithmetic_rule_deviation_exceeding_tolerance_fails,
  ``StructaFormal.arithmetic_rule_treat_missing_as_zero_vs_skip,
  ``StructaFormal.arithmetic_rule_no_coercion_bool_or_nonfinite_skips,
  ``StructaFormal.conditional_rule_inert_when_condition_false,
  ``StructaFormal.conditional_is_missing_distinct_from_present_null,
  ``StructaFormal.conditional_required_if_honors_nullable_and_forbidden_if_semantics,
  ``StructaFormal.group_presence_mutually_exclusive_and_at_least_one_semantics
]

def main : IO UInt32 := do
  initSearchPath (← findSysroot)
  let baseEnv ← importModules #[{ module := `Init }] {}
  let fullEnv ← importModules #[{ module := `StructaFormal }] {}

  -- Stage 1: Replay all StructaFormal declarations from scratch through the Lean 4 kernel.
  let mut newConstants : Std.HashMap Name ConstantInfo := {}
  for (name, cinfo) in fullEnv.constants.map₁ do
    if !baseEnv.constants.contains name then
      newConstants := newConstants.insert name cinfo
  let replayedKernelEnv ← Lean.Kernel.Environment.replay newConstants baseEnv.toKernelEnv

  -- Stage 2: Audit all 108 required theorems (kernel-accepted + core axioms only).
  let mut failures : List String := []
  let mut observedAxioms : List Name := []
  for thm in requiredTheorems do
    if !replayedKernelEnv.constants.contains thm then
      failures := failures ++ [s!"MISSING FROM REPLAYED KERNEL ENV: {thm}"]
    else
      let (axioms, _) ← (Lean.collectAxioms thm : CoreM (Array Name)).toIO
        { fileName := "<structa-axiom-audit>", fileMap := default }
        { env := fullEnv }
      for ax in axioms do
        if !observedAxioms.contains ax then
          observedAxioms := observedAxioms ++ [ax]
        if !allowedAxioms.contains ax then
          failures := failures ++ [s!"DISALLOWED AXIOM '{ax}' used by theorem '{thm}'"]

  if failures.isEmpty then
    IO.println s!"structa-axiom-audit PASSED: {newConstants.size} declarations kernel-replayed; {requiredTheorems.length} theorems verified; observed core axioms: {observedAxioms}"
    return 0
  else
    for msg in failures do
      IO.eprintln msg
    return 1
