/**
 * Dependency-Aware Attention Tracking
 * 
 * Attention items can depend on each other.
 * Understanding decision dependencies, not just ranking isolated alerts.
 */

import {
  AttentionItem,
  AttentionDependency,
  DependencyType,
  AttentionState,
  DecisionLifecycleState,
} from "./types";
import { lifecycleManager } from "./manager";

export type DependencyGraph = {
  nodes: Map<string, AttentionItem>;
  edges: Map<string, Set<string>>;
  reverseEdges: Map<string, Set<string>>;
};

const dependencyGraphs = new Map<string, DependencyGraph>();

export function getOrCreateDependencyGraph(careRecipientId: string): DependencyGraph {
  let graph = dependencyGraphs.get(careRecipientId);
  if (!graph) {
    graph = { nodes: new Map(), edges: new Map(), reverseEdges: new Map() };
    dependencyGraphs.set(careRecipientId, graph);
  }
  return graph;
}

export function addItemToGraph(careRecipientId: string, item: AttentionItem): void {
  const graph = getOrCreateDependencyGraph(careRecipientId);
  graph.nodes.set(item.id, item);
  if (!graph.edges.has(item.id)) graph.edges.set(item.id, new Set());
  if (!graph.reverseEdges.has(item.id)) graph.reverseEdges.set(item.id, new Set());
}

export function removeItemFromGraph(careRecipientId: string, itemId: string): void {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return;

  graph.nodes.delete(itemId);
  graph.edges.delete(itemId);
  graph.reverseEdges.delete(itemId);

  for (const [_, targets] of Array.from(graph.edges.entries())) {
    targets.delete(itemId);
  }
  for (const [_, sources] of Array.from(graph.reverseEdges.entries())) {
    sources.delete(itemId);
  }
}

export function addDependencyEdge(
  careRecipientId: string,
  fromItemId: string,
  toItemId: string,
  dependency: AttentionDependency
): boolean {
  const graph = getOrCreateDependencyGraph(careRecipientId);
  
  if (!graph.nodes.has(fromItemId) || !graph.nodes.has(toItemId)) {
    return false;
  }

  const edges = graph.edges.get(fromItemId)!;
  edges.add(toItemId);

  const reverseEdges = graph.reverseEdges.get(toItemId)!;
  reverseEdges.add(fromItemId);

  const fromItem = graph.nodes.get(fromItemId)!;
  if (!fromItem.dependencies.some(d => d.dependency_id === dependency.dependency_id)) {
    fromItem.dependencies.push(dependency);
  }

  const toItem = graph.nodes.get(toItemId)!;
  if (!toItem.dependents.includes(fromItemId)) {
    toItem.dependents.push(fromItemId);
  }

  return true;
}

export function removeDependencyEdge(
  careRecipientId: string,
  fromItemId: string,
  toItemId: string
): boolean {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return false;

  const edges = graph.edges.get(fromItemId);
  if (edges) edges.delete(toItemId);

  const reverseEdges = graph.reverseEdges.get(toItemId);
  if (reverseEdges) reverseEdges.delete(fromItemId);

  const fromItem = graph.nodes.get(fromItemId);
  if (fromItem) {
    fromItem.dependencies = fromItem.dependencies.filter(d => d.depends_on_item_id !== toItemId);
  }

  const toItem = graph.nodes.get(toItemId);
  if (toItem) {
    toItem.dependents = toItem.dependents.filter(id => id !== fromItemId);
  }

  return true;
}

export function getDependents(careRecipientId: string, itemId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const dependentIds = graph.reverseEdges.get(itemId) || new Set();
  return Array.from(dependentIds)
    .map(id => graph.nodes.get(id))
    .filter((item): item is AttentionItem => item !== undefined);
}

export function getDependencies(careRecipientId: string, itemId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const dependencyIds = graph.edges.get(itemId) || new Set();
  return Array.from(dependencyIds)
    .map(id => graph.nodes.get(id))
    .filter((item): item is AttentionItem => item !== undefined);
}

export function getTransitiveDependents(careRecipientId: string, itemId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const visited = new Set<string>();
  const result: AttentionItem[] = [];

  function traverse(id: string) {
    const dependents = graph.reverseEdges.get(id) || new Set();
    for (const depId of Array.from(dependents)) {
      if (!visited.has(depId)) {
        visited.add(depId);
        const item = graph.nodes.get(depId);
        if (item) result.push(item);
        traverse(depId);
      }
    }
  }

  traverse(itemId);
  return result;
}

export function getTransitiveDependencies(careRecipientId: string, itemId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const visited = new Set<string>();
  const result: AttentionItem[] = [];

  function traverse(id: string) {
    const dependencies = graph.edges.get(id) || new Set();
    for (const depId of Array.from(dependencies)) {
      if (!visited.has(depId)) {
        visited.add(depId);
        const item = graph.nodes.get(depId);
        if (item) result.push(item);
        traverse(depId);
      }
    }
  }

  traverse(itemId);
  return result;
}

export function findBlockedItems(careRecipientId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const blocked: AttentionItem[] = [];
  for (const item of Array.from(graph.nodes.values())) {
    const hasUnsatisfiedBlockingDependency = item.dependencies.some(d => 
      !d.satisfied && (d.type === "blocks_decision" || d.type === "blocks_action" || d.type === "requires_resolution")
    );
    if (hasUnsatisfiedBlockingDependency && isActiveAttentionState(item.state)) {
      blocked.push(item);
    }
  }
  return blocked;
}

export function findUnblockedItems(careRecipientId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const unblocked: AttentionItem[] = [];
  for (const item of Array.from(graph.nodes.values())) {
    const allBlockingSatisfied = item.dependencies
      .filter(d => d.type === "blocks_decision" || d.type === "blocks_action" || d.type === "requires_resolution")
      .every(d => d.satisfied);
    if (allBlockingSatisfied && isActiveAttentionState(item.state)) {
      unblocked.push(item);
    }
  }
  return unblocked;
}

export function findCriticalPath(careRecipientId: string): AttentionItem[] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const activeItems = Array.from(graph.nodes.values()).filter(item => isActiveAttentionState(item.state));
  if (activeItems.length === 0) return [];

  const scored = activeItems.map(item => {
    const dependentCount = getTransitiveDependents(careRecipientId, item.id).length;
    const urgencyScore = getUrgencyScore(item.state);
    const decisionScore = getDecisionUrgencyScore(item.decision_state);
    const blockingScore = item.dependencies.filter(d => !d.satisfied && (d.type === "blocks_decision" || d.type === "blocks_action")).length * 2;
    return { item, score: urgencyScore + decisionScore + dependentCount * 0.5 + blockingScore };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored.map(s => s.item);
}

export function detectCircularDependencies(careRecipientId: string): AttentionItem[][] {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return [];

  const visited = new Set<string>();
  const recStack = new Set<string>();
  const cycles: AttentionItem[][] = [];

  function dfs(nodeId: string, path: string[]): void {
    visited.add(nodeId);
    recStack.add(nodeId);
    path.push(nodeId);

    const edges = graph.edges.get(nodeId) || new Set();
    for (const neighborId of Array.from(edges)) {
      if (!visited.has(neighborId)) {
        dfs(neighborId, [...path]);
      } else if (recStack.has(neighborId)) {
        const cycleStart = path.indexOf(neighborId);
        const cycle = path.slice(cycleStart).concat(neighborId);
        const cycleItems = cycle.map(id => graph.nodes.get(id)).filter((item): item is AttentionItem => item !== undefined);
        if (cycleItems.length > 0) {
          cycles.push(cycleItems);
        }
      }
    }

    recStack.delete(nodeId);
  }

  for (const nodeId of Array.from(graph.nodes.keys())) {
    if (!visited.has(nodeId)) {
      dfs(nodeId, []);
    }
  }

  return cycles;
}

export function propagateStateChange(
  careRecipientId: string,
  changedItemId: string,
  newState: AttentionState,
  newDecisionState: DecisionLifecycleState
): PropagationResult {
  const graph = dependencyGraphs.get(careRecipientId);
  if (!graph) return { affected_items: [], blocked_items: [], unblocked_items: [] };

  const changedItem = graph.nodes.get(changedItemId);
  if (!changedItem) return { affected_items: [], blocked_items: [], unblocked_items: [] };

  const affected: AttentionItem[] = [];
  const newlyBlocked: AttentionItem[] = [];
  const newlyUnblocked: AttentionItem[] = [];

  const dependents = getTransitiveDependents(careRecipientId, changedItemId);
  
  for (const dependent of dependents) {
    const hadBlockingDeps = dependent.dependencies.some(d => 
      !d.satisfied && (d.type === "blocks_decision" || d.type === "blocks_action" || d.type === "requires_resolution")
    );

    const depFromChanged = dependent.dependencies.find(d => d.depends_on_item_id === changedItemId);
    if (depFromChanged) {
      const wasSatisfied = depFromChanged.satisfied;
      const nowSatisfied = isSatisfiedByState(depFromChanged.type, newState, newDecisionState);
      
      if (wasSatisfied !== nowSatisfied) {
        depFromChanged.satisfied = nowSatisfied;
        affected.push(dependent);
        
        const nowHasBlockingDeps = dependent.dependencies.some(d => 
          !d.satisfied && (d.type === "blocks_decision" || d.type === "blocks_action" || d.type === "requires_resolution")
        );
        
        if (!hadBlockingDeps && nowHasBlockingDeps) {
          newlyBlocked.push(dependent);
        } else if (hadBlockingDeps && !nowHasBlockingDeps) {
          newlyUnblocked.push(dependent);
        }
      }
    }
  }

  return { affected_items: affected, blocked_items: newlyBlocked, unblocked_items: newlyUnblocked };
}

export type PropagationResult = {
  affected_items: AttentionItem[];
  blocked_items: AttentionItem[];
  unblocked_items: AttentionItem[];
};

function isSatisfiedByState(dependencyType: DependencyType, state: AttentionState, decisionState: DecisionLifecycleState): boolean {
  switch (dependencyType) {
    case "blocks_decision":
      return decisionState === "DECISION_MADE" || decisionState === "ACTION_PENDING" || 
             decisionState === "ACTION_IN_PROGRESS" || decisionState === "ACTION_COMPLETED" ||
             decisionState === "VERIFICATION_PENDING" || decisionState === "RESOLVED";
    case "blocks_action":
      return decisionState === "ACTION_IN_PROGRESS" || decisionState === "ACTION_COMPLETED" ||
             decisionState === "VERIFICATION_PENDING" || decisionState === "RESOLVED";
    case "requires_verification":
      return decisionState === "VERIFICATION_PENDING" || decisionState === "RESOLVED";
    case "requires_information":
      return state !== "AWAITING_INFORMATION";
    case "requires_resolution":
      return state === "RESOLVED" || state === "CLOSED";
    case "provides_context":
      return state !== "NOT_RELEVANT" && state !== "CLOSED" && state !== "SUPERSEDED";
    default:
      return false;
  }
}

function getUrgencyScore(state: AttentionState): number {
  const scores: Record<AttentionState, number> = {
    EMERGENCY: 10, URGENT: 9, HIGH_PRIORITY: 7, NEEDS_ATTENTION: 5,
    AWAITING_ACTION: 6, IN_PROGRESS: 6, AWAITING_DECISION: 5,
    AWAITING_VERIFICATION: 4, AWAITING_INFORMATION: 3,
    WATCH: 2, BACKGROUND: 1, NOT_RELEVANT: 0, RESOLVED: 0, CLOSED: 0, SUPERSEDED: 0,
  };
  return scores[state] || 0;
}

function getDecisionUrgencyScore(state: DecisionLifecycleState): number {
  const scores: Record<DecisionLifecycleState, number> = {
    DECISION_BLOCKED: 5, AWAITING_CLINICAL_DECISION: 4, AWAITING_HUMAN_DECISION: 4,
    DECISION_NEEDED: 3, ACTION_PENDING: 3, ACTION_IN_PROGRESS: 3,
    VERIFICATION_PENDING: 2, OUTCOME_UNKNOWN: 2, DECISION_MADE: 1,
    ACTION_COMPLETED: 1, RESOLVED: 0, SUPERSEDED: 0, NOT_REQUIRED: 0,
  };
  return scores[state] || 0;
}

import { isActiveAttentionState } from "./types";

export function clearDependencyGraph(careRecipientId: string): void {
  dependencyGraphs.delete(careRecipientId);
}

export function clearAllDependencyGraphs(): void {
  dependencyGraphs.clear();
}