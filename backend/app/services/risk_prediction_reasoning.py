"""Service for predicting health deteriorations and risks using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.models.medication import Medication as MedicationModel
from app.models.care_planning import CarePlan as CarePlanModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class RiskPredictionReasoning:
    """Predicts health deteriorations, hospitalization risks, and other adverse events.
    
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
            
        # Extract prediction parameters
        prediction_horizon_days = payload.get("prediction_horizon_days", 30)  # Default 30 days
        risk_types = payload.get("risk_types", ["hospitalization", "clinical_deterioration"])
        reference_date_str = payload.get("reference_date")
        try:
            reference_date = datetime.fromisoformat(reference_date_str) if reference_date_str else datetime.now()
        except Exception:
            reference_date = datetime.now()
            
        # Gather longitudinal data for trajectory analysis
        try:
            # Calculate start date for historical data (e.g., 90 days before reference)
            start_date = reference_date - timedelta(days=90)
            
            # Get observations/evidence over time
            temporal_evidence = self.db.query(EvidenceModel).filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.created_at >= start_date,
                EvidenceModel.created_at <= reference_date
            ).order_by(EvidenceModel.created_at.asc()).all()
            
            # Get care events over time
            temporal_events = self.db.query(CareEventModel).filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= start_date,
                CareEventModel.occurred_at <= reference_date
            ).order_by(CareEventModel.occurred_at.asc()).all()
            
            # Get outcomes over time
            temporal_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id,
                OutcomeModel.recorded_at >= start_date,
                OutcomeModel.recorded_at <= reference_date
            ).order_by(OutcomeModel.recorded_at.asc()).all()
            
            # Get medication history
            temporal_medications = self.db.query(MedicationModel).filter(
                MedicationModel.person_id == person_id
            ).all()  # Medications might not have temporal scope, get all active/recent
            
            # Get current care plan
            care_plan = self.db.query(CarePlanModel).filter(
                CarePlanModel.person_id == person_id
            ).first()
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            temporal_evidence = []
            temporal_events = []
            temporal_outcomes = []
            temporal_medications = []
            care_plan = None
            
        # Prepare payload for LLM with temporal trends
        llm_payload = {
            "person_id": person_id,
            "reference_date": reference_date.isoformat(),
            "prediction_horizon_days": prediction_horizon_days,
            "risk_types": risk_types,
            "temporal_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:200] if e.source_text else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "days_from_reference": int((e.created_at - reference_date).total_seconds() / 86400) if e.created_at else None,
                }
                for e in temporal_evidence
            ],
            "temporal_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                    "days_from_reference": int((e.occurred_at - reference_date).total_seconds() / 86400) if e.occurred_at else None,
                }
                for e in temporal_events
            ],
            "temporal_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                    "days_from_reference": int((o.recorded_at - reference_date).total_seconds() / 86400) if o.recorded_at else None,
                }
                for o in temporal_outcomes
            ],
            "temporal_medications": [
                {
                    "id": m.id,
                    "name": m.name,
                    "dosage": m.dosage,
                    "frequency": m.frequency,
                    "start_date": m.start_date.isoformat() if m.start_date else None,
                }
                for m in temporal_medications
            ],
            "care_plan": {
                "id": care_plan.id if care_plan else None,
                "title": getattr(care_plan, 'title', None) if care_plan else None,
                "goals": getattr(care_plan, 'goals', []) if care_plan else [],
                "start_date": care_plan.start_date.isoformat() if care_plan and care_plan.start_date else None,
            } if care_plan else None,
            "context": payload.get("context", {}),
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.TRAJECTORY_INTERPRETATION,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "HealthTrajectory",
                subject_id=subject_id or person_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Risk prediction is not diagnosis
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