/-
  Structa Formal Assurance Layer — Record, Schema, Extraction & Contract Identity Invariants.
  Authoritative Python source: `src/structa_maestro/contracts/models.py`
  (`SourceMetadata`, `SchemaMetadata`, `ExtractedRecord`, `ExtractionResult`).
-/

import StructaFormal.Models

namespace StructaFormal

/-- Every valid `ExtractionResult` carries the explicit Contract v1.0 identifier `"1.0"`. -/
theorem result_contract_version_is_v1
    (res : ExtractionResult) (hvalid : ValidExtractionResult res) :
    res.contractVersion = "1.0" :=
  hvalid.1

/-- Record identifiers within a valid `ExtractionResult` are pairwise unique. -/
theorem result_record_ids_unique
    (res : ExtractionResult) (hvalid : ValidExtractionResult res) :
    (res.records.map ExtractedRecord.recordId).Pairwise (· ≠ ·) :=
  hvalid.2.2.2.2.1

/-- Schema identity (`schemaMetadata.schemaId`) and extraction identity (`extractionId`) are
    independently represented and distinguishable on valid results. -/
theorem schema_identity_distinguishable_from_extraction_identity :
    ∃ res : ExtractionResult,
      ValidExtractionResult res ∧
      res.extractionId ≠ res.schemaMetadata.schemaId := by
  refine ⟨{
    contractVersion := "1.0",
    extractionId := "ext-001",
    status := .completed,
    source := some { documentId := "doc-001" },
    schemaMetadata := { schemaId := "invoice-schema", schemaVersion := "1.0" },
    records := [],
    validation := { status := .valid, errors := [] }
  }, ?_, by decide⟩
  simp [ValidExtractionResult, contractVersionV1, ValidSourceMetadata, ValidSchemaMetadata,
    ProvenanceBoundToSource, ValidValidationOutcome]

/-- All core identity strings on a valid `ExtractionResult` (and its records and optional source)
    are non-empty. -/
theorem result_identifiers_nonempty
    (res : ExtractionResult) (hvalid : ValidExtractionResult res) :
    res.extractionId ≠ "" ∧
    res.schemaMetadata.schemaId ≠ "" ∧
    res.schemaMetadata.schemaVersion ≠ "" ∧
    (∀ s, res.source = some s → s.documentId ≠ "") ∧
    (∀ r ∈ res.records, r.recordId ≠ "") := by
  refine ⟨hvalid.2.1, hvalid.2.2.2.1.1, hvalid.2.2.2.1.2, ?_, ?_⟩
  · intro s hs
    exact hvalid.2.2.1 s hs
  · intro r hr
    exact (hvalid.2.2.2.2.2.1 r hr).1

end StructaFormal
