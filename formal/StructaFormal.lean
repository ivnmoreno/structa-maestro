/-
  Root module for the Structa Formal Assurance Layer (Canonical Output Record Contract v1.0).
  Contains only pure mathematical domain models and machine-checked invariant proofs
  (zero compiler/JSON I/O imports; `StructaFormal.Oracle` is imported by `OracleMain`).
-/

import StructaFormal.Enums
import StructaFormal.Models
import StructaFormal.FieldInvariants
import StructaFormal.ValidationInvariants
import StructaFormal.ConfidenceInvariants
import StructaFormal.ProvenanceInvariants
import StructaFormal.IdentityInvariants
import StructaFormal.StatusAssembly
