RULES = {
    "ABSENCE_OF_EVIDENCE_NOT_ABSENCE_OF_FACT": (
        "Absence of evidence does not imply absence of the underlying fact. "
        "No record of a fall does not mean no fall occurred."
    ),
    "UNKNOWN_DISTINCT_FROM_FALSE": (
        "Unknown must remain distinguishable from false. "
        "A missing medication allergy is not the same as a confirmed absence."
    ),
    "AMBIGUITY_NOT_SILENTLY_RESOLVED": (
        "Ambiguous information must not be silently resolved. "
        "If the meaning is unclear, the system must mark it ambiguous and surface the ambiguity."
    ),
    "PARTIAL_KNOWLEDGE_NOT_COMPLETE": (
        "Partial knowledge must not be represented as complete knowledge. "
        "Knowing a medication name without dose, frequency, or status is partial, not complete."
    ),
    "CONFLICTING_NOT_MISSING": (
        "Conflicting information must remain distinguishable from missing information. "
        "Two contradictory documents contain information; they do not create an absence."
    ),
    "NO_FABRICATED_PRECISION": (
        "Missing temporal precision must not be replaced with invented precision. "
        "If only a date range is known, the system must not fabricate an exact date."
    ),
    "MISSING_OUTCOME_NOT_SUCCESS": (
        "Missing outcomes must not be interpreted as successful outcomes. "
        "A referral with no follow-up record is unresolved, not successful."
    ),
    "STALE_NOT_CURRENT": (
        "Stale information must not automatically become current information. "
        "A provider known two years ago is historical, not necessarily current."
    ),
    "GAP_NOT_TASK": (
        "An information gap should not automatically become a user task. "
        "The system must distinguish unresolved information from required actions."
    ),
    "INFERENCE_NOT_SILENT_CLOSE": (
        "An inference should not silently close an information gap unless the system can establish "
        "that the gap is actually resolved by new evidence or explicit user input."
    ),
    "EXPLICIT_NEGATIVE_NOT_UNKNOWN": (
        "An explicit negative assertion in evidence is a confirmed fact, not unknown. "
        "'No falls in the past six months' is KNOWN, not UNKNOWN."
    ),
    "CONTRADICTION_REQUIRES_SURFACE": (
        "When contradictions exist, the system must surface them rather than selecting one side "
        "or collapsing both into uncertainty."
    ),
    "FIELD_LEVEL_UNCERTAINTY": (
        "Uncertainty must be tracked at the field level, not only at the object level. "
        "A medication may be known while its dose remains unknown."
    ),
    "EPISTEMIC_STATE_NOT_CONFIDENCE": (
        "Epistemic state is not a confidence score. "
        "'We know but are 60% confident' differs from 'We do not have the information.'"
    ),
    "RECORD_INCOMPLETENESS": (
        "Record incompleteness is a property of the evidence corpus, not individual fields. "
        "A large record may be incomplete; a small record may be complete for its purpose."
    ),
}


def get_rule(rule_id: str) -> str:
    return RULES.get(rule_id.upper(), "Unknown rule")


def list_rules() -> list[str]:
    return list(RULES.values())
