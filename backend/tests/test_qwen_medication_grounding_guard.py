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

def test_medication_day_refusal_with_prescriber_confirmation_is_allowed():
    reply = "Do not change the medication dose. Ask the prescriber to confirm the instructions."
    source = "Caregiver asks what to do about a medication day."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_medication_advice_refusal_with_pharmacist_handoff_is_allowed():
    reply = "I cannot provide medication advice. Speak with the pharmacist before any dose change."
    source = "Caregiver asks whether the dose should be changed."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_safe_boundary_does_not_mask_separate_unsupported_medication_action():
    reply = "Increase the medication dose tonight. Do not change medication without a prescriber."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True

def test_unsupported_imperative_medication_instruction_is_blocked():
    reply = "Increase the medication dose tonight."
    source = "Caregiver asks whether to double the dose tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True
