from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import uuid


class ConstraintType(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    NOT_DURING = "not_during"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    WITHIN = "within"
    OUTSIDE = "outside"
    DURATION_MIN = "duration_min"
    DURATION_MAX = "duration_max"
    RECURRENCE = "recurrence"
    EFFECTIVE_FROM = "effective_from"
    EFFECTIVE_UNTIL = "effective_until"
    VALID_FROM = "valid_from"
    VALID_TO = "valid_to"


class DependencyType(str, Enum):
    TEMPORAL = "temporal"
    CAUSAL = "causal"
    LOGICAL = "logical"
    EVIDENTIARY = "evidentiary"
    SUPERSESSION = "supersession"


class ConstraintStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"
    PENDING = "pending"


@dataclass
class TemporalNode:
    """A node in the temporal constraint graph"""
    node_id: str
    entity_type: str  # event, state, fact, etc.
    entity_id: str
    temporal_bounds: Dict[str, Any]  # Can represent point, interval, range
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __hash__(self):
        return hash(self.node_id)
    
    def __eq__(self, other):
        return isinstance(other, TemporalNode) and self.node_id == other.node_id


@dataclass
class TemporalConstraint:
    """A constraint between two temporal nodes"""
    constraint_id: str
    source_node: str  # node_id
    target_node: str  # node_id
    constraint_type: ConstraintType
    parameters: Dict[str, Any] = field(default_factory=dict)  # e.g., {"days": 7} for WITHIN
    status: ConstraintStatus = ConstraintStatus.UNKNOWN
    confidence: float = 1.0
    evidence_ids: List[str] = field(default_factory=list)
    derived: bool = False  # True if inferred, False if explicit
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TemporalDependency:
    """A dependency between temporal conclusions"""
    dependency_id: str
    source_conclusion: str  # conclusion_id
    dependent_conclusion: str  # conclusion_id
    dependency_type: DependencyType
    description: str
    strength: float = 1.0  # 0-1, how strongly dependent


@dataclass
class TemporalConclusion:
    """A temporal conclusion (derived or asserted)"""
    conclusion_id: str
    node_id: str
    conclusion_type: str  # "time_assignment", "relation", "duration", etc.
    value: Any
    confidence: float
    derivation: str  # "explicit", "inferred", "derived"
    derivation_chain: List[str] = field(default_factory=list)  # conclusion_ids that support this
    evidence_ids: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "active"  # active, invalidated, superseded
    
    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


class TemporalConstraintGraph:
    """
    A graph of temporal nodes, constraints, and derived conclusions.
    Supports constraint propagation, conflict detection, and dependency tracking.
    """
    
    def __init__(self):
        self.nodes: Dict[str, TemporalNode] = {}
        self.constraints: Dict[str, TemporalConstraint] = {}
        self.conclusions: Dict[str, TemporalConclusion] = {}
        self.dependencies: Dict[str, TemporalDependency] = {}
        self.adjacency: Dict[str, Set[str]] = defaultdict(set)  # node_id -> set of connected node_ids
        self.reverse_adjacency: Dict[str, Set[str]] = defaultdict(set)
    
    def add_node(self, node: TemporalNode) -> None:
        self.nodes[node.node_id] = node
    
    def add_constraint(self, constraint: TemporalConstraint) -> None:
        self.constraints[constraint.constraint_id] = constraint
        self.adjacency[constraint.source_node].add(constraint.target_node)
        self.reverse_adjacency[constraint.target_node].add(constraint.source_node)
    
    def add_conclusion(self, conclusion: TemporalConclusion) -> None:
        self.conclusions[conclusion.conclusion_id] = conclusion
    
    def add_dependency(self, dependency: TemporalDependency) -> None:
        self.dependencies[dependency.dependency_id] = dependency
    
    def get_connected_nodes(self, node_id: str, direction: str = "both") -> Set[str]:
        """Get nodes connected to a given node"""
        if direction == "forward":
            return self.adjacency.get(node_id, set())
        elif direction == "backward":
            return self.reverse_adjacency.get(node_id, set())
        else:
            return self.adjacency.get(node_id, set()) | self.reverse_adjacency.get(node_id, set())
    
    def get_constraints_for_node(self, node_id: str) -> List[TemporalConstraint]:
        """Get all constraints involving a node"""
        result = []
        for c in self.constraints.values():
            if c.source_node == node_id or c.target_node == node_id:
                result.append(c)
        return result
    
    def get_conclusions_for_node(self, node_id: str) -> List[TemporalConclusion]:
        """Get all conclusions about a node"""
        return [c for c in self.conclusions.values() if c.node_id == node_id]
    
    def check_constraint_satisfaction(
        self,
        constraint: TemporalConstraint,
        node_times: Dict[str, datetime]
    ) -> Tuple[ConstraintStatus, str]:
        """
        Check if a constraint is satisfied given node times.
        node_times: mapping from node_id to assigned time
        """
        source_time = node_times.get(constraint.source_node)
        target_time = node_times.get(constraint.target_node)
        
        if source_time is None or target_time is None:
            return ConstraintStatus.UNKNOWN, "Missing time assignment for one or both nodes"
        
        params = constraint.parameters
        
        if constraint.constraint_type == ConstraintType.BEFORE:
            if source_time < target_time:
                return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"{constraint.source_node} not before {constraint.target_node}"
        
        elif constraint.constraint_type == ConstraintType.AFTER:
            if source_time > target_time:
                return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"{constraint.source_node} not after {constraint.target_node}"
        
        elif constraint.constraint_type == ConstraintType.EQUALS:
            if source_time == target_time:
                return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"Times not equal"
        
        elif constraint.constraint_type == ConstraintType.WITHIN:
            max_diff = params.get("max_days", 7)
            diff = abs((source_time - target_time).total_seconds()) / 86400
            if diff <= max_diff:
                return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"Difference {diff:.1f} days exceeds {max_diff}"
        
        elif constraint.constraint_type == ConstraintType.DURATION_MIN:
            min_duration = params.get("min_days", 1)
            # Assumes source is start, target is end
            if target_time > source_time:
                duration = (target_time - source_time).total_seconds() / 86400
                if duration >= min_duration:
                    return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"Duration less than {min_duration} days"
        
        elif constraint.constraint_type == ConstraintType.DURATION_MAX:
            max_duration = params.get("max_days", 30)
            if target_time > source_time:
                duration = (target_time - source_time).total_seconds() / 86400
                if duration <= max_duration:
                    return ConstraintStatus.SATISFIED, ""
            return ConstraintStatus.VIOLATED, f"Duration exceeds {max_duration} days"
        
        return ConstraintStatus.UNKNOWN, f"Unknown constraint type: {constraint.constraint_type}"
    
    def validate_all_constraints(
        self,
        node_times: Dict[str, datetime]
    ) -> Dict[str, Any]:
        """Validate all constraints against current time assignments"""
        results = {
            "satisfied": [],
            "violated": [],
            "unknown": [],
            "overall_valid": True,
        }
        
        for constraint in self.constraints.values():
            status, message = self.check_constraint_satisfaction(constraint, node_times)
            constraint.status = status
            
            if status == ConstraintStatus.SATISFIED:
                results["satisfied"].append({
                    "constraint_id": constraint.constraint_id,
                    "type": constraint.constraint_type.value,
                    "source": constraint.source_node,
                    "target": constraint.target_node,
                })
            elif status == ConstraintStatus.VIOLATED:
                results["violated"].append({
                    "constraint_id": constraint.constraint_id,
                    "type": constraint.constraint_type.value,
                    "source": constraint.source_node,
                    "target": constraint.target_node,
                    "message": message,
                })
                results["overall_valid"] = False
            else:
                results["unknown"].append({
                    "constraint_id": constraint.constraint_id,
                    "type": constraint.constraint_type.value,
                    "message": message,
                })
        
        return results
    
    def propagate_constraints(
        self,
        node_times: Dict[str, datetime],
        max_iterations: int = 10
    ) -> Dict[str, datetime]:
        """
        Propagate temporal constraints to infer missing times.
        Returns updated node_times with inferred values.
        """
        updated = dict(node_times)
        
        for _ in range(max_iterations):
            changed = False
            
            for constraint in self.constraints.values():
                source_time = updated.get(constraint.source_node)
                target_time = updated.get(constraint.target_node)
                
                # Try to infer missing times
                if source_time is not None and target_time is None:
                    inferred = self._infer_time(source_time, constraint, "target")
                    if inferred:
                        updated[constraint.target_node] = inferred
                        changed = True
                elif target_time is not None and source_time is None:
                    inferred = self._infer_time(target_time, constraint, "source")
                    if inferred:
                        updated[constraint.source_node] = inferred
                        changed = True
            
            if not changed:
                break
        
        return updated
    
    def _infer_time(
        self,
        known_time: datetime,
        constraint: TemporalConstraint,
        missing_side: str
    ) -> Optional[datetime]:
        """Infer a missing time from a constraint"""
        params = constraint.parameters
        
        if constraint.constraint_type == ConstraintType.BEFORE:
            if missing_side == "target":
                return known_time + timedelta(days=params.get("min_gap_days", 1))
            else:  # missing source
                return known_time - timedelta(days=params.get("min_gap_days", 1))
        
        elif constraint.constraint_type == ConstraintType.AFTER:
            if missing_side == "target":
                return known_time - timedelta(days=params.get("min_gap_days", 1))
            else:
                return known_time + timedelta(days=params.get("min_gap_days", 1))
        
        elif constraint.constraint_type == ConstraintType.WITHIN:
            max_diff = params.get("max_days", 7)
            if missing_side == "target":
                return known_time + timedelta(days=max_diff / 2)
            else:
                return known_time - timedelta(days=max_diff / 2)
        
        elif constraint.constraint_type == ConstraintType.DURATION_MIN:
            min_dur = params.get("min_days", 1)
            if missing_side == "target":  # target is end
                return known_time + timedelta(days=min_dur)
            else:  # source is start
                return known_time - timedelta(days=min_dur)
        
        elif constraint.constraint_type == ConstraintType.DURATION_MAX:
            max_dur = params.get("max_days", 30)
            if missing_side == "target":
                return known_time + timedelta(days=max_dur)
            else:
                return known_time - timedelta(days=max_dur)
        
        return None
    
    def find_contradiction_cycles(self) -> List[List[str]]:
        """
        Find cycles of BEFORE/AFTER constraints that create contradictions.
        """
        # Build directed graph from BEFORE/AFTER constraints
        graph: Dict[str, List[str]] = defaultdict(list)
        for c in self.constraints.values():
            if c.constraint_type == ConstraintType.BEFORE:
                graph[c.source_node].append(c.target_node)
            elif c.constraint_type == ConstraintType.AFTER:
                graph[c.target_node].append(c.source_node)
        
        # Find cycles using DFS
        cycles = []
        visited = set()
        rec_stack = set()
        path = []
        
        def dfs(node: str):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    idx = path.index(neighbor)
                    cycles.append(path[idx:] + [neighbor])
            
            rec_stack.remove(node)
            path.pop()
        
        for node in self.nodes:
            if node not in visited:
                dfs(node)
        
        return cycles
    
    def get_dependent_conclusions(
        self,
        conclusion_id: str
    ) -> List[TemporalConclusion]:
        """Get all conclusions that depend on a given conclusion"""
        dependent_ids = set()
        for dep in self.dependencies.values():
            if dep.source_conclusion == conclusion_id:
                dependent_ids.add(dep.dependent_conclusion)
        
        return [self.conclusions[cid] for cid in dependent_ids if cid in self.conclusions]
    
    def invalidate_conclusion(
        self,
        conclusion_id: str,
        reason: str
    ) -> List[str]:
        """
        Invalidate a conclusion and cascade to dependent conclusions.
        Returns list of invalidated conclusion IDs.
        """
        invalidated = []
        to_process = [conclusion_id]
        
        while to_process:
            cid = to_process.pop()
            if cid in self.conclusions and self.conclusions[cid].status == "active":
                self.conclusions[cid].status = "invalidated"
                self.conclusions[cid].metadata["invalidation_reason"] = reason
                self.conclusions[cid].metadata["invalidated_at"] = ensure_utc(datetime.now()).isoformat()
                invalidated.append(cid)
                
                # Add dependents to process
                for dep in self.dependencies.values():
                    if dep.source_conclusion == cid:
                        to_process.append(dep.dependent_conclusion)
        
        return invalidated
    
    def recompute_from_evidence(
        self,
        evidence_node_ids: List[str],
        recompute_fn
    ) -> List[str]:
        """
        Recompute conclusions that depend on specific evidence nodes.
        recompute_fn: function(node_id, current_conclusions) -> new_conclusion
        """
        affected_conclusions = []
        
        # Find conclusions directly based on these evidence nodes
        for conclusion in self.conclusions.values():
            if any(eid in evidence_node_ids for eid in conclusion.evidence_ids):
                affected_conclusions.append(conclusion.conclusion_id)
        
        # Also find transitively dependent conclusions
        all_affected = set(affected_conclusions)
        queue = list(affected_conclusions)
        
        while queue:
            cid = queue.pop(0)
            for dep in self.dependencies.values():
                if dep.source_conclusion == cid and dep.dependent_conclusion not in all_affected:
                    all_affected.add(dep.dependent_conclusion)
                    queue.append(dep.dependent_conclusion)
        
        # Invalidate and recompute
        recomputed = []
        for cid in all_affected:
            self.invalidate_conclusion(cid, "evidence_changed")
            # In practice, would call recompute_fn here
            recomputed.append(cid)
        
        return recomputed


def build_constraint_graph_from_events(
    events: List[Dict[str, Any]],
    person_id: str
) -> TemporalConstraintGraph:
    """
    Build a temporal constraint graph from a list of events.
    events: list of dicts with keys: id, type, occurred_at, evidence_ids, etc.
    """
    graph = TemporalConstraintGraph()
    
    # Create nodes for each event
    for event in events:
        node = TemporalNode(
            node_id=event["id"],
            entity_type="event",
            entity_id=event["id"],
            temporal_bounds={
                "type": "point",
                "time": event.get("occurred_at"),
            },
            label=f"{event.get('type', 'event')}: {event.get('title', '')[:50]}",
            metadata=event,
        )
        graph.add_node(node)
    
    # Add temporal ordering constraints (chronological)
    sorted_events = sorted(events, key=lambda e: e.get("occurred_at", ""))
    for i in range(len(sorted_events) - 1):
        constraint = TemporalConstraint(
            constraint_id=f"c_{uuid.uuid4().hex[:8]}",
            source_node=sorted_events[i]["id"],
            target_node=sorted_events[i+1]["id"],
            constraint_type=ConstraintType.BEFORE,
            parameters={"min_gap_days": 0},
            derived=True,
            evidence_ids=sorted_events[i].get("evidence_ids", []) + sorted_events[i+1].get("evidence_ids", []),
        )
        graph.add_constraint(constraint)
    
    # Add domain-specific constraints
    _add_domain_constraints(graph, events)
    
    return graph


def _add_domain_constraints(
    graph: TemporalConstraintGraph,
    events: List[Dict[str, Any]]
) -> None:
    """Add domain-specific temporal constraints"""
    # Group by type
    by_type: Dict[str, List[Dict]] = defaultdict(list)
    for e in events:
        by_type[e.get("type", "unknown")].append(e)
    
    # Medication: start before end
    for med in by_type.get("medication", []):
        if med.get("started_at") and med.get("stopped_at"):
            constraint = TemporalConstraint(
                constraint_id=f"c_{uuid.uuid4().hex[:8]}",
                source_node=med["id"] + "_start",
                target_node=med["id"] + "_end",
                constraint_type=ConstraintType.BEFORE,
                derived=True,
            )
            graph.add_constraint(constraint)
    
    # Hospitalization: admission before discharge
    for hosp in by_type.get("hospitalization", []):
        if hosp.get("admitted_at") and hosp.get("discharged_at"):
            constraint = TemporalConstraint(
                constraint_id=f"c_{uuid.uuid4().hex[:8]}",
                source_node=hosp["id"] + "_admission",
                target_node=hosp["id"] + "_discharge",
                constraint_type=ConstraintType.BEFORE,
                derived=True,
            )
            graph.add_constraint(constraint)
    
    # Symptom during medication
    for symptom in by_type.get("symptom", []):
        for med in by_type.get("medication", []):
            if _overlaps(symptom, med):
                constraint = TemporalConstraint(
                    constraint_id=f"c_{uuid.uuid4().hex[:8]}",
                    source_node=symptom["id"],
                    target_node=med["id"],
                    constraint_type=ConstraintType.DURING,
                    derived=True,
                )
                graph.add_constraint(constraint)


def _overlaps(event_a: Dict, event_b: Dict) -> bool:
    """Check if two events overlap in time"""
    a_start = event_a.get("occurred_at") or event_a.get("started_at")
    a_end = event_a.get("ended_at") or event_a.get("stopped_at") or a_start
    b_start = event_b.get("occurred_at") or event_b.get("started_at")
    b_end = event_b.get("ended_at") or event_b.get("stopped_at") or b_start
    
    if not all([a_start, a_end, b_start, b_end]):
        return False
    
    return a_start <= b_end and b_start <= a_end


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# Export
__all__ = [
    "ConstraintType",
    "DependencyType",
    "ConstraintStatus",
    "TemporalNode",
    "TemporalConstraint",
    "TemporalDependency",
    "TemporalConclusion",
    "TemporalConstraintGraph",
    "ensure_utc",
    "build_constraint_graph_from_events",
]