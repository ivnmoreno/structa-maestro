/-
  Structa Formal Assurance Layer — Provenance Association & Source-Span Mapping.
  Authoritative Python sources:
    - `src/structa_maestro/provenance/associator.py` (`ProvenanceAssociator`, `_location_from_span`, `_bound_excerpt`)
    - `src/structa_maestro/documents/models.py` (`DocumentSegment`)

  Explicit Verification Boundaries:
  - Substring / whitespace-tolerant regex search (`_find_span`) and heuristic scalar-in-excerpt
    matching (`_scalar_value_supported_by_excerpt`) are abstracted via `ProposedEvidenceInput`
    fields (`matchedSpan`, `matchedText`, `scalarSupported`).
  - Structural provenance verification never proves real-world semantic truth of an extracted value.
-/

import StructaFormal.ProvenanceInvariants
import StructaFormal.SchemaModels

namespace StructaFormal

/-- Normalized text segment carrying source location metadata (`DocumentSegment` in `documents/models.py`). -/
structure DocumentSegment where
  charStart : Int
  charEnd : Int
  page : Option Int := none
  paragraph : Option Int := none
  lineStart : Option Int := none
  lineEnd : Option Int := none
  heading : Option String := none
  deriving DecidableEq, Repr

/-- Well-formedness of a `DocumentSegment` produced by a document parser. -/
def ValidDocumentSegment (seg : DocumentSegment) : Prop :=
  seg.charStart ≥ 0 ∧
  seg.charEnd ≥ 0 ∧
  seg.charStart ≤ seg.charEnd ∧
  (∀ p, seg.page = some p → p ≥ 1) ∧
  (∀ p, seg.paragraph = some p → p ≥ 1) ∧
  (∀ s, seg.lineStart = some s → s ≥ 1) ∧
  (∀ e, seg.lineEnd = some e → e ≥ 1)

/-- Candidate evidence item plus abstracted span-location and scalar-support verdicts. -/
structure ProposedEvidenceInput where
  quote : String
  sourceDocumentId : Option String := none
  relationship : String := "direct"
  matchedSpan : Option (Int × Int) := none
  matchedText : String := ""
  scalarSupported : Bool := true
  deriving DecidableEq, Repr

/-- Pure formalization of `_parse_relationship` (`associator.py:110-114`). -/
def parseRelationship (raw : String) : EvidenceRelationship :=
  match EvidenceRelationship.fromString? raw.toLower with
  | some rel => rel
  | none => .direct

/-- Pure formalization of `ProvenanceAssociator._bound_excerpt` (`associator.py:231-234`). -/
def boundExcerpt (text : String) (maxChars : Nat) : String × Bool :=
  let chars := text.toList
  if chars.length ≤ maxChars then
    (text, false)
  else
    (String.ofList (chars.take maxChars), true)

/-- Helper selecting the last element of a non-empty list. -/
private def listLast {α : Type} : (xs : List α) → α → α
  | [], fallback => fallback
  | [x], _ => x
  | _ :: y :: rest, fallback => listLast (y :: rest) fallback

private theorem listLast_mem {α : Type} (x : α) (xs : List α) (fb : α) :
    listLast (x :: xs) fb ∈ (x :: xs) := by
  induction xs generalizing x with
  | nil => exact List.mem_cons_self
  | cons y rest ih =>
    unfold listLast
    exact List.mem_cons_of_mem x (ih y)

/-- Pure formalization of `_location_from_span` (`associator.py:59-85`). -/
def locationFromSpan
    (segments : List DocumentSegment) (charStart charEnd : Int) : SourceLocation :=
  let overlapping :=
    segments.filter (fun seg => decide (seg.charEnd ≥ charStart ∧ seg.charStart ≤ charEnd))
  match overlapping with
  | [] => { charStart := some charStart, charEnd := some charEnd }
  | first :: rest =>
    let last := listLast (first :: rest) first
    let lineStart := first.lineStart
    let rawLineEnd := match last.lineEnd with
      | some e => some e
      | none => first.lineEnd
    let lineEnd := match lineStart, rawLineEnd with
      | some ls, some le => some (if le < ls then ls else le)
      | _, other => other
    {
      page := first.page,
      paragraph := first.paragraph,
      lineStart := lineStart,
      lineEnd := lineEnd,
      charStart := some charStart,
      charEnd := some charEnd,
      sectionName := first.heading
    }

/-- Construct a `PROVENANCE`-stage `WARNING` diagnostic guaranteed to satisfy `ValidDiagnosticIssue`. -/
def mkProvenanceWarning
    (code message recordId fieldName : String) : DiagnosticIssue :=
  {
    code := if code == "" then "provenance_warning" else code,
    severity := .warning,
    stage := .provenance,
    message := if message == "" then "Provenance warning." else message,
    recordId := some recordId,
    fieldPath := some fieldName
  }

theorem mkProvenanceWarning_valid (code message recordId fieldName : String) :
    let w := mkProvenanceWarning code message recordId fieldName
    ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .provenance := by
  refine ⟨⟨?_, ?_⟩, rfl, rfl⟩
  · dsimp only [mkProvenanceWarning]
    by_cases hc : code = "" <;> simp [hc]
  · dsimp only [mkProvenanceWarning]
    by_cases hm : message = "" <;> simp [hm]

/-- Pure formalization of a single iteration of `ProvenanceAssociator.associate` (`associator.py:141-227`). -/
def associateSingleItem
    (docId : String)
    (segments : List DocumentSegment)
    (fieldName : String)
    (presence : FieldPresence)
    (recordId : String)
    (maxExcerptChars : Nat)
    (item : ProposedEvidenceInput) : Option ProvenanceEvidence × List DiagnosticIssue :=
  match item.sourceDocumentId with
  | some pid =>
    if pid != docId then
      (none, [mkProvenanceWarning "cross_document_evidence_rejected"
        s!"Rejected provenance referencing foreign document for field '{fieldName}'."
        recordId fieldName])
    else if !isNonBlank item.quote then
      (none, [])
    else
      let requestedRel := parseRelationship item.relationship
      match item.matchedSpan with
      | none =>
        let (excerpt, truncated) := boundExcerpt item.quote.trimAscii.toString maxExcerptChars
        (some {
          sourceDocumentId := docId,
          location := none,
          evidence := excerpt,
          relationship := .unverified,
          truncated := truncated
        }, [mkProvenanceWarning "unverified_evidence"
          s!"Proposed evidence for field '{fieldName}' could not be located in the source document."
          recordId fieldName])
      | some (cs, ce) =>
        let (excerpt, truncated) := boundExcerpt item.matchedText maxExcerptChars
        let loc := locationFromSpan segments cs ce
        let isConflict :=
          presence == .present && requestedRel == .direct && !item.scalarSupported
        let rel := if isConflict then .conflicting else requestedRel
        let warns := if isConflict then
          [mkProvenanceWarning "evidence_value_conflict"
            s!"Cited direct evidence for field '{fieldName}' does not match the extracted value."
            recordId fieldName]
        else []
        (some {
          sourceDocumentId := docId,
          location := some loc,
          evidence := excerpt,
          relationship := rel,
          truncated := truncated
        }, warns)
  | none =>
    if !isNonBlank item.quote then
      (none, [])
    else
      let requestedRel := parseRelationship item.relationship
      match item.matchedSpan with
      | none =>
        let (excerpt, truncated) := boundExcerpt item.quote.trimAscii.toString maxExcerptChars
        (some {
          sourceDocumentId := docId,
          location := none,
          evidence := excerpt,
          relationship := .unverified,
          truncated := truncated
        }, [mkProvenanceWarning "unverified_evidence"
          s!"Proposed evidence for field '{fieldName}' could not be located in the source document."
          recordId fieldName])
      | some (cs, ce) =>
        let (excerpt, truncated) := boundExcerpt item.matchedText maxExcerptChars
        let loc := locationFromSpan segments cs ce
        let isConflict :=
          presence == .present && requestedRel == .direct && !item.scalarSupported
        let rel := if isConflict then .conflicting else requestedRel
        let warns := if isConflict then
          [mkProvenanceWarning "evidence_value_conflict"
            s!"Cited direct evidence for field '{fieldName}' does not match the extracted value."
            recordId fieldName]
        else []
        (some {
          sourceDocumentId := docId,
          location := some loc,
          evidence := excerpt,
          relationship := rel,
          truncated := truncated
        }, warns)

/-- Pure formalization of multi-item `ProvenanceAssociator.associate` (`associator.py:123-229`). -/
def associateEvidence
    (docId : String)
    (segments : List DocumentSegment)
    (fieldName : String)
    (presence : FieldPresence)
    (recordId : String)
    (maxExcerptChars : Nat := 500)
    (items : List ProposedEvidenceInput) : List ProvenanceEvidence × List DiagnosticIssue :=
  match items with
  | [] => ([], [])
  | item :: rest =>
    let (ev?, w1) := associateSingleItem docId segments fieldName presence recordId maxExcerptChars item
    let (restEv, restW) :=
      associateEvidence docId segments fieldName presence recordId maxExcerptChars rest
    (ev?.toList ++ restEv, w1 ++ restW)

-- =============================================================================
-- Machine-Checked Provenance Association & Span Mapping Theorems (13 theorems)
-- =============================================================================

/-- Every `SourceLocation` produced by `locationFromSpan` satisfies `ValidSourceLocation` whenever
    `0 <= charStart <= charEnd` and all input `DocumentSegment`s satisfy `ValidDocumentSegment`. -/
theorem location_from_span_is_valid_source_location
    (segments : List DocumentSegment) (charStart charEnd : Int)
    (hcs : charStart ≥ 0) (hce : charEnd ≥ 0) (hord : charStart ≤ charEnd)
    (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg) :
    ValidSourceLocation (locationFromSpan segments charStart charEnd) := by
  unfold locationFromSpan
  cases hover : segments.filter (fun seg => decide (seg.charEnd ≥ charStart ∧ seg.charStart ≤ charEnd)) with
  | nil =>
    simp [ValidSourceLocation, hcs, hce, hord]
  | cons first rest =>
    have hfirstMem : first ∈ segments := by
      have hm : first ∈ segments.filter (fun seg => decide (seg.charEnd ≥ charStart ∧ seg.charStart ≤ charEnd)) := by
        rw [hover]; exact List.mem_cons_self
      exact (List.mem_filter.mp hm).1
    have hlastMem : listLast (first :: rest) first ∈ segments := by
      have hm1 : listLast (first :: rest) first ∈ (first :: rest) := listLast_mem first rest first
      have hm2 : listLast (first :: rest) first ∈
          segments.filter (fun seg => decide (seg.charEnd ≥ charStart ∧ seg.charStart ≤ charEnd)) := by
        rw [hover]; exact hm1
      exact (List.mem_filter.mp hm2).1
    have hf := hsegs first hfirstMem
    have hl := hsegs (listLast (first :: rest) first) hlastMem
    dsimp only [ValidSourceLocation]
    refine ⟨hf.2.2.2.1, hf.2.2.2.2.1, hf.2.2.2.2.2.1, ?_, ?_, ?_, ?_, ?_⟩
    · -- lineEnd >= 1
      intro e he
      cases hls : first.lineStart with
      | none =>
        rw [hls] at he
        cases hle : (listLast (first :: rest) first).lineEnd with
        | none =>
          rw [hle] at he
          exact hf.2.2.2.2.2.2 e he
        | some le =>
          rw [hle] at he
          cases he
          exact hl.2.2.2.2.2.2 e hle
      | some ls =>
        rw [hls] at he
        cases hle : (listLast (first :: rest) first).lineEnd with
        | none =>
          rw [hle] at he
          cases hfe : first.lineEnd with
          | none => rw [hfe] at he; cases he
          | some fe =>
            rw [hfe] at he
            dsimp only at he
            cases he
            by_cases hlt : fe < ls
            · simp only [hlt, ↓reduceIte]
              exact hf.2.2.2.2.2.1 ls hls
            · simp only [hlt, ↓reduceIte]
              exact hf.2.2.2.2.2.2 fe hfe
        | some le =>
          rw [hle] at he
          dsimp only at he
          cases he
          by_cases hlt : le < ls
          · simp only [hlt, ↓reduceIte]
            exact hf.2.2.2.2.2.1 ls hls
          · simp only [hlt, ↓reduceIte]
            exact hl.2.2.2.2.2.2 le hle
    · intro s hs; cases hs; exact hcs
    · intro e he; cases he; exact hce
    · -- lineStart <= lineEnd
      intro s e hs he
      rw [hs] at he
      cases hle : (listLast (first :: rest) first).lineEnd with
      | none =>
        rw [hle] at he
        cases hfe : first.lineEnd with
        | none => rw [hfe] at he; cases he
        | some fe =>
          rw [hfe] at he
          dsimp only at he
          cases he
          by_cases hlt : fe < s
          · simp only [hlt, ↓reduceIte]; omega
          · simp only [hlt, ↓reduceIte]; omega
      | some le =>
        rw [hle] at he
        dsimp only at he
        cases he
        by_cases hlt : le < s
        · simp only [hlt, ↓reduceIte]; omega
        · simp only [hlt, ↓reduceIte]; omega
    · intro s e hs he; cases hs; cases he; exact hord

/-- When overlapping segments produce `last.lineEnd < first.lineStart`, `locationFromSpan` clamps
    `lineEnd` up to `first.lineStart`. -/
theorem location_from_span_clamps_reversed_lines
    (seg1 seg2 : DocumentSegment) (cs ce ls le : Int)
    (hov1 : seg1.charEnd ≥ cs ∧ seg1.charStart ≤ ce)
    (hov2 : seg2.charEnd ≥ cs ∧ seg2.charStart ≤ ce)
    (hls : seg1.lineStart = some ls) (hle : seg2.lineEnd = some le) (hlt : le < ls) :
    (locationFromSpan [seg1, seg2] cs ce).lineStart = some ls ∧
    (locationFromSpan [seg1, seg2] cs ce).lineEnd = some ls := by
  unfold locationFromSpan
  simp [hov1, hov2, listLast, hls, hle, hlt]

/-- When no segments overlap the span, `locationFromSpan` falls back to `(charStart, charEnd)` only. -/
theorem location_from_span_fallback_when_no_overlap
    (segments : List DocumentSegment) (cs ce : Int)
    (hnone : ∀ seg ∈ segments, ¬ (seg.charEnd ≥ cs ∧ seg.charStart ≤ ce)) :
    locationFromSpan segments cs ce = { charStart := some cs, charEnd := some ce } := by
  have hfilt : segments.filter (fun seg => decide (seg.charEnd ≥ cs ∧ seg.charStart ≤ ce)) = [] := by
    rw [List.filter_eq_nil_iff]
    intro seg hseg
    simp [hnone seg hseg]
  unfold locationFromSpan
  rw [hfilt]

/-- `boundExcerpt` always bounds excerpt length by `maxChars` and sets `truncated = true` iff
    the input exceeds `maxChars`. -/
theorem bound_excerpt_length_and_truncation (text : String) (maxChars : Nat) :
    let res := boundExcerpt text maxChars
    res.1.toList.length ≤ maxChars ∧ (res.2 = true ↔ text.toList.length > maxChars) := by
  unfold boundExcerpt
  dsimp only
  by_cases hle : text.toList.length ≤ maxChars
  · simp only [hle, ↓reduceIte, Bool.false_eq_true, false_iff]
    exact ⟨trivial, by omega⟩
  · simp only [hle, ↓reduceIte, String.toList_ofList, List.length_take, true_iff]
    exact ⟨Nat.min_le_left _ _, by omega⟩

/-- Unrecognized relationship strings fall back to `EvidenceRelationship.direct`. -/
theorem parse_relationship_unknown_defaults_to_direct
    (raw : String) (hunk : EvidenceRelationship.fromString? raw.toLower = none) :
    parseRelationship raw = .direct := by
  unfold parseRelationship
  rw [hunk]

/-- Multi-item provenance association is strictly deterministic. -/
theorem associate_evidence_is_deterministic
    (d1 d2 : String) (s1 s2 : List DocumentSegment) (f1 f2 : String)
    (p1 p2 : FieldPresence) (r1 r2 : String) (m1 m2 : Nat)
    (i1 i2 : List ProposedEvidenceInput)
    (hd : d1 = d2) (hs : s1 = s2) (hf : f1 = f2) (hp : p1 = p2)
    (hr : r1 = r2) (hm : m1 = m2) (hi : i1 = i2) :
    associateEvidence d1 s1 f1 p1 r1 m1 i1 = associateEvidence d2 s2 f2 p2 r2 m2 i2 := by
  subst hd; subst hs; subst hf; subst hp; subst hr; subst hm; subst hi; rfl

/-- Foreign-document evidence is omitted from `evidence` and emits `cross_document_evidence_rejected`. -/
theorem associate_evidence_rejects_foreign_with_warning
    (docId pid : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String)
    (maxChars : Nat) (item : ProposedEvidenceInput)
    (hpid : item.sourceDocumentId = some pid) (hne : pid ≠ docId) :
    let res := associateEvidence docId segments fieldName presence recordId maxChars [item]
    res.1 = [] ∧ (∃ w, res.2 = [w] ∧ w.code = "cross_document_evidence_rejected") := by
  simp [associateEvidence, associateSingleItem, hpid, hne, mkProvenanceWarning]

/-- Whitespace-only or empty quotes on the current document are skipped silently. -/
theorem associate_evidence_skips_blank_quote
    (docId : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String)
    (maxChars : Nat) (item : ProposedEvidenceInput)
    (hsame : item.sourceDocumentId = none ∨ item.sourceDocumentId = some docId)
    (hblank : isNonBlank item.quote = false) :
    associateEvidence docId segments fieldName presence recordId maxChars [item] = ([], []) := by
  cases hsame with
  | inl h => simp [associateEvidence, associateSingleItem, h, hblank]
  | inr h => simp [associateEvidence, associateSingleItem, h, hblank]

/-- Unlocated non-blank quotes yield `UNVERIFIED` with `location = none` and emit `unverified_evidence`. -/
theorem associate_evidence_unlocated_is_unverified_with_warning
    (docId : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String)
    (maxChars : Nat) (item : ProposedEvidenceInput)
    (hsame : item.sourceDocumentId = none ∨ item.sourceDocumentId = some docId)
    (hnonblank : isNonBlank item.quote = true)
    (hunloc : item.matchedSpan = none) :
    let res := associateEvidence docId segments fieldName presence recordId maxChars [item]
    (∃ ev, res.1 = [ev] ∧ ev.relationship = .unverified ∧ ev.location = none ∧ ev.sourceDocumentId = docId) ∧
    (∃ w, res.2 = [w] ∧ w.code = "unverified_evidence") := by
  cases hsame with
  | inl h => simp [associateEvidence, associateSingleItem, h, hnonblank, hunloc, mkProvenanceWarning]
  | inr h => simp [associateEvidence, associateSingleItem, h, hnonblank, hunloc, mkProvenanceWarning]

/-- Located `DIRECT` evidence on a `PRESENT` field with unsupported scalar value downgrades to
    `CONFLICTING` (preserving valid location) and emits `evidence_value_conflict`. -/
theorem associate_evidence_direct_conflict_downgrades_with_warning
    (docId : String) (segments : List DocumentSegment)
    (fieldName recordId : String) (maxChars : Nat) (cs ce : Int)
    (item : ProposedEvidenceInput)
    (hsame : item.sourceDocumentId = none ∨ item.sourceDocumentId = some docId)
    (hnonblank : isNonBlank item.quote = true)
    (hloc : item.matchedSpan = some (cs, ce))
    (hrel : parseRelationship item.relationship = .direct)
    (hunsup : item.scalarSupported = false) :
    let res := associateEvidence docId segments fieldName .present recordId maxChars [item]
    (∃ ev, res.1 = [ev] ∧ ev.relationship = .conflicting ∧
      ev.location = some (locationFromSpan segments cs ce) ∧ ev.sourceDocumentId = docId) ∧
    (∃ w, res.2 = [w] ∧ w.code = "evidence_value_conflict") := by
  cases hsame with
  | inl h => simp [associateEvidence, associateSingleItem, h, hnonblank, hloc, hrel, hunsup, mkProvenanceWarning]
  | inr h => simp [associateEvidence, associateSingleItem, h, hnonblank, hloc, hrel, hunsup, mkProvenanceWarning]

/-- Non-`DIRECT` located quotes or quotes on `MISSING` fields never trigger `evidence_value_conflict`. -/
theorem associate_evidence_non_direct_or_missing_never_conflicts
    (docId : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String)
    (maxChars : Nat) (cs ce : Int) (item : ProposedEvidenceInput)
    (hsame : item.sourceDocumentId = none ∨ item.sourceDocumentId = some docId)
    (hnonblank : isNonBlank item.quote = true)
    (hloc : item.matchedSpan = some (cs, ce))
    (hcond : parseRelationship item.relationship ≠ .direct ∨ presence = .missing) :
    let res := associateEvidence docId segments fieldName presence recordId maxChars [item]
    (∃ ev, res.1 = [ev] ∧ ev.relationship = parseRelationship item.relationship) ∧
    res.2 = [] := by
  cases hsame with
  | inl h =>
    cases hcond with
    | inl hnd => simp [associateEvidence, associateSingleItem, h, hnonblank, hloc, hnd]
    | inr hmiss => subst hmiss; simp [associateEvidence, associateSingleItem, h, hnonblank, hloc]
  | inr h =>
    cases hcond with
    | inl hnd => simp [associateEvidence, associateSingleItem, h, hnonblank, hloc, hnd]
    | inr hmiss => subst hmiss; simp [associateEvidence, associateSingleItem, h, hnonblank, hloc]

private theorem associateSingleItem_valid
    (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (fieldName : String) (presence : FieldPresence) (recordId : String) (maxChars : Nat)
    (item : ProposedEvidenceInput)
    (hspan : ∀ cs ce, item.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce) :
    (∀ ev ∈ (associateSingleItem docId segments fieldName presence recordId maxChars item).1.toList,
      ValidProvenanceEvidence ev ∧ ev.sourceDocumentId = docId) ∧
    (∀ w ∈ (associateSingleItem docId segments fieldName presence recordId maxChars item).2,
      ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .provenance) := by
  unfold associateSingleItem
  cases hpid : item.sourceDocumentId with
  | none =>
    by_cases hnb : isNonBlank item.quote = true
    · simp only [hnb, Bool.not_true, Bool.false_eq_true, ↓reduceIte]
      cases hms : item.matchedSpan with
      | none =>
        simp only [Option.toList_some, List.mem_singleton]
        constructor
        · intro ev hev; subst hev; exact ⟨⟨hdoc, by intro _ h; cases h⟩, rfl⟩
        · intro w hw; subst hw; exact mkProvenanceWarning_valid _ _ _ _
      | some p =>
        cases p with
        | mk cs ce =>
          have ⟨hcs, hce, hord⟩ := hspan cs ce hms
          have hlocValid := location_from_span_is_valid_source_location segments cs ce hcs hce hord hsegs
          simp only [Option.toList_some, List.mem_singleton]
          constructor
          · intro ev hev; subst hev
            refine ⟨⟨hdoc, ?_⟩, rfl⟩
            intro l hl; cases hl; exact hlocValid
          · intro w hw
            split at hw
            · simp only [List.mem_singleton] at hw; subst hw
              exact mkProvenanceWarning_valid _ _ _ _
            · simp at hw
    · simp [hnb]
  | some pid =>
    by_cases hne : pid != docId
    · simp only [hne, ↓reduceIte, Option.toList_none, List.not_mem_nil, List.mem_singleton]
      exact ⟨by intro _ h; exact False.elim h, fun w hw => by subst hw; exact mkProvenanceWarning_valid _ _ _ _⟩
    · simp only [hne, Bool.false_eq_true, ↓reduceIte]
      by_cases hnb : isNonBlank item.quote = true
      · simp only [hnb, Bool.not_true, Bool.false_eq_true, ↓reduceIte]
        cases hms : item.matchedSpan with
        | none =>
          simp only [Option.toList_some, List.mem_singleton]
          constructor
          · intro ev hev; subst hev; exact ⟨⟨hdoc, by intro _ h; cases h⟩, rfl⟩
          · intro w hw; subst hw; exact mkProvenanceWarning_valid _ _ _ _
        | some p =>
          cases p with
          | mk cs ce =>
            have ⟨hcs, hce, hord⟩ := hspan cs ce hms
            have hlocValid := location_from_span_is_valid_source_location segments cs ce hcs hce hord hsegs
            simp only [Option.toList_some, List.mem_singleton]
            constructor
            · intro ev hev; subst hev
              refine ⟨⟨hdoc, ?_⟩, rfl⟩
              intro l hl; cases hl; exact hlocValid
            · intro w hw
              split at hw
              · simp only [List.mem_singleton] at hw; subst hw
                exact mkProvenanceWarning_valid _ _ _ _
              · simp at hw
      · simp [hnb]

/-- Every provenance entry emitted by `associateEvidence` satisfies `ValidProvenanceEvidence` and
    references `docId`. -/
theorem associate_evidence_all_entries_valid_and_bound
    (docId : String) (hdoc : docId ≠ "")
    (segments : List DocumentSegment) (hsegs : ∀ seg ∈ segments, ValidDocumentSegment seg)
    (fieldName : String) (presence : FieldPresence) (recordId : String) (maxChars : Nat)
    (items : List ProposedEvidenceInput)
    (hspans : ∀ it ∈ items, ∀ cs ce, it.matchedSpan = some (cs, ce) → cs ≥ 0 ∧ ce ≥ 0 ∧ cs ≤ ce) :
    ∀ ev ∈ (associateEvidence docId segments fieldName presence recordId maxChars items).1,
      ValidProvenanceEvidence ev ∧ ev.sourceDocumentId = docId := by
  induction items with
  | nil => intro ev hev; simp [associateEvidence] at hev
  | cons item rest ih =>
    intro ev hev
    unfold associateEvidence at hev
    dsimp only at hev
    rw [List.mem_append] at hev
    cases hev with
    | inl h1 =>
      exact (associateSingleItem_valid docId hdoc segments hsegs fieldName presence recordId maxChars item
        (hspans item List.mem_cons_self)).1 ev h1
    | inr h2 =>
      exact ih (fun it hit => hspans it (List.mem_cons_of_mem _ hit)) ev h2

private theorem associateSingleItem_warnings_valid
    (docId : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String) (maxChars : Nat)
    (item : ProposedEvidenceInput) :
    ∀ w ∈ (associateSingleItem docId segments fieldName presence recordId maxChars item).2,
      ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .provenance := by
  unfold associateSingleItem
  cases item.sourceDocumentId with
  | none =>
    by_cases hnb : isNonBlank item.quote = true
    · simp only [hnb, Bool.not_true, Bool.false_eq_true, ↓reduceIte]
      cases item.matchedSpan with
      | none =>
        intro w hw; simp only [List.mem_singleton] at hw; subst hw
        exact mkProvenanceWarning_valid _ _ _ _
      | some p =>
        intro w hw
        dsimp only at hw
        split at hw
        · simp only [List.mem_singleton] at hw; subst hw
          exact mkProvenanceWarning_valid _ _ _ _
        · simp at hw
    · simp [hnb]
  | some pid =>
    by_cases hne : pid != docId
    · simp only [hne, ↓reduceIte]
      intro w hw; simp only [List.mem_singleton] at hw; subst hw
      exact mkProvenanceWarning_valid _ _ _ _
    · simp only [hne, Bool.false_eq_true, ↓reduceIte]
      by_cases hnb : isNonBlank item.quote = true
      · simp only [hnb, Bool.not_true, Bool.false_eq_true, ↓reduceIte]
        cases item.matchedSpan with
        | none =>
          intro w hw; simp only [List.mem_singleton] at hw; subst hw
          exact mkProvenanceWarning_valid _ _ _ _
        | some p =>
          intro w hw
          dsimp only at hw
          split at hw
          · simp only [List.mem_singleton] at hw; subst hw
            exact mkProvenanceWarning_valid _ _ _ _
          · simp at hw
      · simp [hnb]

/-- Every warning emitted by `associateEvidence` is a valid `PROVENANCE`-stage `WARNING`. -/
theorem associate_evidence_all_warnings_are_provenance_warnings
    (docId : String) (segments : List DocumentSegment)
    (fieldName : String) (presence : FieldPresence) (recordId : String) (maxChars : Nat)
    (items : List ProposedEvidenceInput) :
    ∀ w ∈ (associateEvidence docId segments fieldName presence recordId maxChars items).2,
      ValidDiagnosticIssue w ∧ w.severity = .warning ∧ w.stage = .provenance := by
  induction items with
  | nil => intro w hw; simp [associateEvidence] at hw
  | cons item rest ih =>
    intro w hw
    unfold associateEvidence at hw
    dsimp only at hw
    rw [List.mem_append] at hw
    cases hw with
    | inl h1 => exact associateSingleItem_warnings_valid docId segments fieldName presence recordId maxChars item w h1
    | inr h2 => exact ih w h2

end StructaFormal
