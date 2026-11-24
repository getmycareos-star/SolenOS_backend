"""Deterministic guard against invented numeric confidence and certainty claims."""

import pytest

from app.api.collaboration import _contains_unsupported_numeric_confidence


@pytest.mark.parametrize(
    "reply",
    [
        "I am 92% confident that this is the correct interpretation.",
        "Confidence score: 0.82.",
        "The certainty level is 85%.",
        "I am 0.91 certain about this conclusion.",
        "This answer has an 8/10 confidence rating.",
        "My confidence is 8 out of 10.",
    ],
)
def test_rejects_unsupported_numeric_confidence_and_certainty(reply):
    assert _contains_unsupported_numeric_confidence(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "The document reports an oxygen saturation of 92%.",
        "The recorded blood pressure is 120/80.",
        "Two caregiver reports conflict, and the record does not resolve them.",
        "I cannot confirm that from the information available.",
        "The caregiver reported that she slept for about six hours.",
    ],
)
def test_does_not_misclassify_clinical_measurements_or_plain_language_uncertainty(reply):
    assert not _contains_unsupported_numeric_confidence(reply)
