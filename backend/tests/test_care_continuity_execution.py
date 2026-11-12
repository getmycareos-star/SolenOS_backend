from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.care import CareEvent, Caregiver, Evidence, Person
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


def test_shared_coordinator_returns_verified_readback_and_caller_owned_status(monkeypatch):
    person = SimpleNamespace(id="person-1")
    caregiver = SimpleNamespace(id="caregiver-1", person_id="person-1")
    event = SimpleNamespace(id="event-1", evidence_ids=["evidence-1"])
    evidence = SimpleNamespace(id="evidence-1", person_id="person-1")
    db = _session({
        Person: person,
        Caregiver: caregiver,
        CareEvent: event,
        Evidence: evidence,
    })
    monkeypatch.setattr(
        execution,
        "reconcile_person_change_intelligence",
        Mock(),
    )
    monkeypatch.setattr(
        execution,
        "build_lcr_context",
        Mock(return_value={"missing_required_sources": []}),
    )

    result = execution.execute_care_continuity_reconciliation(
        db,
        "person-1",
        workflow="daily_capture",
        source_event_id="event-1",
        source_evidence_id="evidence-1",
        caregiver_id="caregiver-1",
        stage_statuses={
            "extraction": "available",
            "assessment": "pending",
            "review": "required",
        },
    )

    assert result["stages"] == {
        "source_preservation": "verified",
        "evidence": "verified",
        "extraction": "available",
        "assessment": "pending",
        "review": "required",
        "reconciliation": "staged_in_caller_transaction",
        "longitudinal_retrieval": "available",
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
