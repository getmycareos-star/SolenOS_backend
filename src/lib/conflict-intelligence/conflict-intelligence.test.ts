import assert from "node:assert";
import {
  analyzeCompatibility,
  analyzeTemporalContext,
  createTemporalAssertion,
  createRangeTemporalAssertion,
  compareSources,
  createSourceLineage,
  createClaimSource,
  createConflictObject,
  transitionResolutionStatus,
  reopenConflict,
  supersedeConflict,
  invalidateConflict,
  generateConflictExplanation,
  detectConflicts,
  checkConflictReopening,
  runConflictIntelligence,
  resolveConflict,
  applyExplicitCorrection,
  invalidateConflictObject,
  CONFLICT_INTELLIGENCE_IDENTITY,
  CONFLICT_INTELLIGENCE_DEFINING_PRINCIPLE,
  NO_SILENT_RESOLUTION_POLICY,
  CONTRADICTION_DETECTION_RULES,
} from "./index";
import type {
  ConflictClaim,
  ConflictObject,
  ConflictResolutionInput,
} from "./types";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function check(name: string, fn: () => void): void {
  try {
    fn();
    passed += 1;
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failed += 1;
    const msg = err instanceof Error ? err.message : String(err);
    failures.push(`${name}: ${msg}`);
    console.log(`  FAIL  ${name} -> ${msg}`);
  }
}

function makeClaim(over: Partial<ConflictClaim> = {}): ConflictClaim {
  return {
    claim_id: `claim_${Math.random().toString(36).slice(2, 9)}`,
    subject: "Medication X",
    predicate: "is_current",
    object: "active",
    raw_text: "Medication X is active.",
    temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8),
    evidence_derivation: "first_hand_report",
    source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null),
    numeric_confidence: 0.8,
    evidence_status: "reported",
    created_at: new Date().toISOString(),
    superseded_by: null,
    ...over,
  };
}

console.log("\n=== CONTRACT CONSTANTS ===\n");

check("CONFLICT_INTELLIGENCE_IDENTITY is defined", () => {
  assert.ok(CONFLICT_INTELLIGENCE_IDENTITY.length > 0);
});

check("NO_SILENT_RESOLUTION_POLICY is defined", () => {
  assert.ok(NO_SILENT_RESOLUTION_POLICY.includes("explicit, defensible resolution rule"));
});

check("CONTRADICTION_DETECTION_RULES contains key invariants", () => {
  assert.ok(CONTRADICTION_DETECTION_RULES.includes("difference_is_not_contradiction"));
  assert.ok(CONTRADICTION_DETECTION_RULES.includes("temporal_change_is_not_contradiction"));
  assert.ok(CONTRADICTION_DETECTION_RULES.includes("source_authority_is_not_truth"));
  assert.ok(CONTRADICTION_DETECTION_RULES.includes("majority_vote_is_not_truth"));
  assert.ok(CONTRADICTION_DETECTION_RULES.includes("preserve_all_competing_claims"));
});

console.log("\n=== COMPATIBILITY ANALYSIS ===\n");

check("identical claims => compatible", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2", raw_text: a.raw_text });
  const result = analyzeCompatibility({ claim_a: a, claim_b: b, temporal_context: { current_time: new Date().toISOString(), claim_a_temporal: a.temporal_assertion, claim_b_temporal: b.temporal_assertion } });
  assert.strictEqual(result.status, "compatible");
});

check("same subject/predicate, different object, no reconciling factors => genuine_conflict", () => {
  const a = makeClaim({ claim_id: "c1", object: "active", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const b = makeClaim({ claim_id: "c2", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const result = analyzeCompatibility({ claim_a: a, claim_b: b, temporal_context: { current_time: new Date().toISOString(), claim_a_temporal: a.temporal_assertion, claim_b_temporal: b.temporal_assertion } });
  assert.strictEqual(result.status, "genuine_conflict");
});

check("same subject/predicate, different object, different event times => apparent_conflict", () => {
  const a = makeClaim({ claim_id: "c1", object: "active", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const b = makeClaim({ claim_id: "c2", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) });
  const result = analyzeCompatibility({ claim_a: a, claim_b: b, temporal_context: { current_time: new Date().toISOString(), claim_a_temporal: a.temporal_assertion, claim_b_temporal: b.temporal_assertion } });
  assert.strictEqual(result.status, "apparent_conflict");
  assert.ok(result.reconciling_factors.includes("temporal_change"));
});

check("different specificity => compatible", () => {
  const a = makeClaim({ claim_id: "c1", object: "Alzheimer's disease", predicate: "has_diagnosis" });
  const b = makeClaim({ claim_id: "c2", object: "dementia, unspecified", predicate: "has_diagnosis" });
  const result = analyzeCompatibility({ claim_a: a, claim_b: b, temporal_context: { current_time: new Date().toISOString(), claim_a_temporal: a.temporal_assertion, claim_b_temporal: b.temporal_assertion } });
  assert.strictEqual(result.status, "apparent_conflict");
  assert.ok(result.reconciling_factors.includes("specificity_difference"));
});

check("subjective perspectives => compatible", () => {
  const a = makeClaim({ claim_id: "c1", predicate: "is_doing_well", object: "well", raw_text: "Mom is doing well." });
  const b = makeClaim({ claim_id: "c2", predicate: "is_doing_well", object: "struggling", raw_text: "Mom is struggling." });
  const result = analyzeCompatibility({ claim_a: a, claim_b: b, temporal_context: { current_time: new Date().toISOString(), claim_a_temporal: a.temporal_assertion, claim_b_temporal: b.temporal_assertion } });
  assert.strictEqual(result.status, "apparent_conflict");
  assert.ok(result.reconciling_factors.includes("subjective_perspective"));
});

console.log("\n=== TEMPORAL ANALYSIS ===\n");

check("only event dates with gap => legitimate_state_transition", () => {
  const claims = [
    makeClaim({ claim_id: "c1", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) }),
    makeClaim({ claim_id: "c2", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) }),
  ];
  const result = analyzeTemporalContext(claims);
  assert.strictEqual(result.interpretation, "legitimate_state_transition");
  assert.ok(result.confidence > 0.5);
});

check("mixed document and event dates => unresolved_temporal_conflict", () => {
  const claims = [
    makeClaim({ claim_id: "c1", temporal_assertion: createTemporalAssertion("document_time", "2026-08-20", 0.9) }),
    makeClaim({ claim_id: "c2", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) }),
  ];
  const result = analyzeTemporalContext(claims);
  assert.strictEqual(result.interpretation, "unresolved_temporal_conflict");
});

check("no temporal assertions => unresolved_temporal_conflict", () => {
  const claims = [
    makeClaim({ claim_id: "c1", temporal_assertion: null }),
    makeClaim({ claim_id: "c2", temporal_assertion: null }),
  ];
  const result = analyzeTemporalContext(claims);
  assert.strictEqual(result.interpretation, "unresolved_temporal_conflict");
});

console.log("\n=== SOURCE COMPARISON ===\n");

check("independent claims => corroboration_quality weak/moderate/strong", () => {
  const claims = [
    makeClaim({ claim_id: "c1", source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null) }),
    makeClaim({ claim_id: "c2", source: createClaimSource("caregiver", "Caregiver B", "src_b", "cg_b", null, "ri_b", null) }),
    makeClaim({ claim_id: "c3", source: createClaimSource("caregiver", "Caregiver C", "src_c", "cg_c", null, "ri_c", null) }),
  ];
  const result = compareSources(claims);
  assert.strictEqual(result.corroboration_quality, "strong");
  assert.strictEqual(result.independent_claims.length, 3);
});

check("duplicate sources => dependent claims, warning issued", () => {
  const lineage = createSourceLineage("copied_from", "src_original", "Copied from original document");
  const claims = [
    makeClaim({ claim_id: "c1", source: createClaimSource("document", "Doc A", "src_a", null, "doc_a", "ri_a", lineage) }),
    makeClaim({ claim_id: "c2", source: createClaimSource("document", "Doc B", "src_b", null, "doc_b", "ri_b", createSourceLineage("derived_from", "src_a", "Extracted from Doc A")) }),
  ];
  const result = compareSources(claims);
  assert.ok(result.warnings.length > 0);
  assert.ok(result.warnings.some((w) => w.includes("Not independent")));
});

console.log("\n=== CONFLICT OBJECT LIFECYCLE ===\n");

check("createConflictObject => unresolved, with history", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2", object: "discontinued" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test conflict", null);
  assert.strictEqual(conflict.resolution_status, "unresolved");
  assert.strictEqual(conflict.claims.length, 2);
  assert.strictEqual(conflict.history.length, 1);
  assert.strictEqual(conflict.history[0]!.action, "detected");
});

check("transitionResolutionStatus => resolved, with evidence", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2", object: "discontinued" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test conflict", null);
  const resolved = transitionResolutionStatus(conflict, "resolved", "Direct observation", "direct_observation", ["c2"], "user");
  assert.strictEqual(resolved.resolution_status, "resolved");
  assert.strictEqual(resolved.resolution_evidence?.mechanism, "direct_observation");
  assert.strictEqual(resolved.history.length, 2);
  assert.strictEqual(resolved.history[1]!.action, "resolved");
});

check("reopenConflict => unresolved again", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2", object: "discontinued" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test conflict", null);
  const resolved = transitionResolutionStatus(conflict, "resolved", "Direct observation", "direct_observation", ["c2"], "user");
  const reopened = reopenConflict(resolved, "New contradictory evidence", "system");
  assert.strictEqual(reopened.resolution_status, "unresolved");
  assert.strictEqual(reopened.resolution_evidence, null);
  assert.strictEqual(reopened.history.length, 3);
  assert.strictEqual(reopened.history[2]!.action, "reopened");
});

check("supersedeConflict => superseded, with explanation", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test conflict", null);
  const superseded = supersedeConflict(conflict, "c2", "Explicit correction", "caregiver");
  assert.strictEqual(superseded.resolution_status, "superseded");
  assert.strictEqual(superseded.resolution_evidence?.mechanism, "explicit_correction");
});

check("invalidateConflict => invalidated", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test conflict", null);
  const invalidated = invalidateConflict(conflict, "Claims were about different subjects", "system");
  assert.strictEqual(invalidated.resolution_status, "invalidated");
});

console.log("\n=== EXPLANATION GENERATION ===\n");

check("generateConflictExplanation => detailed, accurate", () => {
  const a = makeClaim({ claim_id: "c1", source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null) });
  const b = makeClaim({ claim_id: "c2", object: "discontinued", source: createClaimSource("document", "Med List", "src_b", null, "doc_b", "ri_b", null) });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Medication status conflict", null);
  const explanation = generateConflictExplanation(conflict);
  assert.ok(explanation.summary.includes("Conflicting evidence"));
  assert.ok(explanation.detailed.includes("CONFLICT:"));
  assert.ok(explanation.detailed.includes("COMPETING CLAIMS:"));
  assert.ok(explanation.detailed.includes("Caregiver A"));
  assert.ok(explanation.detailed.includes("Med List"));
  assert.strictEqual(explanation.compatibility_status, "genuine_conflict");
  assert.strictEqual(explanation.resolution_status, "unresolved");
  assert.ok(explanation.resolution_path === null);
});

console.log("\n=== CONFLICT DETECTION ===\n");

check("detectConflicts => no conflict for unrelated claims", () => {
  const newClaim = makeClaim({ claim_id: "c1", subject: "Medication X", predicate: "is_current" });
  const existing = [makeClaim({ claim_id: "c2", subject: "Medication Y", predicate: "is_current" })];
  const result = detectConflicts({ new_claim: newClaim, existing_claims: existing, temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
  assert.strictEqual(result.conflicts.length, 0);
});

check("detectConflicts => genuine conflict for incompatible claims", () => {
  const newClaim = makeClaim({ claim_id: "c1", object: "active", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const existing = [makeClaim({ claim_id: "c2", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) })];
  const result = detectConflicts({ new_claim: newClaim, existing_claims: existing, temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, false);
  assert.strictEqual(result.conflicts.length, 1);
  assert.strictEqual(result.conflicts[0]!.compatibility_status, "genuine_conflict");
});

check("detectConflicts => apparent conflict resolved for temporal change", () => {
  const newClaim = makeClaim({ claim_id: "c1", object: "active", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const existing = [makeClaim({ claim_id: "c2", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) })];
  const result = detectConflicts({ new_claim: newClaim, existing_claims: existing, temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
  assert.strictEqual(result.apparent_conflicts_resolved.length, 1);
  assert.strictEqual(result.apparent_conflicts_resolved[0]!.resolution, "state_transition_identified");
});

console.log("\n=== CONFLICT REOPENING ===\n");

check("checkConflictReopening => reopens resolved conflict when new genuine conflict emerges", () => {
  const newClaim = makeClaim({ claim_id: "c3", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const existingConflict = createConflictObject("state", [makeClaim({ claim_id: "c1" })], "genuine_conflict", "Test", null);
  const resolved = transitionResolutionStatus(existingConflict, "resolved", "Previous resolution", "direct_observation", ["c1"], "user");
  const result = checkConflictReopening(newClaim, [resolved]);
  assert.strictEqual(result.updatedConflicts.length, 1);
  assert.strictEqual(result.updatedConflicts[0]!.resolution_status, "unresolved");
  assert.strictEqual(result.updatedConflicts[0]!.history.length, 3);
});

console.log("\n=== PIPELINE ORCHESTRATION ===\n");

check("runConflictIntelligence => full pipeline", () => {
  const newClaim = makeClaim({ claim_id: "c1", object: "active" });
  const existing = [makeClaim({ claim_id: "c2", object: "discontinued" })];
  const result = runConflictIntelligence({
    newClaim,
    existingClaims: existing,
    existingConflicts: [],
    temporalContext: { current_time: new Date().toISOString(), event_timeline: [] },
  });
  assert.ok(result.hasUnresolvedConflicts);
  assert.strictEqual(result.newConflicts.length, 1);
  assert.ok(result.explanation !== null);
});

check("resolveConflict => provisionally resolved for temporal clarification", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  const input: ConflictResolutionInput = {
    mechanism: "temporal_clarification",
    reason: "Dates clarified",
    evidenceClaimIds: ["c1"],
    actor: "user",
  };
  const resolved = resolveConflict(conflict, input);
  assert.strictEqual(resolved.resolution_status, "provisionally_resolved");
});

check("applyExplicitCorrection => superseded", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  const superseded = applyExplicitCorrection(conflict, "c2", "Correction received", "caregiver");
  assert.strictEqual(superseded.resolution_status, "superseded");
});

check("invalidateConflictObject => invalidated", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  const invalidated = invalidateConflictObject(conflict, "False alarm", "system");
  assert.strictEqual(invalidated.resolution_status, "invalidated");
});

console.log("\n=== END-TO-END: HARD TEST CASES ===\n");

check("Case A — Two caregivers disagree => conflict preserved, not resolved", () => {
  const caregiverA = makeClaim({
    claim_id: "cg_a",
    raw_text: "Mom takes Medication X every morning.",
    source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null),
    evidence_derivation: "first_hand_report",
  });
  const caregiverB = makeClaim({
    claim_id: "cg_b",
    raw_text: "Mom stopped taking Medication X.",
    object: "discontinued",
    source: createClaimSource("caregiver", "Caregiver B", "src_b", "cg_b", null, "ri_b", null),
    evidence_derivation: "first_hand_report",
  });
  const result = detectConflicts({ new_claim: caregiverA, existing_claims: [caregiverB], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, false);
  assert.strictEqual(result.conflicts[0]!.resolution_status, "unresolved");
  assert.strictEqual(result.conflicts[0]!.claims.length, 2);
});

check("Case B — Different dates => not automatically contradictory", () => {
  const a = makeClaim({ claim_id: "c1", raw_text: "Medication X stopped March 1.", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) });
  const b = makeClaim({ claim_id: "c2", raw_text: "Medication X stopped March 15.", temporal_assertion: createTemporalAssertion("event_time", "2026-03-15", 0.8) });
  const result = detectConflicts({ new_claim: a, existing_claims: [b], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
});

check("Case C — Legitimate state transition => not a conflict", () => {
  const jan = makeClaim({ claim_id: "c1", raw_text: "Medication X active in January.", object: "active", temporal_assertion: createTemporalAssertion("event_time", "2026-01-01", 0.8) });
  const mar = makeClaim({ claim_id: "c2", raw_text: "Medication X discontinued in March.", object: "discontinued", temporal_assertion: createTemporalAssertion("event_time", "2026-03-01", 0.8) });
  const result = detectConflicts({ new_claim: jan, existing_claims: [mar], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
});

check("Case D — Stale record does not auto-resolve", () => {
  const stale = makeClaim({
    claim_id: "stale",
    raw_text: "2024 medication list: Medication X active.",
    source: createClaimSource("document", "2024 Medication List", "src_stale", null, "doc_stale", "ri_stale", null),
    temporal_assertion: createTemporalAssertion("document_time", "2024-01-01", 0.9),
  });
  const fresh = makeClaim({
    claim_id: "fresh",
    raw_text: "Caregiver report: Medication X discontinued.",
    object: "discontinued",
    source: createClaimSource("caregiver", "Caregiver", "src_fresh", "cg_1", null, "ri_fresh", null),
    temporal_assertion: createTemporalAssertion("event_time", "2026-08-01", 0.8),
  });
  const result = detectConflicts({ new_claim: fresh, existing_claims: [stale], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, false);
  assert.strictEqual(result.conflicts[0]!.resolution_status, "unresolved");
});

check("Case E — Majority vote with duplicate sources => NOT independent corroboration", () => {
  const original = makeClaim({ claim_id: "orig", source: createClaimSource("document", "Original List", "src_orig", null, "doc_orig", "ri_orig", null) });
  const copy1 = makeClaim({ claim_id: "copy1", source: createClaimSource("document", "Copy A", "src_a", null, "doc_a", "ri_a", createSourceLineage("copied_from", "src_orig", "Copy of original")) });
  const copy2 = makeClaim({ claim_id: "copy2", source: createClaimSource("document", "Copy B", "src_b", null, "doc_b", "ri_b", createSourceLineage("copied_from", "src_orig", "Copy of original")) });
  const conflicting = makeClaim({ claim_id: "conf", object: "discontinued", source: createClaimSource("caregiver", "Caregiver", "src_conf", "cg_1", null, "ri_conf", null) });
  const sourceResult = compareSources([original, copy1, copy2, conflicting]);
  assert.ok(sourceResult.warnings.some((w) => w.includes("Not independent")));
});

check("Case F — Conflicting diagnosis specificity => not necessarily contradictory", () => {
  const a = makeClaim({ claim_id: "c1", predicate: "has_diagnosis", object: "dementia", raw_text: "Diagnosis: dementia." });
  const b = makeClaim({ claim_id: "c2", predicate: "has_diagnosis", object: "Alzheimer's disease", raw_text: "Diagnosis: Alzheimer's disease." });
  const result = detectConflicts({ new_claim: a, existing_claims: [b], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
  assert.ok(result.apparent_conflicts_resolved.some((r) => r.resolution === "specificity_reconciled"));
});

check("Case G — Subjective caregiver reports => not automatic contradiction", () => {
  const a = makeClaim({ claim_id: "c1", predicate: "is_doing_well", object: "well", raw_text: "Mom is doing well." });
  const b = makeClaim({ claim_id: "c2", predicate: "is_doing_well", object: "struggling", raw_text: "Mom is struggling." });
  const result = detectConflicts({ new_claim: a, existing_claims: [b], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, true);
});

check("Case H — Conflicting medication states => flagged as conflict without temporal context", () => {
  const a = makeClaim({ claim_id: "c1", predicate: "medication_status", object: "discontinued", raw_text: "Medication X discontinued." });
  const b = makeClaim({ claim_id: "c2", predicate: "medication_status", object: "held_temporarily", raw_text: "Medication X held temporarily." });
  const result = detectConflicts({ new_claim: a, existing_claims: [b], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.no_conflict, false);
  assert.strictEqual(result.conflicts.length, 1);
  assert.strictEqual(result.conflicts[0]!.resolution_status, "unresolved");
});

check("Case I — Explicit correction => superseded, not deleted", () => {
  const original = makeClaim({ claim_id: "orig", raw_text: "Medication X started June 1." });
  const correction = makeClaim({ claim_id: "corr", raw_text: "Correction: Medication X started June 5.", object: "corrected", evidence_derivation: "first_hand_report" });
  const conflict = createConflictObject("state", [original, correction], "genuine_conflict", "Correction conflict", null);
  const superseded = applyExplicitCorrection(conflict, "corr", "Explicit correction from source", "caregiver");
  assert.strictEqual(superseded.resolution_status, "superseded");
  assert.ok(superseded.history.some((h) => h.action === "superseded"));
});

check("Case J — Unresolved conflict => system says unresolved", () => {
  const a = makeClaim({ claim_id: "c1", object: "active", source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null) });
  const b = makeClaim({ claim_id: "c2", object: "discontinued", source: createClaimSource("caregiver", "Caregiver B", "src_b", "cg_b", null, "ri_b", null) });
  const result = detectConflicts({ new_claim: a, existing_claims: [b], temporal_context: { current_time: new Date().toISOString(), event_timeline: [] } });
  assert.strictEqual(result.conflicts[0]!.resolution_status, "unresolved");
  const explanation = generateConflictExplanation(result.conflicts[0]!);
  assert.ok(explanation.resolution_path === null);
});

check("Case K — New direct observation => resolved, old claim preserved", () => {
  const old = makeClaim({ claim_id: "old", object: "discontinued", source: createClaimSource("document", "Old Record", "src_old", null, "doc_old", "ri_old", null) });
  const newObs = makeClaim({ claim_id: "new", object: "active", evidence_derivation: "direct_observation", source: createClaimSource("caregiver", "Caregiver", "src_new", "cg_1", null, "ri_new", null) });
  const conflict = createConflictObject("state", [old, newObs], "genuine_conflict", "Observation conflict", null);
  const resolved = transitionResolutionStatus(conflict, "resolved", "Direct observation confirms active", "direct_observation", ["new"], "user");
  assert.strictEqual(resolved.resolution_status, "resolved");
  assert.strictEqual(resolved.claims.length, 2);
  assert.ok(resolved.claims.some((c) => c.claim_id === "old"));
  assert.ok(resolved.claims.some((c) => c.claim_id === "new"));
});

check("Case L — Conflict explanation => points to actual claims and evidence", () => {
  const a = makeClaim({ claim_id: "c1", raw_text: "Caregiver A: Mom takes Medication X.", source: createClaimSource("caregiver", "Caregiver A", "src_a", "cg_a", null, "ri_a", null), temporal_assertion: createTemporalAssertion("event_time", "2026-08-12", 0.8) });
  const b = makeClaim({ claim_id: "c2", raw_text: "Med List: Medication X discontinued.", object: "discontinued", source: createClaimSource("document", "Med List", "src_b", null, "doc_b", "ri_b", null), temporal_assertion: createTemporalAssertion("document_time", "2026-08-15", 0.9) });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Medication status disputed", null);
  const explanation = generateConflictExplanation(conflict);
  assert.ok(explanation.detailed.includes("Caregiver A"));
  assert.ok(explanation.detailed.includes("Med List"));
  assert.ok(explanation.detailed.includes("event_time: 2026-08-12"));
  assert.ok(explanation.detailed.includes("document_time: 2026-08-15"));
  assert.ok(explanation.why_conflicting.includes("incompatible assertions"));
});

console.log("\n=== INVARIANTS ===\n");

check("NO_SILENT_RESOLUTION_POLICY is enforced: unresolved conflicts remain unresolved", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2", object: "discontinued" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  assert.strictEqual(conflict.resolution_status, "unresolved");
  assert.strictEqual(conflict.resolution_evidence, null);
});

check("Resolution preserves history: history entries accumulate", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  let conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  conflict = transitionResolutionStatus(conflict, "resolved", "R1", "direct_observation", ["c2"], "user");
  conflict = reopenConflict(conflict, "New evidence", "system");
  conflict = transitionResolutionStatus(conflict, "resolved", "R2", "user_confirmation", ["c2"], "caregiver");
  assert.strictEqual(conflict.history.length, 4);
  assert.ok(conflict.history.some((h) => h.action === "detected"));
  assert.ok(conflict.history.some((h) => h.action === "resolved"));
  assert.ok(conflict.history.some((h) => h.action === "reopened"));
  assert.ok(conflict.history.some((h) => h.action === "resolved"));
});

check("Claims are never deleted during resolution", () => {
  const a = makeClaim({ claim_id: "c1" });
  const b = makeClaim({ claim_id: "c2" });
  const conflict = createConflictObject("state", [a, b], "genuine_conflict", "Test", null);
  const resolved = transitionResolutionStatus(conflict, "resolved", "R", "direct_observation", ["c2"], "user");
  assert.strictEqual(resolved.claims.length, 2);
  assert.ok(resolved.claims.some((c) => c.claim_id === "c1"));
  assert.ok(resolved.claims.some((c) => c.claim_id === "c2"));
});

console.log("\n========================================");
console.log(`RESULT: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.log("\nFAILED TESTS:");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
} else {
  console.log("ALL CONFLICT INTELLIGENCE TESTS PASSED.");
}
