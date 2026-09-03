import assert from "node:assert";
import { eventToAssertions } from "..";
import type { CanonicalCareEvent } from "../types";

function makeEvent(overrides: Partial<CanonicalCareEvent> = {}): CanonicalCareEvent {
  return {
    id: `event-${Math.random().toString(36).slice(2, 9)}`,
    event_time: { type: "observed" as const, start: "2024-01-01T00:00:00Z", confidence: 1 },
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

console.log("=== Event Adapter Tests ===");

// Test 1: condition event
{
  const event = makeEvent({ extracted_type: "condition" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions.length, 1);
  assert.strictEqual(assertions[0]!.dimension, "active_conditions");
  assert.strictEqual(assertions[0]!.status, "active");
  console.log("PASS: condition event");
}

// Test 2: symptom event
{
  const event = makeEvent({ extracted_type: "symptom" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions.length, 1);
  assert.strictEqual(assertions[0]!.dimension, "symptoms");
  console.log("PASS: symptom event");
}

// Test 3: medication event
{
  const event = makeEvent({ extracted_type: "medication" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions.length, 1);
  assert.strictEqual(assertions[0]!.dimension, "medications");
  console.log("PASS: medication event");
}

// Test 4: provisional event
{
  const event = makeEvent({ status: "provisional" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions[0]!.status, "active");
  assert.ok(assertions[0]!.confidence < 0.5);
  console.log("PASS: provisional event");
}

// Test 5: unparsed event
{
  const event = makeEvent({ status: "unparsed_raw" });
  const assertions = eventToAssertions(event);
  assert.strictEqual(assertions[0]!.status, "unknown");
  console.log("PASS: unparsed event");
}

// Test 6: attribute mapping
{
  const event = makeEvent({
    extracted_type: "condition",
    attributes: { mobility: "requires_assistance" },
  });
  const assertions = eventToAssertions(event);
  const mobilityAssertions = assertions.filter((a) => a.dimension === "mobility");
  assert.strictEqual(mobilityAssertions.length, 1);
  assert.strictEqual(mobilityAssertions[0]!.value, "requires_assistance");
  console.log("PASS: attribute mapping");
}

// Test 7: evidence linking
{
  const event = makeEvent({ id: "event-123" });
  const assertions = eventToAssertions(event);
  assert.ok(assertions[0]!.evidence_ids.includes("event-123"));
  assert.ok(assertions[0]!.event_ids.includes("event-123"));
  console.log("PASS: evidence linking");
}

// Test 8: confidence computation
{
  const event = makeEvent({
    status: "committed",
    integrity: { field_confidence: { extracted_fact: { extraction: "high", user_confirmed: true } } },
    uncertainty: [],
  });
  const assertions = eventToAssertions(event);
  assert.ok(assertions[0]!.confidence > 0.7);
  console.log("PASS: confidence computation");
}

console.log("\n=== All 8 event adapter tests passed ===");
