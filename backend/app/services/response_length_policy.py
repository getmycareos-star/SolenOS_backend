"""Deterministic caregiver-response length contracts shared by Qwen workflows.

Targets guide generation. Hard maxima are enforced after generation and repair.
Critical safety responses are not shortened to meet a generic word count.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ResponseLengthPolicy:
    intent: str
    target_words: int | None
    max_words: int | None
    token_budget: int


_POLICIES = {
    "first_time_greeting": ResponseLengthPolicy("first_time_greeting", 25, 40, 112),
    "returning_greeting": ResponseLengthPolicy("returning_greeting", 30, 55, 160),
    "simple_acknowledgement": ResponseLengthPolicy("simple_acknowledgement", 10, 15, 64),
    "simple_factual_answer": ResponseLengthPolicy("simple_factual_answer", 35, 60, 192),
    "explanation": ResponseLengthPolicy("explanation", 100, 180, 448),
    "complex_care_history": ResponseLengthPolicy("complex_care_history", 150, 250, 768),
    "change_pattern": ResponseLengthPolicy("change_pattern", 120, 200, 640),
    "document_explanation": ResponseLengthPolicy("document_explanation", 100, 180, 512),
    "appointment_preparation": ResponseLengthPolicy("appointment_preparation", 150, 250, 768),
    "care_handoff": ResponseLengthPolicy("care_handoff", 150, 250, 768),
    "low_capacity_support": ResponseLengthPolicy("low_capacity_support", 35, 60, 192),
    "critical_safety": ResponseLengthPolicy("critical_safety", None, None, 1024),
}

_GREETING = re.compile(
    r"^(?:hi|hello|hey|hiya|howdy|good morning|good afternoon|good evening|good day|"
    r"how are you|how are you doing|how are you today|how're you|how'?s it going|what'?s up|yo|sup)(?: there)?(?: solenos)?(?: again)?[.!?]*$",
    re.I,
)
_ACK = re.compile(
    r"^(?:thanks|thank you|thank you so much|got it|okay|ok|understood|yes|no|"
    r"that helps|good night|goodbye|bye|see you)[.! ]*$",
    re.I,
)
_LOW_CAPACITY_SIGNAL = re.compile(
    r"\b(?:i(?:'m| am) (?:really |very |so )?(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)|"
    r"i(?:'m| am) (?:really |very |so )?(?:angry|frustrated|sad|scared|frightened|afraid|lonely|upset) "
    r"(?:and|but) (?:really |very |so )?(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)|"
    r"i feel (?:really |very |so )?(?:angry|frustrated|sad|scared|frightened|afraid|lonely|upset) "
    r"(?:and|but) (?:really |very |so )?(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)|"
    r"i feel (?:really |very |so )?(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)|"
    r"i (?:really )?(?:can't|cannot) (?:cope|continue|keep up|keep doing this|manage|do this anymore)|"
    r"i (?:need|want) (?:a break|a pause|to stop)|i just want a break|"
    r"i don't know what to do anymore|everything is too much|"
    r"please stop|stop for now|enough for now|not now|leave it for now|that's enough|"
    r"i don't want to talk(?: about this)?|i do not want to talk(?: about this)?|"
    r"i've had enough|i have had enough)\b",
    re.I,
)
_STANDALONE_LOW_CAPACITY = re.compile(
    r"^(?:really |very |so )?(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)[.!?]*$",
    re.I,
)

def is_low_capacity_caregiver_message(message: str) -> bool:
    value = " ".join((message or "").casefold().split())
    if _LOW_CAPACITY_SIGNAL.search(value) or _STANDALONE_LOW_CAPACITY.fullmatch(value):
        return True

    capacity_terms = r"(?:overwhelmed|exhausted|tired|worn out|drained|burnt out|burned out)"
    first_person_capacity = re.search(
        rf"\bi(?:'m| am| feel| have|[' ]ve)\b[^.!?]{0,50}\b{capacity_terms}\b",
        value,
    )
    explicitly_negated = re.search(
        rf"\b(?:not|never|no longer)\s+(?:(?:really|very|so)\s+)?{capacity_terms}\b",
        value,
    )
    return bool(first_person_capacity and not explicitly_negated)

_SELF_HARM_CRITICAL = re.compile(
    r"\b(?:suicidal|suicide attempt|suicide plan|thinking about suicide|"
    r"trying to kill myself|kill myself|end my life|want to die|"
    r"i (?:don't|do not) want to live|i wish i (?:was|were) dead|"
    r"better off dead|no reason to live|life isn't worth living|"
    r"everyone would be better off without me|"
    r"(?:want|wants|wanted) to die|(?:want|wants|wanted) to kill (?:myself|himself|herself|themselves|themself)|"
    r"(?:thinking|talking) about suicide|"
    r"(?:said|says|told me) (?:he|she|they) (?:want|wants|wanted) to die|"
    r"(?:wish|wishes|wished) (?:he|she|they) (?:was|were) dead|"
    r"hurt myself|harm myself|(?:hurting|harming) (?:himself|herself|themselves|themself)|"
    r"self[- ]harm|can't keep myself safe|cannot keep myself safe|"
    r"can't keep (?:himself|herself|themselves|themself) safe|cannot keep (?:himself|herself|themselves|themself) safe|"
    r"i can't go on|i cannot go on|"
    r"might hurt (?:myself|someone|him|her|them)|"
    r"afraid i might hurt (?:myself|someone|him|her|them))\b",
    re.I,
)


_CRITICAL = re.compile(
    r"\b(?:not breathing|trouble breathing|can't breathe|cannot breathe|unresponsive|"
    r"unconscious|stroke symptoms|face drooping|possible stroke|suspected stroke|"
    r"having a stroke|signs of stroke|sudden weakness|slurred speech|speech is slurred|"
    r"sudden confusion|suddenly confused|new confusion|sudden vision loss|"
    r"one[- ]sided weakness|weakness on one side|numbness on one side|"
    r"sudden numbness|one side of (?:the )?face drooping|"
    r"chest pain with difficulty breathing|"
    r"severe chest pain|new chest pain|chest pain now|having chest pain|"
    r"heart attack symptoms|possible heart attack|suspected heart attack|having a heart attack|"
    r"severe bleeding|heavy bleeding|is choking|currently choking|choking now|"
    r"having a seizure|seizure right now|ongoing seizure|is seizing|repeated seizures|"
    r"possible overdose|suspected overdose|overdosed|took too much|poisoning|"
    r"suicidal|suicide attempt|trying to kill myself|self[- ]harm|immediate danger|"
    r"life[- ]threatening|collapsed|cannot wake|can't wake|not waking up|"
    r"blue lips|gasping|anaphylaxis|severe allergic reaction)\b",
    re.I,
)
_APPOINTMENT = re.compile(
    r"\b(?:prepare|preparing|preparation)\b.{0,80}\b(?:appointment|doctor visit|clinic visit|visit)\b|"
    r"\b(?:appointment|doctor visit|clinic visit)\b.{0,80}\b(?:prep|preparing|preparation|briefing)\b|"
    r"\bquestions?\b.{0,60}\b(?:for|to ask)\s+(?:the\s+)?doctor\b",
    re.I,
)
_HANDOFF = re.compile(
    r"\b(?:handoff|hand-off|shift change|update the next caregiver|share with the next caregiver)\b",
    re.I,
)
_DOCUMENT = re.compile(
    r"\b(?:document|discharge summary|letter|medical record|uploaded file|attachment)\b",
    re.I,
)
_PATTERN = re.compile(
    r"\b(?:trend|pattern|over time|changed|change|worse|worsening|improved|decline|"
    r"increased|decreased|more often|less often)\b",
    re.I,
)
_COMPLEX_HISTORY = re.compile(
    r"\b(?:full history|complete history|care journey|timeline|everything that happened|"
    r"over the last|over the past|across the last|compare .* records|multiple events)\b",
    re.I,
)
_EXPLANATION = re.compile(
    r"\b(?:explain|why|how does|what does .* mean|help me understand|what should i know)\b",
    re.I,
)


def is_self_harm_safety_message(message: str) -> bool:
    """Recognize explicit and indirect self-harm, suicide, and imminent harm language."""
    return bool(_SELF_HARM_CRITICAL.search(message or ""))


def is_critical_safety_message(message: str) -> bool:
    """Shared deterministic emergency classifier for response policy and Companion."""
    value = message or ""
    return bool(_CRITICAL.search(value) or _SELF_HARM_CRITICAL.search(value))


def is_greeting_message(message: str) -> bool:
    """Shared exact-match greeting detector; emotional messages are not greetings."""
    value = re.sub(r"[.!?,;:]+", " ", (message or "").casefold().strip())
    value = " ".join(value.split())
    return bool(_GREETING.fullmatch(value))


def resolve_response_length_policy(
    message: str,
    caregiver_stage: str | None = None,
    *,
    intent_hint: str | None = None,
    critical_safety: bool = False,
) -> ResponseLengthPolicy:
    """Resolve the contract without spending another model call on classification.

    Existing deterministic urgency checks can set critical_safety. Explicit
    workflow callers should pass intent_hint rather than rely on prompt keywords.
    """
    if critical_safety:
        return _POLICIES["critical_safety"]

    value = " ".join((message or "").casefold().split())
    if is_critical_safety_message(value):
        return _POLICIES["critical_safety"]
    # Emotion and capacity are separate. Sadness, grief, fear, anger, or
    # loneliness alone must not shorten an answer the caregiver explicitly wants.
    # Only explicit overload, inability to continue, or a request to pause selects
    # the low-capacity response budget.
    if is_low_capacity_caregiver_message(value):
        return _POLICIES["low_capacity_support"]

    if intent_hint:
        normalized_hint = intent_hint.strip().casefold()
        if normalized_hint not in _POLICIES:
            raise ValueError(f"Unsupported response-length intent hint: {intent_hint}")
        return _POLICIES[normalized_hint]

    # Acknowledgements (including goodbye) must not enter the orientation path.
    if _ACK.fullmatch(value):
        return _POLICIES["simple_acknowledgement"]
    if is_greeting_message(value):
        if caregiver_stage == "returning":
            return _POLICIES["returning_greeting"]
        return _POLICIES["first_time_greeting"]

    if _APPOINTMENT.search(value):
        return _POLICIES["appointment_preparation"]
    if _HANDOFF.search(value):
        return _POLICIES["care_handoff"]
    if _DOCUMENT.search(value):
        return _POLICIES["document_explanation"]
    if _COMPLEX_HISTORY.search(value):
        return _POLICIES["complex_care_history"]
    if _PATTERN.search(value):
        return _POLICIES["change_pattern"]
    if _EXPLANATION.search(value):
        return _POLICIES["explanation"]
    if len(value.split()) <= 12 and re.search(
        r"\b(?:when|where|which|who|what time|how many|is|are|was|were)\b", value
    ):
        return _POLICIES["simple_factual_answer"]
    return _POLICIES["explanation"]


_WORKFLOW_INTENT_HINTS: dict[str, str | None] = {
    "daily_capture": "explanation",
    "add_record": "explanation",
    "document": "document_explanation",
    "living_care_record": "complex_care_history",
    "care_history_search": "complex_care_history",
    "care_situation_change": "change_pattern",
    "delta_engine": "change_pattern",
    "evidence_provenance": "explanation",
    "uncertainty_contradictions": "complex_care_history",
    "returning_caregiver": "change_pattern",
    "appointment": "appointment_preparation",
    "after_appointment": "appointment_preparation",
    "handoff": "care_handoff",
    "professional_summary": "complex_care_history",
    "open_loops": "explanation",
    "two_week_review": "change_pattern",
    "decision_outcome": "explanation",
    "medication_day": "explanation",
    "reminders_notifications": "simple_factual_answer",
    "care_coordination": "care_handoff",
    "companion": None,
    "caregiver_feedback": "explanation",
    "sharing_export": "care_handoff",
    "roles_access": "simple_factual_answer",
    "account_recovery": "simple_factual_answer",
    "settings_security": "simple_factual_answer",
    "record_lifecycle": "simple_factual_answer",
    "offline_sync": "simple_factual_answer",
    "ai_reliability": "simple_factual_answer",
    "audit_review": "complex_care_history",
    "care_transition": "care_handoff",
    "incident_follow_through": "complex_care_history",
    "emergency_readiness": "complex_care_history",
    "care_coverage_gap": "change_pattern",
    "care_instruction_change_tracker": "change_pattern",
    "critical_information_completeness": "complex_care_history",
    "personal_preferences_distress_context": "explanation",
    "caregiver_strain_support": "low_capacity_support",
    "document_validity_renewal": "document_explanation",
}


def resolve_workflow_response_length_policy(
    workflow: str,
    message: str,
    caregiver_stage: str | None = None,
    *,
    critical_safety: bool = False,
) -> ResponseLengthPolicy:
    """Resolve a workflow-specific contract without another model classification call."""
    if workflow not in _WORKFLOW_INTENT_HINTS:
        raise ValueError(f"Unsupported Qwen response-length workflow: {workflow}")
    return resolve_response_length_policy(
        message,
        caregiver_stage=caregiver_stage,
        intent_hint=_WORKFLOW_INTENT_HINTS[workflow],
        critical_safety=critical_safety,
    )


def count_response_words(text: str) -> int:
    # Count natural-language words while keeping contractions and hyphenated
    # compounds intact. Em dashes and surrounding punctuation separate words.
    return len(re.findall(r"[\w]+(?:['’][\w]+)*(?:-[\w]+(?:['’][\w]+)*)*", text or "", re.UNICODE))


def response_exceeds_hard_maximum(text: str, policy: ResponseLengthPolicy) -> bool:
    return policy.max_words is not None and count_response_words(text) > policy.max_words
