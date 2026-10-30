"""Regression tests for the medication-specific Qwen grounding boundary."""
from app.api.collaboration import _contains_unsupported_operational_claim


def test_safe_medication_refusal_is_not_rejected_as_unsupported_fact():
    reply = "I cannot advise a dose change. Please confirm with the prescriber."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_explicit_do_not_change_boundary_is_allowed():
    reply = "Do not change the dose without confirmation from the pharmacist."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_unsupported_medication_action_remains_blocked():
    reply = "Increase the medication dose tonight."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_unverified_medication_fact_remains_blocked():
    reply = "The medication dose was increased yesterday."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True
