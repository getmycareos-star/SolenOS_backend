"""Source-Pointer Trust Layer — deterministic validator + enforcer.

INVARIANT (non-negotiable):
  A claim with evidence_status = "confirmed" | "reported" MUST have a verified,
  exact source pointer into the original evidence.

A source pointer is valid ONLY if:
  original_text[start_offset:end_offset] == source_span
with NO normalization, trimming, paraphrasing, case conversion, whitespace
substitution, or fuzzy matching.

The validator may ONLY DOWNGRADE evidence_status (never upgrade). It also
never manufactures confirmation from numeric confidence or repeated signals.

Ported from backend-fix/src/lib/data-acquisition-resilience/source-pointer.ts
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.change_enums import EvidenceStatus


_REQUIRES_POINTER = frozenset({EvidenceStatus.CONFIRMED.value, EvidenceStatus.REPORTED.value})


def requires_source_pointer(status: str) -> bool:
    """Whether the given evidence status requires a verified source pointer."""
    return status in _REQUIRES_POINTER


PointerVerificationResult = Dict[str, Any]


def verify_source_pointer(
    source_span: str,
    start_offset: Optional[int],
    end_offset: Optional[int],
    original_text: str,
) -> PointerVerificationResult:
    """Verify that the candidate's source pointer exactly identifies a substring.

    Pure and deterministic. Returns {"verified": bool, "reason": str}.
    """
    if not source_span:
        return {"verified": False, "reason": "missing_source_span"}
    if start_offset is None:
        return {"verified": False, "reason": "missing_start_offset"}
    if end_offset is None:
        return {"verified": False, "reason": "missing_end_offset"}
    if start_offset < 0:
        return {"verified": False, "reason": "negative_start_offset"}
    if end_offset <= start_offset:
        return {"verified": False, "reason": "end_not_greater_than_start"}
    if end_offset > len(original_text):
        return {"verified": False, "reason": "end_beyond_original_text"}

    actual = original_text[start_offset:end_offset]
    if actual != source_span:
        return {"verified": False, "reason": "span_mismatch"}

    return {"verified": True, "reason": "exact_match"}


def enforce_evidence_status(
    status: str,
    verification: PointerVerificationResult,
) -> Dict[str, Any]:
    """Deterministic evidence-status downgrade path.

    Any confirmed/reported claim whose pointer is missing or unverifiable is
    downgraded to "unknown" (never deleted). Lower statuses are left untouched.

    Returns {"status": str, "reason": Optional[str]}.
    """
    if not requires_source_pointer(status):
        return {"status": status, "reason": None}
    if verification["verified"]:
        return {"status": status, "reason": None}
    return {"status": EvidenceStatus.UNKNOWN.value, "reason": verification["reason"]}


def enforce_source_pointer(
    candidate: Dict[str, Any],
    original_text: str,
    evidence_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Enforce the source-pointer invariant on a candidate.

    - Sets source_span_verified and the offsets.
    - If the claim is confirmed/reported but the pointer is missing/invalid,
      downgrades evidence_status to "unknown".
    - NEVER upgrades evidence_status.

    Returns (updated_candidate, downgrade_record_or_None).
    """
    verification = verify_source_pointer(
        candidate.get("source_span", ""),
        candidate.get("source_span_start_offset"),
        candidate.get("source_span_end_offset"),
        original_text,
    )

    enforced = enforce_evidence_status(
        candidate.get("evidence_status", EvidenceStatus.UNKNOWN.value),
        verification,
    )

    updated = dict(candidate)
    updated["source_span_verified"] = verification["verified"]
    updated["evidence_status"] = enforced["status"]

    if enforced["status"] == candidate.get("evidence_status") or enforced["reason"] is None:
        return updated, None

    from app.services.source_pointer_store import make_downgrade_record

    downgrade = make_downgrade_record(
        claim_id=candidate.get("id", ""),
        evidence_id=evidence_id,
        original_confidence=candidate.get("confidence"),
        final_confidence=candidate.get("confidence"),
        original_status=candidate.get("evidence_status"),
        final_status=enforced["status"],
        reason=enforced["reason"],
        source_span_before=candidate.get("source_span"),
        source_span_start_offset=candidate.get("source_span_start_offset"),
        source_span_end_offset=candidate.get("source_span_end_offset"),
    )

    return updated, downgrade


def enforce_source_pointers_for_raw_input(
    candidates: List[Dict[str, Any]],
    raw_input: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convenience: run the full enforce step over a list of candidates.

    Returns (enforced_candidates, downgrade_records).
    """
    downgrades: List[Dict[str, Any]] = []
    enforced: List[Dict[str, Any]] = []
    original_text = raw_input.get("content", "")
    evidence_id = raw_input.get("id")

    for c in candidates:
        updated, downgrade = enforce_source_pointer(c, original_text, evidence_id)
        if downgrade:
            downgrades.append(downgrade)
        enforced.append(updated)

    return enforced, downgrades


def enforce_validated_event_pointer(
    event: Dict[str, Any],
    original_text: Optional[str],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Apply the invariant to a validated care event.

    A high-confidence validated event must still be traceable to a verified
    source pointer. If not, downgrade to "unknown" (never delete the event).
    """
    status = event.get("evidence_status", EvidenceStatus.UNKNOWN.value)
    if not requires_source_pointer(status) or original_text is None:
        return event, None

    verification = verify_source_pointer(
        event.get("extracted_fact", ""),
        event.get("source_span_start_offset"),
        event.get("source_span_end_offset"),
        original_text,
    )
    enforced = enforce_evidence_status(status, verification)

    updated = dict(event)
    updated["source_span_verified"] = verification["verified"]
    updated["evidence_status"] = enforced["status"]

    if enforced["status"] == status or enforced["reason"] is None:
        return updated, None

    from app.services.source_pointer_store import make_downgrade_record

    downgrade = make_downgrade_record(
        claim_id=event.get("id", ""),
        evidence_id=event.get("raw_input_id"),
        original_confidence=event.get("confidence_score"),
        final_confidence=event.get("confidence_score"),
        original_status=status,
        final_status=enforced["status"],
        reason=enforced["reason"],
        source_span_before=event.get("extracted_fact"),
        source_span_start_offset=event.get("source_span_start_offset"),
        source_span_end_offset=event.get("source_span_end_offset"),
    )

    return updated, downgrade
