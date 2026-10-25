"""Contract coverage for the shared Qwen workflow selector.

These tests verify API schema coverage only. They do not substitute for live
Qwen inference, authorization, persistence, or production integration tests.
"""
import pytest
from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest


WORKFLOWS = (
    "daily_capture",
    "add_record",
    "document",
    "living_care_record",
    "care_history_search",
    "care_situation_change",
    "delta_engine",
    "evidence_provenance",
    "uncertainty_contradictions",
    "returning_caregiver",
    "appointment",
    "after_appointment",
    "handoff",
    "professional_summary",
    "open_loops",
    "two_week_review",
    "decision_outcome",
    "medication_day",
    "reminders_notifications",
    "care_coordination",
    "companion",
    "caregiver_feedback",
    "sharing_export",
    "roles_access",
    "account_recovery",
    "settings_security",
    "record_lifecycle",
    "offline_sync",
    "ai_reliability",
    "audit_review",
)


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_all_solenos_workflows_are_accepted_by_shared_qwen_contract(workflow):
    request = QwenAssistRequest(workflow=workflow, text="Help me organize this information.")
    assert request.workflow == workflow
    assert request.text == "Help me organize this information."


def test_unknown_workflow_is_rejected():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="unregistered_workflow", text="Help me.")

from app.api.collaboration import _greeting_reply_needs_revision


@pytest.mark.parametrize("reply", ["Hello!", "Hi there!", "Good morning!"])
def test_clear_short_greeting_passes_quality_guard(reply):
    assert _greeting_reply_needs_revision(reply) is False


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "Hello! How are you doing?",
        "Hi there! How can I help you today?",
        "Hello? What do you need?",
    ],
)
def test_interview_style_or_empty_greeting_fails_quality_guard(reply):
    assert _greeting_reply_needs_revision(reply) is True



def test_community_workflow_is_not_supported_while_community_is_disabled():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="community", text="Summarize peer experiences.")
