import pytest

from app.api.collaboration import _is_companion_identity_question


@pytest.mark.parametrize(
    "question",
    [
        "Who are you?",
        "Who made you?",
        "Who created you?",
        "Who founded SolenOS?",
        "Who is your creator?",
        "Who owns you?",
        "Are you human?",
        "Are you a doctor?",
        "Are you ChatGPT?",
        "What is your purpose?",
        "What can you do?",
        "Do you remember everything?",
        "Can you make mistakes?",
        "Can I trust everything you say?",
    ],
)
def test_explicit_product_identity_questions_are_classified_without_care_history(question):
    assert _is_companion_identity_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "What changed in Mum's sleep this week?",
        "Did Mum take her evening medication?",
        "Who was with Dad when he fell?",
        "What did the doctor say at the last appointment?",
        "Can you help me prepare questions for the neurologist?",
        "What should I do about this new behavior?",
    ],
)
def test_care_questions_are_not_misclassified_as_product_identity(question):
    assert not _is_companion_identity_question(question)
