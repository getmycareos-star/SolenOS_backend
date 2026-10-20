"""Service for identifying caregiver training gaps using AI-assisted reasoning.
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
from app.models.care_planning import CarePlan as CarePlanModel
from app.models.outcome import Outcome as OutcomeModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class TrainingGapIdentificationReasoning:
    """Identifies areas for caregiver education based on patient needs and care context.
    
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
        - The task is not FOLLOW_UP_RATIONALE (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.FOLLOW_UP_RATIONALE:
            return None
            
        # Extract caregiver ID
        caregiver_id = payload.get("caregiver_id")
        if not caregiver_id:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
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
            
        # Gather relevant contextual data
        try:
            # Get recent evidence
            recent_evidence = []
            if evidence_ids:
                recent_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                recent_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id
                ).order_by(EvidenceModel.created_at.desc()).limit(10).all()
                
            # Get recent care events
            recent_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get recent outcomes to see what's working/not working
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
            
            # Get current care plan
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.person_id == person_id,
                CarePlanModel.is_active == True  # Assuming there's an is_active field
            ).first()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            recent_evidence = []
            recent_events = []
            recent_outcomes = []
            care_plan = None
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "caregiver_id": caregiver_id,
            "caregiver_info": {
                "id": caregiver.id,
                "name": caregiver.name,
                "relationship": caregiver.relationship,
                "email": caregiver.email,
                "training_completed": getattr(caregiver, 'training_completed', []),
            },
            "recent_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:400] if e.source_text else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in recent_evidence
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
            "care_plan": {
                "id": care_plan.id if care_plan else None,
                "title": getattr(care_plan, 'title', None) if care_plan else None,
                "goals": getattr(care_plan, 'goals', []) if care_plan else [],
                "required_skills": getattr(care_plan, 'required_skills', []) if care_plan else [],
            } if care_plan else None,
            "context": payload.get("context", {}),
            "specific_concerns": payload.get("specific_concerns", []),
            "time_period": payload.get("time_period", "last_60_days"),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.FOLLOW_UP_RATIONALE,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type="CaregiverTraining",
                subject_id=caregiver_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Training gap ID is not diagnostic
                allow_medical_action=False,         # This is educational, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.FOLLOW_UP_RATIONALE]