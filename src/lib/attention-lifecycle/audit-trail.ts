/**
 * Attention History / Audit Trail
 * 
 * Preserves why attention was created, what evidence triggered it, who saw it,
 * what decision was made, what action occurred, what changed afterward,
 * why attention was reduced/closed.
 */

import {
  AttentionHistoryEntry,
  AttentionState,
  DecisionLifecycleState,
  HistoryTrigger,
  AttentionItem,
} from "./types";
import { lifecycleManager } from "./manager";

const auditTrailStore = new Map<string, AttentionHistoryEntry[]>();

export type AuditTrailQuery = {
  care_recipient_id?: string;
  item_id?: string;
  state?: AttentionState;
  decision_state?: DecisionLifecycleState;
  trigger?: HistoryTrigger;
  actor?: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
};

export type AuditTrailSummary = {
  item_id: string;
  item_title: string;
  total_transitions: number;
  state_changes: StateChangeSummary[];
  decision_changes: DecisionChangeSummary[];
  key_actors: string[];
  created_at: string;
  last_updated: string;
  current_state: AttentionState;
  current_decision_state: DecisionLifecycleState;
  time_in_current_state_ms: number;
  escalation_count: number;
  deescalation_count: number;
  reopen_count: number;
};

export type StateChangeSummary = {
  from_state: AttentionState | null;
  to_state: AttentionState;
  count: number;
  first_occurrence: string;
  last_occurrence: string;
  triggers: HistoryTrigger[];
};

export type DecisionChangeSummary = {
  from_state: DecisionLifecycleState | null;
  to_state: DecisionLifecycleState;
  count: number;
  first_occurrence: string;
  last_occurrence: string;
  triggers: HistoryTrigger[];
};

export type AttentionProvenance = {
  item_id: string;
  original_signal: string;
  creation_reason: string;
  creation_actor: string;
  creation_timestamp: string;
  all_evidence: string[];
  all_decisions: DecisionProvenance[];
  all_actions: ActionProvenance[];
  all_outcomes: OutcomeProvenance[];
  closure_reason?: string;
  closure_actor?: string;
  closure_timestamp?: string;
};

export type DecisionProvenance = {
  decision_id: string;
  question: string;
  chosen_option: string;
  reasoning: string;
  made_by: string;
  made_at: string;
  clinical_approval: boolean;
  clinical_approver?: string;
};

export type ActionProvenance = {
  plan_id: string;
  steps: StepProvenance[];
  owner: string;
  started_at?: string;
  completed_at?: string;
  status: string;
};

export type StepProvenance = {
  step_id: string;
  description: string;
  owner: string;
  completed_at?: string;
  status: string;
};

export type OutcomeProvenance = {
  verification_id: string;
  outcome_observed: string;
  matches_expected: boolean;
  verified_by: string;
  verified_at: string;
  notes: string;
};

export const auditTrail = {
  /**
   * Record a history entry (called by lifecycle manager)
   */
  recordEntry(entry: Omit<AttentionHistoryEntry, "history_id" | "timestamp">): AttentionHistoryEntry {
    const fullEntry: AttentionHistoryEntry = {
      ...entry,
      history_id: `hist_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      timestamp: new Date().toISOString(),
    };

    const history = auditTrailStore.get(entry.item_id || "global") || [];
    history.push(fullEntry);
    auditTrailStore.set(entry.item_id || "global", history);

    return fullEntry;
  },

  /**
   * Get history for a specific item
   */
  getItemHistory(itemId: string): AttentionHistoryEntry[] {
    return auditTrailStore.get(itemId) || [];
  },

  /**
   * Query history with filters
   */
  queryHistory(query: AuditTrailQuery): AttentionHistoryEntry[] {
    let results: AttentionHistoryEntry[] = [];

    if (query.item_id) {
      results = this.getItemHistory(query.item_id);
    } else {
      for (const history of Array.from(auditTrailStore.values())) {
        results.push(...history);
      }
    }

    if (query.care_recipient_id) {
      // Would need item lookup - simplified for now
    }

    if (query.state) {
      results = results.filter(e => e.to_state === query.state);
    }

    if (query.decision_state) {
      results = results.filter(e => e.to_decision_state === query.decision_state);
    }

    if (query.trigger) {
      results = results.filter(e => e.trigger === query.trigger);
    }

    if (query.actor) {
      results = results.filter(e => e.actor === query.actor);
    }

    if (query.from_date) {
      const from = new Date(query.from_date).getTime();
      results = results.filter(e => new Date(e.timestamp).getTime() >= from);
    }

    if (query.to_date) {
      const to = new Date(query.to_date).getTime();
      results = results.filter(e => new Date(e.timestamp).getTime() <= to);
    }

    results.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    if (query.limit) {
      results = results.slice(0, query.limit);
    }

    return results;
  },

  /**
   * Get summary of an item's audit trail
   */
  getItemSummary(itemId: string): AuditTrailSummary | null {
    const history = this.getItemHistory(itemId);
    if (history.length === 0) return null;

    const item = lifecycleManager.getAttentionItem(itemId);
    if (!item) return null;

    const stateChanges = new Map<string, StateChangeSummary>();
    const decisionChanges = new Map<string, DecisionChangeSummary>();
    const actors = new Set<string>();
    let escalationCount = 0;
    let deescalationCount = 0;
    let reopenCount = 0;

    for (const entry of history) {
      actors.add(entry.actor);

      const stateKey = `${entry.from_state || "null"} -> ${entry.to_state}`;
      const existingState = stateChanges.get(stateKey) || {
        from_state: entry.from_state,
        to_state: entry.to_state,
        count: 0,
        first_occurrence: entry.timestamp,
        last_occurrence: entry.timestamp,
        triggers: [],
      };
      existingState.count++;
      existingState.last_occurrence = entry.timestamp;
      if (!existingState.triggers.includes(entry.trigger)) {
        existingState.triggers.push(entry.trigger);
      }
      stateChanges.set(stateKey, existingState);

      const decisionKey = `${entry.from_decision_state || "null"} -> ${entry.to_decision_state}`;
      const existingDecision = decisionChanges.get(decisionKey) || {
        from_state: entry.from_decision_state,
        to_state: entry.to_decision_state,
        count: 0,
        first_occurrence: entry.timestamp,
        last_occurrence: entry.timestamp,
        triggers: [],
      };
      existingDecision.count++;
      existingDecision.last_occurrence = entry.timestamp;
      if (!existingDecision.triggers.includes(entry.trigger)) {
        existingDecision.triggers.push(entry.trigger);
      }
      decisionChanges.set(decisionKey, existingDecision);

      if (entry.trigger === "escalation") escalationCount++;
      if (entry.trigger === "de_escalation") deescalationCount++;
      if (entry.trigger === "reopening") reopenCount++;
    }

    const lastEntry = history[history.length - 1];
    const timeInCurrentState = Date.now() - new Date(lastEntry.timestamp).getTime();

    return {
      item_id: itemId,
      item_title: item.title,
      total_transitions: history.length,
      state_changes: Array.from(stateChanges.values()),
      decision_changes: Array.from(decisionChanges.values()),
      key_actors: Array.from(actors),
      created_at: item.created_at,
      last_updated: item.updated_at,
      current_state: item.state,
      current_decision_state: item.decision_state,
      time_in_current_state_ms: timeInCurrentState,
      escalation_count: escalationCount,
      deescalation_count: deescalationCount,
      reopen_count: reopenCount,
    };
  },

  /**
   * Get full provenance for an item (why it was created, what happened)
   */
  getItemProvenance(itemId: string): AttentionProvenance | null {
    const history = this.getItemHistory(itemId);
    if (history.length === 0) return null;

    const item = lifecycleManager.getAttentionItem(itemId);
    if (!item) return null;

    const creationEntry = history.find(e => e.trigger === "signal_detected");
    const allEvidence = history.flatMap(e => e.evidence);
    const allDecisions = history
      .filter(e => e.decision_made)
      .map(e => ({
        decision_id: e.history_id,
        question: "Decision made",
        chosen_option: e.decision_made || "unknown",
        reasoning: e.reasoning,
        made_by: e.actor,
        made_at: e.timestamp,
        clinical_approval: false,
      }));
    const allActions = history
      .filter(e => e.action_taken)
      .map(e => ({
        plan_id: e.history_id,
        steps: [{ step_id: e.history_id, description: e.action_taken, owner: e.actor, completed_at: e.timestamp, status: "completed" }],
        owner: e.actor,
        started_at: e.timestamp,
        completed_at: e.timestamp,
        status: "completed",
      }));
    const allOutcomes = history
      .filter(e => e.outcome)
      .map(e => ({
        verification_id: e.history_id,
        outcome_observed: e.outcome || "unknown",
        matches_expected: e.to_state === "RESOLVED",
        verified_by: e.actor,
        verified_at: e.timestamp,
        notes: e.reasoning,
      }));

    const closureEntry = history.find(e => e.trigger === "closure");

    return {
      item_id: itemId,
      original_signal: item.triggering_signal,
      creation_reason: creationEntry?.reasoning || "Signal detected",
      creation_actor: creationEntry?.actor || "system",
      creation_timestamp: creationEntry?.timestamp || item.created_at,
      all_evidence: Array.from(new Set(allEvidence)),
      all_decisions: allDecisions,
      all_actions: allActions,
      all_outcomes: allOutcomes,
      closure_reason: closureEntry?.reasoning,
      closure_actor: closureEntry?.actor,
      closure_timestamp: closureEntry?.timestamp,
    };
  },

  /**
   * Answer "Why was this considered important on [date]?"
   */
  answerWhyImportant(itemId: string, date: string): string[] {
    const history = this.getItemHistory(itemId);
    const targetDate = new Date(date).getTime();
    
    const relevantEntries = history.filter(e => {
      const entryDate = new Date(e.timestamp).getTime();
      return entryDate <= targetDate && 
             (e.trigger === "escalation" || e.trigger === "signal_detected" || e.trigger === "assessment_updated");
    });

    return relevantEntries.map(e => {
      const dateStr = new Date(e.timestamp).toLocaleDateString();
      return `[${dateStr}] ${e.reasoning} (State: ${e.from_state || "none"} -> ${e.to_state})`;
    });
  },

  /**
   * Answer "Why is this no longer active today?"
   */
  answerWhyClosed(itemId: string): string[] {
    const history = this.getItemHistory(itemId);
    const closureEntries = history.filter(e => 
      e.trigger === "closure" || e.trigger === "de_escalation" || e.trigger === "resolution_confirmed"
    );

    return closureEntries.map(e => {
      const dateStr = new Date(e.timestamp).toLocaleDateString();
      return `[${dateStr}] ${e.reasoning} (State: ${e.from_state} -> ${e.to_state})`;
    });
  },

  /**
   * Export audit trail for compliance/review
   */
  exportAuditTrail(itemId?: string): string {
    let entries: AttentionHistoryEntry[] = [];
    
    if (itemId) {
      entries = this.getItemHistory(itemId);
    } else {
      for (const history of Array.from(auditTrailStore.values())) {
        entries.push(...history);
      }
    }

    entries.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

    return JSON.stringify(entries.map(e => ({
      history_id: e.history_id,
      timestamp: e.timestamp,
      item_id: e.item_id,
      from_state: e.from_state,
      to_state: e.to_state,
      from_decision_state: e.from_decision_state,
      to_decision_state: e.to_decision_state,
      trigger: e.trigger,
      actor: e.actor,
      evidence: e.evidence,
      reasoning: e.reasoning,
      decision_made: e.decision_made,
      action_taken: e.action_taken,
      outcome: e.outcome,
    })), null, 2);
  },

  /**
   * Clear audit trail (for testing)
   */
  clear(): void {
    auditTrailStore.clear();
  },

  /**
   * Get all history entries across all items
   */
  getAllHistory(): AttentionHistoryEntry[] {
    const all: AttentionHistoryEntry[] = [];
    for (const history of Array.from(auditTrailStore.values())) {
      all.push(...history);
    }
    return all.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  },
};

export function getAuditTrailStore(): Map<string, AttentionHistoryEntry[]> {
  return new Map(auditTrailStore);
}