"""Regression tests for the personal preferences and distress context workflow."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_personal_preferences_distress_context_is_enabled():
    assert "personal_preferences_distress_context" in ENABLED_WORKFLOWS


def test_personal_preferences_distress_prompts_route_correctly():
    prompts = [
        "Review personal preferences and distress context.",
        "What are the documented personal preferences?",
        "Summarize known distress triggers from the care record.",
        "What helps when they are distressed according to recorded evidence?",
        "Review documented comfort preferences and communication needs.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "personal_preferences_distress_context", prompt


def test_personal_preferences_distress_contract_and_community_exclusion():
    payload = QwenAssistRequest(
        workflow="personal_preferences_distress_context",
        text="Summarize documented preferences and distress context.",
    )
    assert payload.workflow == "personal_preferences_distress_context"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
