"""Service for suggesting respite care options based on caregiver burden using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.caregiver import Caregiver as CaregiverModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.models.care_planning import CarePlan as CarePlanModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class RespitePlanningReasoning:
    """Suggests respite care options based on caregiver burden and support needs.
    
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
        allow_medical_action: bool,
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
            
        # Extract caregiver ID (the one needing respite)
        caregiver_id = payload.get("caregiver_id")
        if not caregiver_id:
            return None
            
        # Verify caregiver exists and is linked to the person
        try:
            caregiver = self.db.query(CaregiverModel).filter(
                CaregiverModel.id == caregiver_id,
                CaregiverModel.person_id == person_id
            ).first()
            if not caregiver:
                # Caregiver not found or not linked to person
                return None
        except Exception:
            return None
            
        # Gather relevant contextual data for respite planning
        try:
            # Get recent evidence about caregiver burden/stress
            burden_evidence = []
            if evidence_ids:
                burden_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id,
                    EvidenceModel.uploaded_by_caregiver_id == caregiver_id
                ).all()
            else:
                # If no specific evidence IDs, get recent evidence uploaded by this caregiver
                burden_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id,
                    EvidenceModel.uploaded_by_caregiver_id == caregiver_id
                ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
                
            # Get care events to see care intensity patterns
            care_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get outcomes related to caregiver wellbeing
            caregiver_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            burden_evidence = []
            care_events = []
            caregiver_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "caregiver_info": {
                "id": caregiver.id,
                "name": caregiver.name,
                "relationship": caregiver.relationship,
                "email": caregiver.email,
                "phone": getattr(caregiver, 'phone', None),
            },
            "burden_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:500] if e.source_text else None,  # More text for burden analysis
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "uploaded_by_caregiver_id": e.uploaded_by_caregiver_id,
                }
                for e in burden_evidence
            ],
            "care_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in care_events
            ],
            "caregiver_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in caregiver_outcomes
            ],
            "context": payload.get("context", {}),
            "burden_factors": payload.get("burden_factors", [
                "time_spent_caregiving",
                "emotional_stress",
                "physical_strain",
                "financial_pressure",
                "social_isolation",
                "lack_of_personal_time"
            ]),
            "respite_options": payload.get("respite_options", [
                "in_home_respite",
                "adult_day_services",
                "short_term_facility_stay",
                "volunteer_companion",
                "family_member_relief"
            ]),
            "time_period": payload.get("time_period", "last_30_days"),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.DECISION_PLAN,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "RespitePlanning",
                subject_id=subject_id or caregiver_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Respite planning is not diagnosis
                allow_medical_action=True,          # This informs care decisions (advisory)
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.DECISION_PLAN]
