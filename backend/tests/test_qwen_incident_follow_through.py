"""Regression tests for internal incident follow-through workflow routing."""

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


def test_incident_follow_through_is_enabled():
    assert "incident_follow_through" in ENABLED_WORKFLOWS


def test_common_incident_follow_up_requests_route_correctly():
    prompts = [
        "Help me follow up after a fall and identify what outcome is recorded.",
        "Track the outcome of an incident and list unresolved follow-up.",
        "What happened after the fall, and is any follow-up still undocumented?",
        "Review the missed visit follow-up in the care record.",
        "Check the follow-up on the wandering episode without assuming it was resolved.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "incident_follow_through", prompt


def test_incident_follow_through_contract_is_allowed_and_community_is_not():
    payload = QwenAssistRequest(
        workflow="incident_follow_through",
        text="Review the recorded fall and identify unresolved follow-up.",
    )
    assert payload.workflow == "incident_follow_through"

    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")
