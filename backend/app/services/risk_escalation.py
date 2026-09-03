import re
from typing import List, Optional, Dict, Any
from app.schemas.safety import RiskClass
from app.core.safety_enums import (
    ClaimType,
    EscalationDestination,
    EscalationStatus,
)


class RiskClassifier:
    EMERGENCY_KEYWORDS = [
        "severe difficulty breathing", "can't breathe", "shortness of breath",
        "chest pain", "heart attack", "stroke", "unconscious", "loss of consciousness",
        "severe bleeding", "not breathing", "blue lips", "blue fingertips",
        "suicidal", "self-harm", "kill myself", "end my life",
        "severe allergic reaction", "anaphylaxis", "swelling of throat",
        "severe head injury", "head trauma",
    ]

    HIGH_RISK_KEYWORDS = [
        "stop.*medication", "discontinue", "halt.*medication", "end.*medication",
        "increase.*dose", "decrease.*dose", "double.*dose", "reduce.*dose",
        "change.*medication", "switch.*medication",
        "fall", "fell", "head injury",
        "confusion", "disoriented", "severe pain",
        "medication error", "overdose", "adverse reaction",
        "worsening", "deteriorating", "decline",
    ]

    MODERATE_RISK_KEYWORDS = [
        "medication", "dose", "dosage", "side effect", "interaction",
        "follow up", "appointment", "symptom", "behavior",
        "sleep", "appetite", "mood", "agitation",
        "infection", "fever", "pain",
    ]

    def classify(self, claims: List[Any], evidence_refs: List[Any], text: str) -> RiskClass:
        text_lower = text.lower()
        for keyword in self.EMERGENCY_KEYWORDS:
            if re.search(keyword, text_lower):
                return RiskClass.EMERGENCY
        for keyword in self.HIGH_RISK_KEYWORDS:
            if re.search(keyword, text_lower):
                return RiskClass.HIGH_RISK_MEDICAL
        for keyword in self.MODERATE_RISK_KEYWORDS:
            if keyword in text_lower:
                return RiskClass.MODERATE_RISK
        return RiskClass.INFORMATIONAL

    def classify_from_claims(self, claim_types: List[ClaimType]) -> RiskClass:
        if ClaimType.MEDICAL_ACTION in claim_types or ClaimType.DIAGNOSTIC in claim_types:
            return RiskClass.HIGH_RISK_MEDICAL
        if ClaimType.CAUSAL in claim_types:
            return RiskClass.MODERATE_RISK
        if ClaimType.RECOMMENDATION in claim_types:
            return RiskClass.MODERATE_RISK
        return RiskClass.INFORMATIONAL


class EscalationEngine:
    DESTINATION_RULES = {
        RiskClass.EMERGENCY: {
            "destination": EscalationDestination.EMERGENCY_SERVICES,
            "urgency": "immediate",
            "action": "call_emergency",
        },
        RiskClass.HIGH_RISK_MEDICAL: {
            "destination": EscalationDestination.CAREGIVER,
            "urgency": "urgent",
            "action": "notify_caregiver",
        },
        RiskClass.MODERATE_RISK: {
            "destination": EscalationDestination.CLINICIAN,
            "urgency": "routine",
            "action": "notify_clinician",
        },
        RiskClass.LOW_RISK: {
            "destination": EscalationDestination.HUMAN_REVIEWER,
            "urgency": "routine",
            "action": "request_review",
        },
        RiskClass.INFORMATIONAL: {
            "destination": EscalationDestination.HUMAN_REVIEWER,
            "urgency": "routine",
            "action": "log_only",
        },
    }

    def determine_escalation(self, risk_class: RiskClass, trigger: str, person_id: str) -> Dict[str, Any]:
        rules = self.DESTINATION_RULES.get(risk_class, self.DESTINATION_RULES[RiskClass.LOW_RISK])
        return {
            "risk_category": risk_class,
            "destination": rules["destination"],
            "urgency": rules["urgency"],
            "action": rules["action"],
            "trigger": trigger,
            "person_id": person_id,
            "status": EscalationStatus.DETECTED,
        }

    def should_escalate(self, risk_class: RiskClass, has_human_review: bool, safety_violations: List[str]) -> bool:
        if risk_class == RiskClass.EMERGENCY:
            return True
        if risk_class == RiskClass.HIGH_RISK_MEDICAL:
            return True
        if risk_class == RiskClass.MODERATE_RISK and not has_human_review:
            return True
        if any(v in ("prompt_injection", "unsupported_advice", "causal_overreach") for v in safety_violations):
            return True
        return False

    def deduplicate(self, new_escalation: Dict[str, Any], existing_escalations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for existing in existing_escalations:
            if (
                existing.get("person_id") == new_escalation.get("person_id")
                and existing.get("risk_category") == new_escalation.get("risk_category")
                and existing.get("trigger") == new_escalation.get("trigger")
                and existing.get("status") in (EscalationStatus.DETECTED, EscalationStatus.PENDING_REVIEW, EscalationStatus.ACKNOWLEDGED)
            ):
                return existing
        return None
