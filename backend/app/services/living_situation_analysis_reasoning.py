"""Service for analyzing living situation and environmental factors affecting care.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.models.care_planning import CarePlan as CarePlanModel
from app.models.medication import Medication as MedicationModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class LivingSituationAnalysisReasoning:
    """Analyzes living situation and environmental factors affecting care.
    
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
        - The task is not PERSON_PROFILE (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.PERSON_PROFILE:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Gather relevant contextual data for living situation analysis
        try:
            # Get recent evidence mentioning living situation/environment
            recent_evidence = []
            if evidence_ids:
                recent_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                # If no specific evidence IDs, get recent evidence for the person
                recent_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id
                ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
                
            # Get recent care events
            recent_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get recent outcomes
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
            
            # Get current care plan
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.person_id == person_id
            ).first()
            
            # Get current medications
            medications = self.db.query(MedicationModel).filter(
                MedicationModel.person_id == person_id
            ).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            recent_evidence = []
            recent_events = []
            recent_outcomes = []
            care_plan = None
            medications = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "person_info": {
                "id": person.id,
                "name": getattr(person, 'name', None),
                "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
                "address": getattr(person, 'address', None),
                "living_arrangement": getattr(person, 'living_arrangement', None),
                "primary_language": getattr(person, 'primary_language', None),
            },
            "recent_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:500] if e.source_text else None,  # More text for environmental analysis
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
                "environmental_notes": getattr(care_plan, 'environmental_notes', None) if care_plan else None,
            } if care_plan else None,
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
            "analysis_focus": payload.get("analysis_focus", [
                "physical_accessibility",
                "safety_hazards",
                "social_support",
                "access_to_services",
                "environmental_stressors",
                "caregiver_living_arrangement"
            ]),
            "time_period": payload.get("time_period", "last_30_days"),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.PERSON_PROFILE,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "LivingSituation",
                subject_id=subject_id or person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Living situation analysis is not diagnostic
                allow_medical_action=False,         # This is observational, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.PERSON_PROFILE]