"""Service for identifying gaps in insurance coverage and documentation needed using AI-assisted reasoning.
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


class InsuranceNavigationHelpReasoning:
    """Identifies gaps in insurance coverage and documentation needed for care services.
    
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
        - The task is not FOLLOW_UP_RATIONALE (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.FOLLOW_UP_RATIONALE:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Extract insurance-related parameters
        insurance_types = payload.get("insurance_types", [])  # e.g., ["health", "long_term_care", "medicare", "medicaid"]
        services_to_check = payload.get("services_to_check", [])  # e.g., ["home_health", "respite", "therapy", "equipment"]
        documentation_needed = payload.get("documentation_needed", [])  # e.g., ["prescription", "doctor_note", "assessment"]
        time_period = payload.get("time_period", "last_60_days")
        
        # Gather relevant data for insurance navigation help
        try:
            # Get care plan for current services and needs
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.person_id == person_id
            ).first()
            
            # Get recent outcomes to see what services are being used
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
            
            # Get recent events to see care patterns and potential service needs
            recent_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get medications that might require specific coverage
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
            "insurance_types": insurance_types,
            "services_to_check": services_to_check,
            "documentation_needed": documentation_needed,
            "time_period": time_period,
            "care_plan": {
                "id": care_plan.id if care_plan else None,
                "title": getattr(care_plan, 'title', None) if care_plan else None,
                "services_included": getattr(care_plan, 'services_included', []) if care_plan else [],
                "estimated_costs": getattr(care_plan, 'estimated_costs', None) if care_plan else None,
                "coverage_notes": getattr(care_plan, 'coverage_notes', None) if care_plan else None,
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
            "current_coverage": payload.get("current_coverage", {}),  # e.g., {"medicare": "part_b", "supplemental": "plan_f"}
            "known_gaps": payload.get("known_gaps", []),  # e.g., ["dental", "vision", "hearing_aids"]
        }
         
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.FOLLOW_UP_RATIONALE,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "InsuranceNavigation",
                subject_id=subject_id or person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Insurance navigation help is not diagnosis
                allow_medial_action=False,         # This is administrative, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
             
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.FOLLOW_UP_RATIONALE]