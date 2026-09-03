import type {
  CareStateAssertion,
  CareStateBaseline,
  CareStateConflict,
  CareStateDelta,
  CareStateDimension,
  CareStateSnapshot,
  CareStateTransition,
  CareStateTransition as Transition,
  LongitudinalCareState,
  StateReconstructionResult,
  StateReconstructionRequest,
  MeaningfulChangeClassification,
} from "./types";
import {
  CARE_STATE_CONFLICT_STATUSES,
  CARE_STATE_DIMENSIONS,
  CARE_STATE_STATUSES,
  TRANSITION_MECHANISMS,
  TransitionMechanism,
} from "./types";
import { CARE_STATE_CONFLICT_STATUSES as CONFLICT_STATUSES } from "./types";
import type { CanonicalCareEvent } from "../situation-entry/types";
import { eventToAssertions } from "./event-adapter";

export { eventToAssertions } from "./event-adapter";

/**
 * LONGITUDINAL CARE STATE — CORE MODEL
 *
 * Implements: L = (D, A, B, τ)
 *
 * Invariants:
 * 1. No assertion deletion. Assertions are expired (validity_end set), never removed.
 * 2. Corrections are new assertions. Originals persist.
 * 3. Current state = {a in A : validity_start <= now < validity_end OR validity_end == null}
 * 4. Historical state at t = {a in A : validity_start <= t < validity_end OR validity_end == null}
 * 5. Baseline references are preserved across state changes.
 * 6. Conflicts are explicit; never silently resolved.
 * 7. Unknown = absence of assertion, NOT false.
 * 8. Reconstruction is deterministic given A and t.
 */

export function createLongitudinalCareState(
  care_recipient_id: string,
): LongitudinalCareState {
  return {
    care_recipient_id,
    assertions: new Map(),
    baselines: new Map(),
    transitions: [],
    conflicts: [],
    reconstruction_cache: new Map(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

/* ------------------------------------------------------------------ */
/* ASSERTION CRUD — APPEND ONLY                                       */
/* ------------------------------------------------------------------ */

export function addAssertion(
  state: LongitudinalCareState,
  assertion: CareStateAssertion,
): LongitudinalCareState {
  assertValidAssertion(assertion);
  const next = new Map(state.assertions);
  next.set(assertion.id, assertion);
  return {
    ...state,
    assertions: next,
    updated_at: new Date().toISOString(),
  };
}

export function expireAssertion(
  state: LongitudinalCareState,
  assertion_id: string,
  validity_end: string,
): LongitudinalCareState {
  const existing = state.assertions.get(assertion_id);
  if (!existing) return state;
  if (existing.validity_end !== null) return state;
  if (new Date(validity_end) <= new Date(existing.validity_start)) return state;

  const updated: CareStateAssertion = {
    ...existing,
    validity_end,
    updated_at: new Date().toISOString(),
  };
  const next = new Map(state.assertions);
  next.set(assertion_id, updated);
  return {
    ...state,
    assertions: next,
    updated_at: new Date().toISOString(),
  };
}

export function supersedeAssertion(
  state: LongitudinalCareState,
  old_id: string,
  new_assertion: CareStateAssertion,
): LongitudinalCareState {
  const old = state.assertions.get(old_id);
  if (!old) return addAssertion(state, new_assertion);

  const expired_old = expireAssertion(state, old_id, new_assertion.validity_start);
  const updated_new: CareStateAssertion = {
    ...new_assertion,
    supersedes_id: old_id,
  };
  const updated_old = expired_old.assertions.get(old_id);
  if (updated_old) {
    updated_old.superseded_by_id = new_assertion.id;
  }

  return addAssertion(expired_old, updated_new);
}

export function getAssertion(
  state: LongitudinalCareState,
  id: string,
): CareStateAssertion | undefined {
  return state.assertions.get(id);
}

export function getAssertionsForDimension(
  state: LongitudinalCareState,
  dimension: CareStateDimension,
): CareStateAssertion[] {
  return Array.from(state.assertions.values()).filter((a) => a.dimension === dimension);
}

/* ------------------------------------------------------------------ */
/* CURRENT STATE                                                      */
/* ------------------------------------------------------------------ */

export function computeCurrentState(
  state: LongitudinalCareState,
  as_of_time = new Date().toISOString(),
): CareStateAssertion[] {
  const t = new Date(as_of_time);
  return Array.from(state.assertions.values()).filter((a) => {
    const start = new Date(a.validity_start);
    const end = a.validity_end ? new Date(a.validity_end) : null;
    return start <= t && (end === null || t < end);
  });
}

export function computeCurrentStateForDimension(
  state: LongitudinalCareState,
  dimension: CareStateDimension,
  as_of_time = new Date().toISOString(),
): CareStateAssertion[] {
  return computeCurrentState(state, as_of_time).filter(
    (a) => a.dimension === dimension,
  );
}

export function getCurrentStateSnapshot(
  state: LongitudinalCareState,
  as_of_time = new Date().toISOString(),
): CareStateSnapshot {
  const assertions = computeCurrentState(state, as_of_time);
  const baselines = Array.from(state.baselines.values());
  const transitions = state.transitions;
  const confidenceValues = assertions.map((a) => a.confidence);
  const confidence_summary =
    confidenceValues.length > 0
      ? confidenceValues.reduce((sum, c) => sum + c, 0) / confidenceValues.length
      : 0;

  return {
    id: `snapshot-${state.care_recipient_id}-${as_of_time}`,
    care_recipient_id: state.care_recipient_id,
    computed_at: new Date().toISOString(),
    as_of_time,
    assertions,
    baselines,
    transition_count: transitions.length,
    confidence_summary,
    materialized: false,
  };
}

/* ------------------------------------------------------------------ */
/* HISTORICAL STATE RECONSTRUCTION                                    */
/* ------------------------------------------------------------------ */

export function reconstructStateAtTime(
  state: LongitudinalCareState,
  as_of_time: string,
): StateReconstructionResult {
  const t = new Date(as_of_time);
  const assertions = computeCurrentState(state, as_of_time);
  const activeConflicts = detectConflicts(assertions);
  const unknownDimensions = CARE_STATE_DIMENSIONS.filter((d) => {
    const count = assertions.filter((a) => a.dimension === d).length;
    return count === 0;
  });

  const recentTransitions = state.transitions.filter((tr) => {
    const trTime = new Date(tr.occurred_at);
    const window = 30 * 24 * 60 * 60 * 1000;
    return trTime >= new Date(t.getTime() - window) && trTime <= t;
  });

  const gaps = computeTemporalGaps(state, as_of_time);

  const confidence =
    assertions.length > 0
      ? assertions.reduce((sum, a) => sum + a.confidence, 0) / assertions.length
      : 0;

  const result: StateReconstructionResult = {
    care_recipient_id: state.care_recipient_id,
    as_of_time,
    state: getCurrentStateSnapshot(state, as_of_time),
    unknown_dimensions: unknownDimensions,
    conflicts: activeConflicts,
    recent_transitions: recentTransitions,
    gaps,
    confidence,
  };

  state.reconstruction_cache.set(as_of_time, result);
  return result;
}

function computeTemporalGaps(
  state: LongitudinalCareState,
  as_of_time: string,
): { dimension: CareStateDimension; from: string; to: string }[] {
  const gaps: { dimension: CareStateDimension; from: string; to: string }[] = [];
  const allAssertions = Array.from(state.assertions.values());

  for (const dim of CARE_STATE_DIMENSIONS) {
    const dimAssertions = allAssertions
      .filter((a) => a.dimension === dim)
      .sort((a, b) => new Date(a.validity_start).getTime() - new Date(b.validity_start).getTime());

    if (dimAssertions.length === 0) {
      gaps.push({ dimension: dim, from: "1970-01-01", to: as_of_time });
      continue;
    }

    const first = dimAssertions[0];
    if (new Date(first.validity_start).getTime() > new Date("1970-01-01").getTime()) {
      gaps.push({ dimension: dim, from: "1970-01-01", to: first.validity_start });
    }
  }

  return gaps;
}

/* ------------------------------------------------------------------ */
/* STATE DELTAS                                                       */
/* ------------------------------------------------------------------ */

export function computeDelta(
  state: LongitudinalCareState,
  t1: string,
  t2: string,
): CareStateDelta {
  const s1 = computeCurrentState(state, t1);
  const s2 = computeCurrentState(state, t2);

  const s1Map = new Map(s1.map((a) => [a.dimension, a]));
  const s2Map = new Map(s2.map((a) => [a.dimension, a]));

  const additions: CareStateAssertion[] = [];
  const removals: CareStateAssertion[] = [];
  const modifications: CareStateDelta["modifications"] = [];

  for (const [dim, a2] of s2Map) {
    const a1 = s1Map.get(dim);
    if (!a1) {
      additions.push(a2);
    } else if (a1.value !== a2.value || a1.status !== a2.status) {
      modifications.push({
        assertion_id: a2.id,
        dimension: dim,
        from_value: a1.value,
        to_value: a2.value,
      });
    }
  }

  for (const [dim, a1] of s1Map) {
    if (!s2Map.has(dim)) {
      removals.push(a1);
    }
  }

  let learning_type: CareStateDelta["learning_type"] = "new_observation";
  if (additions.some((a) => a.supersedes_id) || removals.some((a) => a.superseded_by_id)) {
    learning_type = "retroactive_correction";
  }

  return {
    id: `delta-${state.care_recipient_id}-${t1}-${t2}`,
    care_recipient_id: state.care_recipient_id,
    computed_at: new Date().toISOString(),
    from_time: t1,
    to_time: t2,
    additions,
    removals,
    modifications,
    learning_type,
    description: `Delta from ${t1} to ${t2}: ${additions.length} additions, ${removals.length} removals, ${modifications.length} modifications`,
  };
}

/* ------------------------------------------------------------------ */
/* CHANGE DETECTION                                                   */
/* ------------------------------------------------------------------ */

export function detectChange(
  state: LongitudinalCareState,
  t1: string,
  t2: string,
): boolean {
  const delta = computeDelta(state, t1, t2);
  return delta.additions.length > 0 || delta.removals.length > 0 || delta.modifications.length > 0;
}

/* ------------------------------------------------------------------ */
/* MEANINGFUL-CHANGE DETECTION                                        */
/* ------------------------------------------------------------------ */

export function classifyMeaningfulChange(
  state: LongitudinalCareState,
  delta: CareStateDelta,
): MeaningfulChangeClassification {
  const highImpactDimensions: CareStateDimension[] = [
    "active_conditions",
    "functional_status",
    "mobility",
    "care_dependencies",
    "medications",
  ];

  const hasHighImpactChange = [
    ...delta.additions,
    ...delta.removals,
    ...delta.modifications,
  ].some((change) => {
    const dim = change.dimension || change.dimension;
    return highImpactDimensions.includes(dim);
  });

  if (delta.learning_type === "retroactive_correction") {
    return {
      is_meaningful: true,
      category: "clinical",
      severity: "high",
      reason: "Retroactive correction indicates prior state was misrepresented",
      requires_review: true,
    };
  }

  if (hasHighImpactChange) {
    return {
      is_meaningful: true,
      category: "clinical",
      severity: "high",
      reason: `Change detected in high-impact dimension: ${delta.modifications[0]?.dimension || "unknown"}`,
      requires_review: true,
    };
  }

  if (delta.additions.length > 0 || delta.removals.length > 0) {
    return {
      is_meaningful: true,
      category: "clinical",
      severity: "medium",
      reason: "New assertions added or removed from state",
      requires_review: true,
    };
  }

  return {
    is_meaningful: false,
    category: "data_quality",
    severity: "none",
    reason: "No dimension changes detected",
    requires_review: false,
  };
}

/* ------------------------------------------------------------------ */
/* CONFLICT DETECTION                                                 */
/* ------------------------------------------------------------------ */

export function detectConflicts(
  assertions: CareStateAssertion[],
): CareStateConflict[] {
  const conflicts: CareStateConflict[] = [];
  const byDimension = new Map<CareStateDimension, CareStateAssertion[]>();

  for (const a of assertions) {
    const list = byDimension.get(a.dimension) || [];
    list.push(a);
    byDimension.set(a.dimension, list);
  }

  for (const [dim, dimAssertions] of byDimension) {
    if (dimAssertions.length > 1) {
      const values = new Set(dimAssertions.map((a) => a.value));
      if (values.size > 1) {
        const conflict: CareStateConflict = {
          id: `conflict-${dim}-${Date.now()}`,
          care_recipient_id: dimAssertions[0].care_recipient_id,
          dimension: dim,
          assertion_ids: dimAssertions.map((a) => a.id),
          detected_at: new Date().toISOString(),
          resolved: false,
        };
        conflicts.push(conflict);
      }
    }
  }

  return conflicts;
}

/* ------------------------------------------------------------------ */
/* TRANSITIONS                                                        */
/* ------------------------------------------------------------------ */

export function createTransition(
  state: LongitudinalCareState,
  params: {
    occurred_at: string;
    from_assertion_ids: string[];
    to_assertion_ids: string[];
    mechanism: TransitionMechanism;
    confidence: number;
    evidence_ids: string[];
    event_ids: string[];
    detection_method: "explicit" | "reconstructed" | "inferred";
    description: string;
  },
): CareStateTransition {
  const transition: CareStateTransition = {
    id: `transition-${state.care_recipient_id}-${Date.now()}`,
    care_recipient_id: state.care_recipient_id,
    occurred_at: params.occurred_at,
    from_assertion_ids: params.from_assertion_ids,
    to_assertion_ids: params.to_assertion_ids,
    mechanism: params.mechanism,
    confidence: params.confidence,
    evidence_ids: params.evidence_ids,
    event_ids: params.event_ids,
    detection_method: params.detection_method,
    description: params.description,
    created_at: new Date().toISOString(),
  };

  return transition;
}

export function addTransition(
  state: LongitudinalCareState,
  transition: CareStateTransition,
): LongitudinalCareState {
  return {
    ...state,
    transitions: [...state.transitions, transition],
    updated_at: new Date().toISOString(),
  };
}

export function detectTransitionsFromDelta(
  state: LongitudinalCareState,
  t1: string,
  t2: string,
): CareStateTransition[] {
  const delta = computeDelta(state, t1, t2);
  if (
    delta.additions.length === 0 &&
    delta.removals.length === 0 &&
    delta.modifications.length === 0
  ) {
    return [];
  }

  const transitions: CareStateTransition[] = [];

  for (const mod of delta.modifications) {
    const fromAssertions = state.assertions.values();
    const fromId = mod.assertion_id;
    transitions.push(
      createTransition(state, {
        occurred_at: t2,
        from_assertion_ids: [fromId],
        to_assertion_ids: [mod.assertion_id],
        mechanism: "new_evidence",
        confidence: 0.8,
        evidence_ids: [],
        event_ids: [],
        detection_method: "reconstructed",
        description: `Value changed from ${mod.from_value} to ${mod.to_value} for ${mod.dimension}`,
      }),
    );
  }

  for (const add of delta.additions) {
    transitions.push(
      createTransition(state, {
        occurred_at: add.validity_start,
        from_assertion_ids: [],
        to_assertion_ids: [add.id],
        mechanism: "new_evidence",
        confidence: add.confidence,
        evidence_ids: add.evidence_ids,
        event_ids: add.event_ids,
        detection_method: "reconstructed",
        description: `New assertion added: ${add.dimension} = ${add.value}`,
      }),
    );
  }

  for (const rem of delta.removals) {
    transitions.push(
      createTransition(state, {
        occurred_at: rem.validity_end || t2,
        from_assertion_ids: [rem.id],
        to_assertion_ids: [],
        mechanism: "new_evidence",
        confidence: 0.9,
        evidence_ids: rem.evidence_ids,
        event_ids: rem.event_ids,
        detection_method: "reconstructed",
        description: `Assertion expired: ${rem.dimension} = ${rem.value}`,
      }),
    );
  }

  return transitions;
}

/* ------------------------------------------------------------------ */
/* BASELINES                                                          */
/* ------------------------------------------------------------------ */

export function establishBaseline(
  state: LongitudinalCareState,
  baseline: CareStateBaseline,
): LongitudinalCareState {
  const next = new Map(state.baselines);
  next.set(baseline.id, baseline);
  return {
    ...state,
    baselines: next,
    updated_at: new Date().toISOString(),
  };
}

export function getBaselineForDimension(
  state: LongitudinalCareState,
  dimension: CareStateDimension,
  as_of_time?: string,
): CareStateBaseline | undefined {
  const baselines = Array.from(state.baselines.values()).filter(
    (b) => b.care_state_dimension === dimension,
  );
  if (baselines.length === 0) return undefined;
  if (!as_of_time) return baselines[baselines.length - 1];
  return baselines
    .filter((b) => new Date(b.established_at) <= new Date(as_of_time))
    .sort(
      (a, b) =>
        new Date(b.established_at).getTime() - new Date(a.established_at).getTime(),
    )[0];
}

/* ------------------------------------------------------------------ */
/* VALIDATION                                                         */
/* ------------------------------------------------------------------ */

function assertValidAssertion(assertion: CareStateAssertion): void {
  if (!CARE_STATE_DIMENSIONS.includes(assertion.dimension)) {
    throw new Error(`Invalid dimension: ${assertion.dimension}`);
  }
  if (!CARE_STATE_STATUSES.includes(assertion.status)) {
    throw new Error(`Invalid status: ${assertion.status}`);
  }
  if (assertion.confidence < 0 || assertion.confidence > 1) {
    throw new Error(`Confidence must be in [0,1]: ${assertion.confidence}`);
  }
  if (new Date(assertion.validity_start) > new Date(assertion.validity_end || Date.now())) {
    throw new Error(`Invalid validity period: ${assertion.validity_start} > ${assertion.validity_end}`);
  }
}

/* ------------------------------------------------------------------ */
/* STATE INTEGRITY CHECK                                              */
/* ------------------------------------------------------------------ */

export function verifyStateIntegrity(state: LongitudinalCareState): string[] {
  const violations: string[] = [];

  for (const assertion of state.assertions.values()) {
    if (!CARE_STATE_DIMENSIONS.includes(assertion.dimension)) {
      violations.push(`Assertion ${assertion.id} has invalid dimension ${assertion.dimension}`);
    }
    if (!CARE_STATE_STATUSES.includes(assertion.status)) {
      violations.push(`Assertion ${assertion.id} has invalid status ${assertion.status}`);
    }
    if (assertion.confidence < 0 || assertion.confidence > 1) {
      violations.push(`Assertion ${assertion.id} has invalid confidence ${assertion.confidence}`);
    }
    if (assertion.validity_end && new Date(assertion.validity_end) <= new Date(assertion.validity_start)) {
      violations.push(`Assertion ${assertion.id} has invalid validity period`);
    }
  }

  for (const baseline of state.baselines.values()) {
    if (!CARE_STATE_DIMENSIONS.includes(baseline.care_state_dimension)) {
      violations.push(`Baseline ${baseline.id} has invalid dimension ${baseline.care_state_dimension}`);
    }
  }

  const assertionIds = new Set(state.assertions.keys());
  for (const transition of state.transitions) {
    for (const id of transition.from_assertion_ids) {
      if (!assertionIds.has(id)) {
        violations.push(`Transition ${transition.id} references missing assertion ${id}`);
      }
    }
    for (const id of transition.to_assertion_ids) {
      if (!assertionIds.has(id)) {
        violations.push(`Transition ${transition.id} references missing assertion ${id}`);
      }
    }
  }

  return violations;
}

/* ------------------------------------------------------------------ */
/* EXPORT / IMPORT                                                    */
/* ------------------------------------------------------------------ */

export function exportState(state: LongitudinalCareState): {
  care_recipient_id: string;
  assertions: CareStateAssertion[];
  baselines: CareStateBaseline[];
  transitions: CareStateTransition[];
  conflicts: CareStateConflict[];
  created_at: string;
  updated_at: string;
} {
  return {
    care_recipient_id: state.care_recipient_id,
    assertions: Array.from(state.assertions.values()),
    baselines: Array.from(state.baselines.values()),
    transitions: state.transitions,
    conflicts: state.conflicts,
    created_at: state.created_at,
    updated_at: state.updated_at,
  };
}

export function importState(data: {
  care_recipient_id: string;
  assertions: CareStateAssertion[];
  baselines: CareStateBaseline[];
  transitions: CareStateTransition[];
  conflicts: CareStateConflict[];
  created_at: string;
  updated_at: string;
}): LongitudinalCareState {
  const state = createLongitudinalCareState(data.care_recipient_id);
  for (const a of data.assertions) {
    state.assertions.set(a.id, a);
  }
  for (const b of data.baselines) {
    state.baselines.set(b.id, b);
  }
  state.transitions = data.transitions;
  state.conflicts = data.conflicts;
  state.created_at = data.created_at;
  state.updated_at = data.updated_at;
  return state;
}
