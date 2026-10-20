"""Event Relationship Service.

Establishes typed, provenance-backed relationships between care events.
Distinguishes temporal vs causal relationships.

Key principle: Causality must be supported by evidence - never assumed.
Relationships are removable/correctable without destroying events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import update

from app.core.event_enums import (
    CareEventType,
    CareEventStatus,
    CareEventRelationshipType,
    EventStateImpactType,
    EventStateImpactConfidence,
)
from app.models.care_event import (
    CareEvent,
    CareEventRelationship,
    CareEventStateImpact,
    CareEventRecurrenceGroup,
)


@dataclass
class RelationshipInference:
    """Inferred relationship between two events."""
    source_event_id: str
    target_event_id: str
    relationship_type: CareEventRelationshipType
    is_temporal: bool
    is_causal: bool
    causal_evidence_ids: List[str]
    temporal_relationship: Optional[str]
    time_gap_seconds: Optional[float]
    strength: Optional[float]
    confidence: Optional[float]
    detection_method: str
    detection_confidence: Optional[float]
    inference_reasoning: Optional[str] = None


class EventRelationshipService:
    """Establishes relationships between care events.

    Temporal relationships: BEFORE, AFTER, DURING, OVERLAPS, SIMULTANEOUS
    Causal relationships: TRIGGERS, RESULTS_IN, CONTRIBUTES_TO, PREVENTS, etc.

    Critical: Causal relationships MUST have supporting evidence.
    """

    # Relationship inference patterns
    CAUSAL_PATTERNS: Dict[str, List[CareEventRelationshipType]] = {
        "medication_taken": [
            CareEventRelationshipType.RESULTS_IN,
        ],
        "medication_started": [
            CareEventRelationshipType.RESULTS_IN,
        ],
        "procedure_completed": [
            CareEventRelationshipType.RESULTS_IN,
        ],
        "hospitalization": [
            CareEventRelationshipType.RESULTS_IN,
        ],
    }

    TEMPORAL_RELATIONSHIP_RULES: Dict[CareEventType, List[CareEventType]] = {
        CareEventType.HOSPITALIZATION_EVENT: [
            CareEventType.APPOINTMENT_EVENT,
            CareEventType.DIAGNOSTIC_EVENT,
            CareEventType.ED_VISIT_EVENT,
        ],
        CareEventType.MEDICATION_START_EVENT: [
            CareEventType.APPOINTMENT_EVENT,
            CareEventType.DIAGNOSTIC_EVENT,
        ],
        CareEventType.SURGERY_EVENT: [
            CareEventType.APPOINTMENT_EVENT,
            CareEventType.DIAGNOSTIC_EVENT,
        ],
        CareEventType.FALL_EVENT: [
            CareEventType.HOSPITALIZATION_EVENT,
            CareEventType.ED_VISIT_EVENT,
            CareEventType.APPOINTMENT_EVENT,
        ],
    }

    def __init__(self):
        pass

    def infer_relationships(
        self,
        db: Session,
        person_id: str,
        event_id: Optional[str] = None,
        lookback_days: int = 30,
        lookahead_days: int = 30,
    ) -> List[RelationshipInference]:
        """Infer relationships for a person's events.

        If event_id is provided, infer relationships involving that specific event.
        Otherwise, infer relationships for all events in the time window.
        """
        query = db.query(CareEvent).filter(
            CareEvent.person_id == person_id,
            CareEvent.lifecycle_stage != CareEventStatus.INVALIDATED.value,
        )

        if event_id:
            # Get the specific event and all events for the person in time window
            target = db.get(CareEvent, event_id)
            if not target:
                return []
            window_start = (target.event_time or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
            window_end = (target.event_time or datetime.now(timezone.utc)) + timedelta(days=lookahead_days)
            query = query.filter(
                CareEvent.event_time >= window_start,
                CareEvent.event_time <= window_end,
            )

        events = query.all()
        if event_id:
            target_event = next((e for e in events if e.id == event_id), None)
            events_to_compare = [e for e in events if e.id != event_id]
        else:
            target_event = events[0] if events else None
            events_to_compare = events[1:] if len(events) > 1 else []

        inferences: List[RelationshipInference] = []

        if target_event:
            for other_event in events_to_compare:
                # Infer temporal relationships
                temporal_rel = self._infer_temporal_relationship(target_event, other_event)
                if temporal_rel:
                    inferences.append(temporal_rel)

                # Infer causal relationships
                causal_rel = self._infer_causal_relationship(db, target_event, other_event)
                if causal_rel:
                    inferences.append(causal_rel)

                # Infer pattern-based relationships
                pattern_rel = self._infer_pattern_relationship(target_event, other_event)
                if pattern_rel:
                    inferences.append(pattern_rel)

        return inferences

    def _infer_temporal_relationship(
        self,
        event_a: CareEvent,
        event_b: CareEvent,
    ) -> Optional[RelationshipInference]:
        """Infer temporal relationship between two events."""
        if not event_a.event_time or not event_b.event_time:
            return None

        time_a = event_a.event_time
        time_b = event_b.event_time

        if time_a == time_b:
            relationship_type = CareEventRelationshipType.SIMULTANEOUS
            temporal_rel = "simultaneous"
            time_gap = 0.0
        elif time_a < time_b:
            diff = (time_b - time_a).total_seconds()
            if diff < 1:
                relationship_type = CareEventRelationshipType.IMMEDIATELY_PRECEDES
                temporal_rel = "immediately_precedes"
            elif diff < 3600:  # Within 1 hour
                relationship_type = CareEventRelationshipType.IMMEDIATELY_PRECEDES
                temporal_rel = "close_precedence"
            elif diff < 86400:  # Within 1 day
                relationship_type = CareEventRelationshipType.PRECEDES
                temporal_rel = "same_day_precedence"
            else:
                relationship_type = CareEventRelationshipType.PRECEDES
                temporal_rel = "temporal_precedence"
            time_gap = diff
        else:
            diff = (time_a - time_b).total_seconds()
            if diff < 1:
                relationship_type = CareEventRelationshipType.IMMEDIATELY_FOLLOWS
                temporal_rel = "immediately_follows"
            elif diff < 3600:
                relationship_type = CareEventRelationshipType.IMMEDIATELY_FOLLOWS
                temporal_rel = "close_follow"
            else:
                relationship_type = CareEventRelationshipType.FOLLOWS
                temporal_rel = "temporal_follow"
            time_gap = diff

        # Only infer temporal relationships for same-person events
        if event_a.person_id != event_b.person_id:
            return None

        # Calculate confidence based on temporal certainty
        confidence = 0.9
        if not event_a.event_time_confidence or not event_b.event_time_confidence:
            confidence = 0.7

        strength = min(1.0, 1.0 - (time_gap / 172800) if time_gap else 1.0)  # Normalize
        if time_gap == 0:
            strength = 1.0

        return RelationshipInference(
            source_event_id=event_a.id,
            target_event_id=event_b.id,
            relationship_type=relationship_type,
            is_temporal=True,
            is_causal=False,
            causal_evidence_ids=[],
            temporal_relationship=temporal_rel,
            time_gap_seconds=time_gap,
            strength=round(strength, 2),
            confidence=round(confidence, 2),
            detection_method="temporal_analysis",
            detection_confidence=round(confidence, 2),
            inference_reasoning=f"Events at {time_a} and {time_b} have {temporal_rel} relationship",
        )

    def _infer_causal_relationship(
        self,
        db: Session,
        event_a: CareEvent,
        event_b: CareEvent,
    ) -> Optional[RelationshipInference]:
        """Infer causal relationship based on event type patterns.

        CRITICAL: Causal relationships must be supported by evidence or domain rules.
        """
        # Person contamination check
        if event_a.person_id != event_b.person_id:
            return None

        # Time ordering: causal event must precede effect
        if event_a.event_time and event_b.event_time and event_a.event_time >= event_b.event_time:
            return None

        # Check event type patterns for causal inference
        source_pattern = self._get_causal_pattern_key(event_a.event_type)
        target_pattern = self._get_causal_pattern_key(event_b.event_type)

        # Medication -> Symptom improvement
        if (
            event_a.event_type in (
                CareEventType.MEDICATION_START_EVENT,
                CareEventType.MEDICATION_TAKEN_EVENT,
                CareEventType.THERAPY_EVENT,
                CareEventType.REHABILITATION_EVENT,
            )
            and event_b.event_type in (
                CareEventType.SYMPTOM_EVENT,
                CareEventType.CARE_EVENT.value == "cognitive_observation" and CareEventType.OBSERVATION_EVENT,
            )
        ):
            causal_evidence = self._gather_causal_evidence(db, event_a, event_b)
            if not causal_evidence:
                return None

            return RelationshipInference(
                source_event_id=event_a.id,
                target_event_id=event_b.id,
                relationship_type=CareEventRelationshipType.RESULTS_IN,
                is_temporal=True,
                is_causal=True,
                causal_evidence_ids=causal_evidence,
                temporal_relationship="temporal_precedence",
                time_gap_seconds=(
                    (event_b.event_time - event_a.event_time).total_seconds()
                    if event_a.event_time and event_b.event_time else None
                ),
                strength=0.7,
                confidence=0.6,
                detection_method="causal_inference",
                detection_confidence=0.6,
                inference_reasoning="Medication/therapy event precedes improvement event with evidence",
            )

        # Fall -> Hospitalization
        if (
            event_a.event_type == CareEventType.FALL_EVENT
            and event_b.event_type in (CareEventType.HOSPITALIZATION_EVENT, CareEventType.ED_VISIT_EVENT)
        ):
            causal_evidence = self._gather_causal_evidence(db, event_a, event_b)
            if not causal_evidence:
                return None

            return RelationshipInference(
                source_event_id=event_a.id,
                target_event_id=event_b.id,
                relationship_type=CareEventRelationshipType.TRIGGERS,
                is_temporal=True,
                is_causal=True,
                causal_evidence_ids=causal_evidence,
                temporal_relationship="temporal_precedence",
                time_gap_seconds=(
                    (event_b.event_time - event_a.event_time).total_seconds()
                    if event_a.event_time and event_b.event_time else None
                ),
                strength=0.8,
                confidence=0.85,
                detection_method="causal_inference",
                detection_confidence=0.85,
                inference_reasoning="Fall event triggers hospitalization/ED visit",
            )

        # Appointment -> Medication Start
        if (
            event_a.event_type == CareEventType.APPOINTMENT_EVENT
            and event_b.event_type == CareEventType.MEDICATION_START_EVENT
        ):
            causal_evidence = self._gather_causal_evidence(db, event_a, event_b)
            if not causal_evidence:
                return None

            return RelationshipInference(
                source_event_id=event_a.id,
                target_event_id=event_b.id,
                relationship_type=CareEventRelationshipType.ENABLES,
                is_temporal=True,
                is_causal=True,
                causal_evidence_ids=causal_evidence,
                temporal_relationship="temporal_precedence",
                time_gap_seconds=(
                    (event_b.event_time - event_a.event_time).total_seconds()
                    if event_a.event_time and event_b.event_time else None
                ),
                strength=0.75,
                confidence=0.7,
                detection_method="causal_inference",
                detection_confidence=0.7,
                inference_reasoning="Appointment enables medication start",
            )

        return None

    def _get_causal_pattern_key(self, event_type: CareEventType) -> str:
        """Get a pattern key for causal inference."""
        return event_type.value

    def _gather_causal_evidence(
        self,
        db: Session,
        event_a: CareEvent,
        event_b: CareEvent,
    ) -> List[str]:
        """Gather evidence that supports a causal relationship."""
        evidence_ids: List[str] = list(event_a.evidence_ids or [])
        evidence_ids.extend(event_b.evidence_ids or [])

        # Also check for evidence relationships between the events' evidence
        from app.models.evidence import EvidenceRelationship
        evidence_rels = db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id.in_(event_a.evidence_ids or []),
            EvidenceRelationship.target_evidence_id.in_(event_b.evidence_ids or []),
        ).all()

        for rel in evidence_rels:
            if rel.is_active:
                evidence_ids.extend([rel.source_evidence_id, rel.target_evidence_id])

        # Deduplicate
        return list(set(evidence_ids))

    def _infer_pattern_relationship(
        self,
        event_a: CareEvent,
        event_b: CareEvent,
    ) -> Optional[RelationshipInference]:
        """Infer pattern-based relationships (recurrence, dependency)."""
        # Person contamination check
        if event_a.person_id != event_b.person_id:
            return None

        # Check for recurrence patterns
        if (
            event_a.event_type == event_b.event_type
            and event_a.event_subtype == event_b.event_subtype
            and event_a.event_time and event_b.event_time
        ):
            time_diff = abs((event_b.event_time - event_a.event_time).total_seconds())

            # If events are similar type and temporally close, they may recur
            if 1 < time_diff / 3600 < 168:  # Between 1 hour and 1 week
                return RelationshipInference(
                    source_event_id=min(event_a.id, event_b.id),
                    target_event_id=max(event_a.id, event_b.id),
                    relationship_type=CareEventRelationshipType.RECURS_WITH,
                    is_temporal=True,
                    is_causal=False,
                    causal_evidence_ids=list(set(
                        (event_a.evidence_ids or []) + (event_b.evidence_ids or [])
                    )),
                    temporal_relationship="temporal_precedence",
                    time_gap_seconds=time_diff,
                    strength=0.6,
                    confidence=0.5,
                    detection_method="pattern_matching",
                    detection_confidence=0.5,
                    inference_reasoning="Same-type events suggest recurrence pattern",
                )

        return None

    def establish_relationships_batch(
        self,
        db: Session,
        person_id: str,
        event_ids: List[str],
        created_by: Optional[str] = None,
    ) -> List[CareEventRelationship]:
        """Establish all inferred relationships between a batch of events."""
        results: List[CareEventRelationship] = []

        for event_id in event_ids:
            inferences = self.infer_relationships(
                db,
                person_id,
                event_id=event_id,
            )
            for inference in inferences:
                try:
                    rel = self._create_relationship_from_inference(db, inference, created_by)
                    results.append(rel)
                except ValueError:
                    continue

        return results

    def _create_relationship_from_inference(
        self,
        db: Session,
        inference: RelationshipInference,
        created_by: Optional[str] = None,
    ) -> CareEventRelationship:
        """Create a relationship record from an inference."""
        # Check if relationship already exists
        existing = db.query(CareEventRelationship).filter(
            CareEventRelationship.source_event_id == inference.source_event_id,
            CareEventRelationship.target_event_id == inference.target_event_id,
            CareEventRelationship.relationship_type == inference.relationship_type,
        ).first()

        if existing and existing.is_active:
            return existing

        relationship = CareEventRelationship(
            source_event_id=inference.source_event_id,
            target_event_id=inference.target_event_id,
            relationship_type=inference.relationship_type,
            is_temporal=inference.is_temporal,
            is_causal=inference.is_causal,
            causal_evidence_ids=inference.causal_evidence_ids,
            temporal_relationship=inference.temporal_relationship,
            time_gap_seconds=inference.time_gap_seconds,
            strength=inference.strength,
            confidence=inference.confidence,
            evidence_ids=inference.causal_evidence_ids,
            detection_method=inference.detection_method,
            detection_confidence=inference.detection_confidence,
            created_by=created_by,
            created_by_system=created_by is None,
            is_active=True,
        )
        db.add(relationship)
        db.commit()
        db.refresh(relationship)

        return relationship

    def create_relationship(
        self,
        db: Session,
        source_event_id: str,
        target_event_id: str,
        relationship_type: CareEventRelationshipType,
        created_by: Optional[str] = None,
        is_temporal: bool = True,
        is_causal: bool = False,
        causal_evidence_ids: Optional[List[str]] = None,
        temporal_relationship: Optional[str] = None,
        time_gap_seconds: Optional[float] = None,
        strength: Optional[float] = None,
        confidence: Optional[float] = None,
        detection_method: Optional[str] = None,
        evidence_ids: Optional[List[str]] = None,
    ) -> CareEventRelationship:
        """Create a typed relationship between two events.

        Person-contamination prevention is enforced: both events must belong
        to the same person.
        """
        source = db.get(CareEvent, source_event_id)
        target = db.get(CareEvent, target_event_id)

        if not source:
            raise ValueError(f"Source event {source_event_id} not found")
        if not target:
            raise ValueError(f"Target event {target_event_id} not found")

        # Person contamination check
        if source.person_id != target.person_id:
            raise ValueError(
                f"Person contamination: source event belongs to person {source.person_id}, "
                f"target to person {target.person_id}"
            )

        relationship = CareEventRelationship(
            source_event_id=source_event_id,
            target_event_id=target_event_id,
            relationship_type=relationship_type,
            is_temporal=is_temporal,
            is_causal=is_causal,
            causal_evidence_ids=causal_evidence_ids or [],
            temporal_relationship=temporal_relationship,
            time_gap_seconds=time_gap_seconds,
            strength=strength,
            confidence=confidence,
            evidence_ids=evidence_ids or [],
            detection_method=detection_method,
            created_by=created_by,
            created_by_system=created_by is None,
            is_active=True,
        )
        db.add(relationship)
        db.commit()
        db.refresh(relationship)

        return relationship

    def get_event_relationships(
        self,
        db: Session,
        event_id: str,
        relationship_types: Optional[List[CareEventRelationshipType]] = None,
        include_inactive: bool = False,
    ) -> List[CareEventRelationship]:
        """Get all relationships for an event (both source and target)."""
        query = db.query(CareEventRelationship).filter(
            (CareEventRelationship.source_event_id == event_id)
            | (CareEventRelationship.target_event_id == event_id)
        )

        if relationship_types:
            type_values = [r.value if hasattr(r, 'value') else r for r in relationship_types]
            query = query.filter(CareEventRelationship.relationship_type.in_(type_values))

        if not include_inactive:
            query = query.filter(CareEventRelationship.is_active == True)

        return query.all()

    def get_relationship_graph(
        self,
        db: Session,
        person_id: str,
        event_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get the full relationship graph for a person's events."""
        query = db.query(CareEvent).filter(
            CareEvent.person_id == person_id,
            CareEvent.lifecycle_stage != CareEventStatus.INVALIDATED.value,
        )

        if event_ids:
            query = query.filter(CareEvent.id.in_(event_ids))

        events = query.all()
        event_id_set = {e.id for e in events}

        # Get all relationships
        rels = db.query(CareEventRelationship).filter(
            CareEventRelationship.source_event_id.in_(event_id_set)
            | CareEventRelationship.target_event_id.in_(event_id_set),
            CareEventRelationship.is_active == True,
        ).all()

        temporal_edges = []
        causal_edges = []
        for rel in rels:
            edge = {
                "source": rel.source_event_id,
                "target": rel.target_event_id,
                "type": rel.relationship_type.value,
                "confidence": rel.confidence,
                "strength": rel.strength,
                "time_gap_seconds": rel.time_gap_seconds,
                "detection_method": rel.detection_method,
            }
            if rel.is_causal:
                causal_edges.append(edge)
            if rel.is_temporal:
                temporal_edges.append(edge)

        return {
            "nodes": [e.id for e in events],
            "temporal_edges": temporal_edges,
            "causal_edges": causal_edges,
            "total_events": len(events),
            "total_relationships": len(rels),
        }
