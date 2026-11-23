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
    "simple_acknowledgement": ResponseLengthPolicy("simple_acknowledgement", 10, 15, 48),
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
    r"how are you|how are you doing|how are you today|how're you|how'?s it going|what'?s up|yo|sup)(?: there| solenos)?[.!?]*$",
    re.I,
)
_ACK = re.compile(
    r"^(?:thanks|thank you|thank you so much|got it|okay|ok|understood|yes|no|"
    r"that helps|good night|goodbye|bye|see you)[.! ]*$",
    re.I,
)
_EMOTIONAL = re.compile(
    r"\b(?:exhausted|overwhelmed|burnt out|burned out|can't cope|cannot cope|scared|"
    r"frightened|panicking|terrified|at my limit|falling apart|i'm struggling|"
    r"i am struggling|need a break|no sleep|up all night)\b",
    re.I,
)
_CRITICAL = re.compile(
    r"\b(?:not breathing|trouble breathing|can't breathe|cannot breathe|unresponsive|"
    r"unconscious|stroke symptoms|face drooping|chest pain|severe bleeding|choking|"
    r"overdose|suicide attempt|life[- ]threatening|collapsed|seizure|cannot wake|"
    r"can't wake|blue lips|gasping|heavy bleeding)\b",
    re.I,
)
_APPOINTMENT = re.compile(
    r"\b(?:prepare|preparing|preparation)\b.{0,80}\b(?:appointment|doctor visit|clinic visit|visit)\b|"
    r"\b(?:appointment|doctor visit|clinic visit)\b.{0,80}\b(?:prep|preparing|preparation|briefing)\b|"
    r"\bquestions? (?:for|to ask )(?:the )?doctor\b",
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
    if _CRITICAL.search(value):
        return _POLICIES["critical_safety"]
    if _EMOTIONAL.search(value):
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


def count_response_words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text or "", flags=re.UNICODE))


def response_exceeds_hard_maximum(text: str, policy: ResponseLengthPolicy) -> bool:
    return policy.max_words is not None and count_response_words(text) > policy.max_words
