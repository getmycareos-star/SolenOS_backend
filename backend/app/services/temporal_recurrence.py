from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum
import math


class RecurrenceType(str, Enum):
    EXPLICIT_STATEMENT = "explicit_statement"
    OBSERVED_PATTERN = "observed_pattern"
    INFERRED_PATTERN = "inferred_pattern"


class RecurrenceFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"
    EPISODIC = "episodic"
    CLUSTERED = "clustered"
    UNKNOWN = "unknown"


@dataclass
class RecurrencePattern:
    """Detected recurrence pattern"""
    pattern_type: RecurrenceType
    frequency: RecurrenceFrequency
    custom_interval_days: Optional[float] = None
    event_type: str = ""
    occurrences: List[datetime] = field(default_factory=list)
    confidence: float = 0.0
    ongoing: bool = True
    next_expected: Optional[datetime] = None
    evidence: List[str] = field(default_factory=list)


@dataclass
class FrequencyAnalysis:
    """Frequency analysis result"""
    event_type: str
    total_occurrences: int
    time_span_days: float
    average_frequency_per_month: float
    frequency_trend: str  # "increasing", "decreasing", "stable", "unknown"
    frequency_by_period: Dict[str, int]
    detected_patterns: List[RecurrencePattern]
    confidence: float


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def detect_recurrence(
    events: List[datetime],
    event_type: str,
    min_occurrences: int = 3,
    max_gap_days: int = 90,
    window_days: int = 365
) -> List[RecurrencePattern]:
    """
    Detect recurrence patterns in a series of event timestamps.
    Returns list of detected patterns sorted by confidence.
    """
    if len(events) < min_occurrences:
        return []
    
    # Sort events
    sorted_events = sorted([ensure_utc(e) for e in events])
    now = ensure_utc(datetime.now())
    
    # Filter to window
    cutoff = now - timedelta(days=window_days)
    recent_events = [e for e in sorted_events if e >= cutoff]
    
    if len(recent_events) < min_occurrences:
        return []
    
    patterns = []
    
    # 1. Check for explicit regular patterns (daily, weekly, monthly)
    regular_patterns = _detect_regular_patterns(recent_events, event_type)
    patterns.extend(regular_patterns)
    
    # 2. Check for irregular/episodic patterns
    irregular_patterns = _detect_irregular_patterns(recent_events, event_type, max_gap_days)
    patterns.extend(irregular_patterns)
    
    # 3. Check for clustering
    cluster_patterns = _detect_clusters(recent_events, event_type)
    patterns.extend(cluster_patterns)
    
    # Sort by confidence
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    
    return patterns


def _detect_regular_patterns(
    events: List[datetime],
    event_type: str
) -> List[RecurrencePattern]:
    """Detect regular patterns (daily, weekly, monthly, etc.)"""
    patterns = []
    
    if len(events) < 3:
        return patterns
    
    # Compute intervals between consecutive events
    intervals = []
    for i in range(1, len(events)):
        delta = (events[i] - events[i-1]).total_seconds() / 86400  # days
        intervals.append(delta)
    
    if not intervals:
        return patterns
    
    avg_interval = sum(intervals) / len(intervals)
    std_interval = math.sqrt(sum((x - avg_interval) ** 2 for x in intervals) / len(intervals))
    cv = std_interval / avg_interval if avg_interval > 0 else float('inf')  # coefficient of variation
    
    # Determine frequency based on average interval and consistency
    frequency = RecurrenceFrequency.UNKNOWN
    confidence = 0.0
    
    # Daily: ~1 day intervals, low variation
    if 0.5 <= avg_interval <= 1.5 and cv < 0.3:
        frequency = RecurrenceFrequency.DAILY
        confidence = 0.9 - cv
    # Weekly: ~7 day intervals
    elif 6 <= avg_interval <= 8 and cv < 0.3:
        frequency = RecurrenceFrequency.WEEKLY
        confidence = 0.85 - cv
    # Biweekly: ~14 day intervals
    elif 13 <= avg_interval <= 15 and cv < 0.3:
        frequency = RecurrenceFrequency.BIWEEKLY
        confidence = 0.8 - cv
    # Monthly: ~30 day intervals
    elif 27 <= avg_interval <= 33 and cv < 0.4:
        frequency = RecurrenceFrequency.MONTHLY
        confidence = 0.75 - cv * 0.5
    # Quarterly: ~90 day intervals
    elif 85 <= avg_interval <= 95 and cv < 0.4:
        frequency = RecurrenceFrequency.QUARTERLY
        confidence = 0.7 - cv * 0.5
    # Yearly: ~365 day intervals
    elif 350 <= avg_interval <= 380 and cv < 0.5:
        frequency = RecurrenceFrequency.YEARLY
        confidence = 0.6 - cv * 0.5
    
    if frequency != RecurrenceFrequency.UNKNOWN and confidence > 0.5:
        # Predict next occurrence
        last_event = events[-1]
        next_expected = last_event + timedelta(days=avg_interval)
        
        patterns.append(RecurrencePattern(
            pattern_type=RecurrenceType.OBSERVED_PATTERN,
            frequency=frequency,
            custom_interval_days=avg_interval,
            event_type=event_type,
            occurrences=events,
            confidence=confidence,
            ongoing=True,
            next_expected=next_expected,
            evidence=[f"Regular interval: {avg_interval:.1f} days (CV: {cv:.2f})"]
        ))
    
    return patterns


def _detect_irregular_patterns(
    events: List[datetime],
    event_type: str,
    max_gap_days: int
) -> List[RecurrencePattern]:
    """Detect irregular but recurring patterns"""
    patterns = []
    
    if len(events) < 3:
        return patterns
    
    intervals = []
    for i in range(1, len(events)):
        delta = (events[i] - events[i-1]).total_seconds() / 86400
        intervals.append(delta)
    
    # Check if intervals are highly variable (irregular)
    avg_interval = sum(intervals) / len(intervals)
    std_interval = math.sqrt(sum((x - avg_interval) ** 2 for x in intervals) / len(intervals))
    cv = std_interval / avg_interval if avg_interval > 0 else float('inf')
    
    # Irregular but recurring: high variation but events keep happening
    if cv > 0.5 and max(intervals) <= max_gap_days * 2:
        # Check if there's a minimum frequency (at least once per max_gap)
        if avg_interval <= max_gap_days:
            confidence = min(0.7, 1.0 - (cv - 0.5))
            
            # Check for episodic pattern (bursts with gaps)
            if _is_episodic(intervals):
                frequency = RecurrenceFrequency.EPISODIC
                evidence = ["Episodic pattern: bursts of events separated by longer gaps"]
            else:
                frequency = RecurrenceFrequency.IRREGULAR
                evidence = [f"Irregular recurrence: avg {avg_interval:.1f} days, CV {cv:.2f}"]
            
            last_event = events[-1]
            next_expected = last_event + timedelta(days=avg_interval)
            
            patterns.append(RecurrencePattern(
                pattern_type=RecurrenceType.OBSERVED_PATTERN,
                frequency=frequency,
                custom_interval_days=avg_interval,
                event_type=event_type,
                occurrences=events,
                confidence=confidence,
                ongoing=True,
                next_expected=next_expected,
                evidence=evidence
            ))
    
    return patterns


def _is_episodic(intervals: List[float]) -> bool:
    """Check if intervals show episodic pattern (short intervals clustered, then long gaps)"""
    if len(intervals) < 4:
        return False
    
    # Look for alternating short/long intervals
    short_threshold = sorted(intervals)[len(intervals) // 3]  # bottom third
    long_threshold = sorted(intervals)[2 * len(intervals) // 3]  # top third
    
    short_count = sum(1 for i in intervals if i <= short_threshold)
    long_count = sum(1 for i in intervals if i >= long_threshold)
    
    return short_count >= 2 and long_count >= 2


def _detect_clusters(
    events: List[datetime],
    event_type: str
) -> List[RecurrencePattern]:
    """Detect temporal clustering of events"""
    patterns = []
    
    if len(events) < 3:
        return patterns
    
    # Use sliding window to find clusters
    cluster_window_days = 14
    clusters = []
    current_cluster = [events[0]]
    
    for i in range(1, len(events)):
        gap = (events[i] - events[i-1]).total_seconds() / 86400
        if gap <= cluster_window_days:
            current_cluster.append(events[i])
        else:
            if len(current_cluster) >= 3:
                clusters.append(current_cluster)
            current_cluster = [events[i]]
    
    if len(current_cluster) >= 3:
        clusters.append(current_cluster)
    
    for cluster in clusters:
        cluster_span = (cluster[-1] - cluster[0]).total_seconds() / 86400
        event_count = len(cluster)
        density = event_count / max(cluster_span, 1)
        
        confidence = min(0.8, density * 2)
        
        patterns.append(RecurrencePattern(
            pattern_type=RecurrenceType.OBSERVED_PATTERN,
            frequency=RecurrenceFrequency.CLUSTERED,
            custom_interval_days=cluster_span / event_count if event_count > 0 else None,
            event_type=event_type,
            occurrences=cluster,
            confidence=confidence,
            ongoing=False,  # Clusters are typically finite
            evidence=[f"Cluster: {event_count} events in {cluster_span:.1f} days"]
        ))
    
    return patterns


def analyze_frequency(
    events: List[datetime],
    event_type: str,
    period_days: int = 30
) -> FrequencyAnalysis:
    """
    Comprehensive frequency analysis for an event type.
    """
    if not events:
        return FrequencyAnalysis(
            event_type=event_type,
            total_occurrences=0,
            time_span_days=0,
            average_frequency_per_month=0,
            frequency_trend="unknown",
            frequency_by_period={},
            detected_patterns=[],
            confidence=0.0
        )
    
    sorted_events = sorted([ensure_utc(e) for e in events])
    now = ensure_utc(datetime.now())
    
    # Time span
    time_span = (sorted_events[-1] - sorted_events[0]).total_seconds() / 86400
    if time_span == 0:
        time_span = 1
    
    total = len(sorted_events)
    avg_freq_per_month = (total / time_span) * 30
    
    # Frequency by period (monthly buckets)
    freq_by_period = defaultdict(int)
    for event in sorted_events:
        key = event.strftime("%Y-%m")
        freq_by_period[key] += 1
    
    # Trend detection
    periods = sorted(freq_by_period.keys())
    if len(periods) >= 3:
        values = [freq_by_period[p] for p in periods]
        # Simple linear trend
        x = list(range(len(values)))
        n = len(values)
        sum_x = sum(x)
        sum_y = sum(values)
        sum_xy = sum(x[i] * values[i] for i in range(n))
        sum_x2 = sum(x[i] ** 2 for i in range(n))
        
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x ** 2) if n * sum_x2 != sum_x ** 2 else 0
        
        if slope > 0.1:
            trend = "increasing"
        elif slope < -0.1:
            trend = "decreasing"
        else:
            trend = "stable"
    else:
        trend = "unknown"
    
    # Detect patterns
    patterns = detect_recurrence(sorted_events, event_type)
    
    # Overall confidence
    confidence = 0.5
    if total >= 10:
        confidence = 0.8
    elif total >= 5:
        confidence = 0.65
    elif total >= 3:
        confidence = 0.5
    
    return FrequencyAnalysis(
        event_type=event_type,
        total_occurrences=total,
        time_span_days=time_span,
        average_frequency_per_month=avg_freq_per_month,
        frequency_trend=trend,
        frequency_by_period=dict(freq_by_period),
        detected_patterns=patterns,
        confidence=confidence
    )


def detect_frequency_change(
    events: List[datetime],
    event_type: str,
    baseline_period_days: int = 180,
    recent_period_days: int = 30
) -> Optional[Dict[str, Any]]:
    """
    Detect if frequency has changed significantly between baseline and recent periods.
    """
    if not events:
        return None
    
    sorted_events = sorted([ensure_utc(e) for e in events])
    now = ensure_utc(datetime.now())
    
    baseline_start = now - timedelta(days=baseline_period_days + recent_period_days)
    baseline_end = now - timedelta(days=recent_period_days)
    recent_start = now - timedelta(days=recent_period_days)
    
    baseline_events = [e for e in sorted_events if baseline_start <= e < baseline_end]
    recent_events = [e for e in sorted_events if recent_start <= e <= now]
    
    if len(baseline_events) < 2 or len(recent_events) < 2:
        return None
    
    baseline_rate = len(baseline_events) / (baseline_period_days / 30)  # per month
    recent_rate = len(recent_events) / (recent_period_days / 30)  # per month
    
    if baseline_rate == 0:
        return None
    
    change_ratio = recent_rate / baseline_rate
    change_percent = (change_ratio - 1) * 100
    
    if change_ratio > 2.0:
        significance = "significant_increase"
    elif change_ratio > 1.5:
        significance = "moderate_increase"
    elif change_ratio < 0.5:
        significance = "significant_decrease"
    elif change_ratio < 0.67:
        significance = "moderate_decrease"
    else:
        significance = "stable"
    
    return {
        "event_type": event_type,
        "baseline_rate_per_month": round(baseline_rate, 2),
        "recent_rate_per_month": round(recent_rate, 2),
        "change_ratio": round(change_ratio, 2),
        "change_percent": round(change_percent, 1),
        "significance": significance,
        "baseline_events": len(baseline_events),
        "recent_events": len(recent_events),
        "baseline_period_days": baseline_period_days,
        "recent_period_days": recent_period_days,
    }


def predict_next_occurrence(
    events: List[datetime],
    event_type: str
) -> Optional[Dict[str, Any]]:
    """
    Predict next occurrence based on detected patterns.
    """
    patterns = detect_recurrence(events, event_type)
    
    if not patterns:
        return None
    
    # Use highest confidence pattern
    best_pattern = patterns[0]
    
    if best_pattern.next_expected:
        return {
            "event_type": event_type,
            "predicted_next": best_pattern.next_expected.isoformat(),
            "pattern_type": best_pattern.pattern_type.value,
            "frequency": best_pattern.frequency.value,
            "confidence": best_pattern.confidence,
            "based_on_occurrences": len(best_pattern.occurrences),
        }
    
    return None


# Export
__all__ = [
    "RecurrenceType",
    "RecurrenceFrequency",
    "RecurrencePattern",
    "FrequencyAnalysis",
    "ensure_utc",
    "detect_recurrence",
    "analyze_frequency",
    "detect_frequency_change",
    "predict_next_occurrence",
]