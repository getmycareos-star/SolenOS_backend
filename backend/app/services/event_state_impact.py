"""Event-to-State Impact Service.

Establishes explicit event-to-state contracts with reversibility.
Validates state impacts against the state engine where available.

Architecture:
  - Rule-based impact inference from event type
  - Evidence-backed impact validation
  - Explicit contract between event and state
  - Reversibility support (impacts can be revoked)
  - Validation against downstream state changes
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from enum import Enum
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    EventStateImpactType,
    EventStateImpactConfidence,
    EventSubjectRole,
)
from app.core.evidence_enums import (
    EvidenceType,
    EvidenceStatus,
    NegationStatus,
    UncertaintyLevel,
)
from app.models.care_event import (
    CareEvent,
    CareEventStateImpact,
)


@dataclass
class StateImpactInference:
    """Inferred state impact from an event."""
    state_type: str
    impact_type: EventStateImpactType
    impact_confidence: EventStateImpactConfidence
    impact_details: Optional[Dict[str, Any]]
    supporting_evidence_ids: List[str]
    supporting_care_event_ids: List[str]
    reasoning_rule: str
    reasoning_confidence: float
    temporal_relationship: Optional[str]
    time_lag_days: Optional[float]


# Rule definitions: event_type -> list of state impact rules
STATE_IMPACT_RULES: Dict[CareEventType, List[Dict[str, Any]]] = {
    CareEventType.FALL_EVENT: [
        {
            "state_type": "mobility",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "fall_implies_mobility_change",
            "reasoning_confidence": 0.7,
        },
        {
            "state_type": "injury_status",
            "impact_type": EventStateImpactType.CREATES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "fall_implies_injury_risk",
            "reasoning_confidence": 0.5,
        },
    ],
    CareEventType.HOSPITALIZATION_EVENT: [
        {
            "state_type": "care_intensity",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "hospitalization_implies_care_intensity_increase",
            "reasoning_confidence": 0.9,
        },
        {
            "state_type": "location_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "hospitalization_implies_location_change",
            "reasoning_confidence": 0.95,
        },
    ],
    CareEventType.MEDICATION_START_EVENT: [
        {
            "state_type": "medication_regimen",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "medication_start_implies_regimen_change",
            "reasoning_confidence": 0.95,
        },
    ],
    CareEventType.MEDICATION_STOP_EVENT: [
        {
            "state_type": "medication_regimen",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "medication_stop_implies_regimen_change",
            "reasoning_confidence": 0.95,
        },
    ],
    CareEventType.MEDICATION_MISSED_EVENT: [
        {
            "state_type": "medication_adherence",
            "impact_type": EventStateImpactType.CONTRADICTS,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "missed_dose_implies_adherence_decrease",
            "reasoning_confidence": 0.9,
        },
    ],
    CareEventType.MEDICATION_REFUSED_EVENT: [
        {
            "state_type": "medication_adherence",
            "impact_type": EventStateImpactType.CONTRADICTS,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "refused_dose_implies_adherence_decrease",
            "reasoning_confidence": 0.9,
        },
    ],
    CareEventType.MEDICATION_TAKEN_EVENT: [
        {
            "state_type": "medication_adherence",
            "impact_type": EventStateImpactType.SUPPORTS,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "med_taken_supports_adherence",
            "reasoning_confidence": 0.9,
        },
    ],
    CareEventType.MEDICATION_DOSE_CHANGE_EVENT: [
        {
            "state_type": "medication_regimen",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "dose_change_implies_regimen_change",
            "reasoning_confidence": 0.9,
        },
    ],
    CareEventType.SYMPTOM_EVENT: [
        {
            "state_type": "symptom_status",
            "impact_type": EventStateImpactType.CREATES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "symptom_report_implies_symptom_state",
            "reasoning_confidence": 0.7,
        },
    ],
    CareEventType.OBSERVATION_EVENT: [
        {
            "state_type": "cognitive_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "observation_implies_state_change",
            "reasoning_confidence": 0.5,
        },
    ],
    CareEventType.COGNITIVE_OBSERVATION: [
        {
            "state_type": "cognitive_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "cognitive_observation_implies_cognitive_change",
            "reasoning_confidence": 0.6,
        },
    ],
    CareEventType.MOBILITY_OBSERVATION: [
        {
            "state_type": "mobility_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "mobility_observation_implies_mobility_change",
            "reasoning_confidence": 0.6,
        },
    ],
    CareEventType.SLEEP_OBSERVATION: [
        {
            "state_type": "sleep_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "sleep_observation_implies_sleep_change",
            "reasoning_confidence": 0.5,
        },
    ],
    CareEventType.APPETITE_OBSERVATION: [
        {
            "state_type": "nutrition_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "appetite_observation_implies_nutrition_change",
            "reasoning_confidence": 0.5,
        },
    ],
    CareEventType.BEHAVIOURAL_OBSERVATION: [
        {
            "state_type": "behavioral_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "behavioral_observation_implies_behavior_change",
            "reasoning_confidence": 0.5,
        },
    ],
    CareEventType.ED_VISIT_EVENT: [
        {
            "state_type": "acute_care_status",
            "impact_type": EventStateImpactType.CREATES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "ed_visit_implies_acute_care_need",
            "reasoning_confidence": 0.85,
        },
    ],
    CareEventType.SURGERY_EVENT: [
        {
            "state_type": "procedure_status",
            "impact_type": EventStateImpactType.CREATES,
            "confidence": EventStateImpactConfidence.SUPPORTED,
            "reasoning_rule": "surgery_implies_procedure_state",
            "reasoning_confidence": 0.9,
        },
    ],
    CareEventType.THERAPY_EVENT: [
        {
            "state_type": "functional_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "therapy_implies_functional_change",
            "reasoning_confidence": 0.6,
        },
    ],
    CareEventType.CARE_ACTION_EVENT: [
        {
            "state_type": "care_activity_status",
            "impact_type": EventStateImpactType.CHANGES,
            "confidence": EventStateImpactConfidence.POSSIBLE,
            "reasoning_rule": "care_action_implies_activity_change",
            "reasoning_confidence": 0.5,
        },
    ],
}

# State type hierarchy: dependent states that are affected by a state change
STATE_TYPE_DEPENDENCIES: Dict[str, List[str]] = {
    "medication_regimen": ["medication_adherence", "treatment_response"],
    "mobility_status": ["fall_risk", "functional_status"],
    "cognitive_status": ["care_decision_making", "safety_status"],
    "sleep_status": ["cognitive_status", "behavioral_status"],
    "nutrition_status": ["weight_status", "energy_level"],
    "care_intensity": ["staffing_needs", "resource_allocation"],
    "symptom_status": ["quality_of_life", "comfort_level"],
}


class EventStateImpactService:
    """Manages event-to-state impact relationships.

    Creates explicit, evidence-backed contracts between events and state changes.
    Impacts are validated, reversible, and propagated.
    """

    def __init__(self):
        pass

    def infer_state_impacts(
        self,
        db: Session,
        event: CareEvent,
    ) -> List[StateImpactInference]:
        """Infer state impacts for an event based on rules and evidence."""
        rules = STATE_IMPACT_RULES.get(event.event_type, [])
        if not rules:
            return []

        inferences: List[StateImpactInference] = []
        evidence_ids = list(set(event.evidence_ids or []))

        # Gather supporting evidence from the event's evidence
        supporting_evidence = self._validate_evidence_support(db, event, rules)

        for rule in rules:
            # Check if evidence supports this impact
            evidence_confidence = self._calculate_evidence_confidence(
                event, evidence_ids, rule["reasoning_confidence"]
            )

            # Adjust confidence based on evidence support
            final_confidence = self._adjust_confidence(
                rule["reasoning_confidence"],
                evidence_confidence,
                event.confidence if hasattr(event, 'confidence') and event.confidence else None,
            )

            # Determine temporal relationship
            temporal_rel = "during" if event.event_time else None
            time_lag = 0.0  # Events and their direct impacts are simultaneous

            inference = StateImpactInference(
                state_type=rule["state_type"],
                impact_type=rule["impact_type"],
                impact_confidence=rule["confidence"],
                impact_details={
                    "reasoning_rule": rule["reasoning_rule"],
                    "confidence": evidence_confidence,
                },
                supporting_evidence_ids=evidence_ids,
                supporting_care_event_ids=[event.id],
                reasoning_rule=rule["reasoning_rule"],
                reasoning_confidence=round(final_confidence, 2),
                temporal_relationship=temporal_rel,
                time_lag_days=time_lag,
            )
            inferences.append(inference)

        # Propagate to dependent states
        propagated = self._propagate_to_dependent_states(inferences, rules)
        inferences.extend(propagated)

        return inferences

    def _validate_evidence_support(
        self,
        db: Session,
        event: CareEvent,
        rules: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """Validate that supporting evidence exists for each rule."""
        from app.models.evidence import Evidence

        evidence_map = {}
        if event.evidence_ids:
            evidence_items = db.query(Evidence).filter(
                Evidence.id.in_(event.evidence_ids),
                Evidence.status == EvidenceStatus.ACTIVE,
            ).all()
            evidence_map = {e.id: e for e in evidence_items}

        # Check evidence quality for each rule
        support = {}
        for rule in rules:
            rule_key = rule["state_type"]
            if event.evidence_ids:
                active_evidence_count = sum(
                    1 for eid in event.evidence_ids
                    if eid in evidence_map and evidence_map[eid].quality_gate_passed
                )
                support[rule_key] = active_evidence_count / len(event.evidence_ids) if event.evidence_ids else 0.5
            else:
                support[rule_key] = 0.5

        return support

    def _calculate_evidence_confidence(
        self,
        event: CareEvent,
        evidence_ids: List[str],
        base_confidence: float,
    ) -> float:
        """Calculate confidence based on evidence quality."""
        if not evidence_ids:
            return 0.5

        # Base on event's confidence dimensions
        if hasattr(event, 'confidence') and event.confidence:
            if isinstance(event.confidence, dict):
                return event.confidence.get('overall', 0.5) or 0.5
            elif hasattr(event.confidence, 'overall'):
                return event.confidence.overall or 0.5

        return base_confidence

    def _adjust_confidence(
        self,
        reasoning_confidence: float,
        evidence_confidence: float,
        event_confidence: Optional[float],
    ) -> float:
        """Adjust confidence based on evidence quality."""
        # Weight: reasoning 40%, evidence 40%, event 20%
        weights = [0.4, 0.4, 0.2]
        values = [reasoning_confidence, evidence_confidence, event_confidence or 0.5]
        return sum(v * w for v, w in zip(values, weights))

    def _propagate_to_dependent_states(
        self,
        inferences: List[StateImpactInference],
        rules: List[Dict[str, Any]],
    ) -> List[StateImpactInference]:
        """Propagate state impacts to dependent state types."""
        propagated: List[StateImpactInference] = []

        for inference in inferences:
            dependent_states = STATE_TYPE_DEPENDENCIES.get(inference.state_type, [])
            for dep_state in dependent_states:
                # Create propagated impact with lower confidence
                propagated_inference = StateImpactInference(
                    state_type=dep_state,
                    impact_type=EventStateImpactType.CHANGES,
                    impact_confidence=inference.impact_confidence,
                    impact_details={
                        "propagated_from": inference.state_type,
                        "propagation_reasoning": f"State {inference.state_type} affects {dep_state}",
                    },
                    supporting_evidence_ids=[],
                    supporting_care_event_ids=[],
                    reasoning_rule=f"{inference.reasoning_rule}_propagated",
                    reasoning_confidence=inference.reasoning_confidence * 0.7,  # Reduce confidence for propagated
                    temporal_relationship=inference.temporal_relationship,
                    time_lag_days=(inference.time_lag_days or 0) + 0.5,
                )
                propagated.append(propagated_inference)

        return propagated

    def create_state_impact(
        self,
        db: Session,
        event_id: str,
        state_type: str,
        state_subject_id: Optional[str],
        state_subject_role: EventSubjectRole,
        impact_type: EventStateImpactType,
        impact_confidence: EventStateImpactConfidence,
        impact_details: Optional[Dict[str, Any]] = None,
        supporting_evidence_ids: Optional[List[str]] = None,
        supporting_care_event_ids: Optional[List[str]] = None,
        reasoning_rule: Optional[str] = None,
        reasoning_confidence: Optional[float] = None,
        temporal_relationship: Optional[str] = None,
        time_lag_days: Optional[float] = None,
    ) -> CareEventStateImpact:
        """Create a state impact record for a care event."""
        event = db.get(CareEvent, event_id)
        if not event:
            raise ValueError(f"CareEvent {event_id} not found")

        impact = CareEventStateImpact(
            care_event_id=event_id,
            state_type=state_type,
            state_subject_id=state_subject_id or event.subject_id or event.person_id,
            state_subject_role=state_subject_role,
            impact_type=impact_type,
            impact_confidence=impact_confidence,
            impact_details=impact_details,
            supporting_evidence_ids=supporting_evidence_ids or [],
            supporting_care_event_ids=supporting_care_event_ids or [],
            reasoning_rule=reasoning_rule,
            reasoning_confidence=reasoning_confidence,
            temporal_relationship=temporal_relationship,
            time_lag_days=time_lag_days,
            validated_by_state_engine=False,
            is_revoked=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(impact)
        db.commit()
        db.refresh(impact)

        return impact

    def revoke_state_impact(
        self,
        db: Session,
        impact_id: str,
        revoked_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> CareEventStateImpact:
        """Revoke a state impact (mark as invalid)."""
        impact = db.get(CareEventStateImpact, impact_id)
        if not impact:
            raise ValueError(f"State impact {impact_id} not found")

        impact.is_revoked = True
        impact.revoked_at = datetime.now(timezone.utc)

        # Mark dependent state impacts for review
        db.commit()
        db.refresh(impact)

        return impact

    def supersede_state_impact(
        self,
        db: Session,
        old_impact_id: str,
        new_impact: CareEventStateImpact,
    ) -> CareEventStateImpact:
        """Supersede an old state impact with a new one."""
        old_impact = db.get(CareEventStateImpact, old_impact_id)
        if not old_impact:
            raise ValueError(f"State impact {old_impact_id} not found")

        # Mark old as superseded
        old_impact.is_revoked = True
        old_impact.revoked_at = datetime.now(timezone.utc)
        old_impact.superseded_by_impact_id = new_impact.id

        db.commit()

        return old_impact

    def validate_state_impact(
        self,
        db: Session,
        impact_id: str,
        validated: bool = True,
        validation_notes: Optional[str] = None,
    ) -> CareEventStateImpact:
        """Mark a state impact as validated by the state engine."""
        impact = db.get(CareEventStateImpact, impact_id)
        if not impact:
            raise ValueError(f"State impact {impact_id} not found")

        impact.validated_by_state_engine = validated
        impact.state_engine_validation_time = datetime.now(timezone.utc)
        impact.state_engine_validation_notes = validation_notes

        db.commit()
        db.refresh(impact)

        return impact

    def get_event_state_impacts(
        self,
        db: Session,
        event_id: str,
        include_revoked: bool = False,
    ) -> List[CareEventStateImpact]:
        """Get all state impacts for an event."""
        query = db.query(CareEventStateImpact).filter(
            CareEventStateImpact.care_event_id == event_id,
        )
        if not include_revoked:
            query = query.filter(CareEventStateImpact.is_revoked == False)
        return query.all()

    def get_state_type_impacts(
        self,
        db: Session,
        state_type: str,
        state_subject_id: Optional[str] = None,
        include_revoked: bool = False,
    ) -> List[CareEventStateImpact]:
        """Get all impacts affecting a specific state type."""
        query = db.query(CareEventStateImpact).filter(
            CareEventStateImpact.state_type == state_type,
        )
        if state_subject_id:
            query = query.filter(CareEventStateImpact.state_subject_id == state_subject_id)
        if not include_revoked:
            query = query.filter(CareEventStateImpact.is_revoked == False)
        return query.all()

    def calculate_event_to_state_confidence(
        self,
        event: CareEvent,
        evidence: List,
    ) -> float:
        """Calculate overall confidence that an event impacts a state."""
        if not evidence:
            return 0.3

        # Average evidence confidence
        confidences = [
            e.confidence_overall or e.confidence_source_support or 0.5
            for e in evidence
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        # Adjust for event status
        if event.status == CareEventStatus.INVALIDATED:
            return 0.0
        if event.lifecycle_stage == EventLifecycleStage.SUPERSEDED:
            return avg_confidence * 0.5

        return round(avg_confidence, 2)
