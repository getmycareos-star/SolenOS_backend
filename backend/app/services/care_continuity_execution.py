"""Shared transaction-aware coordinator for persisted care-continuity workflows.

Workflow adapters retain their own source and review semantics. This coordinator
owns the common person/source validation and reconciliation boundary. It never
commits or rolls back: the API caller owns the surrounding transaction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.care import CareEvent, Correction, Evidence, Person
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion
from app.models.appointment_workspace import AppointmentWorkspaceRecord, AppointmentWorkspaceRevision
from app.models.privacy_delivery import HandoffDraft, HandoffDraftRevision
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
    source_caregiver_id: str | None = None,
    source_event_id: str | None = None,
    source_event_ids: list[str] | None = None,
    source_evidence_id: str | None = None,
    source_evidence_ids: list[str] | None = None,
    source_message_id: str | None = None,
    source_conversation_id: str | None = None,
    correction_id: str | None = None,
    source_assertion_id: str | None = None,
    source_appointment_workspace_id: str | None = None,
    source_appointment_workspace_revision_id: str | None = None,
    source_handoff_draft_id: str | None = None,
    source_handoff_draft_revision_number: int | None = None,
    reconcile_lcr: bool = True,
    reference_time: datetime | None = None,
    stage_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate persisted lineage and stage the shared LCR reconciliation.

    The function deliberately does not commit. Accepted review + resulting
    reconciliation must be committed by the caller as one transaction.
    """
    if not isinstance(person_id, str) or not person_id.strip():
        raise CareContinuityExecutionError("A person scope is required")
    if not isinstance(workflow, str) or not workflow.strip():
        raise CareContinuityExecutionError("A workflow identifier is required")
    event_ids = list(dict.fromkeys(
        [value for value in (source_event_ids or []) if isinstance(value, str) and value.strip()]
        + ([source_event_id] if source_event_id else [])
    ))
    evidence_ids = list(dict.fromkeys(
        [value for value in (source_evidence_ids or []) if isinstance(value, str) and value.strip()]
        + ([source_evidence_id] if source_evidence_id else [])
    ))
    if not event_ids and not evidence_ids and not source_message_id and not correction_id and not source_assertion_id and not source_appointment_workspace_id and not source_handoff_draft_id:
        raise CareContinuityExecutionError("At least one persisted source reference is required")
    if source_message_id and (not source_conversation_id or not source_caregiver_id):
        raise CareContinuityExecutionError(
            "A Companion source message must include its conversation and caregiver references"
        )

    person = db.query(Person).filter(Person.id == person_id).first()
    if person is None:
        raise CareContinuityExecutionError("The care record could not be verified")

    events = []
    for event_id in event_ids:
        event = db.query(CareEvent).filter(
            CareEvent.id == event_id,
            CareEvent.person_id == person_id,
        ).first()
        if event is None:
            raise CareContinuityExecutionError(
                "A source event is missing or belongs to another care record"
            )
        events.append(event)

    evidence_rows = []
    for evidence_id in evidence_ids:
        evidence = db.query(Evidence).filter(
            Evidence.id == evidence_id,
            Evidence.person_id == person_id,
        ).first()
        if evidence is None:
            raise CareContinuityExecutionError(
                "The source evidence is missing or belongs to another care record"
            )
        evidence_rows.append(evidence)

    if source_caregiver_id and any(
        str(event.created_by_caregiver_id) != str(source_caregiver_id)
        for event in events
    ):
        raise CareContinuityExecutionError(
            "A source event was not created by the authenticated caregiver"
        )
    if source_caregiver_id and any(
        str(item.uploaded_by_caregiver_id) != str(source_caregiver_id)
        for item in evidence_rows
    ):
        raise CareContinuityExecutionError(
            "The source evidence was not uploaded by the authenticated caregiver"
        )
    if events and evidence_rows:
        linked_ids = {
            str(evidence_id)
            for event in events
            for evidence_id in (event.evidence_ids or [])
        }
        if not set(evidence_ids).issubset(linked_ids):
            raise CareContinuityExecutionError(
                "The source evidence is not linked to the supplied source events"
            )

    assertion = None
    if source_assertion_id:
        assertion = db.query(CareAssertion).filter(
            CareAssertion.id == source_assertion_id,
            CareAssertion.person_id == person_id,
        ).first()
        if assertion is None:
            raise CareContinuityExecutionError(
                "The source assertion is missing or belongs to another care record"
            )

        # An assertion is only as trustworthy as its own persisted lineage.
        # Validate every evidence reference carried by the assertion, even
        # when the caller did not separately supply source_evidence_ids.
        assertion_evidence_ids = list(dict.fromkeys(
            value for value in (assertion.evidence_ids or [])
            if isinstance(value, str) and value.strip()
        ))
        for evidence_id in assertion_evidence_ids:
            linked_evidence = db.query(Evidence).filter(
                Evidence.id == evidence_id,
                Evidence.person_id == person_id,
            ).first()
            if linked_evidence is None:
                raise CareContinuityExecutionError(
                    "The source assertion references evidence missing from or outside this care record"
                )

        if evidence_ids and not set(evidence_ids).issubset(set(assertion_evidence_ids)):
            raise CareContinuityExecutionError(
                "The supplied evidence is not part of the source assertion's persisted lineage"
            )
        if assertion.source_event_id:
            assertion_event = db.query(CareEvent).filter(
                CareEvent.id == assertion.source_event_id,
                CareEvent.person_id == person_id,
            ).first()
            if assertion_event is None:
                raise CareContinuityExecutionError(
                    "The source assertion references an event missing from or outside this care record"
                )
            if event_ids and assertion.source_event_id not in event_ids:
                raise CareContinuityExecutionError(
                    "The source assertion does not belong to the supplied source event"
                )

        if assertion.source_message_id:
            assertion_conversation_query = db.query(ConversationSession).filter(
                ConversationSession.id == assertion.source_conversation_id,
                ConversationSession.person_id == person_id,
            )
            if source_caregiver_id:
                assertion_conversation_query = assertion_conversation_query.filter(
                    ConversationSession.caregiver_id == source_caregiver_id
                )
            assertion_conversation = assertion_conversation_query.first()
            assertion_message = db.query(ConversationMessage).filter(
                ConversationMessage.id == assertion.source_message_id,
                ConversationMessage.session_id == assertion.source_conversation_id,
                ConversationMessage.role == "user",
            ).first()
            if assertion_conversation is None or assertion_message is None:
                raise CareContinuityExecutionError(
                    "The source assertion references a Companion message missing from or outside this care record"
                )

        if source_message_id and (
            str(assertion.source_message_id or "") != str(source_message_id)
            or str(assertion.source_conversation_id or "") != str(source_conversation_id)
        ):
            raise CareContinuityExecutionError(
                "The source assertion does not belong to the supplied Companion message"
            )

    appointment_workspace = None
    appointment_revision = None
    if bool(source_appointment_workspace_id) != bool(source_appointment_workspace_revision_id):
        raise CareContinuityExecutionError(
            "An appointment workspace and its persisted revision must be supplied together"
        )
    if source_appointment_workspace_id and source_appointment_workspace_revision_id:
        appointment_workspace = db.query(AppointmentWorkspaceRecord).filter(
            AppointmentWorkspaceRecord.id == source_appointment_workspace_id,
            AppointmentWorkspaceRecord.person_id == person_id,
        ).first()
        appointment_revision = db.query(AppointmentWorkspaceRevision).filter(
            AppointmentWorkspaceRevision.id == source_appointment_workspace_revision_id,
            AppointmentWorkspaceRevision.record_id == source_appointment_workspace_id,
            AppointmentWorkspaceRevision.person_id == person_id,
        ).first()
        if appointment_workspace is None or appointment_revision is None:
            raise CareContinuityExecutionError(
                "The appointment workspace revision is missing or belongs to another care record"
            )
        if source_caregiver_id and (
            str(appointment_revision.changed_by_caregiver_id) != str(source_caregiver_id)
        ):
            raise CareContinuityExecutionError(
                "The appointment workspace revision was not created by the authenticated caregiver"
            )

    handoff_draft = None
    handoff_revision = None
    if bool(source_handoff_draft_id) != bool(source_handoff_draft_revision_number):
        raise CareContinuityExecutionError(
            "A handoff draft and its persisted revision must be supplied together"
        )
    if source_handoff_draft_id and source_handoff_draft_revision_number:
        handoff_draft = db.query(HandoffDraft).filter(
            HandoffDraft.id == source_handoff_draft_id,
            HandoffDraft.person_id == person_id,
            HandoffDraft.caregiver_id == source_caregiver_id,
        ).first()
        handoff_revision = db.query(HandoffDraftRevision).filter(
            HandoffDraftRevision.draft_id == source_handoff_draft_id,
            HandoffDraftRevision.person_id == person_id,
            HandoffDraftRevision.caregiver_id == source_caregiver_id,
            HandoffDraftRevision.revision_number == source_handoff_draft_revision_number,
        ).first()
        if handoff_draft is None or handoff_revision is None:
            raise CareContinuityExecutionError(
                "The handoff draft revision is missing or belongs to another care record or caregiver"
            )

    correction = None
    if correction_id:
        correction = db.query(Correction).filter(
            Correction.id == correction_id,
            Correction.person_id == person_id,
        ).first()
        if correction is None:
            raise CareContinuityExecutionError(
                "The correction is missing or belongs to another care record"
            )
        if source_caregiver_id and str(correction.caregiver_id) != str(source_caregiver_id):
            raise CareContinuityExecutionError(
                "The correction was not created by the authenticated caregiver"
            )

    conversation = None
    message = None
    if source_message_id:
        conversation = db.query(ConversationSession).filter(
            ConversationSession.id == source_conversation_id,
            ConversationSession.person_id == person_id,
            ConversationSession.caregiver_id == source_caregiver_id,
        ).first()
        if conversation is None:
            raise CareContinuityExecutionError(
                "The Companion conversation is missing or belongs to another care record"
            )
        message = db.query(ConversationMessage).filter(
            ConversationMessage.id == source_message_id,
            ConversationMessage.session_id == source_conversation_id,
            ConversationMessage.role == "user",
        ).first()
        if message is None:
            raise CareContinuityExecutionError(
                "The original caregiver-authored Companion message could not be verified"
            )

    stages = {
        "source_preservation": "verified",
        "evidence": "verified" if evidence_rows else "not_applicable",
        "source_message": "verified" if message is not None else "not_applicable",
        "appointment_workspace_source": "verified" if appointment_workspace is not None else "not_applicable",
        "appointment_workspace_revision": "verified" if appointment_revision is not None else "not_applicable",
        "handoff_draft": "verified" if handoff_draft is not None else "not_applicable",
        "handoff_draft_revision": "verified" if handoff_revision is not None else "not_applicable",
        "extraction": "not_applicable",
        "assessment": "not_applicable",
        "review": "not_required",
        "reconciliation": (
            "staged_in_caller_transaction" if reconcile_lcr else "not_applicable_read_only_output"
        ),
        "longitudinal_retrieval": "not_verified",
    }
    if stage_statuses:
        allowed = {
            "extraction": {"available", "pending", "failed", "not_applicable"},
            "assessment": {"available", "pending", "failed", "not_applicable"},
            "review": {"required", "available", "completed", "not_required", "pending"},
        }
        for name, value in stage_statuses.items():
            if name not in allowed or value not in allowed[name]:
                raise CareContinuityExecutionError(
                    f"Unsupported execution stage status: {name}={value}"
                )
            stages[name] = value

    # Make pending assertions/review decisions visible to reconciliation in
    # this same unit of work. The caller retains commit/rollback ownership.
    db.flush()
    if reconcile_lcr:
        reconcile_person_change_intelligence(db, person_id, reference_time)
        db.flush()

    return {
        "contract_version": "1.0",
        "workflow": workflow,
        "person_id": person_id,
        "source_caregiver_id": source_caregiver_id,
        "source_event_id": source_event_id or (event_ids[0] if len(event_ids) == 1 else None),
        "source_event_ids": event_ids,
        "source_evidence_id": source_evidence_id or (evidence_ids[0] if len(evidence_ids) == 1 else None),
        "source_evidence_ids": evidence_ids,
        "source_message_id": source_message_id,
        "source_conversation_id": source_conversation_id,
        "correction_id": correction_id,
        "source_assertion_id": source_assertion_id,
        "source_appointment_workspace_id": source_appointment_workspace_id,
        "source_appointment_workspace_revision_id": source_appointment_workspace_revision_id,
        "source_handoff_draft_id": source_handoff_draft_id,
        "source_handoff_draft_revision_number": source_handoff_draft_revision_number,
        "stages": stages,
        "transaction_owner": "caller",
    }
