"""Regression tests for evidence-grounded reassurance in Companion replies."""

import pytest

from app.api import collaboration


@pytest.mark.parametrize(
    "reply",
    [
        "Everything will be okay.",
        "Everything is fine.",
        "Nothing is wrong.",
        "All is well.",
        "Mum is fine.",
        "Dad is okay.",
        "She will be fine.",
        "I don't know, everything will be okay.",
        "I'm not upset. Everything will be okay.",
        "She is safe now.",
        "There is no immediate danger.",
        "There is nothing to worry about.",
        "There is no need to worry.",
        "Don't worry about it.",
        "It's probably nothing.",
        "This is probably nothing serious.",
        "I'm sure she's fine.",
        "He will definitely be fine.",
        "She should be fine.",
        "They are probably okay.",
        "Mum seems stable.",
        "Everything appears stable.",
        "It doesn't sound serious.",
    ],
)
def test_companion_rejects_categorical_reassurance_without_emotional_prompt(reply):
    assert collaboration._contains_unsupported_reassurance(reply)
    # The check applies to factual requests too; it is not limited to
    # messages where the caregiver has explicitly expressed distress.
    assert collaboration._caregiver_response_needs_revision(
        "What do the notes say?",
        reply,
    )


@pytest.mark.parametrize(
    "reply",
    [
        "I can't promise that everything will be okay.",
        "I cannot confirm that there is nothing to worry about from these notes.",
        "The available record does not establish that everything will be okay.",
        "I don't have enough evidence to say she should be fine.",
        "I can't tell whether this is serious from the information provided.",
        "The notes describe what was reported, but they do not establish her current condition.",
        "I hope things become easier, but I can't predict the outcome.",
    ],
)
def test_companion_allows_explicit_uncertainty_instead_of_false_reassurance(reply):
    assert not collaboration._contains_unsupported_reassurance(reply)
    assert not collaboration._contains_fabricated_empathy(reply)
    assert not collaboration._caregiver_response_needs_revision(
        "What do the notes say?",
        reply,
    )


def test_companion_does_not_reject_plain_acknowledgement_or_grounded_next_step():
    reply = (
        "I can see why you want a clear answer. The record does not establish "
        "what caused the change, so please contact her care professional for guidance."
    )
    assert not collaboration._contains_unsupported_reassurance(reply)
    assert not collaboration._caregiver_response_needs_revision(
        "What do the notes say?",
        reply,
    )


def test_companion_reassurance_guard_handles_empty_and_non_string_values():
    assert not collaboration._contains_unsupported_reassurance("")
    assert not collaboration._contains_unsupported_reassurance(None)
    assert not collaboration._contains_unsupported_reassurance(123)



@pytest.mark.parametrize(
    "reply",
    [
        "I know exactly how you feel.",
        "I know how you feel.",
        "I understand exactly how you feel.",
        "I feel your pain.",
        "I've been through this too.",
        "I've been there.",
        "As a caregiver myself, I understand.",
        "You must be feeling guilty.",
        "You are probably feeling overwhelmed.",
    ],
)
def test_companion_rejects_fabricated_empathy_and_unfounded_emotional_assumptions(reply):
    assert collaboration._contains_fabricated_empathy(reply)
    assert collaboration._caregiver_response_needs_revision(
        "I feel exhausted and guilty.",
        reply,
    )


@pytest.mark.parametrize(
    "reply",
    [
        "You said you feel exhausted. We can take this one concern at a time.",
        "I can help you review the information without assuming how you feel.",
        "That sounds difficult. What would be most useful to focus on right now?",
    ],
)
def test_companion_allows_proportionate_acknowledgement_without_claiming_lived_experience(reply):
    assert not collaboration._contains_fabricated_empathy(reply)
