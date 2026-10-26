"""Regression tests for the internal care-instruction change tracker workflow."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_care_instruction_change_tracker_is_enabled():
    assert "care_instruction_change_tracker" in ENABLED_WORKFLOWS


def test_care_instruction_change_prompts_route_correctly():
    prompts = [
        "Review the care instruction change tracker.",
        "Compare old and new care instructions from the record.",
        "Check the documented effective date for updated care instructions.",
        "Identify conflicting care instructions without choosing which one wins.",
        "Track a care instruction change and preserve its source.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "care_instruction_change_tracker", prompt


def test_care_instruction_change_contract_is_allowed_and_community_is_not():
    payload = QwenAssistRequest(
        workflow="care_instruction_change_tracker",
        text="Compare the documented care instruction changes.",
    )
    assert payload.workflow == "care_instruction_change_tracker"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
