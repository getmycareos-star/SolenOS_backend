"""Conversation response policy.

This service intentionally does not fabricate AI answers. Until a verified model provider
is configured and grounded against the Living Care Record, it stores messages and returns
a transparent non-generative fallback. Emergency language is handled conservatively.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CompanionReply:
    reply: str
    guard_verdict: str
    safety_note: str | None
    confidence: float | None
    evidence_ids: list[str]


_EMERGENCY_PATTERN = re.compile(
    r"\b("
    r"not breathing|can't breathe|cannot breathe|difficulty breathing|"
    r"unconscious|not waking|stroke|face droop|sudden weakness|"
    r"severe chest pain|heavy bleeding|overdose|suicide attempt|"
    r"choking|seizure lasting|blue lips"
    r")\b",
    re.IGNORECASE,
)


def build_companion_reply(message: str) -> CompanionReply:
    """Return a safe, transparent response without claiming clinical reasoning."""
    if _EMERGENCY_PATTERN.search(message):
        return CompanionReply(
            reply=(
                "This may describe an emergency. Contact your local emergency service now "
                "and follow the dispatcher’s instructions. Do not wait for this Companion "
                "or use this chat to decide whether emergency care is needed."
            ),
            guard_verdict="blocked",
            safety_note="Potential emergency language detected; seek immediate local emergency help.",
            confidence=None,
            evidence_ids=[],
        )

    return CompanionReply(
        reply=(
            "Your message has been saved to this conversation. SolenOS Companion’s "
            "AI reasoning provider is not configured, so I have not generated a care "
            "recommendation or inferred anything about the person’s condition. You can "
            "record the relevant observation in the Living Care Record, or tell me what "
            "you want to organize and I can keep it as a user-provided note."
        ),
        guard_verdict="not_configured",
        safety_note="No AI-generated clinical or care advice was produced.",
        confidence=None,
        evidence_ids=[],
    )
