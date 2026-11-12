"""Shared transaction-aware coordinator for persisted care-continuity workflows.

Workflow adapters retain their own source and review semantics. This coordinator
owns the common person/source validation and reconciliation boundary. It never
commits or rolls back: the API caller owns the surrounding transaction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.care import CareEvent, Evidence, Person
from app.services.change_intelligence_reconciliation import (
    reconcile_person_change_intelligence,
)


class CareContinuityExecutionError(ValueError):
    """Raised when a workflow cannot prove its persisted source lineage."""


def execute_care_continuity_reconciliation(
    db: Session,
    person_id: str,
    *,
    workflow: str,
    source_event_id: str | None = None,
    source_evidence_id: str | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Validate persisted lineage and stage the shared LCR reconciliation.

    The function deliberately does not commit. Accepted review + resulting
    reconciliation must be committed by the caller as one transaction.
    """
    if not isinstance(person_id, str) or not person_id.strip():
        raise CareContinuityExecutionError("A person scope is required")
    if not isinstance(workflow, str) or not workflow.strip():
        raise CareContinuityExecutionError("A workflow identifier is required")
    if not source_event_id and not source_evidence_id:
        raise CareContinuityExecutionError("At least one persisted source reference is required")

    person = db.query(Person).filter(Person.id == person_id).first()
    if person is None:
        raise CareContinuityExecutionError("The care record could not be verified")

    event = None
    if source_event_id:
        event = db.query(CareEvent).filter(
            CareEvent.id == source_event_id,
            CareEvent.person_id == person_id,
        ).first()
        if event is None:
            raise CareContinuityExecutionError(
                "The source event is missing or belongs to another care record"
            )

    evidence = None
    if source_evidence_id:
        evidence = db.query(Evidence).filter(
            Evidence.id == source_evidence_id,
            Evidence.person_id == person_id,
        ).first()
        if evidence is None:
            raise CareContinuityExecutionError(
                "The source evidence is missing or belongs to another care record"
            )

    if event is not None and evidence is not None:
        linked_ids = list(event.evidence_ids or [])
        if source_evidence_id not in linked_ids:
            raise CareContinuityExecutionError(
                "The source evidence is not linked to the supplied source event"
            )

    # Make pending assertions/review decisions visible to reconciliation in
    # this same unit of work. The caller retains commit/rollback ownership.
    db.flush()
    reconcile_person_change_intelligence(db, person_id, reference_time)
    db.flush()

    return {
        "contract_version": "1.0",
        "workflow": workflow,
        "person_id": person_id,
        "source_event_id": source_event_id,
        "source_evidence_id": source_evidence_id,
        "stages": {
            "source_preservation": "verified",
            "evidence": "verified" if evidence is not None else "not_applicable",
            "reconciliation": "staged_in_caller_transaction",
            "longitudinal_retrieval": "not_verified",
        },
        "transaction_owner": "caller",
    }
