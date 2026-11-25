/-
  Structa Formal Assurance Layer — Provenance, Source Location & ProvenanceAssociator Invariants.
  Authoritative Python sources:
  - `src/structa_maestro/contracts/models.py` (`SourceLocation`, `ProvenanceEvidence`, `ExtractionResult`)
  - `src/structa_maestro/provenance/associator.py` (`ProvenanceAssociator`)
-/

import StructaFormal.Models

namespace StructaFormal

/-- Abstract observation passed to `ProvenanceAssociator.associate` for a single candidate quote. -/
structure ProposedEvidenceObservation where
  proposedDocId : Option String := none
  locatedSpan : Option SourceLocation := none
  requestedRelationship : EvidenceRelationship := .direct
  presentField : Bool := true
  scalarSupported : Bool := true
  excerpt : String := ""
  truncated : Bool := false
  deriving DecidableEq, Repr

/-- Pure formalization of `ProvenanceAssociator.associate` per evidence item (`associator.py:141-227`). -/
def associateOne (docId : String) (obs : ProposedEvidenceObservation) : Option ProvenanceEvidence :=
  match obs.proposedDocId with
  | some pid =>
    if pid != docId then
      none
    else
      match obs.locatedSpan with
      | none =>
        some {
          sourceDocumentId := docId,
          location := none,
          evidence := obs.excerpt,
          relationship := .unverified,
          truncated := obs.truncated
        }
      | some loc =>
        let rel :=
          if obs.presentField && obs.requestedRelationship == .direct && !obs.scalarSupported then
            .conflicting
          else
            obs.requestedRelationship
        some {
          sourceDocumentId := docId,
          location := some loc,
          evidence := obs.excerpt,
          relationship := rel,
          truncated := obs.truncated
        }
  | none =>
    match obs.locatedSpan with
    | none =>
      some {
        sourceDocumentId := docId,
        location := none,
        evidence := obs.excerpt,
        relationship := .unverified,
        truncated := obs.truncated
      }
    | some loc =>
      let rel :=
        if obs.presentField && obs.requestedRelationship == .direct && !obs.scalarSupported then
          .conflicting
        else
          obs.requestedRelationship
      some {
        sourceDocumentId := docId,
        location := some loc,
        evidence := obs.excerpt,
        relationship := rel,
        truncated := obs.truncated
      }

/-- Whenever `ExtractionResult.source` is present, every provenance entry across all records and
    fields references the originating document identifier. -/
theorem provenance_bound_to_source_document
    (res : ExtractionResult) (hvalid : ValidExtractionResult res)
    (s : SourceMetadata) (hsrc : res.source = some s)
    (r : ExtractedRecord) (hr : r ∈ res.records)
    (kv : String × ExtractedField) (hkv : kv ∈ r.fields)
    (ev : ProvenanceEvidence) (hev : ev ∈ kv.2.provenance) :
    ev.sourceDocumentId = s.documentId :=
  hvalid.2.2.2.2.2.2.1 s hsrc r hr kv hkv ev hev

/-- Every valid `SourceLocation` enforces lower bounds and start-to-end span ordering. -/
theorem source_location_bounds_and_ordering
    (loc : SourceLocation) (hvalid : ValidSourceLocation loc) :
    (∀ p, loc.page = some p → p ≥ 1) ∧
    (∀ p, loc.paragraph = some p → p ≥ 1) ∧
    (∀ s, loc.lineStart = some s → s ≥ 1) ∧
    (∀ e, loc.lineEnd = some e → e ≥ 1) ∧
    (∀ s, loc.charStart = some s → s ≥ 0) ∧
    (∀ e, loc.charEnd = some e → e ≥ 0) ∧
    (∀ s e, loc.lineStart = some s → loc.lineEnd = some e → s ≤ e) ∧
    (∀ s e, loc.charStart = some s → loc.charEnd = some e → s ≤ e) :=
  hvalid

/-- All `SourceLocation` components are independently optional (empty, single-ended, and partial
    combinations are all valid). -/
theorem source_location_components_independently_optional :
    ValidSourceLocation {} ∧
    ValidSourceLocation { page := some 1 } ∧
    ValidSourceLocation { lineStart := some 3 } ∧
    ValidSourceLocation { lineEnd := some 5 } ∧
    ValidSourceLocation { charStart := some 0 } ∧
    ValidSourceLocation { charEnd := some 0 } ∧
    ValidSourceLocation { sectionName := some "Totals" } := by
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;> simp [ValidSourceLocation]

/-- `UNVERIFIED` evidence is mathematically distinct from every verified/located relationship category. -/
theorem unverified_distinct_from_verified_relationships :
    EvidenceRelationship.unverified ≠ EvidenceRelationship.direct ∧
    EvidenceRelationship.unverified ≠ EvidenceRelationship.inferred ∧
    EvidenceRelationship.unverified ≠ EvidenceRelationship.conflicting := by
  refine ⟨?_, ?_, ?_⟩ <;> intro h <;> cases h

/-- Evidence document identity (`sourceDocumentId`) and evidence location (`location`) are modeled
    separately: two valid provenance entries can share the same document identity while one carries
    a granular location and the other has `location = none`. -/
theorem evidence_identity_separate_from_location :
    ∃ evNoLoc evWithLoc : ProvenanceEvidence,
      ValidProvenanceEvidence evNoLoc ∧
      ValidProvenanceEvidence evWithLoc ∧
      evNoLoc.sourceDocumentId = evWithLoc.sourceDocumentId ∧
      evNoLoc.location = none ∧
      evWithLoc.location = some { page := some 1, charStart := some 0, charEnd := some 12 } := by
  refine ⟨
    { sourceDocumentId := "doc-1", location := none, evidence := "INV-1", relationship := .unverified },
    { sourceDocumentId := "doc-1",
      location := some { page := some 1, charStart := some 0, charEnd := some 12 },
      evidence := "INV-1", relationship := .direct },
    ?_, ?_, rfl, rfl, rfl⟩
  · simp [ValidProvenanceEvidence]
  · simp [ValidProvenanceEvidence, ValidSourceLocation]

/-- `ProvenanceAssociator` rejects evidence attributed to a foreign document identifier. -/
theorem associator_rejects_foreign_document
    (docId pid : String) (obs : ProposedEvidenceObservation)
    (hpid : obs.proposedDocId = some pid) (hne : pid ≠ docId) :
    associateOne docId obs = none := by
  unfold associateOne
  simp [hpid, hne]

/-- Unlocated quotes are always classified by `ProvenanceAssociator` as `UNVERIFIED` with
    `location = none` (never promoted to `DIRECT`/`INFERRED` and never given a fabricated location). -/
theorem associator_unlocated_is_unverified_without_location
    (docId : String) (obs : ProposedEvidenceObservation) (ev : ProvenanceEvidence)
    (hunloc : obs.locatedSpan = none)
    (hres : associateOne docId obs = some ev) :
    ev.relationship = .unverified ∧ ev.location = none := by
  unfold associateOne at hres
  cases hpid : obs.proposedDocId with
  | none =>
    simp only [hpid, hunloc, Option.some.injEq] at hres
    subst hres
    exact ⟨rfl, rfl⟩
  | some pid =>
    by_cases hne : pid = docId
    · simp only [hpid, hne, bne_self_eq_false, Bool.false_eq_true, ↓reduceIte, hunloc,
        Option.some.injEq] at hres
      subst hres
      exact ⟨rfl, rfl⟩
    · simp [hpid, hne] at hres

/-- Every provenance entry emitted by `ProvenanceAssociator` is bound to `docId`. -/
theorem associator_preserves_document_binding
    (docId : String) (obs : ProposedEvidenceObservation) (ev : ProvenanceEvidence)
    (hres : associateOne docId obs = some ev) :
    ev.sourceDocumentId = docId := by
  unfold associateOne at hres
  cases hpid : obs.proposedDocId with
  | none =>
    cases hloc : obs.locatedSpan with
    | none =>
      simp only [hpid, hloc, Option.some.injEq] at hres
      subst hres
      rfl
    | some loc =>
      simp only [hpid, hloc, Option.some.injEq] at hres
      subst hres
      rfl
  | some pid =>
    by_cases hne : pid = docId
    · cases hloc : obs.locatedSpan with
      | none =>
        simp only [hpid, hne, bne_self_eq_false, Bool.false_eq_true, ↓reduceIte, hloc,
          Option.some.injEq] at hres
        subst hres
        rfl
      | some loc =>
        simp only [hpid, hne, bne_self_eq_false, Bool.false_eq_true, ↓reduceIte, hloc,
          Option.some.injEq] at hres
        subst hres
        rfl
    · simp [hpid, hne] at hres

/-- Evidence relationship is independent of confidence: any relationship category can pair with
    unavailable confidence or valid numeric confidence. -/
theorem evidence_relationship_independent_of_confidence
    (rel : EvidenceRelationship) (conf : Option ConfidenceAssessment)
    (hconf : ∀ c, conf = some c → ValidConfidenceAssessment c) :
    ∃ f : ExtractedField,
      ValidExtractedField f ∧
      f.confidence = conf ∧
      ∃ ev ∈ f.provenance, ev.relationship = rel := by
  let ev : ProvenanceEvidence :=
    { sourceDocumentId := "doc-1", location := none, evidence := "quote", relationship := rel }
  refine ⟨{
    presence := .present,
    value := .nonNull "val",
    confidence := conf,
    provenance := [ev],
    validation := { status := .valid, errors := [] }
  }, ?_, rfl, ⟨ev, List.mem_cons_self, rfl⟩⟩
  refine ⟨(by intro h; cases h), hconf, ?_, ?_, by simp, by simp⟩
  · intro x hx
    simp only [List.mem_singleton] at hx
    subst hx
    simp [ev, ValidProvenanceEvidence]
  · simp [ValidValidationOutcome]

end StructaFormal
