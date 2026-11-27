import pytest

from app.services.caregiver_language_boundary import (
    caregiver_language_violations,
    contains_internal_caregiver_language,
)


@pytest.mark.parametrize(
    "text",
    [
        "The Qwen inference pipeline returned a payload.",
        "The epistemic status is uncertain.",
        "PostgreSQL database unavailable.",
        "The backend returned HTTP 500.",
        "The source_id is 12345.",
        "Confidence score: 0.82.",
        "The hidden reasoning is not part of the answer.",
        "Request ID: 123e4567-e89b-42d3-a456-426614174000.",
    ],
)
def test_internal_implementation_language_is_rejected(text):
    assert contains_internal_caregiver_language(text)
    assert caregiver_language_violations(text)


@pytest.mark.parametrize(
    "text",
    [
        "Your mother takes 5 mg of metoprolol each morning.",
        "Her blood pressure was 120/80 mmHg.",
        "The MRI is scheduled for Tuesday.",
        "The doctor recorded a diagnosis of dementia.",
        "You reported that she slept poorly last night.",
        "The original document says to take one tablet daily.",
    ],
)
def test_legitimate_care_information_is_not_misclassified(text):
    assert not contains_internal_caregiver_language(text)
    assert caregiver_language_violations(text) == ()


def test_empty_generated_response_is_rejected():
    assert caregiver_language_violations("   ") == ("empty_response",)
