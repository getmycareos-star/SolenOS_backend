"""Conservative recurrence detection for persisted care events.

This service groups repeated, text-similar events without deleting or merging
individual events. It is a deterministic signal for review, not a clinical
conclusion. Events without an explicit event time are excluded from temporal
recurrence calculations.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.core.event_enums import CareEventType, RecurrenceDetectionMethod
from app.models.care_event import CareEvent, CareEventRecurrenceGroup


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "the", "to", "was", "were", "while", "with",
}


def _tokens(event: CareEvent) -> set[str]:
    text = event.original_expression or event.description or event.title or ""
    return {
        token for token in re.findall(r"\b[a-z0-9]+\b", text.lower())
        if token not in _STOP_WORDS
    }


def _similarity(left: CareEvent, right: CareEvent) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


@dataclass(frozen=True)
class RecurrencePattern:
    event_type: CareEventType
    first_occurrence: datetime
    last_occurrence: datetime
    occurrences: list[CareEvent]
    pattern_type: str
    average_interval_days: Optional[float]
    interval_std_dev_days: Optional[float]
    frequency_per_month: Optional[float]
    trend: str
    confidence: Optional[float]
    detection_method: RecurrenceDetectionMethod
    evidence_ids: list[str]


class EventRecurrenceService:
    """Detect conservative recurrence candidates and persist their grouping."""

    SIMILARITY_THRESHOLD = 0.5

    def detect_recurrence_patterns(
        self,
        db: Session,
        person_id: str,
        event_type: Optional[CareEventType] = None,
        min_occurrences: int = 2,
    ) -> list[RecurrencePattern]:
        if min_occurrences < 2:
            raise ValueError("min_occurrences must be at least 2")

        query = db.query(CareEvent).filter(
            CareEvent.person_id == person_id,
            CareEvent.event_time.is_not(None),
        )
        if event_type is not None:
            query = query.filter(CareEvent.event_type == event_type)

        events = query.order_by(CareEvent.event_time.asc()).all()
        # Group only events of the same type/subtype and with sufficiently
        # similar source wording. A generic 'observation' type alone is not
        # enough evidence to claim that two events are the same recurring issue.
        typed: dict[tuple[CareEventType, object], list[CareEvent]] = defaultdict(list)
        for event in events:
            typed[(event.event_type, event.event_subtype)].append(event)

        patterns: list[RecurrencePattern] = []
        for (typed_event_type, _subtype), typed_events in typed.items():
            clusters: list[list[CareEvent]] = []
            for event in typed_events:
                matching_cluster = None
                for cluster in clusters:
                    if _similarity(event, cluster[0]) >= self.SIMILARITY_THRESHOLD:
                        matching_cluster = cluster
                        break
                if matching_cluster is None:
                    clusters.append([event])
                else:
                    matching_cluster.append(event)

            for cluster in clusters:
                if len(cluster) < min_occurrences:
                    continue
                cluster.sort(key=lambda item: item.event_time)
                times = [item.event_time for item in cluster if item.event_time is not None]
                if len(times) < min_occurrences:
                    continue
                intervals = [
                    (right - left).total_seconds() / 86400
                    for left, right in zip(times, times[1:])
                ]
                span_days = (times[-1] - times[0]).total_seconds() / 86400
                average_interval = statistics.mean(intervals) if intervals else None
                interval_std_dev = statistics.pstdev(intervals) if len(intervals) > 1 else None
                frequency_per_month = (
                    len(cluster) * 30.4375 / span_days if span_days > 0 else None
                )
                evidence_ids = sorted({
                    evidence_id
                    for item in cluster
                    for evidence_id in (item.evidence_ids or [])
                })
                similarities = [
                    _similarity(cluster[0], item) for item in cluster[1:]
                ]
                confidence = (
                    round(statistics.mean(similarities), 3) if similarities else 1.0
                )
                patterns.append(RecurrencePattern(
                    event_type=typed_event_type,
                    first_occurrence=times[0],
                    last_occurrence=times[-1],
                    occurrences=list(cluster),
                    pattern_type="repeated_similar_events",
                    average_interval_days=average_interval,
                    interval_std_dev_days=interval_std_dev,
                    frequency_per_month=frequency_per_month,
                    # No trend is inferred from a simple count of occurrences.
                    trend="unknown",
                    confidence=confidence,
                    detection_method=RecurrenceDetectionMethod.SEMANTIC_SIMILARITY,
                    evidence_ids=evidence_ids,
                ))

        return sorted(
            patterns,
            key=lambda pattern: (pattern.first_occurrence, pattern.event_type.value),
        )

    def create_recurrence_group(
        self,
        db: Session,
        pattern: RecurrencePattern,
        event_type: Optional[CareEventType] = None,
        event_subtype=None,
        created_by_caregiver_id: Optional[str] = None,
    ) -> CareEventRecurrenceGroup:
        del created_by_caregiver_id  # The current group schema has no creator field.
        canonical_type = event_type or pattern.event_type
        member_ids = [event.id for event in pattern.occurrences]

        for existing in db.query(CareEventRecurrenceGroup).filter(
            CareEventRecurrenceGroup.person_id == pattern.occurrences[0].person_id,
            CareEventRecurrenceGroup.event_type == canonical_type,
            CareEventRecurrenceGroup.is_active.is_(True),
        ).all():
            if sorted(existing.member_event_ids or []) == sorted(member_ids):
                return existing

        group = CareEventRecurrenceGroup(
            person_id=pattern.occurrences[0].person_id,
            event_type=canonical_type,
            event_subtype=event_subtype,
            first_occurrence_time=pattern.first_occurrence,
            last_occurrence_time=pattern.last_occurrence,
            occurrence_count=len(pattern.occurrences),
            pattern_type=pattern.pattern_type,
            average_interval_days=pattern.average_interval_days,
            interval_std_dev_days=pattern.interval_std_dev_days,
            frequency_per_month=pattern.frequency_per_month,
            trend=pattern.trend,
            pattern_confidence=pattern.confidence,
            grouping_confidence=pattern.confidence,
            detection_method=pattern.detection_method,
            detection_evidence_ids=pattern.evidence_ids,
            is_active=True,
            status="active",
            representative_event_id=pattern.occurrences[-1].id,
            member_event_ids=member_ids,
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        return group

    def list_recurrence_groups(
        self,
        db: Session,
        person_id: str,
        include_inactive: bool = False,
    ) -> list[CareEventRecurrenceGroup]:
        query = db.query(CareEventRecurrenceGroup).filter(
            CareEventRecurrenceGroup.person_id == person_id,
        )
        if not include_inactive:
            query = query.filter(CareEventRecurrenceGroup.is_active.is_(True))
        return query.order_by(CareEventRecurrenceGroup.first_occurrence_time.asc()).all()

    def delete_recurrence_group(self, db: Session, group_id: str) -> bool:
        """Deactivate a recurrence group without deleting historical events."""
        group = db.get(CareEventRecurrenceGroup, group_id)
        if group is None:
            return False
        group.is_active = False
        group.status = "inactive"
        db.commit()
        return True
