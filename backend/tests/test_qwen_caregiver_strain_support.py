"""Regression tests for the caregiver strain and support workflow."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_caregiver_strain_support_is_enabled():
    assert "caregiver_strain_support" in ENABLED_WORKFLOWS


def test_caregiver_strain_support_prompts_route_correctly():
    prompts = [
        "Review caregiver strain and support needs.",
        "Summarize documented caregiver stress.",
        "The caregiver is overwhelmed; review recorded support requests.",
        "Review caregiver respite needs and workload concerns.",
        "What caregiver support needs have been recorded?",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "caregiver_strain_support", prompt


def test_caregiver_strain_support_contract_and_community_exclusion():
    payload = QwenAssistRequest(
        workflow="caregiver_strain_support",
        text="Summarize documented caregiver strain and support requests.",
    )
    assert payload.workflow == "caregiver_strain_support"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
