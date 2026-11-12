"""Canonical caregiver-facing information status for LCR assertions.

Epistemic state answers what kind of information a statement is. Assertion
lifecycle answers what review action has happened. They must never be merged.
"""
from __future__ import annotations


def assertion_information_status(
    epistemic_state: str | None,
    assertion_status: str | None,
) -> tuple[str, str]:
    state = str(epistemic_state or "reported").strip().casefold()
    lifecycle = str(assertion_status or "reported").strip().casefold()

    if state == "inferred":
        return (
            "inferred_interpretation",
            "AI- or rule-derived interpretation; caregiver review does not make it independently verified.",
        )
    if state == "verified":
        return (
            "verified",
            "A persisted verification state is recorded; retain its source and verification history.",
        )
    if state == "observed":
        return (
            "source_attributed_observation",
            "A direct observation is recorded with its source and observation time.",
        )
    if state == "conflicting":
        return (
            "conflicting_information",
            "Conflicting information is present; do not select a winner automatically.",
        )
    if state in {"uncertain", "missing"}:
        return (
            state,
            "The information is uncertain or missing; do not present it as established.",
        )
    if lifecycle in {"accepted", "caregiver_reviewed"}:
        return (
            "caregiver_reviewed_report",
            "A caregiver reviewed this report; review does not establish independent or clinical verification.",
        )
    if lifecycle in {"rejected", "superseded", "withdrawn"}:
        return (
            "historical_assertion",
            "This assertion is no longer current; retain it only as part of the record history.",
        )
    return (
        "caregiver_reported_assertion",
        "Caregiver-attributed report admitted to the LCR as unverified; do not present it as established fact.",
    )
