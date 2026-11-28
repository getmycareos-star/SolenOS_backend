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
        "Confidence scores: 0.82.",
        "Hash: a9f1c2d3.",
        "FastAPI returned a response payload.",
        "The person_id is missing from the request.",
        "Document processing_failed.",
        "Tika extraction is unavailable.",
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


def test_companion_validator_runs_language_guard_independently_of_formatting_checks():
    from pathlib import Path

    source_path = Path("backend/app/api/collaboration.py")
    if not source_path.exists():
        source_path = Path("app/api/collaboration.py")
    source = source_path.read_text(encoding="utf-8")
    start = source.index("    def response_rejection_reasons(candidate: str)")
    end = source.index("\n    rejection_reasons = response_rejection_reasons(reply)", start)
    validator = source[start:end]
    language_check = validator.index("if contains_internal_caregiver_language(candidate):")
    formatting_check = validator.index("if _companion_reply_needs_revision(candidate")
    assert language_check < formatting_check
    assert 'reasons.append("internal_caregiver_language")' in validator


def test_ordinary_companion_conversation_checks_every_final_reply():
    from pathlib import Path

    source_path = Path("backend/app/api/collaboration.py")
    if not source_path.exists():
        source_path = Path("app/api/collaboration.py")
    source = source_path.read_text(encoding="utf-8")
    start = source.index('@router.post("/conversation/message")')
    end = source.find("\\n@router.", start + 1)
    companion_message = source[start:end if end >= 0 else None]
    assert "or contains_internal_caregiver_language(reply)" in companion_message
    assert "if contains_internal_caregiver_language(reply):" in companion_message
    assert "Companion final response rejected by caregiver-language boundary" in companion_message
    assert "No reply was saved; please retry." in companion_message
