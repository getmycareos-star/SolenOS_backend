import assert from "node:assert";
import {
  createLongitudinalCareState,
  addAssertion,
  expireAssertion,
  supersedeAssertion,
  computeCurrentState,
  computeCurrentStateForDimension,
  reconstructStateAtTime,
  computeDelta,
  detectChange,
  classifyMeaningfulChange,
  detectConflicts,
  createTransition,
  addTransition,
  establishBaseline,
  getBaselineForDimension,
  verifyStateIntegrity,
  eventToAssertions,
  trackedSituationToAssertions,
  expireAssertionsForResolution,
  supersedeAssertionsForNewSituation,
} from "..";

function makeAssertion(overrides: Parameters<typeof addAssertion>[1] extends (state: any, assertion: infer A) => any ? A : never): ReturnType<typeof addAssertion> extends (state: any, assertion: infer A) => any ? A : never {
  const base: any = {
    id: `assertion-${Math.random().toString(36).slice(2, 9)}`,
    dimension: "functional_status",
    value: "independent",
    status: "active",
    validity_start: new Date("2024-01-01T00:00:00Z").toISOString(),
    validity_end: null,
    confidence: 0.8,
    evidence_ids: ["evidence-1"],
    event_ids: ["event-1"],
    conflict_status: "coexisting",
    provenance_note: "Test assertion",
    created_at: new Date("2024-01-01T00:00:00Z").toISOString(),
    updated_at: new Date("2024-01-01T00:00:00Z").toISOString(),
    care_recipient_id: "recipient-1",
  };
  return { ...base, ...overrides };
}

function makeEvent(overrides: any = {}): any {
  return {
    id: `event-${Math.random().toString(36).slice(2, 9)}`,
    event_time: { type: "observed", start: "2024-01-01T00:00:00Z", confidence: 1 },
    ingestion_time: "2024-01-01T00:00:00Z",
    raw_input: "Test input",
    extracted_type: "condition",
    entities: [],
    attributes: {},
    uncertainty: [],
    source: "user_input",
    root_event_id: null,
    situation_id: null,
    document_id: null,
    status: "committed",
    integrity: { field_confidence: { extracted_fact: { extraction: "high", user_confirmed: false } } },
    ...overrides,
  };
}

console.log("=== Longitudinal Care State Tests ===");

// Test 1: createLongitudinalCareState
{
  const state = createLongitudinalCareState("recipient-1");
  assert.strictEqual(state.care_recipient_id, "recipient-1");
  assert.strictEqual(state.assertions.size, 0);
  assert.strictEqual(state.baselines.size, 0);
  assert.strictEqual(state.transitions.length, 0);
  console.log("PASS: createLongitudinalCareState");
}

// Test 2: addAssertion
{
  const state = createLongitudinalCareState("recipient-1");
  const assertion = makeAssertion();
  const next = addAssertion(state, assertion);
  assert.strictEqual(next.assertions.size, 1);
  assert.deepStrictEqual(next.assertions.get(assertion.id), assertion);
  console.log("PASS: addAssertion");
}

// Test 3: addAssertion throws on invalid dimension
{
  const state = createLongitudinalCareState("recipient-1");
  const bad = makeAssertion({ dimension: "invalid_dimension" });
  assert.throws(() => addAssertion(state, bad), /Invalid dimension/);
  console.log("PASS: addAssertion invalid dimension");
}

// Test 4: addAssertion throws on invalid confidence
{
  const state = createLongitudinalCareState("recipient-1");
  const bad = makeAssertion({ confidence: 1.5 });
  assert.throws(() => addAssertion(state, bad), /Confidence must be in \[0,1]/);
  console.log("PASS: addAssertion invalid confidence");
}

// Test 5: expireAssertion
{
  const state = createLongitudinalCareState("recipient-1");
  const assertion = makeAssertion();
  const withAssertion = addAssertion(state, assertion);
  const expired = expireAssertion(withAssertion, assertion.id, "2024-06-01T00:00:00Z");
  const updated = expired.assertions.get(assertion.id)!;
  assert.strictEqual(updated.validity_end, "2024-06-01T00:00:00Z");
  console.log("PASS: expireAssertion");
}

// Test 6: expireAssertion does not double-expire
{
  const state = createLongitudinalCareState("recipient-1");
  const assertion = makeAssertion({ validity_end: "2024-06-01T00:00:00Z" });
  const withAssertion = addAssertion(state, assertion);
  const expired = expireAssertion(withAssertion, assertion.id, "2024-12-01T00:00:00Z");
  const updated = expired.assertions.get(assertion.id)!;
  assert.strictEqual(updated.validity_end, "2024-06-01T00:00:00Z");
  console.log("PASS: expireAssertion no double-expire");
}

// Test 7: supersedeAssertion
{
  const state = createLongitudinalCareState("recipient-1");
  const old = makeAssertion({ id: "old-1", value: "independent" });
  const next = addAssertion(state, old);
  const newAssertion = makeAssertion({
    id: "new-1",
    value: "requires_assistance",
    validity_start: "2024-06-01T00:00:00Z",
    supersedes_id: "old-1",
  });
  const superseded = supersedeAssertion(next, "old-1", newAssertion);
  const updatedOld = superseded.assertions.get("old-1")!;
  assert.strictEqual(updatedOld.validity_end, "2024-06-01T00:00:00Z");
  assert.strictEqual(updatedOld.superseded_by_id, "new-1");
  const updatedNew = superseded.assertions.get("new-1")!;
  assert.strictEqual(updatedNew.supersedes_id, "old-1");
  console.log("PASS: supersedeAssertion");
}

// Test 8: computeCurrentState
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", validity_start: "2024-01-01T00:00:00Z", validity_end: null });
  const a2 = makeAssertion({ id: "a2", validity_start: "2024-01-01T00:00:00Z", validity_end: "2024-06-01T00:00:00Z" });
  const a3 = makeAssertion({ id: "a3", validity_start: "2024-06-01T00:00:00Z", validity_end: null });
  let s = addAssertion(state, a1);
  s = addAssertion(s, a2);
  s = addAssertion(s, a3);
  const current = computeCurrentState(s, "2024-03-01T00:00:00Z");
  assert.strictEqual(current.length, 2);
  const ids = current.map((a) => a.id).sort();
  assert.deepStrictEqual(ids, ["a1", "a2"]);
  console.log("PASS: computeCurrentState");
}

// Test 9: computeCurrentStateForDimension
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status" });
  const a2 = makeAssertion({ id: "a2", dimension: "mobility" });
  let s = addAssertion(state, a1);
  s = addAssertion(s, a2);
  const current = computeCurrentStateForDimension(s, "functional_status");
  assert.strictEqual(current.length, 1);
  assert.strictEqual(current[0].id, "a1");
  console.log("PASS: computeCurrentStateForDimension");
}

// Test 10: reconstructStateAtTime
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", validity_start: "2024-01-01T00:00:00Z", validity_end: null });
  let s = addAssertion(state, a1);
  const result = reconstructStateAtTime(s, "2024-06-01T00:00:00Z");
  assert.strictEqual(result.state.assertions.length, 1);
  assert.strictEqual(result.state.assertions[0].id, "a1");
  console.log("PASS: reconstructStateAtTime");
}

// Test 11: reconstructStateAtTime unknown dimensions
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status" });
  let s = addAssertion(state, a1);
  const result = reconstructStateAtTime(s, "2024-06-01T00:00:00Z");
  assert.ok(result.unknown_dimensions.includes("mobility"));
  console.log("PASS: reconstructStateAtTime unknown dimensions");
}

// Test 12: computeDelta modifications
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status", value: "independent" });
  let s = addAssertion(state, a1);
  const a2 = makeAssertion({ id: "a2", dimension: "functional_status", value: "requires_assistance", validity_start: "2024-06-01T00:00:00Z" });
  s = addAssertion(s, a2);
  const delta = computeDelta(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z");
  assert.strictEqual(delta.additions.length, 0);
  assert.strictEqual(delta.removals.length, 0);
  assert.strictEqual(delta.modifications.length, 1);
  assert.strictEqual(delta.modifications[0]!.from_value, "independent");
  assert.strictEqual(delta.modifications[0]!.to_value, "requires_assistance");
  console.log("PASS: computeDelta modifications");
}

// Test 13: detectChange
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status" });
  let s = addAssertion(state, a1);
  const a2 = makeAssertion({ id: "a2", dimension: "mobility", validity_start: "2024-06-01T00:00:00Z" });
  s = addAssertion(s, a2);
  assert.strictEqual(detectChange(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z"), true);
  console.log("PASS: detectChange true");
}

// Test 14: detectChange false
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status" });
  const s = addAssertion(state, a1);
  assert.strictEqual(detectChange(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z"), false);
  console.log("PASS: detectChange false");
}

// Test 15: classifyMeaningfulChange high-impact
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "active_conditions", value: "none" });
  let s = addAssertion(state, a1);
  const a2 = makeAssertion({ id: "a2", dimension: "active_conditions", value: "diabetes", validity_start: "2024-06-01T00:00:00Z" });
  s = addAssertion(s, a2);
  const delta = computeDelta(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z");
  const classification = classifyMeaningfulChange(s, delta);
  assert.strictEqual(classification.is_meaningful, true);
  assert.strictEqual(classification.severity, "high");
  console.log("PASS: classifyMeaningfulChange high-impact");
}

// Test 16: classifyMeaningfulChange retroactive correction
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "active_conditions", value: "asthma", supersedes_id: "old-1" });
  let s = addAssertion(state, a1);
  const delta = computeDelta(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z");
  delta.learning_type = "retroactive_correction";
  const classification = classifyMeaningfulChange(s, delta);
  assert.strictEqual(classification.is_meaningful, true);
  assert.strictEqual(classification.category, "clinical");
  console.log("PASS: classifyMeaningfulChange retroactive correction");
}

// Test 17: detectConflicts
{
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status", value: "independent" });
  const a2 = makeAssertion({ id: "a2", dimension: "functional_status", value: "requires_assistance" });
  const conflicts = detectConflicts([a1, a2]);
  assert.strictEqual(conflicts.length, 1);
  assert.strictEqual(conflicts[0].dimension, "functional_status");
  assert.deepStrictEqual(conflicts[0].assertion_ids, ["a1", "a2"]);
  console.log("PASS: detectConflicts");
}

// Test 18: detectConflicts same value no conflict
{
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status", value: "independent" });
  const a2 = makeAssertion({ id: "a2", dimension: "functional_status", value: "independent" });
  const conflicts = detectConflicts([a1, a2]);
  assert.strictEqual(conflicts.length, 0);
  console.log("PASS: detectConflicts same value");
}

// Test 19: createTransition
{
  const state = createLongitudinalCareState("recipient-1");
  const transition = createTransition(state, {
    occurred_at: "2024-06-01T00:00:00Z",
    from_assertion_ids: ["a1"],
    to_assertion_ids: ["a2"],
    mechanism: "new_evidence",
    confidence: 0.9,
    evidence_ids: ["evidence-1"],
    event_ids: ["event-1"],
    detection_method: "explicit",
    description: "Mobility changed",
  });
  assert.ok(transition.id.startsWith("transition-"));
  assert.strictEqual(transition.mechanism, "new_evidence");
  console.log("PASS: createTransition");
}

// Test 20: establishBaseline
{
  const state = createLongitudinalCareState("recipient-1");
  const baseline: any = {
    id: "baseline-1",
    dimension: "functional",
    care_state_dimension: "functional_status",
    value: "independent",
    established_at: "2024-01-01T00:00:00Z",
    last_confirmed_at: "2024-01-01T00:00:00Z",
    evidence_ids: ["evidence-1"],
    event_ids: ["event-1"],
    confidence: 0.9,
    context: "stable_period",
    care_recipient_id: "recipient-1",
    created_at: "2024-01-01T00:00:00Z",
  };
  const next = establishBaseline(state, baseline);
  assert.strictEqual(next.baselines.size, 1);
  assert.deepStrictEqual(next.baselines.get("baseline-1"), baseline);
  console.log("PASS: establishBaseline");
}

// Test 21: getBaselineForDimension
{
  const state = createLongitudinalCareState("recipient-1");
  const b1: any = {
    id: "baseline-1",
    dimension: "functional",
    care_state_dimension: "functional_status",
    value: "independent",
    established_at: "2024-01-01T00:00:00Z",
    last_confirmed_at: "2024-01-01T00:00:00Z",
    evidence_ids: [],
    event_ids: [],
    confidence: 0.9,
    context: "stable_period",
    care_recipient_id: "recipient-1",
    created_at: "2024-01-01T00:00:00Z",
  };
  const b2: any = {
    id: "baseline-2",
    dimension: "functional",
    care_state_dimension: "functional_status",
    value: "requires_assistance",
    established_at: "2024-06-01T00:00:00Z",
    last_confirmed_at: "2024-06-01T00:00:00Z",
    evidence_ids: [],
    event_ids: [],
    confidence: 0.8,
    context: "post_event",
    care_recipient_id: "recipient-1",
    created_at: "2024-06-01T00:00:00Z",
  };
  let s = establishBaseline(state, b1);
  s = establishBaseline(s, b2);
  const baseline = getBaselineForDimension(s, "functional_status");
  assert.strictEqual(baseline?.id, "baseline-2");
  console.log("PASS: getBaselineForDimension");
}

// Test 22: verifyStateIntegrity valid
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion();
  const s = addAssertion(state, a1);
  const violations = verifyStateIntegrity(s);
  assert.strictEqual(violations.length, 0);
  console.log("PASS: verifyStateIntegrity valid");
}

// Test 23: verifyStateIntegrity invalid dimension
{
  const state = createLongitudinalCareState("recipient-1");
  const bad = makeAssertion({ dimension: "invalid" as any });
  const s = { ...state, assertions: new Map(state.assertions).set(bad.id, bad) };
  const violations = verifyStateIntegrity(s);
  assert.ok(violations.some((v) => v.includes("invalid dimension")));
  console.log("PASS: verifyStateIntegrity invalid dimension");
}

// Test 24: eventToAssertions condition
{
  const event = makeEvent({ extracted_type: "condition" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions.length, 1);
  assert.strictEqual(assertions[0].dimension, "active_conditions");
  assert.strictEqual(assertions[0].status, "active");
  console.log("PASS: eventToAssertions condition");
}

// Test 25: eventToAssertions provisional
{
  const event = makeEvent({ status: "provisional" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions[0].status, "active");
  assert.ok(assertions[0].confidence < 0.5);
  console.log("PASS: eventToAssertions provisional");
}

// Test 26: eventToAssertions unparsed
{
  const event = makeEvent({ status: "unparsed_raw" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions[0].status, "unknown");
  console.log("PASS: eventToAssertions unparsed");
}

// Test 27: trackedSituationToAssertions resolved
{
  const situation: any = {
    id: "situation-1",
    title: "Post-surgery recovery",
    status: "RESOLVED",
    careSessionId: "recipient-1",
    userId: "caregiver-1",
    createdAt: "2024-01-01T00:00:00Z",
    updatedAt: "2024-06-01T00:00:00Z",
    resolvedAt: "2024-06-01T00:00:00Z",
    documentIds: ["doc-1"],
    timelineEntryIds: ["event-1"],
  };
  const assertions = trackedSituationToAssertions(situation);
  assert.strictEqual(assertions.length, 1);
  assert.strictEqual(assertions[0].dimension, "active_conditions");
  assert.strictEqual(assertions[0].status, "resolved");
  assert.strictEqual(assertions[0].validity_end, "2024-06-01T00:00:00Z");
  console.log("PASS: trackedSituationToAssertions resolved");
}

// Test 28: expireAssertionsForResolution
{
  const assertion = makeAssertion({
    id: "a1",
    event_ids: ["situation-1"],
    validity_end: null,
    status: "active",
  });
  const result = expireAssertionsForResolution([assertion], "situation-1", "2024-06-01T00:00:00Z");
  assert.strictEqual(result[0].validity_end, "2024-06-01T00:00:00Z");
  assert.strictEqual(result[0].status, "resolved");
  console.log("PASS: expireAssertionsForResolution");
}

// Test 29: supersedeAssertionsForNewSituation
{
  const assertion = makeAssertion({
    id: "a1",
    event_ids: ["situation-old"],
    validity_end: null,
    status: "active",
  });
  const result = supersedeAssertionsForNewSituation(
    [assertion],
    "situation-old",
    "situation-new",
    "2024-06-01T00:00:00Z",
  );
  assert.strictEqual(result[0].validity_end, "2024-06-01T00:00:00Z");
  assert.strictEqual(result[0].superseded_by_id, "situation-new");
  console.log("PASS: supersedeAssertionsForNewSituation");
}

// Test 30: computeDelta additions/removals/modifications
{
  const state = createLongitudinalCareState("recipient-1");
  const a1 = makeAssertion({ id: "a1", dimension: "functional_status", value: "independent" });
  let s = addAssertion(state, a1);
  const a2 = makeAssertion({ id: "a2", dimension: "mobility", value: "wheelchair", validity_start: "2024-06-01T00:00:00Z" });
  s = addAssertion(s, a2);
  const delta = computeDelta(s, "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z");
  assert.strictEqual(delta.additions.length, 1);
  assert.strictEqual(delta.additions[0].dimension, "mobility");
  assert.strictEqual(delta.removals.length, 0);
  assert.strictEqual(delta.modifications.length, 0);
  console.log("PASS: computeDelta additions");
}

console.log("\n=== All 30 tests passed ===");
