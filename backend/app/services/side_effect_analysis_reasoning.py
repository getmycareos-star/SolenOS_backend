"""Service for analyzing medication side effects and adverse reactions using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.medication import Medication as MedicationModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class SideEffectAnalysisReasoning:
    """Analyzes reports and observations for potential medication side effects and adverse reactions.
    
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
            
        # Extract medication IDs to analyze for side effects
        medication_ids = payload.get("medication_ids", [])
        # If not specified, we'll analyze all medications, but require at least some context
        
        # Extract symptoms or concerns to analyze
        symptoms = payload.get("symptoms", [])
        concerns = payload.get("concerns", [])
        if not symptoms and not concerns and not medication_ids:
            # Need some input to analyze
            return None
            
        # Verify person exists (already checked above, but double-check)
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Gather relevant contextual data for side effect analysis
        try:
            # Get medications to analyze
            medications = []
            if medication_ids:
                medications = self.db.query(MedicationModel).filter(
                    MedicationModel.id.in_(medication_ids),
                    MedicationModel.person_id == person_id
                ).all()
            else:
                # Get all medications for the person if no specific ones specified
                medications = self.db.query(MedicationModel).filter(
                    MedicationModel.person_id == person_id
                ).all()
            
            # Get evidence that might mention symptoms or side effects
            symptom_evidence = []
            if evidence_ids:
                symptom_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                # If no specific evidence IDs, get recent evidence for the person
                symptom_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id
                ).order_by(EvidenceModel.created_at.desc()).limit(15).all()
                
            # Get care events that might mention symptoms or adverse reactions
            symptom_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id
            ).order_by(CareEventModel.occurred_at.desc()).limit(10).all()
            
            # Get outcomes that might be related to side effects
            symptom_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id
            ).order_by(OutcomeModel.recorded_at.desc()).limit(10).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            medications = []
            symptom_evidence = []
            symptom_events = []
            symptom_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "medications_being_taken": [
                {
                    "id": m.id,
                    "name": m.name,
                    "dosage": m.dosage,
                    "frequency": m.frequency,
                    "start_date": m.start_date.isoformat() if m.start_date else None,
                    "indication": getattr(m, 'purpose_indication', None),
                }
                for m in medications
            ],
            "symptoms_to_analyze": symptoms,
            "concerns_to_analyze": concerns,
            "symptom_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:400] if e.source_text else None,  # More text for side effect analysis
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in symptom_evidence
            ],
            "symptom_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in symptom_events
            ],
            "symptom_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in symptom_outcomes
            ],
            "context": payload.get("context", {}),
            "analysis_focus": payload.get("analysis_focus", [
                "temporal_relationship",
                "dose_response_pattern",
                "known_side_effect_profiles",
                "alternative_explanations",
                "severity_assessment"
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
                subject_type=subject_type or "SideEffectAnalysis",
                subject_id=subject_id or ",".join([str(m.id) for m in medications]) if medications else person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Side effect analysis is not diagnosis (it's observational)
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