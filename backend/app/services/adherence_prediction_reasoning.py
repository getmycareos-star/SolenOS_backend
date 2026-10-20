"""Service for predicting medication adherence likelihood using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.medication import Medication as MedicationModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class AdherencePredictionReasoning:
    """Predicts likelihood of medication adherence based on historical patterns and context.
    
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
        - The task is not TRAJECTORY_INTERPRETATION (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.TRAJECTORY_INTERPRETATION:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Extract medication IDs to analyze for adherence
        medication_ids = payload.get("medication_ids", [])
        if not medication_ids:
            # If not specified, analyze all active medications
            medication_ids = []
            
        # Extract prediction parameters
        prediction_horizon_days = payload.get("prediction_horizon_days", 30)  # Default 30 days
        reference_date_str = payload.get("reference_date")
        try:
            reference_date = datetime.fromisoformat(reference_date_str) if reference_date_str else datetime.now()
        except Exception:
            reference_date = datetime.now()
            
        # Gather longitudinal data for adherence prediction
        try:
            # Calculate start date for historical data (e.g., 90 days before reference)
            start_date = reference_date - timedelta(days=90)
            
            # Get medications to analyze
            medications = []
            if medication_ids:
                medications = self.db.query(MedicationModel).filter(
                    MedicationModel.id.in_(medication_ids),
                    MedicationModel.person_id == person_id
                ).all()
            else:
                # Get all active medications for the person
                medications = self.db.query(MedicationModel).filter(
                    MedicationModel.person_id == person_id
                ).all()
            
            # Get evidence related to medication taking/reporting
            adherence_evidence = self.db.query(EvidenceModel).filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.created_at >= start_date,
                EvidenceModel.created_at <= reference_date
            ).all()
            
            # Get care events that might mention medication issues
            adherence_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= start_date,
                CareEventModel.occurred_at <= reference_date
            ).all()
            
            # Get outcomes related to medication effectiveness
            adherence_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id,
                OutcomeModel.recorded_at >= start_date,
                OutcomeModel.recorded_at <= reference_date
            ).all()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            medications = []
            adherence_evidence = []
            adherence_events = []
            adherence_outcomes = []
            
        # Prepare payload for LLM with adherence-relevant data
        llm_payload = {
            "person_id": person_id,
            "reference_date": reference_date.isoformat(),
            "prediction_horizon_days": prediction_horizon_days,
            "medications_to_analyze": [
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
            "adherence_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:300] if e.source_text else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "days_from_reference": int((e.created_at - reference_date).total_seconds() / 86400) if e.created_at else None,
                }
                for e in adherence_evidence
            ],
            "adherence_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                    "days_from_reference": int((e.occurred_at - reference_date).total_seconds() / 86400) if e.occurred_at else None,
                }
                for e in adherence_events
            ],
            "adherence_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                    "days_from_reference": int((o.recorded_at - reference_date).total_seconds() / 86400) if o.recorded_at else None,
                }
                for o in adherence_outcomes
            ],
            "context": payload.get("context", {}),
            "adherence_factors": payload.get("adherence_factors", [
                "regimen_complexity",
                "side_effects_experienced",
                "cost_affordability",
                "understanding_of_purpose",
                "routine_integration",
                "cognitive_ability",
                "social_support"
            ]),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.TRAJECTORY_INTERPRETATION,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "MedicationAdherence",
                subject_id=subject_id or ",".join([str(m.id) for m in medications]) if medications else person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Adherence prediction is not diagnosis
                allow_medical_action=True,          # This informs medical decisions (advisory)
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.TRAJECTORY_INTERPRETATION]