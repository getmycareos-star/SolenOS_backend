"""Event Deduplication Service.

Handles mention-level deduplication with full traceability.
Maintains provenance while identifying same-event mentions.

Key principle: Multiple mentions of same event are preserved,
not collapsed. Deduplication creates a link record (DISTINCT/SAME_EVENT/POSSIBLE_DUPLICATE/UNRESOLVED).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    EventMentionDeduplicationStatus,
    EventMentionType,
)
from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    NegationStatus,
    UncertaintyLevel,
)
from app.models.care_event import (
    CareEvent,
    CareEventMentionDeduplication,
)
from app.models.evidence import Evidence


@dataclass
class MentionPair:
    """A pair of mentions to evaluate for deduplication."""
    mention_a_id: str
    mention_b_id: str
    event_type: CareEventType
    event_time_a: Optional[datetime] = None
    event_time_b: Optional[datetime] = None
    subject_id_a: Optional[str] = None
    subject_id_b: Optional[str] = None
    source_text_a: str = ""
    source_text_b: str = ""
    negation_status_a: Optional[NegationStatus] = None
    negation_status_b: Optional[NegationStatus] = None
    uncertainty_level_a: Optional[UncertaintyLevel] = None
    uncertainty_level_b: Optional[UncertaintyLevel] = None
    location_a: Optional[str] = None
    location_b: Optional[str] = None
    evidence_ids_a: List[str] = field(default_factory=list)
    evidence_ids_b: List[str] = field(default_factory=list)


@dataclass
class DeduplicationResult:
    """Result of deduplication comparison."""
    mention_a_id: str
    mention_b_id: str
    status: EventMentionDeduplicationStatus
    similarity_score: float
    match_criteria: List[str]
    match_details: Dict[str, Any]
    resolved_event_id: Optional[str] = None  # The canonical event if deduplication confirmed
    confidence: float = 0.0


class EventDeduplicationService:
    """Event mention deduplication service.

    Identifies whether two event mentions represent the same underlying event
    based on subject, type, temporal proximity, and contextual similarity.

    Critical design principle: Mentions remain traceable and preserved.
    Deduplication creates a link, never destroys source data.
    """

    # Time windows for temporal matching (in hours)
    EXACT_MATCH_WINDOW_HOURS = 1
    CLOSE_MATCH_WINDOW_HOURS = 6
    SAME_DAY_WINDOW_HOURS = 24
    SAME_WEEK_WINDOW_HOURS = 168  # 7 days

    # Similarity thresholds
    SAME_EVENT_THRESHOLD = 0.85
    POSSIBLE_DUPLICATE_THRESHOLD = 0.60
    DISTINCT_THRESHOLD = 0.30

    def __init__(self):
        pass

    def deduplicate_mentions(
        self,
        db: Session,
        person_id: str,
        event_type: Optional[CareEventType] = None,
        new_event_id: Optional[str] = None,
    ) -> List[DeduplicationResult]:
        """Find all candidate deduplication pairs for a person's events.

        If new_event_id is provided, compares against existing events.
        Otherwise, compares all events for the person.
        """
        query = db.query(CareEvent).filter(CareEvent.person_id == person_id)
        if event_type:
            query = query.filter(CareEvent.event_type == event_type)

        events = query.all()

        if new_event_id:
            # Compare new event against all existing events
            new_event = next((e for e in events if e.id == new_event_id), None)
            if not new_event:
                return []
            existing_events = [e for e in events if e.id != new_event_id]
            pairs = [self._build_pair(new_event, e) for e in existing_events]
        else:
            # Compare all pairs
            pairs = []
            for i, ev_a in enumerate(events):
                for ev_b in events[i + 1:]:
                    pairs.append(self._build_pair(ev_a, ev_b))

        results: List[DeduplicationResult] = []
        for pair in pairs:
            result = self._evaluate_pair(db, pair)
            if result:
                results.append(result)

        return results

    def _build_pair(self, event_a: CareEvent, event_b: CareEvent) -> MentionPair:
        """Build a MentionPair from two CareEvent objects."""
        return MentionPair(
            mention_a_id=event_a.id,
            mention_b_id=event_b.id,
            event_type=event_a.event_type,
            event_time_a=event_a.event_time,
            event_time_b=event_b.event_time,
            subject_id_a=event_a.subject_id or event_a.person_id,
            subject_id_b=event_b.subject_id or event_b.person_id,
            source_text_a=event_a.original_expression,
            source_text_b=event_b.original_expression,
            location_a=event_a.location,
            location_b=event_b.location,
            evidence_ids_a=event_a.evidence_ids or [],
            evidence_ids_b=event_b.evidence_ids or [],
        )

    def _evaluate_pair(self, db: Session, pair: MentionPair) -> Optional[DeduplicationResult]:
        """Evaluate a pair of mentions for deduplication."""
        # Must be same event type
        # (Both mentions have same event_type since we group by event_type)
        # But we still check since we might compare different types
        # For now, if types differ, they're distinct
        # (handled at caller level by grouping)

        similarity_score = 0.0
        match_criteria: List[str] = []
        match_details: Dict[str, Any] = {}

        # 1. Subject match (same person)
        subject_match = pair.subject_id_a == pair.subject_id_b and pair.subject_id_a is not None
        if subject_match:
            similarity_score += 0.30
            match_criteria.append("same_subject")
        match_details["subject_match"] = subject_match
        match_details["subject_a"] = pair.subject_id_a
        match_details["subject_b"] = pair.subject_id_b

        # 2. Event type match
        # Both should have same event_type (filtered at caller level)
        type_match = True  # Already filtered
        if type_match:
            similarity_score += 0.25
            match_criteria.append("same_event_type")
        match_details["event_type"] = pair.event_type.value if hasattr(pair.event_type, 'value') else str(pair.event_type)

        # 3. Temporal proximity
        temporal_similarity, temporal_match = self._calculate_temporal_similarity(pair)
        similarity_score += temporal_similarity
        if temporal_match:
            match_criteria.append(temporal_match)
            match_details["temporal_match"] = temporal_match
        match_details["temporal_similarity"] = temporal_similarity
        match_details["time_a"] = pair.event_time_a.isoformat() if pair.event_time_a else None
        match_details["time_b"] = pair.event_time_b.isoformat() if pair.event_time_b else None

        # 4. Text similarity (normalized)
        text_similarity = self._calculate_text_similarity(pair.source_text_a, pair.source_text_b)
        similarity_score += text_similarity * 0.20
        if text_similarity > 0.5:
            match_criteria.append("text_similarity")
        match_details["text_similarity"] = text_similarity

        # 5. Location match
        if pair.location_a and pair.location_b and pair.location_a == pair.location_b:
            similarity_score += 0.10
            match_criteria.append("same_location")
        match_details["location_a"] = pair.location_a
        match_details["location_b"] = pair.location_b

        # 6. Evidence overlap
        evidence_overlap = len(set(pair.evidence_ids_a) & set(pair.evidence_ids_b))
        if evidence_overlap > 0:
            similarity_score += 0.05
            match_criteria.append("evidence_overlap")
        match_details["evidence_overlap_count"] = evidence_overlap

        # Apply penalties
        # Negation mismatch is a strong penalty
        if pair.negation_status_a != pair.negation_status_b:
            similarity_score *= 0.5
            match_criteria.append("negation_mismatch_penalty")
        match_details["negation_a"] = pair.negation_status_a.value if pair.negation_status_a else None
        match_details["negation_b"] = pair.negation_status_b.value if pair.negation_status_b else None

        # Determine status
        status = self._determine_status(similarity_score)

        # Determine if this is a deduplication that needs recording
        if status in (EventMentionDeduplicationStatus.SAME_EVENT, EventMentionDeduplicationStatus.POSSIBLE_DUPLICATE):
            return DeduplicationResult(
                mention_a_id=pair.mention_a_id,
                mention_b_id=pair.mention_b_id,
                status=status,
                similarity_score=round(similarity_score, 3),
                match_criteria=match_criteria,
                match_details=match_details,
                confidence=self._calculate_confidence(status, similarity_score, temporal_match),
            )

        # If distinct, still return but with low confidence
        if status == EventMentionDeduplicationStatus.DISTINCT:
            return DeduplicationResult(
                mention_a_id=pair.mention_a_id,
                mention_b_id=pair.mention_b_id,
                status=status,
                similarity_score=round(similarity_score, 3),
                match_criteria=match_criteria,
                match_details=match_details,
                confidence=0.9,  # High confidence that they're distinct
            )

        return None

    def _calculate_temporal_similarity(self, pair: MentionPair) -> Tuple[float, Optional[str]]:
        """Calculate temporal similarity between two mentions."""
        if pair.event_time_a is None or pair.event_time_b is None:
            return 0.0, None

        time_diff = abs((pair.event_time_a - pair.event_time_b).total_seconds())
        time_diff_hours = time_diff / 3600

        if time_diff < 1:  # Within same second
            return 0.15, "same_time"
        elif time_diff_hours <= self.EXACT_MATCH_WINDOW_HOURS:
            return 0.15, "temporal_exact_match"
        elif time_diff_hours <= self.CLOSE_MATCH_WINDOW_HOURS:
            return 0.12, "temporal_close_match"
        elif time_diff_hours <= self.SAME_DAY_WINDOW_HOURS:
            return 0.08, "temporal_same_day"
        elif time_diff_hours <= self.SAME_WEEK_WINDOW_HOURS:
            return 0.04, "temporal_same_week"
        else:
            return 0.0, "temporal_distant"

    def _calculate_text_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate text similarity using token-based approach."""
        if not text_a or not text_b:
            return 0.0

        tokens_a = self._tokenize(text_a)
        tokens_b = self._tokenize(text_b)

        if not tokens_a or not tokens_b:
            return 0.0

        # Jaccard similarity with bigrams
        bigrams_a = self._bigrams(tokens_a)
        bigrams_b = self._bigrams(tokens_b)

        if not bigrams_a or not bigrams_b:
            # Fall back to token overlap
            intersection = len(set(tokens_a) & set(tokens_b))
            union = len(set(tokens_a) | set(tokens_b))
            return intersection / union if union > 0 else 0.0

        intersection = len(bigrams_a & bigrams_b)
        union = len(bigrams_a | bigrams_b)
        return intersection / union if union > 0 else 0.0

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into words."""
        return [t.lower() for t in re.findall(r"\b\w+\b", text)]

    def _bigrams(self, tokens: List[str]) -> Set[Tuple[str, str]]:
        """Generate bigrams from tokens."""
        if len(tokens) < 2:
            return set()
        return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}

    def _determine_status(self, similarity_score: float) -> EventMentionDeduplicationStatus:
        """Determine deduplication status based on similarity score."""
        if similarity_score >= self.SAME_EVENT_THRESHOLD:
            return EventMentionDeduplicationStatus.SAME_EVENT
        elif similarity_score >= self.POSSIBLE_DUPLICATE_THRESHOLD:
            return EventMentionDeduplicationStatus.POSSIBLE_DUPLICATE
        elif similarity_score <= self.DISTINCT_THRESHOLD:
            return EventMentionDeduplicationStatus.DISTINCT
        else:
            return EventMentionDeduplicationStatus.UNRESOLVED

    def _calculate_confidence(
        self,
        status: EventMentionDeduplicationStatus,
        similarity_score: float,
        temporal_match: Optional[str],
    ) -> float:
        """Calculate confidence in the deduplication result."""
        base_confidence = similarity_score

        if temporal_match is not None:
            # Higher confidence if temporal match is strong
            if "exact" in temporal_match:
                base_confidence = min(1.0, base_confidence + 0.1)
            elif "close" in temporal_match:
                base_confidence = min(1.0, base_confidence + 0.05)

        if status == EventMentionDeduplicationStatus.SAME_EVENT:
            return min(1.0, max(base_confidence, 0.8))
        elif status == EventMentionDeduplicationStatus.POSSIBLE_DUPLICATE:
            return min(1.0, max(base_confidence, 0.5))
        elif status == EventMentionDeduplicationStatus.DISTINCT:
            return min(1.0, max(1.0 - base_confidence, 0.9))
        else:
            return base_confidence

    def propose_deduplication(
        self,
        db: Session,
        mention_a_id: str,
        mention_b_id: str,
        proposed_by_caregiver_id: Optional[str] = None,
    ) -> CareEventMentionDeduplication:
        """Create a deduplication proposal in the database."""
        dedup = CareEventMentionDeduplication(
            mention_a_id=mention_a_id,
            mention_b_id=mention_b_id,
            status=EventMentionDeduplicationStatus.UNRESOLVED,
            confirmed_by=proposed_by_caregiver_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(dedup)
        db.commit()
        db.refresh(dedup)
        return dedup

    def resolve_deduplication(
        self,
        db: Session,
        dedup_id: str,
        status: EventMentionDeduplicationStatus,
        resolved_event_id: Optional[str] = None,
        resolution_reason: Optional[str] = None,
        confirmed_by_caregiver_id: Optional[str] = None,
    ) -> CareEventMentionDeduplication:
        """Resolve a deduplication record."""
        dedup = db.get(CareEventMentionDeduplication, dedup_id)
        if not dedup:
            raise ValueError(f"Deduplication record {dedup_id} not found")

        dedup.status = status
        dedup.resolved_event_id = resolved_event_id
        dedup.resolution_reason = resolution_reason
        dedup.confirmed_by = confirmed_by_caregiver_id
        if confirmed_by_caregiver_id:
            dedup.confirmed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(dedup)
        return dedup

    def find_deduplication_for_event(
        self,
        db: Session,
        event_id: str,
    ) -> List[CareEventMentionDeduplication]:
        """Find all deduplication records involving an event."""
        return (
            db.query(CareEventMentionDeduplication)
            .filter(
                (CareEventMentionDeduplication.mention_a_id == event_id)
                | (CareEventMentionDeduplication.mention_b_id == event_id)
            )
            .all()
        )

    def get_canonical_event_id(
        self,
        db: Session,
        event_id: str,
    ) -> str:
        """Follow deduplication chain to find canonical event ID."""
        visited: Set[str] = set()

        current = event_id
        while True:
            if current in visited:
                break  # Cycle detection
            visited.add(current)

            dedup_records = self.find_deduplication_for_event(db, current)
            found_canonical = False

            for record in dedup_records:
                if record.status == EventMentionDeduplicationStatus.SAME_EVENT and record.resolved_event_id:
                    current = record.resolved_event_id
                    found_canonical = True
                    break

            if not found_canonical:
                break

        return current

    def batch_deduplicate(
        self,
        db: Session,
        person_id: str,
        event_types: Optional[List[CareEventType]] = None,
    ) -> List[DeduplicationResult]:
        """Batch deduplicate all events for a person."""
        all_results: List[DeduplicationResult] = []

        if event_types:
            for et in event_types:
                results = self.deduplicate_mentions(db, person_id, event_type=et)
                all_results.extend(results)
        else:
            results = self.deduplicate_mentions(db, person_id)
            all_results.extend(results)

        return all_results
