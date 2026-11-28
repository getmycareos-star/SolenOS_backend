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
        "The AI Companion can answer questions about care.",
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
        "A companion visits your mother every afternoon.",
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


@pytest.mark.parametrize(
    ("status_code", "detail", "expected"),
    [
        (401, "JWT signature verification failed", "Your session may have expired. Please sign in again."),
        (403, "caregiver_access_grant_id is missing", "You don’t have access to that information."),
        (404, "SQLAlchemy: relation does not exist", "We couldn’t find that information."),
        (413, "Upload provider returned a payload error", "The files are too large. Choose fewer or smaller files and try again."),
        (422, "Pydantic validation failed for person_id", "We couldn’t process that information. Please check it and try again."),
        (500, "psycopg connection refused", "We couldn’t complete that request. Please try again."),
        (422, "due_at must include a timezone offset", "We couldn’t process that information. Please check it and try again."),
        (422, "Invalid caregiver_access_grant_id value", "We couldn’t process that information. Please check it and try again."),
    ],
)
def test_api_error_boundary_replaces_internal_details_without_changing_status(status_code, detail, expected):
    from app.services.caregiver_language_boundary import caregiver_safe_api_error_detail

    assert caregiver_safe_api_error_detail(status_code, detail) == expected


def test_api_error_boundary_preserves_plain_caregiver_validation_copy():
    from app.services.caregiver_language_boundary import caregiver_safe_api_error_detail

    detail = "Please choose a date before continuing."
    assert caregiver_safe_api_error_detail(422, detail) == detail


def test_api_error_boundary_scans_nested_error_details():
    from app.services.caregiver_language_boundary import caregiver_safe_api_error_detail

    detail = {"message": "The request failed", "context": ["Missing person_id", "SQLSTATE 23505"]}
    assert caregiver_safe_api_error_detail(500, detail) == "We couldn’t complete that request. Please try again."


def test_read_only_lcr_orientation_rejects_internal_language_before_returning_reply():
    from pathlib import Path

    source_path = Path("backend/app/main.py")
    if not source_path.exists():
        source_path = Path("app/main.py")
    source = source_path.read_text(encoding="utf-8")
    start = source.index('@situation_router.post("/lcr/orientation")')
    end = source.index("\n@situation_router.", start + 1)
    endpoint = source[start:end]
    assert "or contains_internal_caregiver_language(candidate)" in endpoint
    assert "We couldn’t prepare a clear answer just now." in endpoint
    assert "LCR could not produce a safe, grounded response" not in endpoint


def test_action_completion_does_not_return_internal_lifecycle_exception_text():
    from pathlib import Path

    source_path = Path("backend/app/api/actions.py")
    if not source_path.exists():
        source_path = Path("app/api/actions.py")
    source = source_path.read_text(encoding="utf-8")
    start = source.index('@router.post("/actions/{action_id}/complete"')
    end = source.index('@router.post("/actions/{action_id}/cancel"', start)
    endpoint = source[start:end]
    assert "detail=str(exc)" not in endpoint
    assert "We couldn’t confirm that this action is complete." in endpoint


def test_situation_capture_api_does_not_forward_internal_value_errors():
    from pathlib import Path

    source = Path("app/main.py").read_text(encoding="utf-8")
    assert 'detail=str(exc)' not in source
    assert "We couldn’t add that update. Please check the information and try again." in source


def test_fastapi_error_handlers_apply_the_caregiver_boundary_globally():
    from pathlib import Path

    source_path = Path("backend/app/main.py")
    if not source_path.exists():
        source_path = Path("app/main.py")
    source = source_path.read_text(encoding="utf-8")
    assert "@app.exception_handler(StarletteHTTPException)" in source
    assert "safe_detail = caregiver_safe_api_error_detail(exc.status_code, exc.detail)" in source
    assert "@app.exception_handler(RequestValidationError)" in source
    assert '"detail": "Some information is missing or needs to be corrected. Please review it and try again."' in source
    assert "@app.exception_handler(Exception)" in source
    assert '"detail": "We couldn’t complete that request. Please try again."' in source
