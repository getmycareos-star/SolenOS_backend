"""Service for analyzing handoff quality using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.handoff import Handoff as HandoffModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class HandoffQualityAnalysisReasoning:
    """Analyzes the effectiveness of care handoffs using AI reasoning.
    
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
        - The task is not CONTINUITY_HANDOFF (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.CONTINUITY_HANDOFF:
            return None
            
        # Extract handoff ID or parameters
        handoff_id = payload.get("handoff_id")
        # If not provided, we might look for recent handoffs, but for now require it
        if not handoff_id:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Verify handoff exists and is linked to the person
        try:
            handoff = self.db.query(HandoffModel).filter(
                HandoffModel.id == handoff_id,
                HandoffModel.person_id == person_id
            ).first()
            if not handoff:
                # Handoff not found or not linked to person
                return None
        except Exception:
            return None
            
        # Gather relevant contextual data for handoff analysis
        try:
            # Get evidence related to the handoff
            handoff_evidence = []
            if evidence_ids:
                handoff_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                # If no specific evidence IDs, get evidence associated with the handoff
                handoff_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id,
                    EvidenceModel.handoff_id == handoff_id  # Assuming there's a handoff_id foreign key
                ).all()
                
            # Get care events around the handoff time
            handoff_time = handoff.created_at if hasattr(handoff, 'created_at') else None
            time_window_hours = 24  # Look 24 hours before and after
            start_time = handoff_time - timedelta(hours=time_window_hours) if handoff_time else None
            end_time = handoff_time + timedelta(hours=time_window_hours) if handoff_time else None
            
            related_events = []
            if start_time and end_time:
                related_events = self.db.query(CareEventModel).filter(
                    CareEventModel.person_id == person_id,
                    CareEventModel.occurred_at >= start_time,
                    CareEventModel.occurred_at <= end_time
                ).all()
                
            # Get outcomes related to the handoff
            related_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id,
                OutcomeModel.recorded_at >= start_time if start_time else None,
                OutcomeModel.recorded_at <= end_time if end_time else None
            ).all() if start_time and end_time else []
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            handoff_evidence = []
            related_events = []
            related_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "handoff_id": handoff_id,
            "handoff_info": {
                "id": handoff.id,
                "title": getattr(handoff, 'title', None),
                "description": getattr(handoff, 'description', None),
                "handoff_type": getattr(handoff, 'handoff_type', None),
                "from_role": getattr(handoff, 'from_role', None),
                "to_role": getattr(handoff, 'to_role', None),
                "created_at": handoff.created_at.isoformat() if handoff.created_at else None,
                "updated_at": handoff.updated_at.isoformat() if handoff.updated_at else None,
            },
            "handoff_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:400] if e.source_text else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in handoff_evidence
            ],
            "related_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in related_events
            ],
            "related_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in related_outcomes
            ],
            "context": payload.get("context", {}),
            "analysis_goals": payload.get("analysis_goals", [
                "information_transfer_completeness",
                "clarity_of_instructions",
                "timeliness",
                "patient_safety_implications",
                "caregiver_understanding"
            ]),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.CONTINUITY_HANDOFF,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "Handoff",
                subject_id=subject_id or handoff_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Handoff analysis is not diagnostic
                allow_medical_action=False,         # This is process analysis, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.CONTINUITY_HANDOFF]