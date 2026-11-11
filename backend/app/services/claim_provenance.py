"""Claim-level source matching for caregiver-facing Companion responses.

Only exact saved wording is presented as a source match. Paraphrases and claims
that cannot be matched to an exact source passage remain explicitly unsupported.
"""
from __future__ import annotations

import re
from typing import Any


def _normalise(value: str) -> str:
    value = (value or "").casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\r\n.,;:!?\"'()[]{}")


def split_response_claims(reply: str) -> list[str]:
    """Split a response into displayable statements without inventing claims."""
    statements = []
    for line in (reply or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", line).strip()
        if not cleaned:
            continue
        statements.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9\"'])", cleaned)
            if part.strip()
        )
    return statements


def build_claim_provenance(reply: str, source_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Link only statements whose wording occurs in a saved source.

    A match is not an independence assessment and does not establish clinical
    truth. Duplicate records remain separate references and are not counted as
    independent corroboration.
    """
    historical_statuses = {"corrected", "superseded", "withdrawn", "rejected", "retracted", "expired", "deleted", "archived"}
    claims = []
    for index, statement in enumerate(split_response_claims(reply)):
        normalized_claim = _normalise(statement)
        matches = []
        current_matches = 0
        if normalized_claim:
            for source in source_records:
                source_text = str(source.get("source_text") or "")
                normalized_source = _normalise(source_text)
                if normalized_claim and normalized_claim in normalized_source:
                    is_historical = str(source.get("record_status") or "").casefold() in historical_statuses
                    if not is_historical:
                        current_matches += 1
                    matches.append({
                        "record_type": source.get("record_type"),
                        "record_id": str(source.get("record_id")),
                        "label": source.get("label") or "Saved care record",
                        "matched_passage": statement,
                        "source_excerpt": source_text[:700],
                        "occurred_at": source.get("occurred_at"),
                        "recorded_at": source.get("recorded_at"),
                        "reported_by": source.get("reported_by"),
                        "entered_by": source.get("entered_by"),
                        "record_status": source.get("record_status"),
                        "source_independence_status": source.get("source_independence_status", "not_assessed"),
                        "exact_wording_duplicate_count": int(source.get("exact_wording_duplicate_count") or 0),
                        "source_state_label": source.get("source_state_label") or ("Earlier or corrected record; not treated as current" if is_historical else None),
                        "correction_note": source.get("correction_note"),
                        "correction_rationale": source.get("correction_rationale"),
                        "previous_source_excerpt": source.get("previous_source_excerpt"),
                        "related_evidence_ids": list(source.get("related_evidence_ids") or []),
                        "related_evidence": list(source.get("related_evidence") or []),
                    })
        claims.append({
            "claim_id": f"statement-{index + 1}",
            "text": statement,
            "support_status": (
                "matched_saved_wording" if current_matches else
                "matched_historical_only" if matches else
                "not_matched_to_saved_wording"
            ),
            "source_references": matches,
        })
    unmatched = sum(1 for claim in claims if claim["support_status"] != "matched_saved_wording")
    return {
        "version": "claim-provenance-v1",
        "coverage": "all_statements_matched" if claims and unmatched == 0 else (
            "some_statements_unmatched" if claims else "no_statements"
        ),
        "statement_count": len(claims),
        "unmatched_statement_count": unmatched,
        "matching_limit": (
            "A match means the statement wording appears in the saved source. "
            "It does not establish source independence, authenticity, or clinical truth. "
            "Paraphrases are left unmatched rather than assigned an unrelated source."
        ),
        "claims": claims,
    }
