from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.care import CareEvent, Correction, Evidence, Person
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion
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
        caregiver_id="caregiver-1",
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
        caregiver_id="caregiver-1",
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
        caregiver_id="different-caregiver",
    )
    db = _session({Person: person, ConversationSession: None, ConversationMessage: None})
    reconciled = Mock()
    monkeypatch.setattr(execution, "reconcile_person_change_intelligence", reconciled)

    with pytest.raises(execution.CareContinuityExecutionError, match="conversation"):
        execution.execute_care_continuity_reconciliation(
            db,
            "person-1",
            workflow="companion_care_update_review",
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
        caregiver_id="caregiver-1",
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
            caregiver_id="caregiver-1",
            correction_id="correction-1",
        )

    reconciled.assert_not_called()
    db.commit.assert_not_called()


def test_shared_coordinator_returns_verified_readback_and_stage_statuses(monkeypatch):
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
        caregiver_id="caregiver-1",
        source_event_id="event-1",
        source_evidence_id="evidence-1",
        stage_statuses={
            "extraction": "available",
            "assessment": "pending",
            "review": "required",
        },
    )

    assert result["stages"] == {
        "source_preservation": "verified",
        "evidence": "verified",
        "source_message": "not_applicable",
        "extraction": "available",
        "assessment": "pending",
        "review": "required",
        "reconciliation": "staged_in_caller_transaction",
        "longitudinal_retrieval": "not_verified",
    }
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_shared_coordinator_rejects_unknown_stage_status_before_reconciliation(monkeypatch):
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
