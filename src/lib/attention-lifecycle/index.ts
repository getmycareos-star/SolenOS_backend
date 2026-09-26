/**
 * Attention / Decision Lifecycle - Main Export
 * 
 * Complete implementation of SolenOS attention lifecycle management:
 * - Signal → Candidate → Assessment → State → Decision → Action → Monitor → Resolve → Close
 * - Multidimensional assessment (16+ dimensions)
 * - Escalation/de-escalation with decay and persistence
 * - Decision lifecycle integration
 * - Dependency-aware attention tracking
 * - Full audit trail / history
 * - Benchmark tests for validation
 */

// Types
export * from "./types";

// Assessment Engine
export * from "./assessment";

// Lifecycle Manager
export * from "./manager";
export { lifecycleManager, clearAttentionStore, getAllAttentionItems, getAllHistory } from "./manager";

// Decision Integration
export * from "./decision-integration";
export { decisionLifecycle } from "./decision-integration";

// Dependencies
export * from "./dependencies";
export { 
  getOrCreateDependencyGraph,
  addItemToGraph,
  removeItemFromGraph,
  addDependencyEdge,
  removeDependencyEdge,
  getDependents,
  getDependencies,
  getTransitiveDependents,
  getTransitiveDependencies,
  findBlockedItems,
  findUnblockedItems,
  findCriticalPath,
  detectCircularDependencies,
  propagateStateChange,
  clearDependencyGraph,
  clearAllDependencyGraphs,
} from "./dependencies";

// Audit Trail
export * from "./audit-trail";
export { auditTrail, getAuditTrailStore } from "./audit-trail";

// Benchmarks
export * from "./benchmarks";
export { runAllBenchmarks, printBenchmarkResults, BENCHMARK_CASES } from "./benchmarks";

// Core invariant
export const ATTENTION_LIFECYCLE_CORE_INVARIANT = 
  "SolenOS must manage attention as a stateful lifecycle: detect what matters, calibrate its urgency, connect it to the appropriate decision or action, track ownership and dependencies, recognize progress and resolution, de-escalate when warranted, and reopen only when new evidence justifies it.";

export const ATTENTION_LIFECYCLE_DEEPER_PRINCIPLE = 
  "SolenOS should not merely tell the caregiver what matters. It should know whether that thing is new, active, already handled, waiting on someone, blocked, improving, resolved, or worth reopening. That is the difference between an alert system and a care reasoning system.";

// Default configuration
export { DEFAULT_ATTENTION_LIFECYCLE_CONFIG } from "./types";
export type { AttentionLifecycleConfig } from "./types";