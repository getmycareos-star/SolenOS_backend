import pytest

from app.api.collaboration import _contains_unsupported_operational_claim
from app.services.qwen_background_worker import ENABLED_WORKFLOWS, _strong_workflow_hint


@pytest.mark.parametrize(
    ("workflow", "task"),
    [
        ("daily_capture", "Please organize today's caregiver observation into reported facts and unknowns."),
        ("add_record", "Help me add a record entry from this note."),
        ("document", "Explain this exact sentence from a document."),
        ("living_care_record", "Summarize the available care record."),
        ("care_history_search", "Search the available care history for a dentist visit."),
        ("care_situation_change", "Compare these two reports."),
        ("delta_engine", "Review this candidate change against the prior note."),
        ("evidence_provenance", "Label the source and certainty of this claim."),
        ("uncertainty_contradictions", "These two notes conflict."),
        ("returning_caregiver", "I am a returning caregiver."),
        ("appointment", "Prepare questions for my appointment."),
        ("after_appointment", "Organize my after-appointment notes."),
        ("handoff", "Draft a care handoff."),
        ("professional_summary", "Prepare a professional-facing summary."),
        ("open_loops", "Identify the unresolved question."),
        ("two_week_review", "Review the last fourteen days."),
        ("decision_outcome", "Separate the decision from its outcome."),
        ("medication_day", "Organize this medication note."),
        ("reminders_notifications", "Check whether a reminder was delivered."),
        ("care_coordination", "Organize care coordination."),
        ("companion", "I feel overwhelmed today."),
        ("caregiver_feedback", "Report that an entry is wrong."),
        ("sharing_export", "Prepare a shareable summary."),
        ("roles_access", "Who can access this record?"),
        ("account_recovery", "I cannot sign in."),
        ("settings_security", "Is multi-factor authentication enabled?"),
        ("record_lifecycle", "Explain archiving a record."),
        ("offline_sync", "Are offline changes synced?"),
        ("ai_reliability", "The assistant request returned an error."),
        ("audit_review", "Summarize audit records."),
    ],
)
def test_internal_routing_covers_all_30_enabled_workflows(workflow, task):
    assert workflow in ENABLED_WORKFLOWS
    assert _strong_workflow_hint(task) == workflow


def test_community_is_not_an_enabled_background_workflow():
    assert "community" not in ENABLED_WORKFLOWS


def test_rejects_reversed_weekday_correction():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The weekday was recorded as Tuesday, but I now know it was Monday."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_accepts_preserved_correction_without_claiming_a_write():
    source = "The weekday was recorded as Monday, but I now know it was Tuesday."
    reply = "The supplied correction says Monday was recorded and Tuesday is the corrected weekday."
    assert _contains_unsupported_operational_claim(reply, source, []) is False


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


def test_rejects_inferred_medication_from_generic_document_sentence():
    assert _contains_unsupported_operational_claim(
        "The sentence is a general instruction for taking medication.",
        'Explain this exact sentence from a document: "Take with food."',
        [],
    ) is True


def test_rejects_claim_that_assistant_will_change_the_record():
    assert _contains_unsupported_operational_claim(
        "I can update the record accordingly.",
        "The earlier entry has the wrong date.",
        [],
    ) is True


def test_allows_status_when_authoritative_context_explicitly_confirms_it():
    assert _contains_unsupported_operational_claim(
        "Multi-factor authentication is enabled.",
        "What is the account security status?",
        ["Authoritative account setting: Multi-factor authentication is enabled."],
    ) is False

def test_rejects_invented_future_uncertainty_from_past_observation():
    source = "She slept poorly last night."
    reply = "She slept poorly last night. She is unsure if she will be able to sleep tonight."
    assert _contains_unsupported_operational_claim(reply, source, []) is True


def test_rejects_changed_source_attribution():
    source = "Caregiver observed she seemed tired this morning."
    reply = "She reported feeling tired this morning."
    assert _contains_unsupported_operational_claim(reply, source, []) is True

def test_allows_explicit_uncertainty_about_unverified_sync_state():
    reply = "I cannot verify whether the record is synced because no server receipt was supplied."
    assert _contains_unsupported_operational_claim(
        reply, "No synchronization status or server receipt is supplied.", []
    ) is False
