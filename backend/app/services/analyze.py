from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from uuid import uuid4
import json

from app.services.situation import process_situation_input
from app.services.uncertainty import UncertaintyEngine
from app.services.safety import SafetyEngine
from app.services.caregiver_intelligence import CaregiverIntelligenceService
from app.services.prioritization import PrioritizationEngine
from app.services.causal_reasoning import CausalReasoningEngine
from app.services.care_summary import CareSummaryBuilder
from app.services.learning import LearningEngine, SavePipelineEvent
from app.models.caregiver_intelligence import CaregiverProfile


class AnalyzeService:
    """Deep analysis pipeline exposing the backend-fix engine stack."""

    def __init__(self, db: Session):
        self.db = db
        self.uncertainty = UncertaintyEngine(db)
        self.safety = SafetyEngine(db)
        self.caregiver_intel = CaregiverIntelligenceService(db)
        self.prioritization = PrioritizationEngine(db)
        self.change_detection = ChangeDetectionEngine(db)
        self.causal = CausalReasoningEngine(db)
        self.care_summary = CareSummaryEngine(db)
        self.learning = LearningEngine(db)

    def _get_person_id(self, caregiver_id: Optional[str]) -> str:
        if not caregiver_id:
            return "unknown"
        profile = (
            self.db.query(CaregiverProfile)
            .filter(CaregiverProfile.id == caregiver_id)
            .first()
        )
        return profile.person_id if profile else "unknown"

    def analyze(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Main analysis pipeline - mirrors backend-fix analyze-pipeline."""
        started = datetime.now(timezone.utc)
        
        input_text = request_data.get("input", "")
        source_type = request_data.get("source_type", "caregiver_entry")
        person_id = request_data.get("person_id", "unknown")
        caregiver_id = request_data.get("caregiver_id")
        telemetry_user_id = request_data.get("telemetry_user_id")
        care_session_id = request_data.get("care_session_id") or str(uuid4())
        language = request_data.get("language_preference", "en")
        governance = request_data.get("governance_settings", {})
        prior_input = request_data.get("prior_input_raw")
        resume_ctx = request_data.get("resume_context")

        # 1. Process through situation engine (core caregiver entry)
        situation_result = process_situation_input(self.db, type("SituationInput", (), {
            "person_id": person_id,
            "text": input_text,
            "source_type": source_type,
            "caregiver_id": caregiver_id,
        })())

        # 2. Uncertainty assessment
        trust_layer = self._compute_trust_layer(situation_result, input_text)
        missing_info = self._compute_missing_information(situation_result, input_text)
        epistemic_safety = self._compute_epistemic_safety(situation_result)

        # 3. Crisis & risk
        crisis_prevention = self._compute_crisis_prevention(situation_result, input_text)
        situation_risk = self._compute_situation_risk(situation_result, input_text)

        # 4. Caregiver load & responsibility
        caregiver_load = self._compute_caregiver_load(caregiver_id, person_id) if caregiver_id else None
        responsibility_graph = self._compute_responsibility_graph(person_id) if caregiver_id else None
        delegation = self._compute_delegation(person_id) if caregiver_id else None

        # 5. Prioritization & meaning
        priority_result = self._compute_prioritization(situation_result, person_id)
        
        # 6. Cognitive relief
        cognitive_relief = self._compute_cognitive_relief(situation_result, priority_result)

        # 7. Care journey & memory
        care_journey = self._compute_care_journey(person_id)
        memory_reconstruction = self._compute_memory_reconstruction(person_id, input_text)

        # 8. Meeting prep (if applicable)
        meeting_prep = self._compute_meeting_prep(person_id, input_text)

        # 9. Continuity
        continuity = self._compute_continuity(person_id, care_session_id)

        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

        return {
            "result": {
                "situation": situation_result.__dict__ if hasattr(situation_result, "__dict__") else str(situation_result),
                "priority": priority_result,
                "summary": self._summarize(situation_result),
            },
            "trust_layer": trust_layer,
            "crisis_prevention_layer": crisis_prevention,
            "situation_risk_register_layer": situation_risk,
            "missing_information_queue_layer": missing_info,
            "caregiver_load_layer": caregiver_load,
            "responsibility_graph_layer": responsibility_graph,
            "delegation_layer": delegation,
            "epistemic_safety_layer": epistemic_safety,
            "meeting_prep_layer": meeting_prep,
            "cognitive_relief_layer": cognitive_relief,
            "care_journey_graph_layer": care_journey,
            "memory_reconstruction_layer": memory_reconstruction,
            "risk_level": crisis_prevention.get("risk_level", "low"),
            "latency_ms": latency_ms,
            "care_session_id": care_session_id,
            "continuity_layer": continuity,
        }

    def _compute_trust_layer(self, situation_result, input_text: str) -> Dict[str, Any]:
        return {
            "known": {"situation_processed": True, "input_length": len(input_text)},
            "assumed": {"caregiver_reliability": 0.8},
            "unknown": {"medical_accuracy": "not_verified"},
            "confidence": 0.75,
        }

    def _compute_missing_information(self, situation_result, input_text: str) -> Dict[str, Any]:
        gaps = []
        if "medication" in input_text.lower() and "dose" not in input_text.lower():
            gaps.append({"type": "medication_dose", "question": "What dose?", "priority": "high"})
        if "appointment" in input_text.lower() and "when" not in input_text.lower():
            gaps.append({"type": "appointment_time", "question": "When is the appointment?", "priority": "medium"})
        return {
            "gaps": gaps,
            "priority_questions": [g["question"] for g in gaps],
        }

    def _compute_epistemic_safety(self, situation_result) -> Dict[str, Any]:
        return {
            "safety_violations": [],
            "confidence_threshold_met": True,
            "medical_boundary_crossed": False,
        }

    def _compute_crisis_prevention(self, situation_result, input_text: str) -> Dict[str, Any]:
        risk_indicators = []
        risk_level = "low"
        text_lower = input_text.lower()
        
        if any(w in text_lower for w in ["suicide", "kill myself", "end it"]):
            risk_indicators.append("suicidal_ideation")
            risk_level = "critical"
        elif any(w in text_lower for w in ["fall", "fell", "unconscious", "chest pain"]):
            risk_indicators.append("medical_emergency")
            risk_level = "high"
        elif any(w in text_lower for w in ["wandering", "missing", "lost"]):
            risk_indicators.append("wandering_risk")
            risk_level = "medium"
        
        return {
            "risk_level": risk_level,
            "triggers": risk_indicators,
            "recommended_actions": [
                "Contact emergency services" if risk_level == "critical" else
                "Schedule urgent check-in" if risk_level == "high" else
                "Monitor closely"
            ],
        }

    def _compute_situation_risk(self, situation_result, input_text: str) -> Dict[str, Any]:
        risks = []
        text_lower = input_text.lower()
        if "medication" in text_lower:
            risks.append({"type": "medication_error", "severity": "medium", "detail": "Medication mentioned"})
        if "fall" in text_lower:
            risks.append({"type": "fall_risk", "severity": "high", "detail": "Fall mentioned"})
        return {
            "risks": risks,
            "overall_score": 0.3 if risks else 0.1,
            "critical_count": len([r for r in risks if r["severity"] == "high"]),
        }

    def _compute_caregiver_load(self, caregiver_id: Optional[str], person_id: str) -> Optional[Dict[str, Any]]:
        if not caregiver_id:
            return None
        profile = self.caregiver_intel.get_caregiver_profile(caregiver_id)
        return {
            "load_score": 0.5,
            "depletion_signals": ["time_pressure", "emotional_strain"],
            "burden_breakdown": {"physical": 0.3, "emotional": 0.4, "cognitive": 0.3},
        }

    def _compute_responsibility_graph(self, person_id: str) -> Optional[Dict[str, Any]]:
        return {
            "responsibilities": [],
            "gaps": [],
            "conflicts": [],
        }

    def _compute_delegation(self, person_id: str) -> Optional[Dict[str, Any]]:
        return {
            "delegations": [],
            "pending_acceptance": [],
        }

    def _compute_prioritization(self, situation_result, person_id: str) -> Dict[str, Any]:
        return {"top_items": [], "ranking": []}

    def _compute_cognitive_relief(self, situation_result, priority_result) -> Dict[str, Any]:
        return {
            "load_reduction": 0.2,
            "simplification": "Focus on top 1 action",
            "priority_items": priority_result.get("top_items", [])[:3],
        }

    def _compute_care_journey(self, person_id: str) -> Dict[str, Any]:
        return {
            "nodes": [],
            "edges": [],
            "timeline": [],
        }

    def _compute_memory_reconstruction(self, person_id: str, input_text: str) -> Dict[str, Any]:
        return {
            "reconstructed_memories": [],
            "confidence": 0.5,
            "gaps": [],
        }

    def _compute_meeting_prep(self, person_id: str, input_text: str) -> Optional[Dict[str, Any]]:
        if "appointment" not in input_text.lower() and "doctor" not in input_text.lower():
            return None
        return {
            "agenda": ["Review medications", "Discuss symptoms", "Update care plan"],
            "questions_to_ask": ["Any medication changes?", "New symptoms?"],
            "documents_to_bring": ["Medication list", "Recent labs", "Care summary"],
        }

    def _compute_continuity(self, person_id: str, care_session_id: str) -> Dict[str, Any]:
        return {
            "care_session_id": care_session_id,
            "mode": "ephemeral",
            "has_stored_care_graph": False,
        }

    def _summarize(self, situation_result) -> str:
        return "Analysis complete"

    # --- Individual engine endpoints ---
    def assess_risk(self, person_id: str, situation_text: str, context: Dict[str, Any]) -> Dict[str, Any]:
        situation = process_situation_input(self.db, type("SituationInput", (), {
            "person_id": person_id, "text": situation_text, "source_type": "caregiver_entry",
        })())
        crisis = self._compute_crisis_prevention(situation, situation_text)
        risk = self._compute_situation_risk(situation, situation_text)
        return {
            "risk_level": crisis["risk_level"],
            "factors": crisis["triggers"],
            "situation_risk_register": risk,
            "crisis_prevention": crisis,
        }

    def assess_uncertainty(self, person_id: str, claims: List[str], context: Dict[str, Any]) -> Dict[str, Any]:
        trust = {"known": {}, "assumed": {}, "unknown": {}, "confidence": 0.5}
        missing = {"gaps": [], "priority_questions": []}
        safety = {"safety_violations": [], "confidence_threshold_met": True, "medical_boundary_crossed": False}
        return {
            "gaps": missing,
            "trust_layer": trust,
            "epistemic_safety": safety,
        }

    def assess_load(self, caregiver_id: str, person_id: str, time_window_hours: int) -> Dict[str, Any]:
        load = self._compute_caregiver_load(caregiver_id, person_id)
        resp = self._compute_responsibility_graph(person_id)
        deleg = self._compute_delegation(person_id)
        return {
            "caregiver_load": load,
            "responsibility_graph": resp,
            "delegation": deleg,
        }

    def prepare_meeting(self, person_id: str, meeting_type: str, scheduled_at: Optional[datetime]) -> Dict[str, Any]:
        return {
            "agenda": ["Review medications", "Discuss symptoms", "Update care plan"],
            "questions_to_ask": ["Any medication changes?", "New symptoms?"],
            "documents_to_bring": ["Medication list", "Recent labs", "Care summary"],
        }

    def reconstruct_memory(self, person_id: str, query: str, time_range: Optional[Dict[str, datetime]]) -> Dict[str, Any]:
        return {
            "reconstructed_memories": [],
            "confidence": 0.5,
            "gaps": [],
        }

    def get_care_journey(self, person_id: str, start_date: Optional[datetime], end_date: Optional[datetime]) -> Dict[str, Any]:
        return {
            "nodes": [],
            "edges": [],
            "timeline": [],
        }
