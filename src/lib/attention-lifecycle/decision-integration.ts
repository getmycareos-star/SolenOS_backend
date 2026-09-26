/**
 * Decision Lifecycle Integration
 * 
 * Connects attention lifecycle with decision lifecycle:
 * Attention detected → Is decision required? → What decision? → Who owns it? 
 * → What information needed? → Decision made → Action required? 
 * → Action executed → Outcome verified → Close/continue monitoring
 */

import {
  DecisionLifecycleState,
  AttentionState,
  AttentionItem,
  OwnershipAssignment,
  AttentionOwner,
  DECISION_LIFECYCLE_ORDER,
  decisionLifecycleRank,
} from "./types";
import { lifecycleManager } from "./manager";

export type DecisionContext = {
  attention_item_id: string;
  question: string;
  options: DecisionOption[];
  required_information: string[];
  urgency: "low" | "medium" | "high" | "critical";
  clinical_input_needed: boolean;
  time_constraint?: string;
};

export type DecisionOption = {
  id: string;
  description: string;
  pros: string[];
  cons: string[];
  risks: string[];
  requires_clinical_approval: boolean;
};

export type DecisionRecord = {
  decision_id: string;
  attention_item_id: string;
  question: string;
  chosen_option_id: string | null;
  reasoning: string;
  made_by: AttentionOwner;
  made_at: string;
  required_information: string[];
  missing_information: string[];
  clinical_approval: boolean;
  clinical_approver?: string;
  status: DecisionLifecycleState;
  action_plan?: ActionPlan;
};

export type ActionPlan = {
  plan_id: string;
  steps: ActionStep[];
  owner: AttentionOwner;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  status: "pending" | "in_progress" | "completed" | "blocked" | "cancelled";
};

export type ActionStep = {
  step_id: string;
  description: string;
  owner: AttentionOwner;
  due_date?: string;
  completed_at?: string;
  status: "pending" | "in_progress" | "completed" | "blocked";
  dependencies: string[];
};

export type DecisionVerification = {
  verification_id: string;
  decision_id: string;
  attention_item_id: string;
  outcome_observed: string;
  matches_expected: boolean;
  verified_by: AttentionOwner;
  verified_at: string;
  notes: string;
};

const decisionStore = new Map<string, DecisionRecord>();
const actionPlanStore = new Map<string, ActionPlan>();
const verificationStore = new Map<string, DecisionVerification>();

export const decisionLifecycle = {
  /**
   * Determine if a decision is required for an attention item
   */
  isDecisionRequired(item: AttentionItem): boolean {
    if (item.decision_state === "NOT_REQUIRED") return false;
    if (item.decision_state === "DECISION_NEEDED") return true;
    if (item.decision_state === "DECISION_BLOCKED") return true;
    if (item.decision_state === "AWAITING_HUMAN_DECISION") return true;
    if (item.decision_state === "AWAITING_CLINICAL_DECISION") return true;
    return false;
  },

  /**
   * Analyze attention item to determine what decision is needed
   */
  analyzeDecisionNeed(item: AttentionItem): DecisionContext | null {
    const signal = item.triggering_signal.toLowerCase();
    const description = item.description.toLowerCase();

    if (/\b(medication|dose|prescription|pharmacy)\b/i.test(signal)) {
      return {
        attention_item_id: item.id,
        question: "What medication action is needed?",
        options: [
          { id: "contact_prescriber", description: "Contact prescriber for clarification/change", pros: ["Clinical oversight"], cons: ["May take time"], risks: ["Delay in treatment"], requires_clinical_approval: true },
          { id: "verify_with_pharmacy", description: "Verify with pharmacy", pros: ["Quick verification"], cons: ["Limited clinical context"], risks: ["May miss clinical nuance"], requires_clinical_approval: false },
          { id: "monitor_and_document", description: "Monitor and document for next appointment", pros: ["Non-invasive"], cons: ["Risk if urgent"], risks: ["Delay if issue is acute"], requires_clinical_approval: false },
        ],
        required_information: ["Current medication list", "Prescriber contact", "Pharmacy contact"],
        urgency: "high",
        clinical_input_needed: true,
      };
    }

    if (/\b(fall|injury|pain|worsening|confusion|breathing)\b/i.test(signal)) {
      return {
        attention_item_id: item.id,
        question: "What clinical action is needed?",
        options: [
          { id: "seek_immediate_care", description: "Seek immediate medical attention", pros: ["Addresses acute risk"], cons: ["May be unnecessary"], risks: ["Delay could be harmful"], requires_clinical_approval: true },
          { id: "contact_clinician", description: "Contact clinician for guidance", pros: ["Professional assessment"], cons: ["May not be immediately available"], risks: ["Delay in response"], requires_clinical_approval: true },
          { id: "monitor_closely", description: "Monitor closely with defined checkpoints", pros: ["Avoids unnecessary visits"], cons: ["Risk if condition worsens"], risks: ["Requires vigilant monitoring"], requires_clinical_approval: false },
        ],
        required_information: ["Current symptoms", "Vital signs if available", "Clinician contact", "Emergency contacts"],
        urgency: "critical",
        clinical_input_needed: true,
      };
    }

    if (/\b(appointment|refill|follow[- ]?up|schedule|arrange)\b/i.test(signal)) {
      return {
        attention_item_id: item.id,
        question: "What scheduling action is needed?",
        options: [
          { id: "schedule_appointment", description: "Schedule the appointment", pros: ["Addresses the need"], cons: ["Availability constraints"], risks: ["Delay if slots full"], requires_clinical_approval: false },
          { id: "request_refill", description: "Request prescription refill", pros: ["Maintains medication continuity"], cons: ["Processing time"], risks: ["Gap in medication"], requires_clinical_approval: false },
          { id: "coordinate_with_family", description: "Coordinate with family for transport/support", pros: ["Shared responsibility"], cons: ["Coordination overhead"], risks: ["Miscommunication"], requires_clinical_approval: false },
        ],
        required_information: ["Provider availability", "Insurance/referral requirements", "Transport options", "Family availability"],
        urgency: "medium",
        clinical_input_needed: false,
      };
    }

    return {
      attention_item_id: item.id,
      question: "What action is needed for this attention item?",
      options: [
        { id: "monitor", description: "Continue monitoring", pros: ["Low burden"], cons: ["May miss changes"], risks: ["Delayed response"], requires_clinical_approval: false },
        { id: "seek_guidance", description: "Seek professional guidance", pros: ["Expert input"], cons: ["Time and access"], risks: ["Delay"], requires_clinical_approval: true },
        { id: "take_action", description: "Take specific action based on context", pros: ["Directly addresses issue"], cons: ["Requires clarity"], risks: ["Wrong action if uncertain"], requires_clinical_approval: false },
      ],
      required_information: ["More context about the situation"],
      urgency: "low",
      clinical_input_needed: false,
    };
  },

  /**
   * Record a decision made for an attention item
   */
  recordDecision(params: {
    attention_item_id: string;
    question: string;
    chosen_option_id: string;
    reasoning: string;
    made_by: AttentionOwner;
    required_information: string[];
    missing_information: string[];
    clinical_approval: boolean;
    clinical_approver?: string;
  }): DecisionRecord {
    const decision: DecisionRecord = {
      decision_id: `dec_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
      attention_item_id: params.attention_item_id,
      question: params.question,
      chosen_option_id: params.chosen_option_id,
      reasoning: params.reasoning,
      made_by: params.made_by,
      made_at: new Date().toISOString(),
      required_information: params.required_information,
      missing_information: params.missing_information,
      clinical_approval: params.clinical_approval,
      clinical_approver: params.clinical_approver,
      status: "DECISION_MADE",
    };

    decisionStore.set(decision.decision_id, decision);
    
    lifecycleManager.updateAttentionItem(params.attention_item_id, {
      decision_state: "DECISION_MADE",
    });

    return decision;
  },

  /**
   * Create an action plan from a decision
   */
  createActionPlan(params: {
    decision_id: string;
    steps: Omit<ActionStep, "step_id">[];
    owner: AttentionOwner;
  }): ActionPlan {
    const decision = decisionStore.get(params.decision_id);
    if (!decision) throw new Error("Decision not found");

    const plan: ActionPlan = {
      plan_id: `plan_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
      steps: params.steps.map((s, i) => ({
        ...s,
        step_id: `step_${i}_${Date.now().toString(36)}`,
      })),
      owner: params.owner,
      created_at: new Date().toISOString(),
      status: "pending",
    };

    actionPlanStore.set(plan.plan_id, plan);
    decision.action_plan = plan;

    lifecycleManager.updateAttentionItem(decision.attention_item_id, {
      decision_state: "ACTION_PENDING",
    });

    return plan;
  },

  /**
   * Start executing an action plan
   */
  startActionPlan(plan_id: string): ActionPlan | null {
    const plan = actionPlanStore.get(plan_id);
    if (!plan) return null;

    plan.status = "in_progress";
    plan.started_at = new Date().toISOString();

    const decision = Array.from(decisionStore.values()).find(d => d.action_plan?.plan_id === plan_id);
    if (decision) {
      lifecycleManager.updateAttentionItem(decision.attention_item_id, {
        decision_state: "ACTION_IN_PROGRESS",
      });
    }

    return plan;
  },

  /**
   * Complete an action step
   */
  completeActionStep(plan_id: string, step_id: string, completed_by: AttentionOwner): ActionPlan | null {
    const plan = actionPlanStore.get(plan_id);
    if (!plan) return null;

    const step = plan.steps.find(s => s.step_id === step_id);
    if (step) {
      step.status = "completed";
      step.completed_at = new Date().toISOString();
    }

    const allCompleted = plan.steps.every(s => s.status === "completed");
    if (allCompleted) {
      plan.status = "completed";
      plan.completed_at = new Date().toISOString();

      const decision = Array.from(decisionStore.values()).find(d => d.action_plan?.plan_id === plan_id);
      if (decision) {
        lifecycleManager.updateAttentionItem(decision.attention_item_id, {
          decision_state: "ACTION_COMPLETED",
        });
      }
    }

    return plan;
  },

  /**
   * Record verification of outcome
   */
  recordVerification(params: {
    decision_id: string;
    outcome_observed: string;
    matches_expected: boolean;
    verified_by: AttentionOwner;
    notes: string;
  }): DecisionVerification {
    const decision = decisionStore.get(params.decision_id);
    if (!decision) throw new Error("Decision not found");

    const verification: DecisionVerification = {
      verification_id: `ver_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
      decision_id: params.decision_id,
      attention_item_id: decision.attention_item_id,
      outcome_observed: params.outcome_observed,
      matches_expected: params.matches_expected,
      verified_by: params.verified_by,
      verified_at: new Date().toISOString(),
      notes: params.notes,
    };

    verificationStore.set(verification.verification_id, verification);

    if (params.matches_expected) {
      lifecycleManager.updateAttentionItem(decision.attention_item_id, {
        decision_state: "RESOLVED",
        state: "RESOLVED",
      });
      decision.status = "RESOLVED";
    } else {
      lifecycleManager.updateAttentionItem(decision.attention_item_id, {
        decision_state: "OUTCOME_UNKNOWN",
        state: "NEEDS_ATTENTION",
      });
      decision.status = "OUTCOME_UNKNOWN";
    }

    return verification;
  },

  /**
   * Get decision for an attention item
   */
  getDecisionForItem(attention_item_id: string): DecisionRecord | undefined {
    return Array.from(decisionStore.values()).find(d => d.attention_item_id === attention_item_id);
  },

  /**
   * Get action plan for a decision
   */
  getActionPlan(decision_id: string): ActionPlan | undefined {
    const decision = decisionStore.get(decision_id);
    return decision?.action_plan;
  },

  /**
   * Get verification for a decision
   */
  getVerification(decision_id: string): DecisionVerification | undefined {
    return Array.from(verificationStore.values()).find(v => v.decision_id === decision_id);
  },

  /**
   * Determine next decision state based on current state and events
   */
  determineNextDecisionState(
    current_state: DecisionLifecycleState,
    event: "decision_made" | "action_started" | "action_completed" | "outcome_verified" | "outcome_mismatch" | "information_received" | "clinical_input_received" | "blocked" | "cancelled"
  ): DecisionLifecycleState {
    const transitions: Record<DecisionLifecycleState, Record<string, DecisionLifecycleState>> = {
      NOT_REQUIRED: { decision_made: "DECISION_MADE" },
      DECISION_NEEDED: { decision_made: "DECISION_MADE", information_received: "DECISION_NEEDED", blocked: "DECISION_BLOCKED" },
      DECISION_BLOCKED: { information_received: "DECISION_NEEDED", clinical_input_received: "AWAITING_CLINICAL_DECISION" },
      AWAITING_HUMAN_DECISION: { decision_made: "DECISION_MADE", information_received: "AWAITING_HUMAN_DECISION" },
      AWAITING_CLINICAL_DECISION: { decision_made: "DECISION_MADE", clinical_input_received: "DECISION_MADE" },
      DECISION_MADE: { action_started: "ACTION_IN_PROGRESS", action_completed: "ACTION_COMPLETED" },
      ACTION_PENDING: { action_started: "ACTION_IN_PROGRESS" },
      ACTION_IN_PROGRESS: { action_completed: "ACTION_COMPLETED", blocked: "ACTION_PENDING" },
      ACTION_COMPLETED: { outcome_verified: "VERIFICATION_PENDING", outcome_mismatch: "OUTCOME_UNKNOWN" },
      OUTCOME_UNKNOWN: { outcome_verified: "VERIFICATION_PENDING" },
      VERIFICATION_PENDING: { outcome_verified: "RESOLVED", outcome_mismatch: "OUTCOME_UNKNOWN" },
      RESOLVED: {},
      SUPERSEDED: {},
    };

    const stateTransitions = transitions[current_state];
    if (stateTransitions && stateTransitions[event]) {
      return stateTransitions[event];
    }
    return current_state;
  },

  /**
   * Check if decision lifecycle is complete (decision made + action completed + verified)
   */
  isDecisionLifecycleComplete(item: AttentionItem): boolean {
    return item.decision_state === "RESOLVED";
  },

  /**
   * Check if decision was made but action/outcome not yet verified
   * This is the critical distinction: Decision ≠ Implementation ≠ Outcome
   */
  getDecisionImplementationGap(item: AttentionItem): {
    decision_made: boolean;
    action_started: boolean;
    action_completed: boolean;
    outcome_verified: boolean;
    gap_description: string;
  } {
    const decision = this.getDecisionForItem(item.id);
    const plan = decision ? this.getActionPlan(decision.decision_id) : null;
    const verification = decision ? this.getVerification(decision.decision_id) : null;

    return {
      decision_made: item.decision_state !== "NOT_REQUIRED" && item.decision_state !== "DECISION_NEEDED" && item.decision_state !== "DECISION_BLOCKED",
      action_started: plan?.status === "in_progress" || plan?.status === "completed",
      action_completed: plan?.status === "completed",
      outcome_verified: verification?.matches_expected === true,
      gap_description: this.describeGap(item.decision_state, plan?.status, verification?.matches_expected),
    };
  },

  describeGap(decisionState: DecisionLifecycleState, planStatus?: string, outcomeVerified?: boolean): string {
    if (decisionState === "DECISION_NEEDED" || decisionState === "DECISION_BLOCKED") {
      return "Decision not yet made";
    }
    if (decisionState === "AWAITING_HUMAN_DECISION" || decisionState === "AWAITING_CLINICAL_DECISION") {
      return "Awaiting decision maker";
    }
    if (decisionState === "DECISION_MADE") {
      return "Decision made, action not yet started";
    }
    if (decisionState === "ACTION_PENDING") {
      return "Action planned, not yet started";
    }
    if (decisionState === "ACTION_IN_PROGRESS") {
      return "Action in progress, not yet completed";
    }
    if (decisionState === "ACTION_COMPLETED") {
      return outcomeVerified === false ? "Action completed, outcome does not match expectation" : "Action completed, outcome not yet verified";
    }
    if (decisionState === "OUTCOME_UNKNOWN") {
      return "Outcome unknown - verification needed";
    }
    if (decisionState === "VERIFICATION_PENDING") {
      return "Verification in progress";
    }
    if (decisionState === "RESOLVED") {
      return "Decision, action, and outcome all verified";
    }
    return "Unknown state";
  },

  clearStores(): void {
    decisionStore.clear();
    actionPlanStore.clear();
    verificationStore.clear();
  },
};

export function getDecisionLifecycleStateRank(state: DecisionLifecycleState): number {
  return decisionLifecycleRank(state);
}