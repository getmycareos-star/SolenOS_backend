"""Service for analyzing family dynamics and communication patterns using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm = Session

from app.core.llm_enums = LLMTask, LLMReasoningMode
from app.models.identity = Person
from app.models.caregiver = Caregiver as CaregiverModel
from app.models.evidence = Evidence as EvidenceModel
from app.models.care_event = CareEvent as CareEventModel
from app.models.outcome = Outcome as OutcomeModel
from app.models.care_planning = CarePlan as CarePlanModel
from app.services.llm = LocalReasoningEngine, LLMReasoningResult


class FamilyDynamicsAnalysisReasoning:
    """Analyzes family stress, communication patterns, and support dynamics.
    
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
        - The task is not BEHAVIOR_INTERPRETATION (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.BEHAVIOR_INTERPRETATION:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Extract family member IDs or parameters
        family_member_ids = payload.get("family_member_ids", [])
        # If not specified, we might look for caregivers/family members associated with the person
        
        # Verify person exists (already checked above, but double-check)
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Gather relevant contextual data for family dynamics analysis
        try:
            # Get caregivers/family members associated with the person
            family_members = []
            if family_member_ids:
                family_members = self.db.query(CaregiverModel).filter(
                    CaregiverModel.id.in_(family_member_ids),
                    CaregiverModel.person_id == person_id
                ).all()
            else:
                # Get all caregivers associated with the person
                family_members = self.db.query(CaregiverModel).filter(
                    CaregiverModel.person_id == person_id
                ).all()
            
            # Get evidence from family members
            family_evidence = []
            if evidence_ids:
                family_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                # If no specific evidence IDs, get recent evidence uploaded by family members
                if family_members:
                    family_member_ids_list = [fm.id for fm in family_members]
                    family_evidence = self.db.query(EvidenceModel).filter(
                        EvidenceModel.person_id == person_id,
                        EvidenceModel.uploaded_by_caregiver_id.in_(family_member_ids_list)
                    ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
                else:
                    # Fallback to general recent evidence
                    family_evidence = self.db.query(EvidenceModel).filter(
                        EvidenceModel.person_id == person_id
                    ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
                
            # Get care events (to see family involvement patterns)
            family_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get outcomes related to family support
            family_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            family_members = []
            family_evidence = []
            family_events = []
            family_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "family_members": [
                {
                    "id": fm.id,
                    "name": fm.name,
                    "relationship": fm.relationship,
                    "email": fm.email,
                    "phone": getattr(fm, 'phone', None),
                    "living_with_person": getattr(fm, 'living_with_person', False),
                }
                for fm in family_members
            ],
            "family_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:500] if e.source_text else None,  # More text for language analysis
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "uploaded_by_caregiver_id": e.uploaded_by_caregiver_id,
                }
                for e in family_evidence
            ],
            "family_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in family_events
            ],
            "family_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in family_outcomes
            ],
            "context": payload.get("context", {}),
            "analysis_focus": payload.get("analysis_focus", [
                "communication_patterns",
                "stress_levels",
                "support_distribution",
                "conflict_indicators",
                "collaboration_effectiveness",
                "caregiver_burden_sharing"
            ]),
            "time_period": payload.get("time_period", "last_30_days"),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.BEHAVIOR_INTERPRETATION,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "FamilyDynamics",
                subject_id=subject_id or ",".join([str(fm.id) for fm in family_members]) if family_members else person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Family dynamics analysis is not diagnosis
                allow_medical_action=False,         # This is observational, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.BEHAVIOR_INTERPRETATION]