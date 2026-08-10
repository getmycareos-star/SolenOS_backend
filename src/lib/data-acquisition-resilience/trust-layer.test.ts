/**
 * Source-Pointer Trust Layer — acceptance tests.
 *
 * Run: npx tsx src/lib/data-acquisition-resilience/trust-layer.test.ts
 *
 * Verifies the three-layer invariant:
 *   MODEL (initialEvidenceStatus) -> APPLICATION (verify/enforce) -> DATABASE (migration 076 CHECK)
 *
 * Core invariant: NO claim stored as confirmed/reported WITHOUT a verified exact
 * source pointer. Validation may only DOWNGRADE evidence_status, never upgrade.
 * Numeric confidence is kept separate from evidence_status.
 */
import assert from "node:assert";
import {
  verifySourcePointer,
  enforceSourcePointer,
  enforceSourcePointersForRawInput,
  enforceValidatedEventPointer,
  enforceEvidenceStatus,
  requiresSourcePointer,
} from "./source-pointer";
import {
  recordDowngrade,
  listDowngrades,
  listDowngradesForClaim,
  clearDowngradeLog,
} from "./source-pointer-store";
import { initialEvidenceStatus } from "./extract-candidates";
import { ingestRawInput, resetDareStore, getDareProjection } from "./pipeline";
import type { ExtractionCandidate, RawInput, ValidatedCareEvent } from "./types";

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

function makeCandidate(over: Partial<ExtractionCandidate> = {}): ExtractionCandidate {
  return {
    id: "ec_test_1",
    raw_input_id: "ri_test_1",
    extracted_fact: "Mother fell yesterday",
    event_signal: "possible_fall",
    confidence: 0.8,
    confidence_sources: ["nlp_model"],
    source_span: "Mother fell yesterday",
    extraction_method: "user_input",
    ambiguity_flags: [],
    completeness: "complete",
    missing_fields: ["consequence"],
    created_at: new Date().toISOString(),
    evidence_status: "reported",
    source_span_verified: false,
    source_span_start_offset: 0,
    source_span_end_offset: 20,
    ...over,
  };
}

function makeRawInput(content: string, id = "ri_test_1"): RawInput {
  return {
    id,
    caregiver_id: "cg_test",
    input_type: "text",
    content,
    ocr_confidence: null,
    document_id: null,
    document_name: null,
    captured_at: new Date().toISOString(),
    metadata: {},
  };
}

function makeValidated(over: Partial<ValidatedCareEvent> = {}): ValidatedCareEvent {
  return {
    id: "ve_test_1",
    raw_input_id: "ri_test_1",
    candidate_id: "ec_test_1",
    extracted_fact: "Mother fell yesterday",
    event_signal: "possible_fall",
    confidence_score: 0.8,
    confidence_sources: ["nlp_model"],
    validated_at: new Date().toISOString(),
    validation_method: "auto_threshold",
    entities: [],
    attributes: {},
    document_id: null,
    evidence_status: "reported",
    source_span_verified: false,
    source_span_start_offset: 0,
    source_span_end_offset: 20,
    ...over,
  };
}

console.log("\n=== LAYER 1: MODEL (initialEvidenceStatus) ===\n");

check("complete, unambiguous, non-OCR span => 'reported'", () => {
  const s = initialEvidenceStatus({
    ambiguity_flags: [],
    completeness: "complete",
    source_span: "Mother fell yesterday",
    confidence_sources: ["nlp_model"],
  });
  assert.strictEqual(s, "reported");
});

check("contradictory_sources flag => 'contradictory'", () => {
  const s = initialEvidenceStatus({
    ambiguity_flags: ["contradictory_sources"],
    completeness: "complete",
    source_span: "Mother fell yesterday",
    confidence_sources: ["nlp_model"],
  });
  assert.strictEqual(s, "contradictory");
});

check("insufficient completeness => 'unknown' (never confirmed)", () => {
  const s = initialEvidenceStatus({
    ambiguity_flags: [],
    completeness: "insufficient",
    source_span: "fell",
    confidence_sources: ["nlp_model"],
  });
  assert.strictEqual(s, "unknown");
});

check("empty source_span => 'unknown'", () => {
  const s = initialEvidenceStatus({
    ambiguity_flags: [],
    completeness: "complete",
    source_span: "",
    confidence_sources: ["nlp_model"],
  });
  assert.strictEqual(s, "unknown");
});

check("OCR-backed span cannot be 'confirmed' at model layer", () => {
  const s = initialEvidenceStatus({
    ambiguity_flags: [],
    completeness: "complete",
    source_span: "Mother fell yesterday",
    confidence_sources: ["nlp_model", "ocr"],
  });
  assert.notStrictEqual(s, "confirmed");
  assert.strictEqual(s, "inferred");
});

console.log("\n=== LAYER 2a: verifySourcePointer (exact-match determinism) ===\n");

const ORIG = "Mother fell yesterday and was taken to the hospital. She is now resting.";

check("exact slice match => verified", () => {
  const start = ORIG.indexOf("fell");
  const r = verifySourcePointer("fell", start, start + 4, ORIG);
  assert.deepStrictEqual(r, { verified: true, reason: "exact_match" });
});

check("whitespace/case mismatch (normalization) => span_mismatch", () => {
  const r = verifySourcePointer("MOTHER FELL", 0, 11, ORIG);
  assert.strictEqual(r.verified, false);
  assert.strictEqual(r.reason, "span_mismatch");
});

check("missing source_span => missing_source_span", () => {
  const r = verifySourcePointer("", 0, 5, ORIG);
  assert.strictEqual(r.reason, "missing_source_span");
});

check("missing start offset => missing_start_offset", () => {
  const r = verifySourcePointer("fell", null, 5, ORIG);
  assert.strictEqual(r.reason, "missing_start_offset");
});

check("missing end offset => missing_end_offset", () => {
  const r = verifySourcePointer("fell", 0, null, ORIG);
  assert.strictEqual(r.reason, "missing_end_offset");
});

check("negative start offset => negative_start_offset", () => {
  const r = verifySourcePointer("fell", -1, 5, ORIG);
  assert.strictEqual(r.reason, "negative_start_offset");
});

check("end <= start => end_not_greater_than_start", () => {
  const r = verifySourcePointer("fell", 5, 5, ORIG);
  assert.strictEqual(r.reason, "end_not_greater_than_start");
});

check("end beyond original text => end_beyond_original_text", () => {
  const r = verifySourcePointer("fell", 7, ORIG.length + 10, ORIG);
  assert.strictEqual(r.reason, "end_beyond_original_text");
});

console.log("\n=== LAYER 2b: enforceEvidenceStatus (downgrade-only) ===\n");

check("confirmed + valid pointer => stays confirmed", () => {
  const r = enforceEvidenceStatus("confirmed", { verified: true, reason: "exact_match" });
  assert.strictEqual(r.status, "confirmed");
});

check("reported + invalid pointer => downgraded to unknown", () => {
  const r = enforceEvidenceStatus("reported", { verified: false, reason: "span_mismatch" });
  assert.strictEqual(r.status, "unknown");
  assert.strictEqual(r.reason, "span_mismatch");
});

check("confirmed + missing pointer => downgraded to unknown", () => {
  const r = enforceEvidenceStatus("confirmed", { verified: false, reason: "missing_source_span" });
  assert.strictEqual(r.status, "unknown");
});

check("inferred is never downgraded", () => {
  const r = enforceEvidenceStatus("inferred", { verified: false, reason: "span_mismatch" });
  assert.strictEqual(r.status, "inferred");
});

check("contradictory is never downgraded", () => {
  const r = enforceEvidenceStatus("contradictory", { verified: false, reason: "span_mismatch" });
  assert.strictEqual(r.status, "contradictory");
});

check("never upgrades a low status", () => {
  const r = enforceEvidenceStatus("unknown", { verified: true, reason: "exact_match" });
  assert.strictEqual(r.status, "unknown");
});

console.log("\n=== LAYER 2c: enforceSourcePointer (app validator) ===\n");

check("invalid pointer nulls offsets + downgrades + logs", () => {
  clearDowngradeLog();
  const bad = makeCandidate({
    evidence_status: "reported",
    source_span: "Mother fell yesterday",
    source_span_start_offset: 5, // wrong offset -> span mismatch against ORIG below
    source_span_end_offset: 25,
  });
  const { candidate, downgrade } = enforceSourcePointer(bad, ORIG, "ri_x");
  assert.strictEqual(candidate.source_span_verified, false);
  assert.strictEqual(candidate.evidence_status, "unknown");
  assert.ok(downgrade, "expected a downgrade record");
  assert.strictEqual(downgrade!.final_status, "unknown");
  assert.strictEqual(downgrade!.original_status, "reported");
  assert.strictEqual(downgrade!.original_confidence, bad.confidence);
  assert.strictEqual(downgrade!.final_confidence, bad.confidence, "confidence NEVER changes");
});

check("valid pointer preserves reported status + no downgrade", () => {
  clearDowngradeLog();
  const good = makeCandidate({
    evidence_status: "reported",
    source_span: "Mother fell yesterday",
    source_span_start_offset: 0,
    source_span_end_offset: "Mother fell yesterday".length,
  });
  const { candidate, downgrade } = enforceSourcePointer(good, ORIG, "ri_x");
  assert.strictEqual(candidate.source_span_verified, true);
  assert.strictEqual(candidate.evidence_status, "reported");
  assert.strictEqual(downgrade, null);
});

check("enforceSourcePointersForRawInput returns downgrades", () => {
  clearDowngradeLog();
  const raw = makeRawInput(ORIG);
  const c1 = makeCandidate({
    id: "c_good",
    evidence_status: "reported",
    source_span: "Mother fell yesterday",
    source_span_start_offset: 0,
    source_span_end_offset: "Mother fell yesterday".length,
  });
  const c2 = makeCandidate({
    id: "c_bad",
    evidence_status: "reported",
    source_span: "taken to the hospital",
    source_span_start_offset: 999,
    source_span_end_offset: 1010,
  });
  const { candidates, downgrades } = enforceSourcePointersForRawInput([c1, c2], raw);
  assert.strictEqual(candidates[0].source_span_verified, true);
  assert.strictEqual(candidates[1].source_span_verified, false);
  assert.strictEqual(candidates[1].evidence_status, "unknown");
  assert.strictEqual(downgrades.length, 1);
  assert.strictEqual(downgrades[0].claim_id, "c_bad");
});

check("downgrades are recorded and queryable", () => {
  clearDowngradeLog();
  const bad = makeCandidate({
    id: "c_log",
    evidence_status: "confirmed",
    source_span_start_offset: null,
    source_span_end_offset: null,
  });
  const { downgrade } = enforceSourcePointer(bad, ORIG, "ri_log");
  assert.ok(downgrade);
  recordDowngrade(downgrade!);
  assert.strictEqual(listDowngrades().length, 1);
  assert.strictEqual(listDowngradesForClaim("c_log").length, 1);
});

console.log("\n=== LAYER 2d: enforceValidatedEventPointer (persisted truth) ===\n");

check("confirmed validated event w/o original text => untouched (no crash)", () => {
  const ev = makeValidated({ evidence_status: "confirmed" });
  const { event, downgrade } = enforceValidatedEventPointer(ev, null);
  assert.strictEqual(event.evidence_status, "confirmed");
  assert.strictEqual(downgrade, null);
});

check("reported validated event w/ invalid pointer => downgraded to unknown", () => {
  const ev = makeValidated({
    evidence_status: "reported",
    extracted_fact: "Mother fell yesterday",
    source_span_start_offset: 3,
    source_span_end_offset: 23,
  });
  const { event, downgrade } = enforceValidatedEventPointer(ev, ORIG);
  assert.strictEqual(event.evidence_status, "unknown");
  assert.strictEqual(event.source_span_verified, false);
  assert.ok(downgrade);
});

check("reported validated event w/ valid pointer => preserved", () => {
  const ev = makeValidated({
    evidence_status: "reported",
    extracted_fact: "Mother fell yesterday",
    source_span_start_offset: 0,
    source_span_end_offset: "Mother fell yesterday".length,
  });
  const { event, downgrade } = enforceValidatedEventPointer(ev, ORIG);
  assert.strictEqual(event.evidence_status, "reported");
  assert.strictEqual(event.source_span_verified, true);
  assert.strictEqual(downgrade, null);
});

console.log("\n=== INVARIANT HELPERS ===\n");

check("requiresSourcePointer only for confirmed/reported", () => {
  assert.strictEqual(requiresSourcePointer("confirmed"), true);
  assert.strictEqual(requiresSourcePointer("reported"), true);
  assert.strictEqual(requiresSourcePointer("inferred"), false);
  assert.strictEqual(requiresSourcePointer("unknown"), false);
  assert.strictEqual(requiresSourcePointer("contradictory"), false);
});

console.log("\n=== END-TO-END: 5 REALISTIC CAREGIVER INPUTS ===\n");

const caregiverInputs: Array<{ content: string; expectInvariantHeld: boolean }> = [
  {
    content:
      "This morning mom refused to eat breakfast and seemed confused. She kept asking where dad is.",
    expectInvariantHeld: true,
  },
  {
    content:
      "Dad called Dr. Patel at 2pm to report that the new medication was making him dizzy. He will follow up next week.",
    expectInvariantHeld: true,
  },
  {
    content:
      "Last night she fell in the hallway but said she wasn't hurt. She seemed a bit disoriented afterward.",
    expectInvariantHeld: true,
  },
  {
    content:
      "The insurance claim for the hospital visit in March was rejected. We are planning to appeal.",
    expectInvariantHeld: true,
  },
  {
    content:
      "He stopped eating lunch and has been sleeping a lot today. The doctor's appointment is tomorrow.",
    expectInvariantHeld: true,
  },
];

function assertGlobalInvariant(events: ValidatedCareEvent[], candidates: ExtractionCandidate[]): void {
  const all = [
    ...events.map((e) => ({
      id: e.id,
      status: e.evidence_status,
      verified: e.source_span_verified,
      offsets: [e.source_span_start_offset, e.source_span_end_offset],
    })),
    ...candidates.map((c) => ({
      id: c.id,
      status: c.evidence_status,
      verified: c.source_span_verified,
      offsets: [c.source_span_start_offset, c.source_span_end_offset],
    })),
  ];
  for (const item of all) {
    if (item.status === "confirmed" || item.status === "reported") {
      assert.strictEqual(
        item.verified,
        true,
        `claim ${item.id} has status ${item.status} but source_span_verified=false`,
      );
      const [s, e] = item.offsets;
      assert.ok(s !== null && e !== null && e > s, `claim ${item.id} has invalid offsets`);
    }
  }
}

caregiverInputs.forEach((input, i) => {
  check(`caregiver input #${i + 1} — invariant holds end-to-end`, () => {
    resetDareStore();
    clearDowngradeLog();
    const result = ingestRawInput({
      caregiver_id: `cg_real_${i + 1}`,
      content: input.content,
      input_type: "text",
    });
    // Application-level invariant: no confirmed/reported claim without a verified pointer.
    assertGlobalInvariant(result.validated_events, result.candidates);
    // Every downgrade logged preserves numeric confidence.
    for (const d of listDowngrades()) {
      assert.strictEqual(d.original_confidence, d.final_confidence);
    }
    // Projection is queryable and consistent with the same invariant.
    assertGlobalInvariant(
      getDareProjection(`cg_real_${i + 1}`).validated_events,
      result.candidates,
    );
  });
});

console.log(`\n========================================`);
console.log(`RESULT: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  console.log("\nFAILED TESTS:");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
} else {
  console.log("ALL SOURCE-POINTER TRUST LAYER TESTS PASSED.");
}
