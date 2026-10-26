"""Regression tests for the internal critical-information completeness workflow."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_critical_information_completeness_is_enabled():
    assert "critical_information_completeness" in ENABLED_WORKFLOWS


def test_critical_information_completeness_prompts_route_correctly():
    prompts = [
        "Run a critical information completeness review.",
        "Check critical care information is complete.",
        "What critical information is missing from the care record?",
        "Review missing care information and conflicting details.",
        "Check the care record for missing essentials.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "critical_information_completeness", prompt


def test_critical_information_completeness_contract_and_community_exclusion():
    payload = QwenAssistRequest(
        workflow="critical_information_completeness",
        text="Review what critical information is missing.",
    )
    assert payload.workflow == "critical_information_completeness"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
