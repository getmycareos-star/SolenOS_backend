import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models.care import AppointmentModel, CareEventModel, EvidenceModel, Person
from app.models.caregiver import Caregiver
from app.services.post_appointment_workflow import PostAppointmentWorkflowService
from app.schemas.post_appointment.workflow import (
    PostAppointmentWorkflowRequest,
    PostAppointmentCaptureData,
    PostAppointmentWorkflowResponse,
    PostAppointmentReconciliationResult,
    PostAppointmentCareStateUpdate,
)


def test_initiate_workflow(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    # Create an appointment for the person
    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Act
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))

    # Assert
    assert isinstance(response, PostAppointmentWorkflowResponse)
    assert response.appointment_id == appointment.id
    assert response.status == "initiated"
    assert "open_capture_workspace" in response.next_steps

    # Check that workflow was stored
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    assert len(_WORKFLOW_STORAGE) == 1
    workflow_id = uuid.UUID(response.workflow_id)
    assert workflow_id in _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["appointment_id"] == appointment.id
    assert workflow_info["person_id"] == person.id
    assert workflow_info["caregiver_id"] == caregiver.id
    assert workflow_info["status"] == "initiated"


def test_capture_appointment_data(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow first
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    # Prepare capture data
    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension"
    )

    # Act
    capture_result = service.capture_appointment_data(workflow_id, capture_data)

    # Assert
    assert capture_result["status"] == "captured"
    assert capture_result["appointment_id"] == appointment.id
    assert capture_result["evidence_created"] > 0
    assert "care_event_id" in capture_result
    assert capture_result["next_steps"] == ["reconcile_information"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "captured"
    assert workflow_info["capture_data"] == capture_data
    assert workflow_info["care_event_id"] is not None
    assert len(workflow_info["evidence_ids"]) > 0

    # Check that evidence and care event were created in the database
    evidence_count = db.query(EvidenceModel).filter(EvidenceModel.person_id == person.id).count()
    assert evidence_count > 0

    care_event_count = db.query(CareEventModel).filter(CareEventModel.person_id == person.id).count()
    assert care_event_count > 0


def test_reconcile_information(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension"
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # Act
    reconciliation_result = service.reconcile_information(
        workflow_id,
        capture_data,
        str(person.id)
    )

    # Assert
    assert isinstance(reconciliation_result, PostAppointmentReconciliationResult)
    assert reconciliation_result.reconciliation_id is not None
    # Since we are adding new information, we expect some updated or confirmed items
    # The exact counts depend on the implementation, but we can check that the result is valid
    assert hasattr(reconciliation_result, "unchanged_items")
    assert hasattr(reconciliation_result, "updated_items")
    assert hasattr(reconciliation_result, "superseded_items")
    assert hasattr(reconciliation_result, "confirmed_items")
    assert hasattr(reconciliation_result, "contradicted_items")
    assert hasattr(reconciliation_result, "unresolved_items")
    assert hasattr(reconciliation_result, "unknown_items")

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "reconciled"
    assert "reconciliation_result" in workflow_info


def test_update_care_state(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow, capture data, and reconcile
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension"
    )
    service.capture_appointment_data(workflow_id, capture_data)

    reconciliation_result = service.reconcile_information(
        workflow_id,
        capture_data,
        str(person.id)
    )

    # Act
    care_state_update = service.update_care_state(
        workflow_id,
        reconciliation_result,
        str(person.id)
    )

    # Assert
    assert isinstance(care_state_update, PostAppointmentCareStateUpdate)
    assert care_state_update.care_state_version >= 1
    assert hasattr(care_state_update, "updated_fields")
    assert hasattr(care_state_update, "previous_state_summary")
    assert hasattr(care_state_update, "new_state_summary")
    assert hasattr(care_state_update, "evidence_ids")

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "care_state_updated"
    assert "care_state_updated_at" in workflow_info
    assert "updated_fields" in workflow_info


def test_update_open_loops(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension",
        new_appointments=[{"title": "Follow-up", "type": "follow_up", "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()}],
        new_tests_orders=[{"type": "Blood Test", "requires_follow_up": True, "expected_result_date": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()}],
        new_responsibilities=[{"type": "medication_administration", "description": "Give aspirin daily", "requires_follow_up": True}]
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # Act
    open_loops_result = service.update_open_loops(workflow_id, capture_data, str(person.id))

    # Assert
    assert open_loops_result["status"] == "open_loops_updated"
    assert "loops_resolved" in open_loops_result
    assert "loops_created" in open_loops_result
    assert "loops_updated" in open_loops_result
    assert open_loops_result["next_steps"] == ["update_responsibilities"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "open_loops_updated"
    assert "open_loops_updated_at" in workflow_info
    assert "loops_resolved" in workflow_info
    assert "loops_created" in workflow_info
    assert "loops_updated" in workflow_info


def test_update_responsibilities(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension",
        new_responsibilities=[{"type": "medication_administration", "description": "Give aspirin daily", "requires_follow_up": True, "acceptance_status": "explicit"}]
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # Act
    responsibilities_result = service.update_responsibilities(workflow_id, capture_data, str(person.id))

    # Assert
    assert responsibilities_result["status"] == "responsibilities_updated"
    assert "responsibilities_created" in responsibilities_result
    assert "responsibilities_updated" in responsibilities_result
    assert "responsibilities_resolved" in responsibilities_result
    assert responsibilities_result["next_steps"] == ["update_upcoming_events"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "responsibilities_updated"
    assert "responsibilities_updated_at" in workflow_info
    assert "responsibilities_created" in workflow_info
    assert "responsibilities_updated" in workflow_info
    assert "responsibilities_resolved" in workflow_info


def test_update_upcoming_events(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension",
        new_appointments=[{"title": "Cardiology Follow-up", "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()}],
        new_tests_orders=[{"type": "MRI Scan", "scheduled_date": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()}]
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # Act
    upcoming_events_result = service.update_upcoming_events(workflow_id, capture_data, str(person.id))

    # Assert
    assert upcoming_events_result["status"] == "upcoming_events_updated"
    assert "events_created" in upcoming_events_result
    assert "events_updated" in upcoming_events_result
    assert "events_completed" in upcoming_events_result
    assert upcoming_events_result["next_steps"] == ["update_handoff_context"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "upcoming_events_updated"
    assert "upcoming_events_updated_at" in workflow_info
    assert "events_created" in workflow_info
    assert "events_updated" in workflow_info
    assert "events_completed" in workflow_info


def test_update_handoff_context(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension"
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # Act
    handoff_result = service.update_handoff_context(workflow_id, str(person.id))

    # Assert
    assert handoff_result["status"] == "handoff_context_updated"
    assert "handoff_context_updated" in handoff_result
    assert "handoff_version" in handoff_result
    assert handoff_result["next_steps"] == ["generate_family_summary_if_appropriate"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    assert workflow_info["status"] == "handoff_context_updated"
    assert "handoff_context_updated_at" in workflow_info
    assert "handoff_context_updated" in workflow_info
    assert "handoff_version" in workflow_info


def test_generate_family_summary(db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", email="caregiver@example.com", relationship="family", person_id="p1", timezone="UTC")
    db.add_all([person, caregiver])
    db.commit()

    appointment = AppointmentModel(
        id=str(uuid.uuid4()),
        person_id=person.id,
        title="Test Appointment",
        scheduled_at=datetime.now(timezone.utc),
        status="scheduled",
        created_by_caregiver_id=caregiver.id
    )
    db.add(appointment)
    db.commit()

    service = PostAppointmentWorkflowService(db)

    # Initiate workflow and capture data
    workflow_request = PostAppointmentWorkflowRequest(
        appointment_id=appointment.id,
        capture_immediately=False
    )
    init_response = service.initiate_workflow(workflow_request, str(person.id), str(caregiver.id))
    workflow_id = uuid.UUID(init_response.workflow_id)

    capture_data = PostAppointmentCaptureData(
        captured_by_caregiver_id=caregiver.id,
        capture_timestamp=datetime.now(timezone.utc),
        capture_source="caregiver_report",
        appointment_outcome="completed",
        appointment_outcome_confidence=0.9,
        medication_changes=[{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
        new_diagnosis="Hypertension"
    )
    service.capture_appointment_data(workflow_id, capture_data)

    # We need to go through the steps to get to the point where we can generate a family summary
    # For simplicity, we'll just call the method directly and see if it returns None or a dict
    # In a real test, we would mock the dependencies and set up the conditions for generating a summary
    # But for now, we'll just test that the method exists and returns something

    # Act
    family_summary = service.generate_family_summary(workflow_id, str(person.id), str(caregiver.id))

    # Assert
    # The method may return None if not appropriate, or a dict if it is
    # We'll just check that it doesn't throw an exception
    assert family_summary is None or isinstance(family_summary, dict)

    # Check that workflow storage was updated (if applicable)
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[workflow_id]
    # The status may have been updated to indicate that the summary was generated
    # But we don't require a specific status for this test