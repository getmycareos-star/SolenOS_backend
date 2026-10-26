"""Regression tests for internal care coverage gap workflow routing."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_care_coverage_gap_is_enabled():
    assert "care_coverage_gap" in ENABLED_WORKFLOWS


def test_care_coverage_gap_prompts_route_correctly():
    prompts = [
        "Review care coverage gaps in the existing record.",
        "Is there a gap in care coverage?",
        "Check for care coverage gaps without guessing who is available.",
        "Review the caregiver coverage gap for the scheduled visit.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "care_coverage_gap", prompt


def test_care_coverage_gap_contract_is_allowed_and_community_is_not():
    payload = QwenAssistRequest(workflow="care_coverage_gap", text="Review documented care coverage gaps.")
    assert payload.workflow == "care_coverage_gap"
    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
