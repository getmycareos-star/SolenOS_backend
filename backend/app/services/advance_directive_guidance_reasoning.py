"""Service for suggesting considerations for advance directives (living wills, power of attorney) using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.models.care_planning import CarePlan as CarePlanModel
from app.models.medication import Medication as MedicationModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class AdvanceDirectiveGuidanceReasoning:
    """Suggests considerations for advance directives based on patient condition, preferences, and context.
    
    This service is designed to be used via LocalReasoningEngine which provides:
    - Circuit breaker protection
    - Audit logging
    - Fallback to deterministic answers
    - TrustGuard validation of all outputs
    """
    
    def __init__(self, db: Session, llm_engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self._llm_engine = llm_engine or LocalReasoningEngine(db)
        
    def reason(
        self,
        task: LLMTask,
        person_id: str,
        payload: Dict[str, Any],
        evidence_ids: List[str],
        subject_type: Optional[str],
        subject_id: Optional[str],
        mode: LLMReasoningMode,
        allow_diagnostic: bool,
        allow_medial_action: bool,
        require_evidence: bool,
    ) -> Optional[LLMReasoningResult]:
        """Main reasoning method called by LocalReasoningEngine.
        
        Returns None if:
        - The task is not DECISION_PLAN (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.DECISION_PLAN:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Extract advance directive parameters
        directive_types = payload.get("directive_types", [])  # e.g., ["living_will", "power_of_attorney", "do_not_resuscitate"]
        urgency = payload.get("urgency", "routine")  # routine, soon, urgent
        constraints = payload.get("constraints", [])  # e.g., ["family_disagreement", "legal_restrictions", "cultural_factors"]
        look_ahead_days = payload.get("look_ahead_days", 365)  # How far ahead to consider (typically annual review)
        
        # Gather relevant data for advance directive guidance
        try:
            # Get care plan for goals and current situation
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.person_id == person_id
            ).first()
            
            # Get recent outcomes to see trajectory
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
            
            # Get recent events to see health changes
            recent_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get medications that might affect decision capacity
            medications = self.db.query(MedicationModel).filter(
                MedicationModel.person_id == person_id
            ).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            care_plan = None
            recent_outcomes = []
            recent_events = []
            medications = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "directive_types": directive_types,
            "urgency": urgency,
            "constraints": constraints,
            "look_ahead_days": look_ahead_days,
            "care_plan": {
                "id": care_plan.id if care_plan else None,
                "title": getattr(care_plan, 'title', None) if care_plan else None,
                "goals": getattr(care_plan, 'goals', []) if care_plan else [],
                "current_status": getattr(care_plan, 'status', None) if care_plan else None,
                "decision_making_notes": getattr(care_plan, 'decision_making_notes', None) if care_plan else None,
            } if care_plan else None,
            "recent_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in recent_outcomes
            ],
            "recent_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in recent_events
            ],
            "medications": [
                {
                    "id": m.id,
                    "name": m.name,
                    "dosage": m.dosage,
                    "frequency": m.frequency,
                    "start_date": m.start_date.isoformat() if m.start_date else None,
                }
                for m in medications
            ],
            "context": payload.get("context", {}),
            "person_info": {
                "id": person.id,
                "name": getattr(person, 'name', None),
                "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
                "decision_capacity": getattr(person, 'decision_capacity', None),  # e.g., "capacitated", "questionable", "incapacitated"
                "cultural_background": getattr(person, 'cultural_background', None),
                "religious_affiliation": getattr(person, 'religious_affiliation', None),
            },
            "consideration_factors": payload.get("consideration_factors", [
                "medical_prognosis",
                "quality_of_life_views",
                "family_dynamics",
                "financial_implications",
                "legal_requirements",
                "spiritual_beliefs"
            ]),
            "time_period": payload.get("time_period", "last_60_days"),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.DECISION_PLAN,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "AdvanceDirective",
                subject_id=subject_id or person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Advance directive guidance is not diagnosis
                allow_medial_action=True,          # This informs legal/medical decisions (advisory)
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.DECISION_PLAN]
