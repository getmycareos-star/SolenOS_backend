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

    # Terminal review actions override the epistemic label for currentness.
    # A previously verified or inferred assertion that was later withdrawn,
    # rejected, or superseded remains in history but must not read as current.
    if lifecycle in {"rejected", "superseded", "retracted", "withdrawn"}:
        lifecycle_limit = (
            "This assertion is no longer current and is not a current fact; retain it only as part of the record history."
        )
        if state == "inferred":
            lifecycle_limit += " Its epistemic status remains inferred and does not make it independently verified."
        elif state == "reported":
            lifecycle_limit += " Its source remains a caregiver report, not independent verification."
        elif state == "observed":
            lifecycle_limit += " It remains a source-attributed observation, not a current observation."
        elif state == "conflicting":
            lifecycle_limit += " The historical conflict remains unresolved; do not select a winner automatically."
        elif state in {"uncertain", "missing"}:
            lifecycle_limit += " Its information remains uncertain or missing."
        elif state == "verified":
            lifecycle_limit += " Preserve the prior verification history; this does not make the assertion current."
        return ("historical_assertion", lifecycle_limit)

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
    if lifecycle in {"rejected", "superseded", "retracted", "withdrawn"}:
        return (
            "historical_assertion",
            "This assertion is no longer current and is not a current fact; retain it only as part of the record history.",
        )
    return (
        "caregiver_reported_assertion",
        "Caregiver-attributed report admitted to the LCR as unverified; do not present it as established fact.",
    )
