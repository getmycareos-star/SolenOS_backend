from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.care import CareEvent, Correction, Evidence, Person
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion
from app.models.appointment_workspace import AppointmentWorkspaceRecord, AppointmentWorkspaceRevision
from app.models.privacy_delivery import HandoffDraft, HandoffDraftRevision
from app.services import care_continuity_execution as execution


class _Query:
    def __init__(self, row):
        self.row = row

    def filter(self, *args):
        return self

    def first(self):
        return self.row


def _session(rows):
    db = Mock()
    db.query.side_effect = lambda model: _Query(rows.get(model))
    return db


def test_shared_coordinator_validates_lineage_and_leaves_transaction_to_caller(monkeypatch):
    person = SimpleNamespace(id="person-1")
    event = SimpleNamespace(id="event-1", evidence_ids=["evidence-1"])
    evidence = SimpleNamespace(id="evidence-1", person_id="person-1")
    db = _session({Person: person, CareEvent: event, Evidence: evidence})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="daily_capture_review",
        source_event_id="event-1",
        source_evidence_id="evidence-1",
    )

    assert result["contract_version"] == "1.0"
    assert result["stages"]["source_preservation"] == "verified"
    assert result["stages"]["evidence"] == "verified"
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    assert result["transaction_owner"] == "caller"
    reconciled.assert_called_once_with(db, "person-1", None)
    assert db.flush.call_count == 2
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_shared_coordinator_rejects_cross_person_source_before_reconciliation(monkeypatch):
    person = SimpleNamespace(id="person-1")
    db = _session({Person: person, CareEvent: None, Evidence: None})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="document_candidate_review",
            source_evidence_id="evidence-owned-by-another-person",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_unlinked_event_and_evidence(monkeypatch):
    person = SimpleNamespace(id="person-1")
    event = SimpleNamespace(id="event-1", evidence_ids=["different-evidence"])
    evidence = SimpleNamespace(id="evidence-1", person_id="person-1")
    db = _session({Person: person, CareEvent: event, Evidence: evidence})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="not linked"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="daily_capture_review",
            source_event_id="event-1",
            source_evidence_id="evidence-1",
        )

    reconciled.assert_not_called()


def test_shared_coordinator_validates_companion_source_message_and_caregiver(monkeypatch):
    person = SimpleNamespace(id="person-1")
    conversation = SimpleNamespace(
        id="conversation-1",
        person_id="person-1",
        source_caregiver_id="caregiver-1",
    )
    message = SimpleNamespace(
        id="message-1",
        session_id="conversation-1",
        role="user",
    )
    db = _session({
        Person: person,
        ConversationSession: conversation,
        ConversationMessage: message,
    })
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="companion_care_update_review",
        source_caregiver_id="caregiver-1",
        source_message_id="message-1",
        source_conversation_id="conversation-1",
    )

    assert result["stages"]["source_message"] == "verified"
    assert result["stages"]["evidence"] == "not_applicable"
    reconciled.assert_called_once_with(db, "person-1", None)
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_companion_message_from_another_caregiver(monkeypatch):
    person = SimpleNamespace(id="person-1")
    conversation = SimpleNamespace(
        id="conversation-1",
        person_id="person-1",
        source_caregiver_id="different-caregiver",
    )
    db = _session({Person: person, ConversationSession: None, ConversationMessage: None})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="conversation"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="companion_care_update_review",
            source_caregiver_id="caregiver-1",
            caregiver_id="caregiver-1",
            source_message_id="message-1",
            source_conversation_id="conversation-1",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_validates_correction_and_reassesses_in_caller_transaction(monkeypatch):
    person = SimpleNamespace(id="person-1")
    correction = SimpleNamespace(
        id="correction-1",
        person_id="person-1",
        caregiver_id="caregiver-1",
    )
    db = _session({Person: person, Correction: correction})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="care_record_correction",
        source_caregiver_id="caregiver-1",
        correction_id="correction-1",
    )

    assert result["correction_id"] == "correction-1"
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    reconciled.assert_called_once_with(db, "person-1", None)
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_correction_from_another_caregiver(monkeypatch):
    person = SimpleNamespace(id="person-1")
    correction = SimpleNamespace(
        id="correction-1",
        person_id="person-1",
        caregiver_id="different-caregiver",
    )
    db = _session({Person: person, Correction: correction})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="authenticated caregiver"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="care_record_correction",
            source_caregiver_id="caregiver-1",
            correction_id="correction-1",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_validates_source_assertion_scope(monkeypatch):
    person = SimpleNamespace(id="person-1")
    assertion = SimpleNamespace(id="assertion-1", person_id="person-1")
    db = _session({Person: person, CareAssertion: assertion})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="care_assertion_review",
        source_assertion_id="assertion-1",
    )

    assert result["source_assertion_id"] == "assertion-1"
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    reconciled.assert_called_once_with(db, "person-1", None)
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_assertion_from_another_person(monkeypatch):
    person = SimpleNamespace(id="person-1")
    db = _session({Person: person, CareAssertion: None})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="another care record"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="care_assertion_review",
            source_assertion_id="assertion-from-another-person",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()



def test_shared_coordinator_validates_appointment_workspace_revision_and_keeps_caller_transaction(monkeypatch):
    person = SimpleNamespace(id="person-1")
    workspace = SimpleNamespace(
        id="workspace-1",
        person_id="person-1",
        created_by_caregiver_id="caregiver-1",
    )
    revision = SimpleNamespace(
        id="revision-1",
        record_id="workspace-1",
        person_id="person-1",
        changed_by_caregiver_id="caregiver-1",
    )
    db = _session({
        Person: person,
        AppointmentWorkspaceRecord: workspace,
        AppointmentWorkspaceRevision: revision,
    })
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="appointment_outcome_review",
        source_caregiver_id="caregiver-1",
        source_appointment_workspace_id="workspace-1",
        source_appointment_workspace_revision_id="revision-1",
    )

    assert result["source_appointment_workspace_id"] == "workspace-1"
    assert result["source_appointment_workspace_revision_id"] == "revision-1"
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    reconciled.assert_called_once_with(db, "person-1", None)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_shared_coordinator_rejects_appointment_revision_from_another_person(monkeypatch):
    person = SimpleNamespace(id="person-1")
    workspace = SimpleNamespace(id="workspace-1", person_id="person-1")
    db = _session({
        Person: person,
        AppointmentWorkspaceRecord: workspace,
        AppointmentWorkspaceRevision: None,
    })
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="another care record"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="appointment_outcome_review",
            source_appointment_workspace_id="workspace-1",
            source_appointment_workspace_revision_id="revision-from-another-person",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_incomplete_appointment_revision_reference(monkeypatch):
    person = SimpleNamespace(id="person-1")
    db = _session({Person: person})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="supplied together"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="appointment_outcome_review",
            source_appointment_workspace_id="workspace-1",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()



def test_shared_coordinator_validates_multiple_persisted_source_events(monkeypatch):
    person = SimpleNamespace(id="person-1")
    event_one = SimpleNamespace(id="event-1", person_id="person-1", evidence_ids=[])
    event_two = SimpleNamespace(id="event-2", person_id="person-1", evidence_ids=[])
    db = Mock()
    db.query.side_effect = [
        _Query(person),
        _Query(event_one),
        _Query(event_two),
    ]
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="qwen_assistance_summary_review",
        source_event_ids=["event-1", "event-2"],
    )

    assert result["source_event_id"] is None
    assert result["source_event_ids"] == ["event-1", "event-2"]
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    reconciled.assert_called_once_with(db, "person-1", None)
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_shared_coordinator_validates_read_only_handoff_draft_without_reconciliation(monkeypatch):
    person = SimpleNamespace(id="person-1")
    draft = SimpleNamespace(
        id="draft-1",
        person_id="person-1",
        caregiver_id="caregiver-1",
    )
    revision = SimpleNamespace(
        id="draft-revision-1",
        draft_id="draft-1",
        person_id="person-1",
        caregiver_id="caregiver-1",
        revision_number=3,
    )
    db = _session({Person: person, HandoffDraft: draft, HandoffDraftRevision: revision})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="handoff_draft_approval",
        source_caregiver_id="caregiver-1",
        source_handoff_draft_id="draft-1",
        source_handoff_draft_revision_number=3,
        reconcile_lcr=False,
    )

    assert result["stages"]["handoff_draft"] == "verified"
    assert result["stages"]["handoff_draft_revision"] == "verified"
    assert result["stages"]["reconciliation"] == "not_applicable_read_only_output"
    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_handoff_draft_from_another_caregiver(monkeypatch):
    person = SimpleNamespace(id="person-1")
    db = _session({Person: person, HandoffDraft: None, HandoffDraftRevision: None})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="another care record or caregiver"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="handoff_draft_approval",
            source_caregiver_id="caregiver-1",
            source_handoff_draft_id="draft-owned-by-another-caregiver",
            source_handoff_draft_revision_number=3,
            reconcile_lcr=False,
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_incomplete_handoff_revision_reference(monkeypatch):
    person = SimpleNamespace(id="person-1")
    db = _session({Person: person})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="supplied together"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="handoff_draft_approval",
            source_handoff_draft_id="draft-1",
            reconcile_lcr=False,
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_rejects_appointment_revision_from_another_caregiver(monkeypatch):
    person = SimpleNamespace(id="person-1")
    workspace = SimpleNamespace(
        id="workspace-1",
        person_id="person-1",
        created_by_caregiver_id="caregiver-1",
    )
    revision = SimpleNamespace(
        id="revision-1",
        record_id="workspace-1",
        person_id="person-1",
        changed_by_caregiver_id="different-caregiver",
    )
    db = _session({
        Person: person,
        AppointmentWorkspaceRecord: workspace,
        AppointmentWorkspaceRevision: revision,
    })
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="authenticated caregiver"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="appointment_outcome_review",
            source_caregiver_id="caregiver-1",
            source_appointment_workspace_id="workspace-1",
            source_appointment_workspace_revision_id="revision-1",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_returns_execution_stage_receipt(monkeypatch):
    person = SimpleNamespace(id="person-1")
    event = SimpleNamespace(
        id="event-1",
        evidence_ids=["evidence-1"],
        created_by_caregiver_id="caregiver-1",
    )
    evidence = SimpleNamespace(
        id="evidence-1",
        person_id="person-1",
        uploaded_by_caregiver_id="caregiver-1",
    )
    db = _session({Person: person, CareEvent: event, Evidence: evidence})
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", Mock())

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="daily_capture",
        source_caregiver_id="caregiver-1",
        source_event_id="event-1",
        source_evidence_id="evidence-1",
        stage_statuses={
            "extraction": "available",
            "assessment": "pending",
            "review": "required",
        },
    )

    assert result["stages"]["source_preservation"] == "verified"
    assert result["stages"]["evidence"] == "verified"
    assert result["stages"]["extraction"] == "available"
    assert result["stages"]["assessment"] == "pending"
    assert result["stages"]["review"] == "required"
    assert result["stages"]["reconciliation"] == "staged_in_caller_transaction"
    assert result["stages"]["longitudinal_retrieval"] == "not_verified"
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_shared_coordinator_rejects_unknown_execution_stage_before_reconciliation(monkeypatch):
    person = SimpleNamespace(id="person-1")
    event = SimpleNamespace(id="event-1", evidence_ids=[])
    db = _session({Person: person, CareEvent: event})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="Unsupported execution stage"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="daily_capture",
            source_event_id="event-1",
            stage_statuses={"unknown": "done"},
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()
