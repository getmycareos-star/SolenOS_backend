/**
 * Attention Lifecycle Manager
 * 
 * Manages the full lifecycle: detect → assess → state → decision → action → monitor → resolve → close
 * Handles escalation, de-escalation, decay, persistence, and reopening.
 */

import {
  AttentionItem,
  AttentionState,
  DecisionLifecycleState,
  AttentionAssessment,
  AttentionLifecycleConfig,
  AttentionHistoryEntry,
  HistoryTrigger,
  OwnershipAssignment,
  AttentionDependency,
  DependencyType,
  DEFAULT_ATTENTION_LIFECYCLE_CONFIG,
  isActiveAttentionState,
  isTerminalAttentionState,
  compareAttentionStates,
  CompetitionResolution,
} from "./types";
import { assessAttention, createAssessmentContext } from "./assessment";
import { decisionLifecycle } from "./decision-integration";
import { addItemToGraph } from "./dependencies";

const attentionStore = new Map<string, AttentionItem>();
const historyStore = new Map<string, AttentionHistoryEntry[]>();

export type LifecycleManager = {
  createAttentionItem: (params: CreateItemParams) => AttentionItem;
  updateAttentionItem: (itemId: string, params: UpdateItemParams) => AttentionItem | null;
  processSignal: (params: ProcessSignalParams) => AttentionItem;
  escalateAttention: (itemId: string, reason: string, actor: string) => AttentionItem | null;
  deescalateAttention: (itemId: string, reason: string, actor: string) => AttentionItem | null;
  resolveAttention: (itemId: string, resolution: ResolutionParams) => AttentionItem | null;
  closeAttention: (itemId: string, reason: string, actor: string) => AttentionItem | null;
  reopenAttention: (itemId: string, newSignal: string, actor: string) => AttentionItem | null;
  supersedeAttention: (oldItemId: string, newItemId: string, reason: string) => AttentionItem | null;
  assignOwnership: (itemId: string, ownership: OwnershipAssignment) => AttentionItem | null;
  addDependency: (itemId: string, dependency: AttentionDependency) => AttentionItem | null;
  satisfyDependency: (itemId: string, dependencyId: string, actor: string) => AttentionItem | null;
  getAttentionItem: (itemId: string) => AttentionItem | undefined;
  getActiveItems: (careRecipientId: string) => AttentionItem[];
  getItemsByState: (careRecipientId: string, state: AttentionState) => AttentionItem[];
  getHistory: (itemId: string) => AttentionHistoryEntry[];
  evaluateLifecycle: (careRecipientId: string) => LifecycleEvaluationResult;
  setConfig: (config: Partial<AttentionLifecycleConfig>) => void;
  determineInitialDecisionState: (assessment: any, signal: string) => DecisionLifecycleState;
  determineInitialOwnership: (assessment: any, signal: string) => OwnershipAssignment | null;
};

export type CreateItemParams = {
  care_recipient_id: string;
  title: string;
  description: string;
  triggering_signal: string;
  initial_state?: AttentionState;
  initial_decision_state?: DecisionLifecycleState;
  ownership?: OwnershipAssignment;
  dependencies?: AttentionDependency[];
  tags?: string[];
  related_event_ids?: string[];
  clinical_context?: string;
  baseline?: string;
  history?: string[];
  caregiver_capacity?: "high" | "medium" | "low" | "unknown";
  time_since_onset_ms?: number;
  recurrence_count?: number;
  trend?: "worsening" | "improving" | "stable" | "unknown";
  action_taken?: boolean;
  action_description?: string;
  evidence_quality?: "high" | "medium" | "low";
  open_loops?: string[];
  uncertainty_level?: number;
};

export type UpdateItemParams = {
  state?: AttentionState;
  decision_state?: DecisionLifecycleState;
  assessment?: AttentionAssessment;
  ownership?: OwnershipAssignment;
  tags?: string[];
  related_event_ids?: string[];
};

export type ProcessSignalParams = {
  care_recipient_id: string;
  signal: string;
  baseline: string;
  history: string[];
  caregiver_capacity: "high" | "medium" | "low" | "unknown";
  clinical_context: string;
  existing_items: string[];
  time_since_onset_ms: number;
  recurrence_count: number;
  trend: "worsening" | "improving" | "stable" | "unknown";
  action_taken: boolean;
  action_description?: string;
  evidence_quality: "high" | "medium" | "low";
  open_loops: string[];
  uncertainty_level: number;
};

export type ResolutionParams = {
  outcome: string;
  verification_evidence: string[];
  resolved_by: string;
  decision_made?: string;
  action_taken?: string;
};

export type LifecycleEvaluationResult = {
  care_recipient_id: string;
  evaluated_at: string;
  items_evaluated: number;
  escalations: EscalationResult[];
  deescalations: DeescalationResult[];
  resolutions: ResolutionResult[];
  closures: ClosureResult[];
  reopenings: ReopeningResult[];
  ownership_gaps: OwnershipGap[];
  dependency_blocks: DependencyBlock[];
  suppressed_items: SuppressedItem[];
};

export type EscalationResult = {
  item_id: string;
  from_state: AttentionState;
  to_state: AttentionState;
  reason: string;
};

export type DeescalationResult = {
  item_id: string;
  from_state: AttentionState;
  to_state: AttentionState;
  reason: string;
};

export type ResolutionResult = {
  item_id: string;
  outcome: string;
  verified: boolean;
};

export type ClosureResult = {
  item_id: string;
  reason: string;
};

export type ReopeningResult = {
  item_id: string;
  new_signal: string;
  linked_to_history: boolean;
};

export type OwnershipGap = {
  item_id: string;
  item_title: string;
  state: AttentionState;
  message: string;
};

export type DependencyBlock = {
  item_id: string;
  blocked_by_item_id: string;
  dependency_type: DependencyType;
  satisfaction_criteria: string;
};

export type SuppressedItem = {
  item_id: string;
  reason: string;
  primary_item_id: string;
};

let currentConfig = DEFAULT_ATTENTION_LIFECYCLE_CONFIG;

function generateId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function addHistoryEntry(
  itemId: string,
  entry: Omit<AttentionHistoryEntry, "history_id" | "timestamp">
): void {
  const history = historyStore.get(itemId) || [];
  const newEntry: AttentionHistoryEntry = {
    ...entry,
    history_id: generateId("hist"),
    timestamp: new Date().toISOString(),
  };
  history.push(newEntry);
  historyStore.set(itemId, history);
  
  // Also update the item's history array
  const item = attentionStore.get(itemId);
  if (item) {
    item.history = history;
    item.updated_at = newEntry.timestamp;
  }
}

export const lifecycleManager: LifecycleManager = {
  createAttentionItem(params) {
    const now = new Date().toISOString();
    
    const assessmentContext = createAssessmentContext(
      params.triggering_signal,
      params.description || params.triggering_signal,
      params.history || [],
      params.caregiver_capacity || "unknown",
      params.clinical_context || "",
      [],
      params.time_since_onset_ms || 0,
      params.recurrence_count || 0,
      params.trend || "unknown",
      params.action_taken || false,
      params.action_description,
      params.evidence_quality || "medium",
      params.open_loops || [],
      params.uncertainty_level || 0.5,
      params.care_recipient_id
    );
    const assessment = assessAttention(assessmentContext, currentConfig, true);
    
    // Determine initial decision state based on assessment
    const initialDecisionState = params.initial_decision_state || this.determineInitialDecisionState(assessment, params.triggering_signal);
    
    // Sync initial attention state with decision state if decision state requires higher attention
    const syncedState = this.getAttentionStateForDecisionState(initialDecisionState);
    let initialState = params.initial_state || (syncedState && compareAttentionStates(syncedState, assessment.recommended_state) > 0 ? syncedState : assessment.recommended_state);
    
    // Special case: unassigned action (clinician requested/recommended follow-up but not arranged) -> HIGH_PRIORITY
    const signalLower = params.triggering_signal.toLowerCase();
    const isUnassignedAction = (
      /\b(not arranged|nobody arranged|not scheduled|no one arranged|no appointment scheduled|no appointment booked|unarranged|not been arranged|hasn't been arranged|not been scheduled|not been booked)\b/.test(signalLower) ||
      /\b(recommended|requested|ordered|suggested)\s+(follow.?up|appointment|referral| MRI|scan)\b/.test(signalLower) ||
      /recommended follow-up|requested follow-up/.test(signalLower)
    ) && !/\b(scheduled|arranged|booked|completed|done)\b/.test(signalLower);
    if (initialDecisionState === "ACTION_PENDING" && isUnassignedAction) {
      initialState = "HIGH_PRIORITY";
    }
    
    // Determine initial ownership
    const initialOwnership = params.ownership || this.determineInitialOwnership(assessment, params.triggering_signal);

    const item: AttentionItem = {
      id: generateId("attn"),
      care_recipient_id: params.care_recipient_id,
      title: params.title,
      description: params.description,
      triggering_signal: params.triggering_signal,
      state: initialState,
      decision_state: initialDecisionState,
      assessment,
      ownership: initialOwnership,
      dependencies: params.dependencies || [],
      dependents: [],
      history: [],
      superseded_by: null,
      supersedes: null,
      tags: params.tags || [],
      related_event_ids: params.related_event_ids || [],
      created_at: now,
      updated_at: now,
    };

    attentionStore.set(item.id, item);
    historyStore.set(item.id, []);

    addHistoryEntry(item.id, {
      item_id: item.id,
      from_state: null,
      to_state: initialState,
      from_decision_state: null,
      to_decision_state: initialDecisionState,
      trigger: "signal_detected",
      actor: "system",
      evidence: [params.triggering_signal],
      reasoning: `Attention item created from signal: ${params.triggering_signal} (assessed: ${assessment.recommended_state}, decision: ${initialDecisionState})`,
    });

    // Add to dependency graph
    addItemToGraph(params.care_recipient_id, item);

    return item;
  },

determineInitialDecisionState(assessment: any, signal: string): DecisionLifecycleState {
    const signalLower = signal.toLowerCase();
    const domains = assessment.dimension_scores || [];
    const recommendedState = assessment.recommended_state;
    
    // Critical clinical urgency -> awaiting clinical decision
    if (/\b(stroke|seizure|not breathing|unconscious|severe bleeding|chest pain|acute confusion)\b/i.test(signalLower)) {
      return "AWAITING_CLINICAL_DECISION";
    }
    if (/\b(medication|dose|prescription|pharmacy)\b/i.test(signalLower)) {
      return "AWAITING_CLINICAL_DECISION";
    }
    // Medication dose change patterns (e.g., "increased from 5mg to 10mg")
    if (/\b(increased|decreased|changed|adjusted|titrated)\b/i.test(signalLower) && /\b\d+\s*mg\b/i.test(signalLower)) {
      return "DECISION_MADE";
    }
    // Treatment started -> action in progress
    if (/\b(started|began|began taking|on antibiotic|on medication|treatment started)\b/i.test(signalLower)) {
      return "ACTION_IN_PROGRESS";
    }
    // Blocked decision - medication identity uncertain
    if (/\b(medication|dose|pill|prescription)\b/i.test(signalLower) && /\b(uncertain|unclear|unknown|identity|which|confused about)\b/i.test(signalLower)) {
      return "DECISION_BLOCKED";
    }
    // Unassigned action - clinician requested/recommended follow-up but not arranged
    const hasFollowUp = /\b(follow[- ]?up|appointment|referral)\b/i.test(signalLower);
    const hasNotArranged = /\b(not arranged|nobody arranged|not scheduled|no one arranged|no appointment scheduled|no appointment booked|unarranged|not been arranged|hasn't been arranged|not been scheduled|not been booked)\b/i.test(signalLower);
    const hasRecommended = /\b(recommended|requested|ordered|suggested)\b/i.test(signalLower);
    const hasArranged = /\b(scheduled|arranged|booked|completed|done)\b/i.test(signalLower);
    if (hasFollowUp && (hasNotArranged || (hasRecommended && !hasArranged))) {
      return "ACTION_PENDING";
    }
    
    // High priority clinical signals -> decision needed
    if (/\b(fall|injury|pain|worsening|confusion|breathing)\b/i.test(signalLower)) {
      return "DECISION_NEEDED";
    }
    if (/\b(appointment|refill|follow[- ]?up|schedule|arrange)\b/i.test(signalLower)) {
      return "DECISION_NEEDED";
    }
    
    // Use assessment to determine if decision needed
    // If recommended state is NEEDS_ATTENTION or higher, a decision is typically needed
    const activeStates = ["NEEDS_ATTENTION", "HIGH_PRIORITY", "URGENT", "EMERGENCY", "AWAITING_DECISION", "AWAITING_ACTION", "AWAITING_VERIFICATION", "IN_PROGRESS"];
    if (activeStates.includes(recommendedState)) {
      // Check if any dimension indicates clinical input needed
      const needsClinicalInput = domains.some((d: any) => 
        d.clinical_significance === "high" || d.clinical_significance === "critical" || 
        d.requires_clinical_input === true
      );
      if (needsClinicalInput) {
        return "AWAITING_CLINICAL_DECISION";
      }
      return "DECISION_NEEDED";
    }
    
    // WATCH state - monitor, decision may be needed if persistent
    if (recommendedState === "WATCH") {
      return "NOT_REQUIRED"; // Just monitor for now
    }
    
    return "NOT_REQUIRED";
  },

determineInitialOwnership(assessment: any, signal: string): any {
    const signalLower = signal.toLowerCase();
    
    // Critical clinical urgency -> clinician owner
    if (/\b(stroke|seizure|not breathing|unconscious|severe bleeding|chest pain|sudden confusion|severe confusion)\b/i.test(signalLower)) {
      return { owner: "clinician" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Immediate clinical assessment and intervention" };
    }
    if (/\b(fall|injury)\b/i.test(signalLower)) {
      return { owner: "caregiver" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Safety assessment and injury monitoring" };
    }
    if (/\b(medication|dose|prescription|pharmacy)\b/i.test(signalLower)) {
      return { owner: "clinician" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Medication management and clinical coordination" };
    }
    if (/\b(medication|dose|pill|prescription)\b/i.test(signalLower) && /\b(uncertain|unclear|unknown|identity|confused about|wrong)\b/i.test(signalLower)) {
      return { owner: "clinician" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Medication verification and clarification" };
    }
    if (/\b(appointment|refill|follow[- ]?up|schedule|arrange)\b/i.test(signalLower)) {
      return { owner: "caregiver" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Scheduling and coordination" };
    }
    // Check if decision state requires clinical input
    const decisionState = assessment?.initial_decision_state;
    if (decisionState === "AWAITING_CLINICAL_DECISION" || decisionState === "DECISION_BLOCKED") {
      return { owner: "clinician" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Clinical decision and oversight" };
    }
    return { owner: "caregiver" as const, assigned_at: new Date().toISOString(), assigned_by: "system", responsibility: "Monitoring and assessment" };
  },

  updateAttentionItem(itemId, params) {
    const item = attentionStore.get(itemId);
    if (!item) return null;

    const now = new Date().toISOString();
    const oldState = item.state;
    const oldDecisionState = item.decision_state;

    if (params.state && params.state !== item.state) {
      addHistoryEntry(itemId, {
        item_id: itemId,
        from_state: item.state,
        to_state: params.state,
        from_decision_state: item.decision_state,
        to_decision_state: item.decision_state,
        trigger: compareAttentionStates(params.state, item.state) > 0 ? "escalation" : "de_escalation",
        actor: "system",
        evidence: [],
        reasoning: `State changed from ${item.state} to ${params.state}`,
      });
      item.state = params.state;
    }

    if (params.decision_state && params.decision_state !== item.decision_state) {
      addHistoryEntry(itemId, {
        item_id: itemId,
        from_state: item.state,
        to_state: item.state,
        from_decision_state: item.decision_state,
        to_decision_state: params.decision_state,
        trigger: "decision_made",
        actor: "system",
        evidence: [],
        reasoning: `Decision state changed from ${item.decision_state} to ${params.decision_state}`,
      });
      item.decision_state = params.decision_state;
    }

    if (params.assessment) {
      item.assessment = params.assessment;
    }

    if (params.ownership) {
      const oldOwner = item.ownership?.owner;
      item.ownership = params.ownership;
      if (oldOwner !== params.ownership.owner) {
        addHistoryEntry(itemId, {
          item_id: itemId,
          from_state: item.state,
          to_state: item.state,
          from_decision_state: item.decision_state,
          to_decision_state: item.decision_state,
          trigger: "ownership_changed",
          actor: "system",
          evidence: [],
          reasoning: `Ownership changed from ${oldOwner || "unassigned"} to ${params.ownership.owner}`,
        });
      }
    }

    if (params.tags) {
      item.tags = params.tags;
    }

    if (params.related_event_ids) {
      item.related_event_ids = params.related_event_ids;
    }

    item.updated_at = now;
    return item;
  },

  determineDecisionStateProgression(item: AttentionItem, signal: string, actionTaken: boolean, actionDescription?: string): DecisionLifecycleState | null {
    const currentDecisionState = item.decision_state;
    const signalLower = signal.toLowerCase();
    const actionDescLower = (actionDescription || "").toLowerCase();

    // If action was taken with clinical contact -> AWAITING_CLINICAL_DECISION
    // Check both action description and signal text for clinical contact keywords
    const clinicalContactKeywords = ["contacted", "called", "clinician", "doctor", "dr\\.", "physician", "nurse", "provider"];
    const hasClinicalContact = actionTaken && (
      clinicalContactKeywords.some(kw => actionDescLower.includes(kw)) ||
      clinicalContactKeywords.some(kw => signalLower.includes(kw))
    );
    
    if (hasClinicalContact) {
      if (currentDecisionState === "DECISION_NEEDED" || currentDecisionState === "NOT_REQUIRED") {
        return "AWAITING_CLINICAL_DECISION";
      }
      if (currentDecisionState === "AWAITING_CLINICAL_DECISION") {
        return currentDecisionState; // Stay in same state
      }
    }

    // Decision made, action pending
    if (signalLower.includes("medication") && (signalLower.includes("changed") || signalLower.includes("increased") || signalLower.includes("decreased") || signalLower.includes("prescription"))) {
      if (currentDecisionState === "DECISION_NEEDED" || currentDecisionState === "AWAITING_CLINICAL_DECISION") {
        return "DECISION_MADE";
      }
      if (currentDecisionState === "DECISION_MADE") {
        return "ACTION_PENDING";
      }
    }

    // Action pending - not yet obtained/filled
    if (signalLower.includes("not") && (signalLower.includes("picked up") || signalLower.includes("filled") || signalLower.includes("obtained"))) {
      if (currentDecisionState === "DECISION_MADE" || currentDecisionState === "AWAITING_CLINICAL_DECISION") {
        return "ACTION_PENDING";
      }
    }

    // Action in progress - started medication/treatment
    if (signalLower.includes("started") || signalLower.includes("began") || signalLower.includes("began taking")) {
      if (currentDecisionState === "ACTION_PENDING" || currentDecisionState === "DECISION_MADE" || currentDecisionState === "DECISION_NEEDED" || currentDecisionState === "AWAITING_CLINICAL_DECISION") {
        return "ACTION_IN_PROGRESS";
      }
    }

    // Action completed
    if (signalLower.includes("completed") || signalLower.includes("finished") || signalLower.includes("took") || signalLower.includes("taken")) {
      if (currentDecisionState === "ACTION_IN_PROGRESS" || currentDecisionState === "ACTION_PENDING") {
        return "ACTION_COMPLETED";
      }
    }

    // Outcome unknown - no response yet
    if (signalLower.includes("no change") || signalLower.includes("no response") || signalLower.includes("still monitoring") || signalLower.includes("outcome unknown") || signalLower.includes("awaiting")) {
      if (currentDecisionState === "ACTION_COMPLETED" || currentDecisionState === "ACTION_IN_PROGRESS") {
        return "OUTCOME_UNKNOWN";
      }
    }

    // Evaluation/verification
    if (signalLower.includes("evaluated") || signalLower.includes("assessed") || signalLower.includes("verified") || signalLower.includes("check")) {
      if (currentDecisionState === "OUTCOME_UNKNOWN" || currentDecisionState === "ACTION_COMPLETED") {
        return "VERIFICATION_PENDING";
      }
    }

    // Resolved - fully recovered, no further issues
    if (signalLower.includes("resolved") || signalLower.includes("recovered") || signalLower.includes("normal") || signalLower.includes("back to baseline") || signalLower.includes("fully recovered") || signalLower.includes("eating normally") || signalLower.includes("walking normally") || signalLower.includes("no further falls")) {
      if (currentDecisionState === "VERIFICATION_PENDING" || currentDecisionState === "OUTCOME_UNKNOWN" || currentDecisionState === "ACTION_COMPLETED" || currentDecisionState === "DECISION_NEEDED" || currentDecisionState === "AWAITING_CLINICAL_DECISION" || currentDecisionState === "AWAITING_ACTION" || currentDecisionState === "ACTION_PENDING") {
        return "RESOLVED";
      }
    }

    // Closed
    if (signalLower.includes("closed") || signalLower.includes("no further") || signalLower.includes("discharged")) {
      if (currentDecisionState === "RESOLVED") {
        return "SUPERSEDED"; // or RESOLVED depending on context
      }
    }

    // Reopening - new episode after resolution
    if (signalLower.includes("again") || signalLower.includes("recurrence") || signalLower.includes("returned") || signalLower.includes("new episode")) {
      if (currentDecisionState === "RESOLVED" || currentDecisionState === "SUPERSEDED") {
        return "DECISION_NEEDED";
      }
    }

    return null; // No change
  },

  getAttentionStateForDecisionState(decisionState: DecisionLifecycleState): AttentionState | null {
    const mapping: Record<DecisionLifecycleState, AttentionState | null> = {
      NOT_REQUIRED: null,
      DECISION_NEEDED: "NEEDS_ATTENTION",
      DECISION_BLOCKED: "HIGH_PRIORITY",
      AWAITING_HUMAN_DECISION: "AWAITING_DECISION",
      AWAITING_CLINICAL_DECISION: "AWAITING_DECISION",
      DECISION_MADE: "AWAITING_ACTION",
      ACTION_PENDING: "AWAITING_ACTION",
      ACTION_IN_PROGRESS: "IN_PROGRESS",
      ACTION_COMPLETED: "AWAITING_VERIFICATION",
      OUTCOME_UNKNOWN: "AWAITING_VERIFICATION",
      VERIFICATION_PENDING: "AWAITING_VERIFICATION",
      RESOLVED: "RESOLVED",
      SUPERSEDED: "CLOSED",
    };
    return mapping[decisionState] || null;
  },

  processSignal(params) {
    const existingItem = findMatchingItem(params.care_recipient_id, params.signal, params.existing_items);
    
    if (existingItem) {
      const assessmentContext = createAssessmentContext(
        params.signal,
        existingItem.description,
        params.history,
        params.caregiver_capacity,
        params.clinical_context,
        params.existing_items,
        params.time_since_onset_ms,
        params.recurrence_count,
        params.trend,
        params.action_taken,
        params.action_description,
        params.evidence_quality,
        params.open_loops,
        params.uncertainty_level,
        params.care_recipient_id
      );
      const assessment = assessAttention(assessmentContext, currentConfig, true);
      
      // Determine decision state progression based on signal
      const newDecisionState = this.determineDecisionStateProgression(existingItem, params.signal, params.action_taken, params.action_description);
      
      const recommendedState = assessment.recommended_state;
      
      // Track minimum allowed state based on decision state (prevent de-escalation below decision requirements)
      const minStateFromDecision = this.getAttentionStateForDecisionState(existingItem.decision_state) || null;
      const minStateFromNewDecision = newDecisionState ? this.getAttentionStateForDecisionState(newDecisionState) : null;
      const minAllowedState = [minStateFromDecision, minStateFromNewDecision]
        .filter(s => s !== null)
        .sort((a, b) => compareAttentionStates(b, a))[0] || existingItem.state;
      
      // Apply decision state update first
      if (newDecisionState && newDecisionState !== existingItem.decision_state) {
        this.updateAttentionItem(existingItem.id, { decision_state: newDecisionState });
        // Refresh existingItem reference
        const updatedItem = attentionStore.get(existingItem.id);
        if (updatedItem) existingItem.decision_state = updatedItem.decision_state;
        
        // Sync attention state with decision state for waiting states
        // Always sync if state differs, but don't reduce urgency below assessment-based escalation
        const syncedState = this.getAttentionStateForDecisionState(newDecisionState);
        if (syncedState && syncedState !== existingItem.state && compareAttentionStates(syncedState, existingItem.state) <= 0) {
          this.updateAttentionItem(existingItem.id, { state: syncedState });
          if (updatedItem) existingItem.state = syncedState;
        }
        
        // Handle closure: if decision state is RESOLVED and signal indicates closure, set state to CLOSED
        if (newDecisionState === "RESOLVED") {
          const closurePhrases = ["no further", "discharged", "closed", "walking normally", "no further falls", "fully recovered", "back to baseline", "returned to baseline"];
          const signalLower = params.signal.toLowerCase();
          if (closurePhrases.some(phrase => signalLower.includes(phrase))) {
            this.updateAttentionItem(existingItem.id, { state: "CLOSED" });
            if (updatedItem) existingItem.state = "CLOSED";
          }
        }
      }
      
      // Then handle attention state changes from assessment
      // Only escalate based on assessment recommendation, not min-allowed-state floor
      // min-allowed-state only prevents de-escalation below decision requirements
      if (compareAttentionStates(recommendedState, existingItem.state) > 0) {
        return this.escalateAttention(existingItem.id, `Signal update: ${params.signal}`, "system", recommendedState) || existingItem;
      } else if (compareAttentionStates(recommendedState, existingItem.state) < 0 && isActiveAttentionState(existingItem.state)) {
        // Don't de-escalate below min-allowed-state from decision requirements
        if (minAllowedState && compareAttentionStates(minAllowedState, existingItem.state) >= 0) {
          // Min-allowed-state is already at or above current state → don't de-escalate
          return this.updateAttentionItem(existingItem.id, { assessment }) || existingItem;
        }
        const effectiveState = minAllowedState && compareAttentionStates(recommendedState, minAllowedState) < 0 ? minAllowedState : recommendedState;
        if (compareAttentionStates(effectiveState, existingItem.state) < 0) {
          return this.deescalateAttention(existingItem.id, `Signal improved: ${params.signal}`, "system") || existingItem;
        }
      }
      
      return this.updateAttentionItem(existingItem.id, { assessment }) || existingItem;
    }

    return this.createAttentionItem({
      care_recipient_id: params.care_recipient_id,
      title: summarizeSignal(params.signal),
      description: params.signal,
      triggering_signal: params.signal,
      clinical_context: params.clinical_context,
      baseline: params.baseline,
      history: params.history,
      caregiver_capacity: params.caregiver_capacity,
      time_since_onset_ms: params.time_since_onset_ms,
      recurrence_count: params.recurrence_count,
      trend: params.trend,
      action_taken: params.action_taken,
      action_description: params.action_description,
      evidence_quality: params.evidence_quality,
      open_loops: params.open_loops,
      uncertainty_level: params.uncertainty_level,
      tags: ["auto-created"],
    });
  },

  escalateAttention(itemId, reason, actor, targetState?: AttentionState) {
    const item = attentionStore.get(itemId);
    if (!item || isTerminalAttentionState(item.state)) return null;

    // If targetState provided, jump directly there; otherwise use one-step escalation
    let newState: AttentionState;
    if (targetState && compareAttentionStates(targetState, item.state) > 0) {
      newState = targetState;
    } else {
      const escalationPath: Record<AttentionState, AttentionState> = {
        NOT_RELEVANT: "BACKGROUND",
        BACKGROUND: "WATCH",
        WATCH: "NEEDS_ATTENTION",
        NEEDS_ATTENTION: "HIGH_PRIORITY",
        HIGH_PRIORITY: "URGENT",
        URGENT: "EMERGENCY",
        EMERGENCY: "EMERGENCY",
        AWAITING_INFORMATION: "NEEDS_ATTENTION",
        AWAITING_DECISION: "HIGH_PRIORITY",
        AWAITING_ACTION: "HIGH_PRIORITY",
        IN_PROGRESS: "HIGH_PRIORITY",
        AWAITING_VERIFICATION: "NEEDS_ATTENTION", 
        RESOLVED: "NEEDS_ATTENTION",
        CLOSED: "NEEDS_ATTENTION",
        SUPERSEDED: "NEEDS_ATTENTION",
      };
      newState = escalationPath[item.state] || item.state;
    }
    
    if (newState === item.state) return item;

    return this.updateAttentionItem(itemId, { state: newState });
  },

  deescalateAttention(itemId, reason, actor, targetState?: AttentionState) {
    const item = attentionStore.get(itemId);
    if (!item || !isActiveAttentionState(item.state)) return null;

    const minInterval = currentConfig.min_deescalation_interval_ms;
    const lastHistory = historyStore.get(itemId)?.slice(-1)[0];
    if (lastHistory) {
      const timeSinceLastChange = Date.now() - new Date(lastHistory.timestamp).getTime();
      if (timeSinceLastChange < minInterval) return item;
    }

    let newState: AttentionState;
    if (targetState && compareAttentionStates(targetState, item.state) < 0) {
      newState = targetState;
    } else {
      const deescalationPath: Record<AttentionState, AttentionState> = {
        EMERGENCY: "URGENT",
        URGENT: "HIGH_PRIORITY",
        HIGH_PRIORITY: "NEEDS_ATTENTION",
        NEEDS_ATTENTION: "WATCH",
        WATCH: "BACKGROUND",
        BACKGROUND: "NOT_RELEVANT",
        AWAITING_INFORMATION: "WATCH",
        AWAITING_DECISION: "WATCH",
        AWAITING_ACTION: "WATCH",
        IN_PROGRESS: "WATCH",
        AWAITING_VERIFICATION: "WATCH",
        RESOLVED: "RESOLVED",
        CLOSED: "CLOSED",
        SUPERSEDED: "SUPERSEDED",
        NOT_RELEVANT: "NOT_RELEVANT",
      };
      newState = deescalationPath[item.state] || item.state;
    }
    
    if (newState === item.state) return item;

    return this.updateAttentionItem(itemId, { state: newState });
  },

  resolveAttention(itemId, params) {
    const item = attentionStore.get(itemId);
    if (!item) return null;

    return this.updateAttentionItem(itemId, {
      state: "RESOLVED",
      decision_state: "RESOLVED",
    });
  },

  closeAttention(itemId, reason, actor) {
    const item = attentionStore.get(itemId);
    if (!item || item.state !== "RESOLVED") return null;

    return this.updateAttentionItem(itemId, { state: "CLOSED" });
  },

  reopenAttention(itemId, newSignal, actor) {
    const item = attentionStore.get(itemId);
    if (!item || !isTerminalAttentionState(item.state)) return null;

    const newItem = this.createAttentionItem({
      care_recipient_id: item.care_recipient_id,
      title: summarizeSignal(newSignal),
      description: newSignal,
      triggering_signal: newSignal,
      initial_state: "WATCH",
      tags: ["reopened", ...item.tags],
      related_event_ids: item.related_event_ids,
    });

    item.superseded_by = newItem.id;
    newItem.supersedes = item.id;

    addHistoryEntry(newItem.id, {
      item_id: newItem.id,
      from_state: null,
      to_state: newItem.state,
      from_decision_state: null,
      to_decision_state: newItem.decision_state,
      trigger: "reopening",
      actor,
      evidence: [newSignal],
      reasoning: `Reopened from item ${item.id} with new signal: ${newSignal}`,
    });

    return newItem;
  },

  supersedeAttention(oldItemId, newItemId, reason) {
    const oldItem = attentionStore.get(oldItemId);
    const newItem = attentionStore.get(newItemId);
    if (!oldItem || !newItem) return null;

    oldItem.superseded_by = newItemId;
    newItem.supersedes = oldItemId;

    this.updateAttentionItem(oldItemId, { state: "SUPERSEDED" });
    return newItem;
  },

  assignOwnership(itemId, ownership) {
    return this.updateAttentionItem(itemId, { ownership });
  },

  addDependency(itemId, dependency) {
    const item = attentionStore.get(itemId);
    if (!item) return null;

    item.dependencies.push(dependency);
    item.updated_at = new Date().toISOString();
    return item;
  },

  satisfyDependency(itemId, dependencyId, actor) {
    const item = attentionStore.get(itemId);
    if (!item) return null;

    const dep = item.dependencies.find(d => d.dependency_id === dependencyId);
    if (dep) {
      dep.satisfied = true;
      item.updated_at = new Date().toISOString();
      
      addHistoryEntry(itemId, {
        item_id: itemId,
        from_state: item.state,
        to_state: item.state,
        from_decision_state: item.decision_state,
        to_decision_state: item.decision_state,
        trigger: "dependency_satisfied",
        actor,
        evidence: [],
        reasoning: `Dependency ${dependencyId} satisfied: ${dep.satisfaction_criteria}`,
      });
    }
    return item;
  },

  getAttentionItem(itemId) {
    return attentionStore.get(itemId);
  },

  getActiveItems(careRecipientId) {
    return Array.from(attentionStore.values())
      .filter(item => item.care_recipient_id === careRecipientId && isActiveAttentionState(item.state));
  },

  getItemsByState(careRecipientId, state) {
    return Array.from(attentionStore.values())
      .filter(item => item.care_recipient_id === careRecipientId && item.state === state);
  },

  getHistory(itemId) {
    return historyStore.get(itemId) || [];
  },

  evaluateLifecycle(careRecipientId) {
    const activeItems = this.getActiveItems(careRecipientId);
    const now = new Date().toISOString();
    
    const escalations: EscalationResult[] = [];
    const deescalations: DeescalationResult[] = [];
    const resolutions: ResolutionResult[] = [];
    const closures: ClosureResult[] = [];
    const reopenings: ReopeningResult[] = [];
    const ownershipGaps: OwnershipGap[] = [];
    const dependencyBlocks: DependencyBlock[] = [];
    const suppressedItems: SuppressedItem[] = [];

    for (const item of activeItems) {
      if (!item.ownership && isActiveAttentionState(item.state)) {
        ownershipGaps.push({
          item_id: item.id,
          item_title: item.title,
          state: item.state,
          message: `Active attention item "${item.title}" has no assigned owner`,
        });
      }

      for (const dep of item.dependencies) {
        if (!dep.satisfied) {
          dependencyBlocks.push({
            item_id: item.id,
            blocked_by_item_id: dep.depends_on_item_id,
            dependency_type: dep.type,
            satisfaction_criteria: dep.satisfaction_criteria,
          });
        }
      }

      const assessmentContext = createAssessmentContext(
        item.triggering_signal,
        item.description,
        [],
        "unknown",
        "",
        [],
        Date.now() - new Date(item.created_at).getTime(),
        0,
        "unknown",
        item.decision_state === "ACTION_COMPLETED" || item.decision_state === "RESOLVED",
        undefined,
        "medium",
        [],
        0.5,
        careRecipientId
      );
      const assessment = assessAttention(assessmentContext, currentConfig);
      
      if (compareAttentionStates(assessment.recommended_state, item.state) > 0) {
        escalations.push({
          item_id: item.id,
          from_state: item.state,
          to_state: assessment.recommended_state,
          reason: `Assessment recommends escalation: ${assessment.reasoning}`,
        });
      } else if (compareAttentionStates(assessment.recommended_state, item.state) < 0) {
        deescalations.push({
          item_id: item.id,
          from_state: item.state,
          to_state: assessment.recommended_state,
          reason: `Assessment recommends de-escalation: ${assessment.reasoning}`,
        });
      }

      if (item.decision_state === "ACTION_COMPLETED" && item.state !== "RESOLVED") {
        resolutions.push({
          item_id: item.id,
          outcome: "Action completed, awaiting verification",
          verified: false,
        });
      }

      if (item.state === "RESOLVED" && Date.now() - new Date(item.updated_at).getTime() > currentConfig.max_attention_duration_ms) {
        closures.push({
          item_id: item.id,
          reason: "Resolved for extended period, auto-closing",
        });
      }
    }

    const competitionResult = resolveCompetition(activeItems);
    for (const suppressed of competitionResult.suppressed_items) {
      suppressedItems.push({
        item_id: suppressed.item_id,
        reason: suppressed.reason,
        primary_item_id: competitionResult.primary_item_id,
      });
    }

    return {
      care_recipient_id: careRecipientId,
      evaluated_at: now,
      items_evaluated: activeItems.length,
      escalations,
      deescalations,
      resolutions,
      closures,
      reopenings,
      ownership_gaps: ownershipGaps,
      dependency_blocks: dependencyBlocks,
      suppressed_items: suppressedItems,
    };
  },

  setConfig(config) {
    currentConfig = { ...currentConfig, ...config };
  },
};

function findMatchingItem(careRecipientId: string, signal: string, existingItemIds?: string[]): AttentionItem | null {
  // First check explicitly provided existing items
  if (existingItemIds && existingItemIds.length > 0) {
    for (const itemId of existingItemIds) {
      const item = attentionStore.get(itemId);
      if (item && isActiveAttentionState(item.state)) {
        return item;
      }
    }
  }
  
  // Fallback to word-based matching
  const items = Array.from(attentionStore.values())
    .filter(item => item.care_recipient_id === careRecipientId && isActiveAttentionState(item.state));
  
  const signalWords = signal.toLowerCase().split(/\s+/).filter(w => w.length > 3);
  
  for (const item of items) {
    const itemWords = item.description.toLowerCase().split(/\s+/).filter(w => w.length > 3);
    const overlap = signalWords.filter(w => itemWords.includes(w)).length;
    if (overlap >= 2) return item;
  }
  return null;
}

function summarizeSignal(signal: string): string {
  const words = signal.split(/\s+/);
  if (words.length <= 8) return signal;
  return words.slice(0, 8).join(" ") + "...";
}

function resolveCompetition(items: AttentionItem[]): CompetitionResolution {
  if (items.length <= 1) {
    return {
      primary_item_id: items[0]?.id || "",
      reasoning: "Only one active item",
      suppressed_items: [],
    };
  }

  const scored = items.map(item => {
    const urgencyScore = getUrgencyScore(item.state);
    const decisionScore = getDecisionUrgencyScore(item.decision_state);
    const dependencyScore = item.dependencies.filter(d => !d.satisfied).length * 0.1;
    return { item, score: urgencyScore + decisionScore + dependencyScore };
  });

  scored.sort((a, b) => b.score - a.score);
  
  const primary = scored[0];
  const suppressed = scored.slice(1).map(s => ({
    item_id: s.item.id,
    reason: "lower_priority" as const,
    detail: `Priority score ${s.score.toFixed(2)} vs primary ${primary.score.toFixed(2)}`,
  }));

  return {
    primary_item_id: primary.item.id,
    reasoning: `Primary item has highest composite urgency (${primary.score.toFixed(2)}). ${suppressed.length} items suppressed.`,
    suppressed_items: suppressed,
  };
}

function getUrgencyScore(state: AttentionState): number {
  const scores: Record<AttentionState, number> = {
    EMERGENCY: 10,
    URGENT: 9,
    HIGH_PRIORITY: 7,
    NEEDS_ATTENTION: 5,
    AWAITING_ACTION: 6,
    IN_PROGRESS: 6,
    AWAITING_DECISION: 5,
    AWAITING_VERIFICATION: 4,
    AWAITING_INFORMATION: 3,
    WATCH: 2,
    BACKGROUND: 1,
    NOT_RELEVANT: 0,
    RESOLVED: 0,
    CLOSED: 0,
    SUPERSEDED: 0,
  };
  return scores[state] || 0;
}

function getDecisionUrgencyScore(state: DecisionLifecycleState): number {
  const scores: Record<DecisionLifecycleState, number> = {
    DECISION_BLOCKED: 5,
    AWAITING_CLINICAL_DECISION: 4,
    AWAITING_HUMAN_DECISION: 4,
    DECISION_NEEDED: 3,
    ACTION_PENDING: 3,
    ACTION_IN_PROGRESS: 3,
    VERIFICATION_PENDING: 2,
    OUTCOME_UNKNOWN: 2,
    DECISION_MADE: 1,
    ACTION_COMPLETED: 1,
    RESOLVED: 0,
    SUPERSEDED: 0,
    NOT_REQUIRED: 0,
  };
  return scores[state] || 0;
}

export function clearAttentionStore(): void {
  attentionStore.clear();
  historyStore.clear();
}

export function getAllAttentionItems(): AttentionItem[] {
  return Array.from(attentionStore.values());
}

export function getAllHistory(): Map<string, AttentionHistoryEntry[]> {
  return new Map(historyStore);
}
 