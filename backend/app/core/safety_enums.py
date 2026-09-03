from enum import Enum


class SafetyDecision(str, Enum):
    ALLOW = "allow"
    ALLOW_WITH_QUALIFICATION = "allow_with_qualification"
    REQUIRE_MORE_EVIDENCE = "require_more_evidence"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    ESCALATE = "escalate"
    REFUSE = "refuse"


class RiskClass(str, Enum):
    INFORMATIONAL = "informational"
    LOW_RISK = "low_risk"
    MODERATE_RISK = "moderate_risk"
    HIGH_RISK_MEDICAL = "high_risk_medical"
    EMERGENCY = "emergency"
    PROHIBITED = "prohibited"


class ClaimType(str, Enum):
    FACTUAL = "factual"
    CAUSAL = "causal"
    DIAGNOSTIC = "diagnostic"
    MEDICAL_ACTION = "medical_action"
    RECOMMENDATION = "recommendation"
    INTERPRETATION = "interpretation"
    PREDICTION = "prediction"
    CURRENT_STATE = "current_state"
    TEMPORAL = "temporal"


class ClaimStrength(str, Enum):
    DEFINITE = "definite"
    LIKELY = "likely"
    POSSIBLE = "possible"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"
    NOT_DOCUMENTED = "not_documented"
    STALE = "stale"
    HISTORICAL = "historical"


class EscalationDestination(str, Enum):
    USER = "user"
    CAREGIVER = "caregiver"
    CLINICIAN = "clinician"
    EMERGENCY_SERVICES = "emergency_services"
    HUMAN_REVIEWER = "human_reviewer"
    SYSTEM_ADMIN = "system_admin"


class EscalationStatus(str, Enum):
    DETECTED = "detected"
    PENDING_REVIEW = "pending_review"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class SourceAuthority(str, Enum):
    CLINICIAN = "clinician"
    LABORATORY = "laboratory"
    MEDICATION_RECORD = "medication_record"
    DISCHARGE_INSTRUCTION = "discharge_instruction"
    CAREGIVER_REPORT = "caregiver_report"
    PATIENT_REPORT = "patient_report"
    SYSTEM_INFERENCE = "system_inference"
    EXTERNAL_GUIDELINE = "external_guideline"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class SourceQuality(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    UNVERIFIED = "unverified"


class CurrentnessStatus(str, Enum):
    CURRENT = "current"
    RECENT = "recent"
    STALE = "stale"
    HISTORICAL = "historical"
    UNKNOWN_CURRENCY = "unknown_currency"


class SafetyInvariantViolation(str, Enum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CLAIM_STRENGTH_INFLATION = "claim_strength_inflation"
    UNSUPPORTED_ADVICE = "unsupported_advice"
    FALSE_REASSURANCE = "false_reassurance"
    CAUSAL_OVERREACH = "causal_overreach"
    DIAGNOSTIC_OVERREACH = "diagnostic_overreach"
    MEDICATION_OVERREACH = "medication_overreach"
    UNCERTAINTY_SUPPRESSION = "uncertainty_suppression"
    CONTRADICTION_SUPPRESSION = "contradiction_suppression"
    STALE_EVIDENCE = "stale_evidence"
    CITATION_MISMATCH = "citation_mismatch"
    DANGEROUS_OMISSION = "dangerous_omission"
    PROMPT_INJECTION = "prompt_injection"
    OVERCONFIDENCE = "overconfidence"
    AUTHORITY_THEATER = "authority_theater"
    INFORMATION_TO_ADVICE = "information_to_advice"


class SafetyState(str, Enum):
    UNASSESSED = "unassessed"
    GROUNDED = "grounded"
    QUALIFIED = "qualified"
    REQUIRES_MORE_EVIDENCE = "requires_more_evidence"
    HIGH_RISK = "high_risk"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    ESCALATED = "escalated"
    BLOCKED = "blocked"
    ALLOWED = "allowed"
    FAILED = "failed"


class SafetyEventType(str, Enum):
    CLAIM_VERIFIED = "claim_verified"
    CLAIM_FAILED = "claim_failed"
    ESCALATION_CREATED = "escalation_created"
    ESCALATION_RESOLVED = "escalation_resolved"
    HUMAN_REVIEW_REQUESTED = "human_review_requested"
    HUMAN_OVERRIDE = "human_override"
    SAFETY_BLOCK = "safety_block"
    SAFETY_QUALIFICATION = "safety_qualification"
    INVARIANT_VIOLATION = "invariant_violation"
    REGRESSION_DETECTED = "regression_detected"
    POLICY_CHANGE = "policy_change"
    MODEL_CHANGE = "model_change"
    SOURCE_RANKING_CHANGE = "source_ranking_change"
