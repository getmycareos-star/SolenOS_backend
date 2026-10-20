"""Service for generating AI-assisted treatment recommendations.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.medication import Medication as MedicationModel
from app.models.care_planning import CarePlan as CarePlanModel
from app.models.outcome import Outcome as OutcomeModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class TreatmentRecommendationReasoning:
    """Generates treatment recommendations using AI reasoning.
    
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
        - The task is not DECISION_OPTIONS (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.DECISION_OPTIONS:
            return None
            
        # Extract condition or symptoms to treat
        condition = payload.get("condition")
        symptoms = payload.get("symptoms", [])
        goals = payload.get("treatment_goals", [])
        if not condition and not symptoms and not goals:
            # Need some input to analyze
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Get current medications to avoid duplicate or conflicting suggestions
        try:
            current_medications = self.db.query(MedicationModel).filter(
                MedicationModel.person_id == person_id,
                MedicationModel.is_active == True  # Assuming there's an is_active field
            ).all()
        except Exception:
            current_medications = []
            
        # Get care plan context if available
        care_plan_id = payload.get("care_plan_id")
        care_plan = None
        if care_plan_id:
            try:
                care_plan = self.db.query(CarePlanModel).filter(
                    CarePlanModel.id == care_plan_id,
                    CarePlanModel.person_id == person_id
                ).first()
            except Exception:
                care_plan = None
                
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
            
            # Get recent outcomes
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            recent_evidence = []
            recent_events = []
            recent_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "condition": condition,
            "symptoms": symptoms,
            "treatment_goals": goals,
            "current_medications": [
                {
                    "id": m.id,
                    "name": m.name,
                    "dosage": m.dosage,
                    "frequency": m.frequency,
                }
                for m in current_medications
            ],
            "care_plan": {
                "id": care_plan.id if care_plan else None,
                "title": getattr(care_plan, 'title', None) if care_plan else None,
                "description": getattr(care_plan, 'description', None) if care_plan else None,
            } if care_plan else None,
            "recent_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:300] if e.source_text else None,
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
            "context": payload.get("context", {}),
            "urgency": payload.get("urgency", "routine"),
            "exclude_medications": payload.get("exclude_medications", []),  # List of medication IDs to avoid
            "prefer_non_pharmacological": payload.get("prefer_non_pharmacological", False),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.DECISION_OPTIONS,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "TreatmentRecommendation",
                subject_id=subject_id or (condition or ",".join(symptoms)[:100]),
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Treatment recs are not diagnostic
                allow_medical_action=True,          # This IS a medical action (advisory)
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.DECISION_OPTIONS]