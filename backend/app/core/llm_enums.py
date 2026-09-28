from enum import Enum


class LLMStatus(str, Enum):
    OK = "ok"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    BLOCKED = "blocked"
    CIRCUIT_OPEN = "circuit_open"
    ERROR = "error"


class LLMTask(str, Enum):
    PERSON_PROFILE = "person_profile"
    PERSON_TRIGGERS = "person_triggers"
    CONTINUITY_NARRATIVE = "continuity_narrative"
    CONTINUITY_HANDOFF = "continuity_handoff"
    WHAT_MATTERS = "what_matters"
    WHAT_MATTERS_ALIGNMENT = "what_matters_alignment"
    TRAJECTORY_INTERPRETATION = "trajectory_interpretation"
    BEHAVIOR_INTERPRETATION = "behavior_interpretation"
    CONTRADICTION_ANALYSIS = "contradiction_analysis"
    DECISION_OPTIONS = "decision_options"
    DECISION_PLAN = "decision_plan"
    FOLLOW_UP_RATIONALE = "follow_up_rationale"
    PROACTIVE_OPPORTUNITY = "proactive_opportunity"
    CONVERSATION_REPLY = "conversation_reply"


class LLMReasoningMode(str, Enum):
    """How much authority the language model is allowed over a conclusion.

    DETERMINISTIC_ONLY never calls the model. LLM_ASSISTED lets the model enrich a
    conclusion that a deterministic engine already established. LLM_DRAFT lets the
    model propose text, which the trust guard then has to accept before it counts.
    """

    DETERMINISTIC_ONLY = "deterministic_only"
    LLM_ASSISTED = "llm_assisted"
    LLM_DRAFT = "llm_draft"


class GuardVerdict(str, Enum):
    ACCEPTED = "accepted"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class EvidenceClaimType(str, Enum):
    FACTUAL = "factual"
    CAUSAL = "causal"
    DIAGNOSTIC = "diagnostic"
    PREDICTIVE = "predictive"
    NORMATIVE = "normative"
    EMOTIONAL = "emotional"
