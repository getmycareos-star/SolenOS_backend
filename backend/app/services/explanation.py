from typing import List, Optional, Dict, Any
from app.models.change import Baseline, Change, Pattern, Situation


def explain_change(change: Change, baseline: Optional[Baseline] = None) -> str:
    parts = []
    parts.append(f"{change.subject_type} {change.change_type.value} detected.")
    if baseline:
        parts.append(
            f"Compared to the baseline window "
            f"({baseline.window_start.date()} to {baseline.window_end.date()}), "
            f"the count changed from {change.previous_count} to {change.current_count}."
        )
        if baseline.frequency_per_period is not None:
            parts.append(
                f"Baseline frequency: {baseline.frequency_per_period:.2f} events per month."
            )
    if change.magnitude is not None:
        parts.append(f"Magnitude of change: {change.magnitude:.1f}%.")
    if change.is_persistent is not None:
        parts.append(f"Change persistence: {change.persistence_days} days.")
    if change.confidence < 0.7:
        parts.append(
            f"Confidence in this detection is {change.confidence:.2f} "
            f"due to limited evidence or weak baseline."
        )
    return " ".join(parts)


def explain_pattern(pattern: Pattern) -> str:
    parts = []
    parts.append(f"{pattern.pattern_type.value} pattern detected for {pattern.subject_type}.")
    parts.append(f"Pattern strength: {pattern.pattern_strength.value}.")
    parts.append(f"Temporal range: {pattern.temporal_start.date()} to {pattern.temporal_end.date()}.")
    if pattern.frequency is not None:
        parts.append(f"Frequency: {pattern.frequency:.2f} events per day.")
    if pattern.rate_per_period is not None:
        parts.append(f"Rate per period: {pattern.rate_per_period:.2f}.")
    if pattern.direction:
        parts.append(f"Direction: {pattern.direction.value}.")
    parts.append(f"Evidence count: {pattern.evidence_count}.")
    if pattern.confidence < 0.7:
        parts.append(
            f"Pattern confidence is {pattern.confidence:.2f} due to limited evidence."
        )
    return " ".join(parts)


def explain_situation(situation: Situation, changes: List[Change], patterns: List[Pattern]) -> str:
    parts = []
    parts.append(f"Situation detected: {situation.title}.")
    parts.append(f"Type: {situation.situation_type.value}.")
    parts.append(f"State: {situation.state.value}.")
    parts.append(f"Temporal range: {situation.temporal_start.date()} to {situation.temporal_end.date()}.")
    for pattern in patterns:
        if pattern.id in (situation.pattern_ids or []):
            parts.append(explain_pattern(pattern))
    for change in changes:
        if change.id in (situation.change_ids or []):
            parts.append(explain_change(change))
    if situation.alternative_interpretations:
        parts.append("Alternative interpretations exist.")
        for alt in situation.alternative_interpretations:
            parts.append(f"- {alt.get('interpretation', 'Unknown')}")
    return " ".join(parts)


def explain_situation_reconstruction(situation: Situation) -> Dict[str, Any]:
    return {
        "situation_id": situation.id,
        "included_signals": situation.signal_ids,
        "signal_types": situation.signal_types,
        "temporal_window": {
            "start": situation.temporal_start.isoformat(),
            "end": situation.temporal_end.isoformat(),
        },
        "detected_patterns": situation.pattern_ids,
        "current_state": situation.state.value,
        "confidence": situation.confidence,
        "uncertainty": "alternative_interpretations_exist" if situation.alternative_interpretations else "none",
        "alternative_interpretations": situation.alternative_interpretations,
        "explanation": situation.explanation,
    }
