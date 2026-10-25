""""Contract checks for the enabled SolenOS Qwen workflows.

These checks prove accepted workflow IDs and Community exclusion only. They do
not claim live inference or durable background-job execution; those require
separate integration tests.
"""
import pytest
from pydantic import ValidationError

from app.api.collaboration import QwenAssistRequest


ENABLED_WORKFLOWS = (
    "daily_capture", "add_record", "document", "living_care_record",
    "care_history_search", "care_situation_change", "delta_engine",
    "evidence_provenance", "uncertainty_contradictions", "returning_caregiver",
    "appointment", "after_appointment", "handoff", "professional_summary",
    "open_loops", "two_week_review", "decision_outcome", "medication_day",
    "reminders_notifications", "care_coordination", "companion",
    "caregiver_feedback", "sharing_export", "roles_access", "account_recovery",
    "settings_security", "record_lifecycle", "offline_sync", "ai_reliability",
    "audit_review",
)


@pytest.mark.parametrize("workflow", ENABLED_WORKFLOWS)
def test_all_30_enabled_solenos_workflows_are_accepted(workflow):
    request = QwenAssistRequest(workflow=workflow, text="Help organize the supplied information.")
    assert request.workflow == workflow


def test_community_workflow_is_rejected_while_community_is_disabled():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="community", text="Summarize peer experiences.")


def test_unknown_workflow_is_rejected():
    with pytest.raises(ValidationError):
        QwenAssistRequest(workflow="not_a_solenos_workflow", text="Help me.")
