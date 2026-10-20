import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.core.database import Base
from app.models.care import AppointmentModel, Person
from app.models.caregiver import Caregiver
from app.services.post_appointment_workflow import PostAppointmentWorkflowService
from app.schemas.post_appointment.workflow import PostAppointmentWorkflowRequest, PostAppointmentCaptureData


@pytest.fixture
def client(db: Session):
    # Override the dependency to use the test database
    def get_test_db():
        try:
            yield db
        finally:
            pass

    from app.core.database import get_db
    app.dependency_overrides[get_db] = get_test_db

    # Do not enter the lifespan context in unit tests: production startup starts
    # schedulers and backup tasks that must not touch the test database.
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_initiate_post_appointment_workflow(client: TestClient, db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", person_id="p1")
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

    # Mock the service to avoid complex dependencies, or we can rely on the actual service
    # For now, we'll use the actual service but we need to mock the downstream services
    # that might have external dependencies (like FollowUpEngine, CaregiverIntelligenceService, etc.)
    # We'll patch those services in the test.

    with patch('app.services.follow_up.FollowUpEngine') as mock_followup_engine, \
         patch('app.services.caregiver_intelligence.CaregiverIntelligenceService') as mock_caregiver_intelligence_service:

        # Configure the mocks to return sensible defaults
        mock_followup_instance = MagicMock()
        mock_followup_instance.list_for_person.return_value = []
        mock_followup_instance.schedule.return_value = None
        mock_followup_instance.complete.return_value = None
        mock_followup_engine.return_value = mock_followup_instance

        mock_caregiver_intelligence_instance = MagicMock()
        mock_caregiver_intelligence_instance.get_active_responsibilities.return_value = []
        mock_caregiver_intelligence_instance.assign_responsibility.return_value = MagicMock(id=uuid.uuid4())
        mock_caregiver_intelligence_service.return_value = mock_caregiver_intelligence_instance

        # Act
        response = client.post(
            f"/post_appointment/{person.id}/workflow",
            json={
                "appointment_id": appointment.id,
                "capture_immediately": False
            }
        )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["appointment_id"] == appointment.id
    assert data["status"] == "initiated"
    assert "open_capture_workspace" in data["next_steps"]
    workflow_id = data["workflow_id"]

    # Check that the workflow was stored (in the service's storage)
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    assert len(_WORKFLOW_STORAGE) >= 1
    workflow_uuid = uuid.UUID(workflow_id)
    assert workflow_uuid in _WORKFLOW_STORAGE


def test_capture_appointment_data(client: TestClient, db: Session):
    # Arrange
    person = Person(id="p1", name="Test Person")
    caregiver = Caregiver(id="c1", name="Test Caregiver", person_id="p1")
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

    with patch('app.services.follow_up.FollowUpEngine') as mock_followup_engine, \
         patch('app.services.caregiver_intelligence.CaregiverIntelligenceService') as mock_caregiver_intelligence_service:

        mock_followup_instance = MagicMock()
        mock_followup_instance.list_for_person.return_value = []
        mock_followup_instance.schedule.return_value = None
        mock_followup_instance.complete.return_value = None
        mock_followup_engine.return_value = mock_followup_instance

        mock_caregiver_intelligence_instance = MagicMock()
        mock_caregiver_intelligence_instance.get_active_responsibilities.return_value = []
        mock_caregiver_intelligence_instance.assign_responsibility.return_value = MagicMock(id=uuid.uuid4())
        mock_caregiver_intelligence_service.return_value = mock_caregiver_intelligence_instance

        # Initiate workflow
        init_response = client.post(
            f"/post_appointment/{person.id}/workflow",
            json={
                "appointment_id": appointment.id,
                "capture_immediately": False
            }
        )
        assert init_response.status_code == 200
        workflow_id = init_response.json()["workflow_id"]

        # Prepare capture data
        capture_data = {
            "captured_by_caregiver_id": str(caregiver.id),
            "capture_timestamp": datetime.now(timezone.utc).isoformat(),
            "capture_source": "caregiver_report",
            "appointment_outcome": "completed",
            "appointment_outcome_confidence": 0.9,
            "medication_changes": [{"medication": "Aspirin", "change": "started", "dosage": "81mg"}],
            "new_diagnosis": "Hypertension"
        }

        # Act
        response = client.post(
            f"/post_appointment/workflow/{workflow_id}/capture",
            json=capture_data
        )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "captured"
    assert data["appointment_id"] == appointment.id
    assert data["evidence_created"] > 0
    assert "care_event_id" in data
    assert data["next_steps"] == ["reconcile_information"]

    # Check that workflow storage was updated
    from app.services.post_appointment_workflow import _WORKFLOW_STORAGE
    workflow_info = _WORKFLOW_STORAGE[uuid.UUID(workflow_id)]
    assert workflow_info["status"] == "captured"
    assert workflow_info["care_event_id"] is not None
    assert len(workflow_info["evidence_ids"]) > 0


# We can continue with more tests for each step, but for brevity, we'll stop here.
# The pattern is similar for each step: initiate, capture, then test each endpoint.
