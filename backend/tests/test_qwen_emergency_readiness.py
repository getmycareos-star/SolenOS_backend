"""Regression tests for internal emergency-readiness workflow routing."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_emergency_readiness_is_enabled():
    assert "emergency_readiness" in ENABLED_WORKFLOWS


def test_emergency_readiness_requests_route_correctly():
    prompts = [
        "Review emergency readiness information in the care record.",
        "What information is missing for an emergency?",
        "Help me review the documented emergency plan and contacts.",
        "Check emergency handoff information without inventing missing details.",
        "Summarize the care emergency plan from existing records.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "emergency_readiness", prompt


def test_emergency_readiness_contract_is_allowed_and_community_is_not():
    payload = QwenAssistRequest(
        workflow="emergency_readiness",
        text="Review emergency readiness and identify information not recorded.",
    )
    assert payload.workflow == "emergency_readiness"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
