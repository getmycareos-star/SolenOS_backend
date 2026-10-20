"""Service for optimizing care plans using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.care_planning import CarePlan as CarePlanModel
from app.models.identity import Person
from app.models.outcome import Outcome as OutcomeModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class CarePlanOptimizationReasoning:
    """Analyzes and suggests optimizations for care plans using AI reasoning.
    
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
            
        # Extract care plan ID from payload
        care_plan_id = payload.get("care_plan_id")
        if not care_plan_id:
            return None
            
        # Verify person exists and care plan belongs to them
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
                
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.id == care_plan_id,
                CarePlanModel.person_id == person_id
            ).first()
            if not care_plan:
                # Care plan not found or doesn't belong to person
                return None
                
        except Exception:
            # Database error - fail safe
            return None
            
        # Gather contextual data for optimization analysis
        try:
            # Get recent outcomes related to this care plan
            recent_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
            
            # Get recent care events
            recent_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(20).all()
            
            # Get recent evidence
            recent_evidence = self.db.query(EvidenceModel).filter(
                EvidenceModel.person_id == person_id
            ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
            
        except Exception:
            # If we can't gather context, still proceed with what we have
            recent_outcomes = []
            recent_events = []
            recent_evidence = []
            
        # Prepare payload for LLM
        plan_details = {
            "id": care_plan.id,
            "title": getattr(care_plan, 'title', None),
            "description": getattr(care_plan, 'description', None),
            "status": getattr(care_plan, 'status', None),
            "priority_level": getattr(care_plan, 'priority_level', None),
            "created_at": care_plan.created_at.isoformat() if care_plan.created_at else None,
            "updated_at": care_plan.updated_at.isoformat() if care_plan.updated_at else None,
        }
        
        outcomes_summary = []
        for outcome in recent_outcomes[:5]:  # Limit to most recent 5
            outcomes_summary.append({
                "id": outcome.id,
                "expectation_description": outcome.expectation_description,
                "actual_outcome_description": outcome.actual_outcome_description,
                "outcome_status": outcome.outcome_status,
                "comparison_result": outcome.comparison_result,
                "recorded_at": outcome.recorded_at.isoformat() if outcome.recorded_at else None,
            })
            
        events_summary = []
        for event in recent_events[:10]:  # Limit to most recent 10
            events_summary.append({
                "id": event.id,
                "event_type": event.event_type,
                "title": event.title,
                "description": event.description,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            })
            
        evidence_summary = []
        for evidence in recent_evidence[:8]:  # Limit to most recent 8
            evidence_summary.append({
                "id": evidence.id,
                "type": evidence.type,
                "source_text": evidence.source_text[:200] if evidence.source_text else None,  # Truncate for brevity
                "created_at": evidence.created_at.isoformat() if evidence.created_at else None,
            })
            
        llm_payload = {
            "person_id": person_id,
            "care_plan": plan_details,
            "recent_outcomes": outcomes_summary,
            "recent_events": events_summary,
            "recent_evidence": evidence_summary,
            "optimization_goals": payload.get("optimization_goals", [
                "improve_outcome_achievement",
                "reduce_burden_on_caregiver", 
                "increase_patient_wellbeing",
                "address_identified_gaps"
            ]),
            "constraints": payload.get("constraints", []),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.DECISION_PLAN,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type="CarePlan",
                subject_id=care_plan_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Care plan optimization is not diagnostic
                allow_medical_action=True,          # This IS a medical action (advisory)
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.DECISION_PLAN]