"""Regression tests for internally routed care-transition assistance."""
import asyncio

from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest
from app.services.qwen_background_worker import (
    ENABLED_WORKFLOWS,
    _classify_task,
    _route_from_text,
)


def test_care_transition_is_an_enabled_internal_workflow():
    assert "care_transition" in ENABLED_WORKFLOWS


def test_discharge_and_transition_phrasing_routes_to_care_transition():
    prompts = [
        "Help me understand the hospital discharge summary and compare it with the care record.",
        "We are moving from hospital to home. Reconcile the transition documents and open questions.",
        "What needs checking after discharge before the next caregiver takes over?",
        "Review the rehabilitation to home transition and identify missing follow-up details.",
    ]
    for prompt in prompts:
        assert _route_from_text(prompt) == "care_transition", prompt


def test_care_transition_contract_is_accepted_but_community_stays_excluded():
    payload = QwenAssistRequest(workflow="care_transition", text="Review this discharge summary.")
    assert payload.workflow == "care_transition"
    try:
        QwenAssistRequest(workflow="community", text="Find peer stories.")
    except ValidationError:
        pass
    else:
        raise AssertionError("Community must remain excluded from Qwen workflow contracts")


def test_ambiguous_transition_routing_uses_existing_internal_classifier(monkeypatch):
    # No model call should be needed for high-confidence discharge phrasing.
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("High-confidence care transition should route locally")
    monkeypatch.setattr("app.services.qwen_background_worker.qwen_chat", fail_if_called)
    assert asyncio.run(_classify_task("Please review the discharge summary.", "")) == "care_transition"
