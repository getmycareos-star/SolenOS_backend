from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import copy


class ChangeType(str, Enum):
    NEW = "new"
    REMOVED = "removed"
    INCREASED = "increased"
    DECREASED = "decreased"
    PERSISTING = "persisting"
    RESOLVED = "resolved"
    RECURRED = "recurred"
    REAPPEARED = "reappeared"
    WORSENED = "worsened"
    IMPROVED = "improved"
    FLUCTUATING = "fluctuating"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


class ChangeSignificance(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INFORMATIONAL = "informational"


@dataclass
class TemporalState:
    """Represents a care state at a point in time"""
    entity_id: str
    entity_type: str  # symptom, condition, medication, function, etc.
    state_value: str  # e.g., "independent", "needs_assistance", "present", "absent"
    severity: Optional[str] = None  # mild, moderate, severe
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0
    evidence: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)


@dataclass
class StateChange:
    """Represents a change between two states"""
    change_id: str
    entity_id: str
    entity_type: str
    change_type: ChangeType
    significance: ChangeSignificance
    from_state: Optional[TemporalState]
    to_state: Optional[TemporalState]
    change_timestamp: datetime
    confidence: float
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    related_changes: List[str] = field(default_factory=list)  # IDs of related changes
    
    def __post_init__(self):
        if self.change_timestamp.tzinfo is None:
            self.change_timestamp = self.change_timestamp.replace(tzinfo=timezone.utc)


@dataclass
class BaselineComparison:
    """Comparison of current state vs baseline"""
    entity_id: str
    entity_type: str
    baseline_state: TemporalState
    current_state: TemporalState
    change: StateChange
    time_since_baseline_days: float


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compare_states(
    from_state: Optional[TemporalState],
    to_state: Optional[TemporalState],
    change_time: Optional[datetime] = None,
    significance_rules: Optional[Dict[str, Any]] = None
) -> StateChange:
    """
    Compare two temporal states and determine the change type and significance.
    """
    change_time = change_time or ensure_utc(datetime.now())
    significance_rules = significance_rules or _default_significance_rules()
    
    # Determine change type
    if from_state is None and to_state is not None:
        change_type = ChangeType.NEW
    elif from_state is not None and to_state is None:
        change_type = ChangeType.REMOVED
    elif from_state is not None and to_state is not None:
        if from_state.state_value == to_state.state_value:
            # Check severity change
            if from_state.severity != to_state.severity:
                severity_order = {"mild": 1, "moderate": 2, "severe": 3}
                from_sev = severity_order.get(from_state.severity or "", 0)
                to_sev = severity_order.get(to_state.severity or "", 0)
                if to_sev > from_sev:
                    change_type = ChangeType.WORSENED
                elif to_sev < from_sev:
                    change_type = ChangeType.IMPROVED
                else:
                    change_type = ChangeType.UNCHANGED
            else:
                change_type = ChangeType.UNCHANGED
        else:
            # State value changed
            # Domain-specific logic for common transitions
            change_type = _determine_state_transition(
                from_state.state_value, to_state.state_value, from_state.entity_type
            )
    else:
        change_type = ChangeType.UNKNOWN
    
    # Determine significance
    significance = _calculate_significance(
        change_type, from_state, to_state, significance_rules
    )
    
    # Build evidence
    evidence = []
    if from_state:
        evidence.extend(from_state.evidence)
    if to_state:
        evidence.extend(to_state.evidence)
    
    # Confidence
    confidences = []
    if from_state:
        confidences.append(from_state.confidence)
    if to_state:
        confidences.append(to_state.confidence)
    confidence = min(confidences) if confidences else 0.5
    
    return StateChange(
        change_id=f"chg_{uuid.uuid4().hex[:8]}",
        entity_id=from_state.entity_id if from_state else (to_state.entity_id if to_state else "unknown"),
        entity_type=from_state.entity_type if from_state else (to_state.entity_type if to_state else "unknown"),
        change_type=change_type,
        significance=significance,
        from_state=from_state,
        to_state=to_state,
        change_timestamp=change_time,
        confidence=confidence,
        evidence=evidence,
        metadata={
            "from_value": from_state.state_value if from_state else None,
            "to_value": to_state.state_value if to_state else None,
            "from_severity": from_state.severity if from_state else None,
            "to_severity": to_state.severity if to_state else None,
        }
    )


def _determine_state_transition(
    from_value: str,
    to_value: str,
    entity_type: str
) -> ChangeType:
    """Determine change type based on state transition"""
    from_lower = from_value.lower()
    to_lower = to_value.lower()
    
    # Functional status transitions
    functional_order = {
        "independent": 4,
        "supervision": 3,
        "limited_assistance": 2,
        "extensive_assistance": 1,
        "total_dependence": 0,
        "needs_cane": 3,
        "needs_walker": 2,
        "needs_wheelchair": 1,
        "bedbound": 0,
    }
    
    if entity_type in ["mobility", "function", "adl", "iadl"]:
        from_level = functional_order.get(from_lower, -1)
        to_level = functional_order.get(to_lower, -1)
        if from_level >= 0 and to_level >= 0:
            if to_level < from_level:
                return ChangeType.WORSENED
            elif to_level > from_level:
                return ChangeType.IMPROVED
    
    # Presence/absence transitions
    presence_states = {"present", "absent", "yes", "no", "active", "resolved", "ongoing"}
    if from_lower in presence_states and to_lower in presence_states:
        if from_lower in ["absent", "no", "resolved"] and to_lower in ["present", "yes", "active", "ongoing"]:
            return ChangeType.NEW
        elif from_lower in ["present", "yes", "active", "ongoing"] and to_lower in ["absent", "no", "resolved"]:
            return ChangeType.RESOLVED
    
    # Recurrence
    if from_lower in ["resolved", "absent", "no"] and to_lower in ["present", "yes", "active"]:
        # Check if this entity had previous occurrences
        return ChangeType.RECURRED
    
    return ChangeType.WORSENED  # Default for unrecognized transitions


def _calculate_significance(
    change_type: ChangeType,
    from_state: Optional[TemporalState],
    to_state: Optional[TemporalState],
    rules: Dict[str, Any]
) -> ChangeSignificance:
    """Calculate significance of a change"""
    # Critical changes
    if change_type in [ChangeType.NEW, ChangeType.REMOVED]:
        if from_state and from_state.entity_type in ["diagnosis", "condition", "medication"]:
            return ChangeSignificance.HIGH
        return ChangeSignificance.MODERATE
    
    if change_type == ChangeType.WORSENED:
        if to_state and to_state.severity == "severe":
            return ChangeSignificance.CRITICAL
        if from_state and from_state.entity_type in ["falls", "diagnosis", "hospitalization"]:
            return ChangeSignificance.HIGH
        return ChangeSignificance.MODERATE
    
    if change_type == ChangeType.IMPROVED:
        if to_state and to_state.severity == "mild":
            return ChangeSignificance.MODERATE
        return ChangeSignificance.LOW
    
    if change_type == ChangeType.RECURRED:
        if from_state and from_state.entity_type in ["falls", "hospitalization", "infection"]:
            return ChangeSignificance.HIGH
        return ChangeSignificance.MODERATE
    
    if change_type == ChangeType.RESOLVED:
        return ChangeSignificance.LOW
    
    return ChangeSignificance.INFORMATIONAL


def _default_significance_rules() -> Dict[str, Any]:
    return {
        "critical_entities": ["falls", "hospitalization", "diagnosis", "medication_error", "allergy"],
        "high_significance_entities": ["mobility", "cognition", "nutrition", "skin_integrity"],
        "severity_weights": {"mild": 1, "moderate": 2, "severe": 3},
    }


def detect_changes_over_time(
    states: List[TemporalState],
    entity_type: Optional[str] = None,
    window_days: int = 30
) -> List[StateChange]:
    """
    Detect all changes in a timeline of states.
    """
    # Filter by entity type if specified
    if entity_type:
        states = [s for s in states if s.entity_type == entity_type]
    
    # Group by entity
    by_entity: Dict[str, List[TemporalState]] = defaultdict(list)
    for state in states:
        by_entity[state.entity_id].append(state)
    
    # Sort each entity's states by time
    for entity_id in by_entity:
        by_entity[entity_id].sort(key=lambda s: s.timestamp)
    
    changes = []
    
    # Compare consecutive states for each entity
    for entity_id, entity_states in by_entity.items():
        for i in range(1, len(entity_states)):
            # Only compare if within window
            gap = (entity_states[i].timestamp - entity_states[i-1].timestamp).total_seconds() / 86400
            if gap <= window_days:
                change = compare_states(
                    entity_states[i-1],
                    entity_states[i],
                    change_time=entity_states[i].timestamp
                )
                changes.append(change)
    
    # Sort by time
    changes.sort(key=lambda c: c.change_timestamp)
    
    return changes


def compare_baseline_to_current(
    baseline_states: List[TemporalState],
    current_states: List[TemporalState],
    max_baseline_age_days: int = 365
) -> List[BaselineComparison]:
    """
    Compare current states to baseline (historical) states.
    """
    now = ensure_utc(datetime.now())
    
    # Filter baseline to reasonable age
    valid_baseline = [
        s for s in baseline_states
        if (now - s.timestamp).total_seconds() / 86400 <= max_baseline_age_days
    ]
    
    # Group by entity
    baseline_by_entity: Dict[str, TemporalState] = {}
    for state in valid_baseline:
        # Use most recent baseline per entity
        if state.entity_id not in baseline_by_entity or state.timestamp > baseline_by_entity[state.entity_id].timestamp:
            baseline_by_entity[state.entity_id] = state
    
    current_by_entity: Dict[str, TemporalState] = {}
    for state in current_states:
        if state.entity_id not in current_by_entity or state.timestamp > current_by_entity[state.entity_id].timestamp:
            current_by_entity[state.entity_id] = state
    
    comparisons = []
    
    # Compare each entity that has both baseline and current
    all_entities = set(baseline_by_entity.keys()) | set(current_by_entity.keys())
    
    for entity_id in all_entities:
        baseline = baseline_by_entity.get(entity_id)
        current = current_by_entity.get(entity_id)
        
        if baseline and current:
            change = compare_states(baseline, current, change_time=current.timestamp)
            time_since = (current.timestamp - baseline.timestamp).total_seconds() / 86400
            
            comparisons.append(BaselineComparison(
                entity_id=entity_id,
                entity_type=current.entity_type,
                baseline_state=baseline,
                current_state=current,
                change=change,
                time_since_baseline_days=time_since
            ))
        elif baseline and not current:
            # State removed
            change = compare_states(baseline, None, change_time=now)
            comparisons.append(BaselineComparison(
                entity_id=entity_id,
                entity_type=baseline.entity_type,
                baseline_state=baseline,
                current_state=None,
                change=change,
                time_since_baseline_days=(now - baseline.timestamp).total_seconds() / 86400
            ))
        elif current and not baseline:
            # New state
            change = compare_states(None, current, change_time=current.timestamp)
            comparisons.append(BaselineComparison(
                entity_id=entity_id,
                entity_type=current.entity_type,
                baseline_state=None,
                current_state=current,
                change=change,
                time_since_baseline_days=0
            ))
    
    # Sort by significance (critical first) then by time
    significance_order = {
        ChangeSignificance.CRITICAL: 0,
        ChangeSignificance.HIGH: 1,
        ChangeSignificance.MODERATE: 2,
        ChangeSignificance.LOW: 3,
        ChangeSignificance.INFORMATIONAL: 4,
    }
    
    comparisons.sort(key=lambda c: (significance_order.get(c.change.significance, 5), -c.time_since_baseline_days))
    
    return comparisons


def detect_pre_post_changes(
    states: List[TemporalState],
    intervention_time: datetime,
    entity_type: Optional[str] = None,
    pre_window_days: int = 30,
    post_window_days: int = 30
) -> List[BaselineComparison]:
    """
    Compare states before and after an intervention.
    """
    intervention_time = ensure_utc(intervention_time)
    pre_cutoff = intervention_time - timedelta(days=pre_window_days)
    post_cutoff = intervention_time + timedelta(days=post_window_days)
    
    pre_states = [s for s in states if pre_cutoff <= s.timestamp < intervention_time]
    post_states = [s for s in states if intervention_time <= s.timestamp <= post_cutoff]
    
    if entity_type:
        pre_states = [s for s in pre_states if s.entity_type == entity_type]
        post_states = [s for s in post_states if s.entity_type == entity_type]
    
    return compare_baseline_to_current(pre_states, post_states)


def detect_state_transitions(
    states: List[TemporalState],
    entity_id: str,
    entity_type: str,
    valid_transitions: Optional[Dict[str, List[str]]] = None
) -> List[StateChange]:
    """
    Detect valid/invalid state transitions for a specific entity.
    """
    entity_states = [s for s in states if s.entity_id == entity_id and s.entity_type == entity_type]
    entity_states.sort(key=lambda s: s.timestamp)
    
    if len(entity_states) < 2:
        return []
    
    changes = []
    for i in range(1, len(entity_states)):
        change = compare_states(entity_states[i-1], entity_states[i])
        
        # Validate transition if rules provided
        if valid_transitions:
            from_val = entity_states[i-1].state_value
            to_val = entity_states[i].state_value
            valid_next = valid_transitions.get(from_val, [])
            if valid_next and to_val not in valid_next:
                change.metadata["transition_valid"] = False
                change.metadata["valid_transitions"] = valid_next
                change.significance = ChangeSignificance.HIGH
            else:
                change.metadata["transition_valid"] = True
        
        changes.append(change)
    
    return changes


def summarize_changes(
    changes: List[StateChange],
    group_by: str = "entity_type"
) -> ChangeSummary:
    """
    Summarize a list of changes for reporting.
    """
    if not changes:
        return ChangeSummary(
            total_changes=0,
            by_type={},
            by_significance={},
            by_entity_type={},
            critical_changes=[],
            new_conditions=[],
            resolved_conditions=[],
            worsened_conditions=[],
            improved_conditions=[],
        )
    
    summary = {
        "total_changes": len(changes),
        "by_type": defaultdict(int),
        "by_significance": defaultdict(int),
        "by_entity_type": defaultdict(int),
        "critical_changes": [],
        "new_conditions": [],
        "resolved_conditions": [],
        "worsened_conditions": [],
        "improved_conditions": [],
    }
    
    for change in changes:
        summary["by_type"][change.change_type.value] += 1
        summary["by_significance"][change.significance.value] += 1
        summary["by_entity_type"][change.entity_type] += 1
        
        if change.significance == ChangeSignificance.CRITICAL:
            summary["critical_changes"].append({
                "entity_id": change.entity_id,
                "entity_type": change.entity_type,
                "from": change.from_state.state_value if change.from_state else None,
                "to": change.to_state.state_value if change.to_state else None,
                "time": change.change_timestamp.isoformat(),
            })
        
        if change.change_type == ChangeType.NEW:
            summary["new_conditions"].append(change.entity_id)
        elif change.change_type == ChangeType.RESOLVED:
            summary["resolved_conditions"].append(change.entity_id)
        elif change.change_type == ChangeType.WORSENED:
            summary["worsened_conditions"].append(change.entity_id)
        elif change.change_type == ChangeType.IMPROVED:
            summary["improved_conditions"].append(change.entity_id)
    
    # Convert defaultdicts to regular dicts
    for key in ["by_type", "by_significance", "by_entity_type"]:
        summary[key] = dict(summary[key])
    
    return ChangeSummary(**summary)


@dataclass
class ChangeSummary:
    """Structured summary of changes for reporting"""
    total_changes: int
    by_type: Dict[str, int]
    by_significance: Dict[str, int]
    by_entity_type: Dict[str, int]
    critical_changes: List[Dict[str, Any]]
    new_conditions: List[str]
    resolved_conditions: List[str]
    worsened_conditions: List[str]
    improved_conditions: List[str]


# Export
__all__ = [
    "ChangeType",
    "ChangeSignificance",
    "TemporalState",
    "StateChange",
    "BaselineComparison",
    "ChangeSummary",
    "ensure_utc",
    "compare_states",
    "detect_changes_over_time",
    "compare_baseline_to_current",
    "detect_pre_post_changes",
    "detect_state_transitions",
    "summarize_changes",
]


import uuid