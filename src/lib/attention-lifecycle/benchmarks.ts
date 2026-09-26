/**
 * Benchmark Tests for Attention/Decision Lifecycle
 * 
 * Tests all critical benchmark cases ATT-001 through ATT-012
 * and validates against the failure taxonomy.
 */

import {
  lifecycleManager,
  clearAttentionStore,
  getAllAttentionItems,
} from "./manager";
import {
  assessAttention,
  createAssessmentContext,
} from "./assessment";
import {
  auditTrail,
} from "./audit-trail";
import {
  decisionLifecycle,
} from "./decision-integration";
import {
  addItemToGraph,
  addDependencyEdge,
  findBlockedItems,
  findCriticalPath,
  clearAllDependencyGraphs,
} from "./dependencies";
import {
  AttentionState,
  DecisionLifecycleState,
  AttentionOwner,
  AttentionItem,
  AttentionDependency,
  DependencyType,
  OwnershipAssignment,
  AttentionFailureType,
  AttentionBenchmarkCase,
  BenchmarkInput,
  BenchmarkExpected,
  attentionStateRank,
  decisionLifecycleRank,
  ATTENTION_STATE_RANKS,
} from "./types";

function runBenchmarkCase(testCase: AttentionBenchmarkCase): BenchmarkResult {
  clearAttentionStore();
  decisionLifecycle.clearStores();
  clearAllDependencyGraphs();
  auditTrail.clear();

  const { input, expected } = testCase;
  
  // Pre-calculate recurrence counts for each signal based on similar signals in input
  const signalRecurrenceCounts: Map<string, number> = new Map();
  for (let i = 0; i < input.signals.length; i++) {
    const signal = input.signals[i];
    // Count previous signals with similar description (same domain keywords)
    let count = 0;
    for (let j = 0; j < i; j++) {
      const prevSignal = input.signals[j];
      // Simple similarity: check if they share key domain words
      const signalWords = signal.description.toLowerCase().split(/\s+/);
      const prevWords = prevSignal.description.toLowerCase().split(/\s+/);
      const commonWords = signalWords.filter(w => prevWords.includes(w) && w.length > 3);
      if (commonWords.length >= 2) count++;
    }
    signalRecurrenceCounts.set(signal.signal_id, count);
  }
  
  const createdItems: AttentionItem[] = [];
  
  for (const signal of input.signals) {
    const item = lifecycleManager.processSignal({
      care_recipient_id: input.context.care_recipient_id,
      signal: signal.description,
      baseline: input.context.baseline,
      history: input.timeline.flatMap(t => t.signals),
      caregiver_capacity: input.context.caregiver_capacity,
      clinical_context: input.context.clinical_context,
      existing_items: createdItems.map(i => i.id),
      time_since_onset_ms: signal.magnitude ? signal.magnitude * 3600000 : 86400000,
      recurrence_count: signalRecurrenceCounts.get(signal.signal_id) || (signal.description.includes("repeated") || signal.description.includes("several") ? 3 : 0),
      trend: signal.description.includes("worsening") ? "worsening" : "stable",
      action_taken: signal.description.includes("contacted") || signal.description.includes("already"),
      action_description: signal.description.includes("contacted") ? "Contacted clinician" : undefined,
      evidence_quality: "medium",
      open_loops: [],
      uncertainty_level: 0.3,
    });
    createdItems.push(item);
    addItemToGraph(input.context.care_recipient_id, item);
  }

  for (const existing of input.existing_items) {
    const item = lifecycleManager.getAttentionItem(existing);
    if (item) {
      addItemToGraph(input.context.care_recipient_id, item);
    }
  }

  const evaluation = lifecycleManager.evaluateLifecycle(input.context.care_recipient_id);

  // Find the most critical item (highest urgency) instead of first created
  const allItems = [...createdItems];
  for (const existing of input.existing_items) {
    const item = lifecycleManager.getAttentionItem(existing);
    if (item) allItems.push(item);
  }
  
  // Sort by urgency (state rank + decision state rank)
  const primaryItem = allItems.sort((a, b) => {
    const stateRankA = attentionStateRank(a.state);
    const stateRankB = attentionStateRank(b.state);
    if (stateRankA !== stateRankB) return stateRankB - stateRankA;
    const decisionRankA = decisionLifecycleRank(a.decision_state);
    const decisionRankB = decisionLifecycleRank(b.decision_state);
    return decisionRankB - decisionRankA;
  })[0] || null;

  return {
    case_id: testCase.case_id,
    name: testCase.name,
    passed: evaluateResult(primaryItem, expected, evaluation, testCase.failure_modes),
    actual_state: primaryItem?.state || "NONE",
    expected_state: expected.final_state,
    actual_decision_state: primaryItem?.decision_state || "NONE",
    expected_decision_state: expected.final_decision_state,
    evaluation,
    detected_failures: detectFailures(primaryItem, expected, evaluation, testCase.failure_modes),
  };
}

function evaluateResult(
  item: AttentionItem | null | undefined,
  expected: BenchmarkExpected,
  evaluation: ReturnType<typeof lifecycleManager.evaluateLifecycle>,
  failureModes: AttentionFailureType[]
): boolean {
  if (!item) return false;
  
  if (item.state !== expected.final_state) return false;
  if (item.decision_state !== expected.final_decision_state) return false;
  if (expected.expected_owner && item.ownership?.owner !== expected.expected_owner) return false;
  
  // Check escalation/de-escalation based on state rank changes in history
  const history = item.history || [];
  const firstEntry = history.find(h => h.from_state === null);
  const initialRank = firstEntry ? getStateRank(firstEntry.to_state) : getStateRank(item.state);
  const states = history.map(h => h.to_state);
  const peakRank = Math.max(...states.map(s => getStateRank(s)));
  const peakState = states.find(s => getStateRank(s) === peakRank) || item.state;
  const finalRank = getStateRank(item.state);
  
  const alertStates = ["NEEDS_ATTENTION", "HIGH_PRIORITY", "URGENT", "EMERGENCY"];
  const alertOrWaitingStates = ["NEEDS_ATTENTION", "HIGH_PRIORITY", "URGENT", "EMERGENCY",
    "AWAITING_INFORMATION", "AWAITING_DECISION", "AWAITING_ACTION", "IN_PROGRESS", "AWAITING_VERIFICATION"];
  
  // Escalation: state moved to a higher alert level (from lower to alert)
  const hasEscalation = peakRank > initialRank && alertStates.includes(peakState);
  // De-escalation: state moved from alert/urgent to monitoring/terminal state
  const hasDeescalation = finalRank < peakRank && !alertOrWaitingStates.includes(item.state);
  
  if (expected.should_escalate && !hasEscalation) return false;
  if (!expected.should_escalate && hasEscalation) return false;
  
  if (expected.should_deescalate && !hasDeescalation) return false;
  if (!expected.should_deescalate && hasDeescalation) return false;
  
  if (expected.should_close && item.state !== "CLOSED" && item.state !== "RESOLVED") return false;
  if (expected.should_reopen && item.state !== "WATCH" && item.state !== "NEEDS_ATTENTION") return false;

  return true;
}

function detectFailures(
  item: AttentionItem | null | undefined,
  expected: BenchmarkExpected,
  evaluation: ReturnType<typeof lifecycleManager.evaluateLifecycle>,
  failureModes: AttentionFailureType[]
): AttentionFailureType[] {
  const detected: AttentionFailureType[] = [];
  
  if (!item) {
    detected.push("ATTENTION_MISSED");
    return detected;
  }

  if (item.state !== expected.final_state) {
    const expectedRank = getStateRank(expected.final_state);
    const actualRank = getStateRank(item.state);
    if (actualRank > expectedRank) detected.push("ATTENTION_OVER_ESCALATION");
    else if (actualRank < expectedRank) detected.push("ATTENTION_UNDER_ESCALATION");
  }

  if (expected.should_close && (item.state === "NEEDS_ATTENTION" || item.state === "HIGH_PRIORITY" || item.state === "URGENT")) {
    detected.push("UNRESOLVED_ATTENTION_CLEARED");
  }

  if (!expected.should_close && (item.state === "CLOSED" || item.state === "RESOLVED")) {
    detected.push("RESOLVED_ATTENTION_RETAINED");
  }

  if (evaluation.ownership_gaps.length > 0 && expected.expected_owner) {
    detected.push("OWNER_LOSS");
  }

  if (evaluation.dependency_blocks.length > 0) {
    detected.push("DEPENDENCY_FAILURE");
  }

  return detected;
}

function getStateRank(state: AttentionState): number {
  return ATTENTION_STATE_RANKS[state] ?? 0;
}

type BenchmarkResult = {
  case_id: string;
  name: string;
  passed: boolean;
  actual_state: AttentionState | "NONE";
  expected_state: AttentionState;
  actual_decision_state: DecisionLifecycleState | "NONE";
  expected_decision_state: DecisionLifecycleState;
  evaluation: ReturnType<typeof lifecycleManager.evaluateLifecycle>;
  detected_failures: AttentionFailureType[];
};

const BENCHMARK_CASES: AttentionBenchmarkCase[] = [
  {
    case_id: "ATT-001",
    name: "New Signal - Meaningful Deviation from Baseline",
    description: "A meaningful deviation from baseline appears. Expected: NEEDS_ATTENTION",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date().toISOString(), type: "appetite_change", description: "Mom ate very little today - only a few bites at each meal", evidence: "Caregiver observation", magnitude: 1 },
      ],
      timeline: [
        { day: 1, description: "Mom ate very little", signals: ["appetite_change"], expected_state: "WATCH", expected_decision_state: "NOT_REQUIRED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Normally eats full meals three times daily",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, otherwise stable",
      },
    },
    expected: {
      final_state: "NEEDS_ATTENTION",
      final_decision_state: "DECISION_NEEDED",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["ATTENTION_MISSED", "ATTENTION_UNDER_ESCALATION", "BASELINE_BLIND_ATTENTION"],
  },
  {
    case_id: "ATT-002",
    name: "Benign Variation - One Small Meal Then Normal",
    description: "One unusually small meal followed by normal eating. Expected: No unnecessary escalation.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date().toISOString(), type: "appetite_change", description: "Mom ate a small breakfast but had normal lunch and dinner", evidence: "Caregiver observation", magnitude: 1 },
      ],
      timeline: [
        { day: 1, description: "Small breakfast, normal rest of day", signals: ["appetite_change"], expected_state: "BACKGROUND", expected_decision_state: "NOT_REQUIRED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Normally eats full meals three times daily",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, otherwise stable",
      },
    },
    expected: {
      final_state: "BACKGROUND",
      final_decision_state: "NOT_REQUIRED",
      expected_owner: null,
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["FALSE_ATTENTION", "ATTENTION_OVER_ESCALATION", "KEYWORD_BASED_ATTENTION"],
  },
  {
    case_id: "ATT-003",
    name: "Persistent Problem - Reduced Intake for Several Days",
    description: "Reduced intake continues for several days. Expected: Attention increases.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 3*86400000).toISOString(), type: "appetite_change", description: "Mom ate very little on day 1", evidence: "Caregiver observation", magnitude: 24 },
        { signal_id: "s2", timestamp: new Date(Date.now() - 2*86400000).toISOString(), type: "appetite_change", description: "Mom ate very little on day 2", evidence: "Caregiver observation", magnitude: 48 },
        { signal_id: "s3", timestamp: new Date(Date.now() - 1*86400000).toISOString(), type: "appetite_change", description: "Mom ate very little on day 3", evidence: "Caregiver observation", magnitude: 72 },
      ],
      timeline: [
        { day: 1, description: "Day 1: Poor intake", signals: ["appetite_change"], expected_state: "WATCH", expected_decision_state: "NOT_REQUIRED" },
        { day: 3, description: "Day 3: Poor intake for 3 days", signals: ["appetite_change"], expected_state: "NEEDS_ATTENTION", expected_decision_state: "DECISION_NEEDED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Normally eats full meals three times daily",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, otherwise stable",
      },
    },
    expected: {
      final_state: "HIGH_PRIORITY",
      final_decision_state: "DECISION_NEEDED",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: true,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["ATTENTION_PERSISTENCE_ERROR", "ATTENTION_DECAY_FAILURE", "ESCALATION_FAILURE"],
  },
  {
    case_id: "ATT-004",
    name: "Resolution - Poor Appetite Returns to Baseline",
    description: "Poor appetite returns to baseline. Expected: Active attention decreases/ends.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 5*86400000).toISOString(), type: "appetite_change", description: "Mom ate very little for 3 days", evidence: "Caregiver observation", magnitude: 120 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "appetite_change", description: "Mom is eating normally again - full meals all day", evidence: "Caregiver observation", magnitude: 144 },
      ],
      timeline: [
        { day: 3, description: "Day 3: Poor intake", signals: ["appetite_change"], expected_state: "NEEDS_ATTENTION", expected_decision_state: "DECISION_NEEDED" },
        { day: 5, description: "Day 5: Eating normally again", signals: ["appetite_change"], expected_state: "RESOLVED", expected_decision_state: "RESOLVED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Normally eats full meals three times daily",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, otherwise stable",
      },
    },
    expected: {
      final_state: "RESOLVED",
      final_decision_state: "RESOLVED",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: true,
      should_close: true,
      should_reopen: false,
    },
    failure_modes: ["DEESCALATION_FAILURE", "RESOLVED_ATTENTION_RETAINED", "ATTENTION_DECAY_FAILURE"],
  },
  {
    case_id: "ATT-005",
    name: "Action Already Taken - Caregiver Contacted Clinician",
    description: "Caregiver already contacted clinician and is awaiting response. Expected: Do not repeatedly tell caregiver to contact clinician; represent the pending state.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 2*86400000).toISOString(), type: "confusion", description: "Mom had sudden confusion yesterday", evidence: "Caregiver observation", magnitude: 48 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "action_taken", description: "Caregiver already called Dr. Smith and is awaiting callback", evidence: "Caregiver report", magnitude: 24 },
      ],
      timeline: [
        { day: 1, description: "Sudden confusion", signals: ["confusion"], expected_state: "HIGH_PRIORITY", expected_decision_state: "DECISION_NEEDED" },
        { day: 2, description: "Caregiver contacted clinician, awaiting response", signals: ["action_taken"], expected_state: "AWAITING_DECISION", expected_decision_state: "AWAITING_CLINICAL_DECISION" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Usually oriented to person, place, time",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, on donepezil",
      },
    },
    expected: {
      final_state: "AWAITING_DECISION",
      final_decision_state: "AWAITING_CLINICAL_DECISION",
      expected_owner: "clinician",
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["DUPLICATE_ATTENTION", "OPEN_LOOP_ATTENTION_DISCONNECT", "DECISION_LIFECYCLE_FAILURE"],
  },
  {
    case_id: "ATT-006",
    name: "Decision Made, Action Pending - Medication Changed But Not Obtained",
    description: "Doctor changed medication, but caregiver has not obtained it. Expected: Decision complete; implementation still open.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 3*86400000).toISOString(), type: "medication_change", description: "Dr. Smith increased mom's donepezil from 5mg to 10mg", evidence: "Clinical note", magnitude: 72 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "action_pending", description: "Caregiver has not picked up new prescription yet", evidence: "Caregiver report", magnitude: 24 },
      ],
      timeline: [
        { day: 1, description: "Doctor changed medication", signals: ["medication_change"], expected_state: "AWAITING_ACTION", expected_decision_state: "DECISION_MADE" },
        { day: 3, description: "Prescription not yet filled", signals: ["action_pending"], expected_state: "AWAITING_ACTION", expected_decision_state: "ACTION_PENDING" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "On donepezil 5mg daily",
        caregiver_capacity: "medium",
        clinical_context: "Early dementia, medication adjustment",
      },
    },
    expected: {
      final_state: "AWAITING_ACTION",
      final_decision_state: "ACTION_PENDING",
      expected_owner: "caregiver",
      expected_dependencies: ["prescription_pickup"],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["DECISION_AS_RESOLUTION", "ACTION_AS_RESOLUTION", "RECOMMENDATION_AS_COMPLETION"],
  },
  {
    case_id: "ATT-007",
    name: "Action Complete, Outcome Unknown - Medication Started, No Response Yet",
    description: "Medication was started yesterday; no response documented yet. Expected: Monitor/verification pending, not resolved.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 2*86400000).toISOString(), type: "medication_start", description: "Mom started new antibiotic for UTI", evidence: "Caregiver observation", magnitude: 48 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "outcome_unknown", description: "No change in symptoms yet - still monitoring", evidence: "Caregiver observation", magnitude: 24 },
      ],
      timeline: [
        { day: 1, description: "Started antibiotic", signals: ["medication_start"], expected_state: "IN_PROGRESS", expected_decision_state: "ACTION_IN_PROGRESS" },
        { day: 2, description: "No response yet", signals: ["outcome_unknown"], expected_state: "AWAITING_VERIFICATION", expected_decision_state: "OUTCOME_UNKNOWN" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "UTI diagnosed, started antibiotics",
        caregiver_capacity: "medium",
        clinical_context: "UTI treatment initiated",
      },
    },
    expected: {
      final_state: "AWAITING_VERIFICATION",
      final_decision_state: "OUTCOME_UNKNOWN",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["COMPLETION_AS_VERIFICATION", "OUTCOME_UNKNOWN_AS_SUCCESS", "ACTION_AS_RESOLUTION"],
  },
  {
    case_id: "ATT-008",
    name: "Closed Issue - Fall Evaluated, No Ongoing Concern",
    description: "Fall evaluated, no ongoing concern documented, mobility returned to baseline. Expected: Historical event retained; active attention closed.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 14*86400000).toISOString(), type: "fall", description: "Mom fell getting out of bed", evidence: "Caregiver observation", magnitude: 336 },
        { signal_id: "s2", timestamp: new Date(Date.now() - 7*86400000).toISOString(), type: "evaluation", description: "ER evaluated - no injury, baseline mobility", evidence: "ER discharge summary", magnitude: 168 },
        { signal_id: "s3", timestamp: new Date().toISOString(), type: "resolved", description: "Walking normally, no further falls", evidence: "Caregiver observation", magnitude: 0 },
      ],
      timeline: [
        { day: 1, description: "Fall occurred", signals: ["fall"], expected_state: "EMERGENCY", expected_decision_state: "DECISION_NEEDED" },
        { day: 7, description: "Evaluated, no injury", signals: ["evaluation"], expected_state: "AWAITING_VERIFICATION", expected_decision_state: "VERIFICATION_PENDING" },
        { day: 14, description: "Fully recovered", signals: ["resolved"], expected_state: "CLOSED", expected_decision_state: "RESOLVED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Independent mobility, no fall history",
        caregiver_capacity: "medium",
        clinical_context: "Fall risk assessment completed",
      },
    },
    expected: {
      final_state: "CLOSED",
      final_decision_state: "RESOLVED",
      expected_owner: null,
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: true,
      should_close: true,
      should_reopen: false,
    },
    failure_modes: ["HISTORY_BLIND_ATTENTION", "ATTENTION_HISTORY_LOSS", "RESOLVED_ATTENTION_RETAINED"],
  },
  {
    case_id: "ATT-009",
    name: "Reopening - Previously Resolved Confusion Returns",
    description: "Previously resolved confusion returns with new evidence. Expected: New attention episode, linked to history; not blindly merged with the old episode.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 30*86400000).toISOString(), type: "confusion", description: "Mom had confusion episode - resolved after UTI treatment", evidence: "Clinical record", magnitude: 720 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "confusion_recurrence", description: "Mom confused again this morning - different presentation", evidence: "Caregiver observation", magnitude: 2 },
      ],
      timeline: [
        { day: 1, description: "Original confusion episode", signals: ["confusion"], expected_state: "HIGH_PRIORITY", expected_decision_state: "DECISION_NEEDED" },
        { day: 14, description: "Resolved after UTI treatment", signals: ["resolved"], expected_state: "CLOSED", expected_decision_state: "RESOLVED" },
        { day: 30, description: "New confusion episode", signals: ["confusion_recurrence"], expected_state: "NEEDS_ATTENTION", expected_decision_state: "DECISION_NEEDED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Usually oriented, previous UTI-related confusion resolved",
        caregiver_capacity: "medium",
        clinical_context: "History of UTI-related confusion, now new episode",
      },
    },
    expected: {
      final_state: "NEEDS_ATTENTION",
      final_decision_state: "DECISION_NEEDED",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: true,
    },
    failure_modes: ["REOPENING_FAILURE", "FALSE_REOPENING", "ATTENTION_HISTORY_LOSS", "DUPLICATE_ATTENTION"],
  },
  {
    case_id: "ATT-010",
    name: "Competing Priorities - Routine Refill, Missed Appointment, Acute Change",
    description: "Routine refill, missed appointment, and sudden new neurological change occur together. Expected: Acute high-risk change dominates attention.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date().toISOString(), type: "routine_refill", description: "Donepezil refill due next week", evidence: "Pharmacy notification", magnitude: 0 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "missed_appointment", description: "Missed follow-up appointment with neurologist", evidence: "Clinic notification", magnitude: 0 },
        { signal_id: "s3", timestamp: new Date().toISOString(), type: "neurological_change", description: "Sudden left-sided weakness and slurred speech this morning", evidence: "Caregiver observation", magnitude: 2 },
      ],
      timeline: [
        { day: 1, description: "Acute neurological change with routine items", signals: ["routine_refill", "missed_appointment", "neurological_change"], expected_state: "EMERGENCY", expected_decision_state: "DECISION_NEEDED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Stable on donepezil, regular neurology follow-up",
        caregiver_capacity: "medium",
        clinical_context: "Dementia with new acute neurological symptoms",
      },
    },
    expected: {
      final_state: "EMERGENCY",
      final_decision_state: "DECISION_NEEDED",
      expected_owner: "caregiver",
      expected_dependencies: [],
      should_escalate: true,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["ATTENTION_COMPETITION_FAILURE", "KEYWORD_BASED_ATTENTION", "CLINICAL_URGENCY_AS_DIAGNOSIS"],
  },
  {
    case_id: "ATT-011",
    name: "Blocked Decision - Medication Identity Uncertain",
    description: "Medication identity is uncertain and a medication-related symptom needs interpretation. Expected: Verification becomes necessary before medication-specific reasoning.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date().toISOString(), type: "medication_uncertainty", description: "Caregiver unsure which pill is which - bottles got mixed up", evidence: "Caregiver report", magnitude: 4 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "symptom", description: "Mom unusually drowsy this afternoon", evidence: "Caregiver observation", magnitude: 4 },
      ],
      timeline: [
        { day: 1, description: "Medication uncertainty + drowsiness", signals: ["medication_uncertainty", "symptom"], expected_state: "HIGH_PRIORITY", expected_decision_state: "DECISION_BLOCKED" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "On multiple medications, usually organized in pill box",
        caregiver_capacity: "low",
        clinical_context: "Polypharmacy, medication organization issue",
      },
    },
    expected: {
      final_state: "HIGH_PRIORITY",
      final_decision_state: "DECISION_BLOCKED",
      expected_owner: "caregiver",
      expected_dependencies: ["medication_verification"],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["BLOCKED_DECISION_MISSED", "DEPENDENCY_FAILURE", "DECISION_ATTENTION_DISCONNECT"],
  },
  {
    case_id: "ATT-012",
    name: "Unassigned Action - Clinician Requested Follow-up, Nobody Arranged It",
    description: "Clinician requested follow-up but nobody in the family has arranged it. Expected: Open operational loop + ownership gap.",
    input: {
      signals: [
        { signal_id: "s1", timestamp: new Date(Date.now() - 7*86400000).toISOString(), type: "clinical_recommendation", description: "Dr. Smith recommended follow-up MRI in 2 weeks", evidence: "Clinical note", magnitude: 168 },
        { signal_id: "s2", timestamp: new Date().toISOString(), type: "inaction", description: "Two weeks passed - no appointment scheduled", evidence: "Caregiver report", magnitude: 0 },
      ],
      timeline: [
        { day: 1, description: "Clinician requested follow-up MRI", signals: ["clinical_recommendation"], expected_state: "AWAITING_ACTION", expected_decision_state: "DECISION_MADE" },
        { day: 14, description: "Follow-up not arranged", signals: ["inaction"], expected_state: "HIGH_PRIORITY", expected_decision_state: "ACTION_PENDING" },
      ],
      existing_items: [],
      context: {
        care_recipient_id: "mom-001",
        baseline: "Regular imaging surveillance",
        caregiver_capacity: "low",
        clinical_context: "Dementia monitoring, imaging follow-up overdue",
      },
    },
    expected: {
      final_state: "HIGH_PRIORITY",
      final_decision_state: "ACTION_PENDING",
      expected_owner: "caregiver",
      expected_dependencies: ["appointment_scheduling"],
      should_escalate: false,
      should_deescalate: false,
      should_close: false,
      should_reopen: false,
    },
    failure_modes: ["OWNER_LOSS", "OPEN_LOOP_ATTENTION_DISCONNECT", "DECISION_LIFECYCLE_FAILURE"],
  },
];
export { BENCHMARK_CASES };

export function runAllBenchmarks(): BenchmarkSuiteResult {
  const results: BenchmarkResult[] = [];
  
  for (const testCase of BENCHMARK_CASES) {
    const result = runBenchmarkCase(testCase);
    results.push(result);
  }

  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed).length;
  const allFailures = results.flatMap(r => r.detected_failures);

  return {
    total_cases: BENCHMARK_CASES.length,
    passed,
    failed,
    pass_rate: passed / BENCHMARK_CASES.length,
    results,
    all_detected_failures: Array.from(new Set(allFailures)),
    timestamp: new Date().toISOString(),
  };
}

export type BenchmarkSuiteResult = {
  total_cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  results: BenchmarkResult[];
  all_detected_failures: AttentionFailureType[];
  timestamp: string;
};

export function printBenchmarkResults(results: BenchmarkSuiteResult): void {
  console.log("\n=== ATTENTION/DECISION LIFECYCLE BENCHMARK RESULTS ===");
  console.log(`Total: ${results.total_cases} | Passed: ${results.passed} | Failed: ${results.failed} | Pass Rate: ${(results.pass_rate * 100).toFixed(1)}%`);
  console.log("");

  for (const result of results.results) {
    const status = result.passed ? "✓ PASS" : "✗ FAIL";
    console.log(`${status} ${result.case_id}: ${result.name}`);
    console.log(`  Expected: ${result.expected_state} / ${result.expected_decision_state}`);
    console.log(`  Actual:   ${result.actual_state} / ${result.actual_decision_state}`);
    if (result.detected_failures.length > 0) {
      console.log(`  Failures: ${result.detected_failures.join(", ")}`);
    }
    console.log("");
  }

  if (results.all_detected_failures.length > 0) {
    console.log("DETECTED FAILURE TYPES:");
    for (const failure of Array.from(results.all_detected_failures)) {
      console.log(`  - ${failure}`);
    }
  }
}