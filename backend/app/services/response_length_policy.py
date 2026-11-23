"""Deterministic caregiver-response length policy shared by Qwen workflows."""
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
    "returning_greeting": ResponseLengthPolicy("returning_greeting", 30, 55, 144),
    "simple_acknowledgement": ResponseLengthPolicy("simple_acknowledgement", 10, 15, 48),
    "simple_factual_answer": ResponseLengthPolicy("simple_factual_answer", 35, 60, 160),
    "explanation": ResponseLengthPolicy("explanation", 100, 180, 384),
    "complex_care_history": ResponseLengthPolicy("complex_care_history", 150, 250, 640),
    "change_pattern": ResponseLengthPolicy("change_pattern", 120, 200, 512),
    "document_explanation": ResponseLengthPolicy("document_explanation", 100, 180, 384),
    "appointment_preparation": ResponseLengthPolicy("appointment_preparation", 150, 250, 640),
    "care_handoff": ResponseLengthPolicy("care_handoff", 150, 250, 640),
    "low_capacity_support": ResponseLengthPolicy("low_capacity_support", 35, 60, 160),
    "critical_safety": ResponseLengthPolicy("critical_safety", None, None, 768),
}
_GREETING = re.compile(r"^(?:hi|hello|hey|hiya|howdy|good morning|good afternoon|good evening|good day|how are you|how are you doing|how'?s it going|what'?s up|thanks|thank you|thank you so much|bye|goodbye|see you)(?: there)?$", re.I)
_EMOTIONAL = re.compile(r"\b(?:exhausted|overwhelmed|burnt out|burned out|can't cope|cannot cope|scared|frightened|panicking|terrified|at my limit|falling apart|i'm struggling|i am struggling|need a break|no sleep|up all night)\b", re.I)
_CRITICAL = re.compile(r"\b(?:not breathing|trouble breathing|can't breathe|cannot breathe|unresponsive|unconscious|stroke symptoms|face drooping|chest pain|severe bleeding|choking|overdose|suicide attempt|life[- ]threatening)\b", re.I)
_ACK = re.compile(r"^(?:thanks|thank you|got it|okay|ok|understood|yes|no|that helps|goodbye|bye)[.! ]*$", re.I)
_APPOINTMENT = re.compile(r"\b(?:appointment|doctor visit|clinic visit|questions for the doctor|prepare for (?:the )?visit)\b", re.I)
_HANDOFF = re.compile(r"\b(?:handoff|hand-off|shift change|caregiver update|update the next caregiver|share with the next caregiver)\b", re.I)
_DOCUMENT = re.compile(r"\b(?:document|discharge summary|letter|medical record|uploaded file|attachment)\b", re.I)
_PATTERN = re.compile(r"\b(?:trend|pattern|over time|changed|change|worse|worsening|improved|decline|increased|decreased|more often|less often)\b", re.I)
_COMPLEX_HISTORY = re.compile(r"\b(?:full history|complete history|care journey|timeline|everything that happened|over the last|over the past|across the last|compare .* records|multiple events)\b", re.I)
_EXPLANATION = re.compile(r"\b(?:explain|why|how does|what does .* mean|help me understand|what should i know)\b", re.I)

def resolve_response_length_policy(message: str, caregiver_stage: str | None = None) -> ResponseLengthPolicy:
    value = " ".join((message or "").casefold().split())
    if _CRITICAL.search(value):
        return _POLICIES["critical_safety"]
    if _EMOTIONAL.search(value):
        return _POLICIES["low_capacity_support"]
    if _GREETING.fullmatch(value):
        if caregiver_stage == "returning":
            return _POLICIES["returning_greeting"]
        if caregiver_stage == "new":
            return _POLICIES["first_time_greeting"]
        return _POLICIES["simple_acknowledgement"]
    if _ACK.fullmatch(value):
        return _POLICIES["simple_acknowledgement"]
    if _APPOINTMENT.search(value):
        return _POLICIES["appointment_preparation"]
    if _HANDOFF.search(value):
        return _POLICIES["care_handoff"]
    if _DOCUMENT.search(value):
        return _POLICIES["document_explanation"]
    if _PATTERN.search(value):
        return _POLICIES["change_pattern"]
    if _COMPLEX_HISTORY.search(value):
        return _POLICIES["complex_care_history"]
    if _EXPLANATION.search(value):
        return _POLICIES["explanation"]
    if len(value.split()) <= 12 and re.search(r"\b(?:when|where|which|who|what time|how many|is|are|was|were)\b", value):
        return _POLICIES["simple_factual_answer"]
    return _POLICIES["explanation"]

def count_response_words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text or "", flags=re.UNICODE))

def response_exceeds_hard_maximum(text: str, policy: ResponseLengthPolicy) -> bool:
    return policy.max_words is not None and count_response_words(text) > policy.max_words
