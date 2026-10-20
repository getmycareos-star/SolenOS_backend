from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid


class AllenRelation(str, Enum):
    """Allen's Interval Algebra relations"""
    BEFORE = "before"
    AFTER = "after"
    MEETS = "meets"
    MET_BY = "met_by"
    OVERLAPS = "overlaps"
    OVERLAPPED_BY = "overlapped_by"
    DURING = "during"
    CONTAINS = "contains"
    STARTS = "starts"
    STARTED_BY = "started_by"
    FINISHES = "finishes"
    FINISHED_BY = "finished_by"
    EQUALS = "equals"
    UNKNOWN = "unknown"


@dataclass
class TemporalInterval:
    """A temporal interval with start and end"""
    start: datetime
    end: Optional[datetime] = None
    is_open_ended: bool = False
    
    def __post_init__(self):
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=timezone.utc)
        if self.end and self.end.tzinfo is None:
            self.end = self.end.replace(tzinfo=timezone.utc)
    
    @property
    def duration(self) -> Optional[timedelta]:
        if self.end:
            return self.end - self.start
        return None


@dataclass
class TemporalPoint:
    """A single point in time"""
    timestamp: datetime
    
    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


@dataclass
class TemporalRange:
    """A range with lower and upper bounds"""
    lower: datetime
    upper: datetime
    
    def __post_init__(self):
        if self.lower.tzinfo is None:
            self.lower = self.lower.replace(tzinfo=timezone.utc)
        if self.upper.tzinfo is None:
            self.upper = self.upper.replace(tzinfo=timezone.utc)


TemporalEntity = TemporalPoint | TemporalInterval | TemporalRange


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def allen_relation(interval_a: TemporalInterval, interval_b: TemporalInterval) -> AllenRelation:
    """
    Compute Allen's interval relation between two intervals.
    Returns the relation of A to B (e.g., if A is before B, returns BEFORE).
    """
    a_start = ensure_utc(interval_a.start)
    a_end = ensure_utc(interval_a.end) if interval_a.end else None
    b_start = ensure_utc(interval_b.start)
    b_end = ensure_utc(interval_b.end) if interval_b.end else None
    
    # Handle open-ended intervals
    if interval_a.is_open_ended and interval_b.is_open_ended:
        if a_start == b_start:
            return AllenRelation.EQUALS
        elif a_start < b_start:
            return AllenRelation.BEFORE
        else:
            return AllenRelation.AFTER
    
    if interval_a.is_open_ended:
        # A has no end
        if a_start == b_start:
            return AllenRelation.STARTED_BY
        elif a_start < b_start:
            if b_end and a_start < b_end:
                return AllenRelation.OVERLAPS
            return AllenRelation.BEFORE
        else:  # a_start > b_start
            if b_end and a_start >= b_end:
                return AllenRelation.AFTER
            return AllenRelation.DURING
    
    if interval_b.is_open_ended:
        # B has no end
        if a_start == b_start:
            return AllenRelation.STARTS
        elif a_start < b_start:
            if a_end and a_end > b_start:
                return AllenRelation.OVERLAPPED_BY
            return AllenRelation.BEFORE
        else:  # a_start > b_start
            return AllenRelation.CONTAINS
    
    # Both have ends
    if a_start == b_start and a_end == b_end:
        return AllenRelation.EQUALS
    
    if a_end and a_end <= b_start:
        if a_end == b_start:
            return AllenRelation.MEETS
        return AllenRelation.BEFORE
    
    if b_end and b_end <= a_start:
        if b_end == a_start:
            return AllenRelation.MET_BY
        return AllenRelation.AFTER
    
    # Overlapping cases
    if a_start < b_start:
        if a_end and a_end < b_end:
            if a_end > b_start:
                return AllenRelation.OVERLAPS
        elif a_end and a_end == b_end:
            return AllenRelation.FINISHES
        elif a_end and a_end > b_end:
            return AllenRelation.CONTAINS
    elif a_start > b_start:
        if a_end and a_end > b_end:
            if a_start < b_end:
                return AllenRelation.OVERLAPPED_BY
        elif a_end and a_end == b_end:
            return AllenRelation.FINISHED_BY
        elif a_end and a_end < b_end:
            return AllenRelation.DURING
    else:  # a_start == b_start
        if a_end and a_end < b_end:
            return AllenRelation.STARTS
        elif a_end and a_end > b_end:
            return AllenRelation.STARTED_BY
    
    return AllenRelation.UNKNOWN


def point_interval_relation(point: TemporalPoint, interval: TemporalInterval) -> AllenRelation:
    """Relation between a point and an interval"""
    p = ensure_utc(point.timestamp)
    start = ensure_utc(interval.start)
    end = ensure_utc(interval.end) if interval.end else None
    
    if interval.is_open_ended:
        if p == start:
            return AllenRelation.STARTS
        elif p > start:
            return AllenRelation.DURING
        else:
            return AllenRelation.BEFORE
    
    if end is None:
        return AllenRelation.UNKNOWN
    
    if p < start:
        return AllenRelation.BEFORE
    elif p > end:
        return AllenRelation.AFTER
    elif p == start:
        return AllenRelation.STARTS
    elif p == end:
        return AllenRelation.FINISHES
    else:
        return AllenRelation.DURING


def interval_point_relation(interval: TemporalInterval, point: TemporalPoint) -> AllenRelation:
    """Relation between an interval and a point (inverse of point_interval_relation)"""
    rel = point_interval_relation(point, interval)
    inverse_map = {
        AllenRelation.BEFORE: AllenRelation.AFTER,
        AllenRelation.AFTER: AllenRelation.BEFORE,
        AllenRelation.STARTS: AllenRelation.CONTAINS,
        AllenRelation.FINISHES: AllenRelation.CONTAINS,
        AllenRelation.DURING: AllenRelation.CONTAINS,
        AllenRelation.CONTAINS: AllenRelation.DURING,
    }
    return inverse_map.get(rel, rel)


def compute_allen_relation(entity_a: TemporalEntity, entity_b: TemporalEntity) -> AllenRelation:
    """Compute Allen relation between any two temporal entities"""
    if isinstance(entity_a, TemporalInterval) and isinstance(entity_b, TemporalInterval):
        return allen_relation(entity_a, entity_b)
    elif isinstance(entity_a, TemporalPoint) and isinstance(entity_b, TemporalInterval):
        return point_interval_relation(entity_a, entity_b)
    elif isinstance(entity_a, TemporalInterval) and isinstance(entity_b, TemporalPoint):
        return interval_point_relation(entity_a, entity_b)
    elif isinstance(entity_a, TemporalPoint) and isinstance(entity_b, TemporalPoint):
        a = ensure_utc(entity_a.timestamp)
        b = ensure_utc(entity_b.timestamp)
        if a == b:
            return AllenRelation.EQUALS
        elif a < b:
            return AllenRelation.BEFORE
        else:
            return AllenRelation.AFTER
    elif isinstance(entity_a, TemporalRange) and isinstance(entity_b, TemporalRange):
        # For ranges, check if they overlap
        a_lower = ensure_utc(entity_a.lower)
        a_upper = ensure_utc(entity_a.upper)
        b_lower = ensure_utc(entity_b.lower)
        b_upper = ensure_utc(entity_b.upper)
        
        if a_upper < b_lower:
            return AllenRelation.BEFORE
        elif b_upper < a_lower:
            return AllenRelation.AFTER
        elif a_lower == b_lower and a_upper == b_upper:
            return AllenRelation.EQUALS
        elif a_lower >= b_lower and a_upper <= b_upper:
            return AllenRelation.DURING
        elif b_lower >= a_lower and b_upper <= a_upper:
            return AllenRelation.CONTAINS
        elif a_lower < b_lower and a_upper >= b_lower and a_upper <= b_upper:
            return AllenRelation.OVERLAPS
        elif b_lower < a_lower and b_upper >= a_lower and b_upper <= a_upper:
            return AllenRelation.OVERLAPPED_BY
        elif a_lower <= b_lower and a_upper >= b_upper:
            return AllenRelation.CONTAINS
        elif b_lower <= a_lower and b_upper >= a_upper:
            return AllenRelation.CONTAINS
        return AllenRelation.UNKNOWN
    
    return AllenRelation.UNKNOWN


def is_before(entity_a: TemporalEntity, entity_b: TemporalEntity) -> bool:
    """Check if A is definitely before B"""
    rel = compute_allen_relation(entity_a, entity_b)
    return rel in (AllenRelation.BEFORE, AllenRelation.MEETS)


def is_after(entity_a: TemporalEntity, entity_b: TemporalEntity) -> bool:
    """Check if A is definitely after B"""
    rel = compute_allen_relation(entity_a, entity_b)
    return rel in (AllenRelation.AFTER, AllenRelation.MET_BY)


def overlaps(entity_a: TemporalEntity, entity_b: TemporalEntity) -> bool:
    """Check if A and B overlap in time"""
    rel = compute_allen_relation(entity_a, entity_b)
    return rel in (
        AllenRelation.OVERLAPS, AllenRelation.OVERLAPPED_BY,
        AllenRelation.DURING, AllenRelation.CONTAINS,
        AllenRelation.STARTS, AllenRelation.STARTED_BY,
        AllenRelation.FINISHES, AllenRelation.FINISHED_BY,
        AllenRelation.EQUALS
    )


def is_during(entity_a: TemporalEntity, entity_b: TemporalEntity) -> bool:
    """Check if A is during B"""
    rel = compute_allen_relation(entity_a, entity_b)
    return rel in (AllenRelation.DURING, AllenRelation.STARTS, AllenRelation.FINISHES, AllenRelation.EQUALS)


def contains(entity_a: TemporalEntity, entity_b: TemporalEntity) -> bool:
    """Check if A contains B"""
    rel = compute_allen_relation(entity_a, entity_b)
    return rel in (AllenRelation.CONTAINS, AllenRelation.STARTED_BY, AllenRelation.FINISHED_BY, AllenRelation.EQUALS)


# Transitive closure for temporal reasoning
def transitive_closure(relations: List[Tuple[str, str, AllenRelation]]) -> List[Tuple[str, str, AllenRelation]]:
    """
    Compute transitive closure of temporal relations.
    relations: list of (entity_a_id, entity_b_id, relation)
    Returns extended list with inferred relations.
    """
    # Build adjacency
    graph: Dict[str, Dict[str, AllenRelation]] = {}
    for a, b, rel in relations:
        if a not in graph:
            graph[a] = {}
        graph[a][b] = rel
    
    # Composition table for Allen relations
    # For simplicity, we'll only compose BEFORE and AFTER transitively
    # Full composition table is complex; this covers common cases
    composition = {
        (AllenRelation.BEFORE, AllenRelation.BEFORE): AllenRelation.BEFORE,
        (AllenRelation.AFTER, AllenRelation.AFTER): AllenRelation.AFTER,
        (AllenRelation.BEFORE, AllenRelation.MEETS): AllenRelation.BEFORE,
        (AllenRelation.MEETS, AllenRelation.BEFORE): AllenRelation.BEFORE,
        (AllenRelation.MEETS, AllenRelation.MEETS): AllenRelation.BEFORE,
        (AllenRelation.AFTER, AllenRelation.MET_BY): AllenRelation.AFTER,
        (AllenRelation.MET_BY, AllenRelation.AFTER): AllenRelation.AFTER,
        (AllenRelation.MET_BY, AllenRelation.MET_BY): AllenRelation.AFTER,
        (AllenRelation.DURING, AllenRelation.DURING): AllenRelation.DURING,
        (AllenRelation.CONTAINS, AllenRelation.CONTAINS): AllenRelation.CONTAINS,
    }
    
    # Floyd-Warshall style transitive closure
    entities = set(graph.keys())
    for targets in graph.values():
        entities.update(targets.keys())
    
    entities = list(entities)
    changed = True
    while changed:
        changed = False
        for a in entities:
            for b in entities:
                for c in entities:
                    if a in graph and b in graph[a] and b in graph and c in graph[b]:
                        rel_ab = graph[a][b]
                        rel_bc = graph[b][c]
                        composed = composition.get((rel_ab, rel_bc))
                        if composed and (c not in graph[a] or graph[a][c] == AllenRelation.UNKNOWN):
                            graph.setdefault(a, {})[c] = composed
                            changed = True
    
    # Convert back to list
    result = []
    for a, targets in graph.items():
        for b, rel in targets.items():
            result.append((a, b, rel))
    return result


def detect_temporal_cycles(relations: List[Tuple[str, str, AllenRelation]]) -> List[List[str]]:
    """Detect cycles in temporal relations (e.g., A before B before C before A)"""
    graph: Dict[str, List[str]] = {}
    for a, b, rel in relations:
        if rel in (AllenRelation.BEFORE, AllenRelation.MEETS):
            graph.setdefault(a, []).append(b)
    
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
                # Found cycle
                idx = path.index(neighbor)
                cycles.append(path[idx:] + [neighbor])
        
        rec_stack.remove(node)
        path.pop()
    
    for node in graph:
        if node not in visited:
            dfs(node)
    
    return cycles


# Export key functions
__all__ = [
    "AllenRelation",
    "TemporalInterval",
    "TemporalPoint", 
    "TemporalRange",
    "TemporalEntity",
    "ensure_utc",
    "allen_relation",
    "point_interval_relation",
    "interval_point_relation",
    "compute_allen_relation",
    "is_before",
    "is_after",
    "overlaps",
    "is_during",
    "contains",
    "transitive_closure",
    "detect_temporal_cycles",
]