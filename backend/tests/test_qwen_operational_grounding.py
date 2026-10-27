import pytest

from app.api.collaboration import _contains_unsupported_operational_claim
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _route_from_text


@pytest.mark.parametrize(
    "reply",
    [
        "Multi-factor authentication is not enabled.",
        "No professional access.",
        "The record is synced.",
        "The reminder was delivered.",
        "The account has been verified.",
    ],
)
def test_rejects_unverified_operational_status_claims(reply):
    assert _contains_unsupported_operational_claim(reply, "Please check the current status.", []) is True


def test_allows_explicit_uncertainty_about_unverified_sync_state():
    reply = "I cannot verify whether the record is synced because no server receipt was supplied."
    assert _contains_unsupported_operational_claim(reply, "Check sync status.", []) is False


def test_allows_status_when_authoritative_context_explicitly_confirms_it():
    assert _contains_unsupported_operational_claim(
        "Multi-factor authentication is enabled.",
        "What is the account security status?",
        ["Authoritative account setting: Multi-factor authentication is enabled."],
    ) is False


def test_rejects_reversed_weekday_correction():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The weekday was recorded as Tuesday, but I now know it was Monday."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_accepts_preserved_correction_without_claiming_a_write():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The supplied correction says Monday was recorded and Tuesday is the corrected weekday."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


def test_rejects_invented_future_uncertainty_from_past_observation():
    source = "She slept poorly last night."
    reply = "She slept poorly last night. She is unsure if she will be able to sleep tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_rejects_changed_source_attribution():
    source = "Caregiver observed she seemed tired this morning."
    reply = "She reported feeling tired this morning."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_rejects_claim_that_assistant_will_change_the_record():
    assert _contains_unsupported_operational_claim(
        "I can update the record accordingly.",
        "The earlier entry has the wrong date.",
        [],
    ) is True


@pytest.mark.parametrize(
    ("workflow", "task_text"),
    [
        ("daily_capture", "daily capture"),
        ("add_record", "add a record"),
        ("document", "uploaded document"),
        ("living_care_record", "living care record"),
        ("care_history_search", "search care history"),
        ("care_situation_change", "care situation change"),
        ("delta_engine", "delta engine"),
        ("evidence_provenance", "evidence provenance"),
        ("uncertainty_contradictions", "uncertainty and contradictions"),
        ("returning_caregiver", "returning caregiver"),
        ("appointment", "prepare appointment questions"),
        ("after_appointment", "after appointment"),
        ("handoff", "care handoff"),
        ("professional_summary", "professional summary"),
        ("open_loops", "open loops"),
        ("two_week_review", "two-week review"),
        ("decision_outcome", "decision and outcome"),
        ("medication_day", "medication day"),
        ("reminders_notifications", "reminders and notifications"),
        ("care_coordination", "care coordination"),
        ("companion", "I feel overwhelmed"),
        ("caregiver_feedback", "caregiver feedback"),
        ("sharing_export", "shareable care summary"),
        ("roles_access", "roles and access"),
        ("account_recovery", "account recovery"),
        ("settings_security", "security settings"),
        ("record_lifecycle", "record lifecycle"),
        ("offline_sync", "offline sync"),
        ("ai_reliability", "qwen reliability"),
        ("audit_review", "audit review"),
        ("care_transition", "care transition"),
        ("incident_follow_through", "incident follow-through"),
        ("emergency_readiness", "emergency readiness"),
        ("care_coverage_gap", "care coverage gap"),
        ("care_instruction_change_tracker", "care instruction change tracker"),
        ("critical_information_completeness", "critical information completeness"),
        ("personal_preferences_distress_context", "personal preferences and distress context"),
        ("caregiver_strain_support", "caregiver strain and support"),
        ("document_validity_renewal", "document validity and renewal"),
    ],
)
def test_deterministic_routing_covers_all_enabled_workflows(workflow, task_text):
    assert workflow in ENABLED_WORKFLOWS
    assert _route_from_text(task_text) == workflow
