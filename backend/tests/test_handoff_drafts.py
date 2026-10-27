import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
from app.main import app
from app.models.care import CareEvent, Caregiver, Evidence
from app.models.action import Action
from app.models.time_location import Appointment

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS care_facts (
                id VARCHAR(36) PRIMARY KEY,
                person_id VARCHAR(255) NOT NULL,
                fact_type VARCHAR(100),
                fact_value TEXT,
                epistemic_status VARCHAR(40),
                effective_at DATETIME,
                created_at DATETIME,
                updated_at DATETIME,
                review_notes TEXT
            )
        """))
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestSessionLocal
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Care recipient", "caregiver_name": "Family caregiver"},
        )
        assert created.status_code == 201, created.text
        yield test_client, created.json()
    app.dependency_overrides.clear()
    main_module.SessionLocal = DefaultSessionLocal
    Base.metadata.drop_all(bind=engine)


def test_draft_requires_session(client):
    test_client, _ = client
    response = test_client.post("/api/v1/handoff-drafts/prepare", json={})
    assert response.status_code == 401, response.text


@pytest.mark.parametrize("audience, expected_sections", [
    ("family", {"start_here", "daily_routine", "health_medication", "next_actions", "receipt"}),
    ("professional", {"transition_summary", "provenance", "medication_reconciliation", "action_register", "integrity", "receipt"}),
])
def test_draft_returns_audience_specific_template_and_explicit_unassessed_sections(client, audience, expected_sections):
    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"purpose": "family_transition", "audience": audience, "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audience"] == audience
    sections = {section["key"]: section for section in body["template_sections"]}
    assert expected_sections.issubset(sections)
    assert all(section["required_fields"] for section in body["template_sections"])
    # Appointment records can support appointment details, but no medication adapter exists.
    if audience == "professional":
        assert sections["medication_reconciliation"]["coverage_status"] == "not_assessed"
    else:
        assert sections["health_medication"]["coverage_status"] == "no_records_returned"
    assert all("still requires review" in section["coverage_note"] for section in body["template_sections"])


def test_draft_is_read_only_and_only_includes_records_for_session_person(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "owned-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "fact_type": "caregiver_observation",
                "fact_value": "Breakfast was mostly uneaten",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, fact_type, fact_value, epistemic_status, created_at)
                VALUES (:id, :person_id, :subject_type, :fact_type, :fact_value, :status, :created_at)"""),
            {
                "id": "other-person-fact",
                "person_id": "another-person",
                "subject_type": "care_recipient",
                "fact_type": "private",
                "fact_value": "MUST NOT LEAK",
                "status": "reported",
                "created_at": "2026-10-26 08:00:00",
            },
        )
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "family_transition", "window_days": 90},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["read_only"] is True
    assert body["creates_handoff"] is False
    assert body["updates_lcr"] is False
    assert any(item["source_id"] == "owned-fact" for item in body["source_references"])
    assert all(item["source_id"] != "other-person-fact" for item in body["source_references"])
    assert "MUST NOT LEAK" not in response.text
    assert body["source_statuses"]["community"]["status"] == "not_authorized"
    assert body["source_statuses"]["documents"]["status"] == "unavailable"
    with engine.connect() as connection:
        saved = connection.execute(
            text("SELECT fact_value FROM care_facts WHERE id = 'owned-fact'")
        ).scalar_one()
    assert saved == "Breakfast was mostly uneaten"


def test_draft_includes_person_scoped_events_appointments_and_only_actions_from_current_caregivers_evidence(client):
    test_client, session = client
    token = session["access_token"]
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    db = TestSessionLocal()
    try:
        other_caregiver = Caregiver(
            id="other-caregiver-same-person",
            name="Other caregiver",
            relationship="family",
            person_id=person_id,
        )
        db.add(other_caregiver)
        db.flush()
        own_evidence = Evidence(
            id="own-evidence",
            person_id=person_id,
            type="document",
            source_text="Current caregiver's source text",
            uploaded_by_caregiver_id=caregiver_id,
        )
        other_evidence = Evidence(
            id="other-evidence",
            person_id=person_id,
            type="document",
            source_text="Private source text from another caregiver",
            uploaded_by_caregiver_id=other_caregiver.id,
        )
        db.add_all([own_evidence, other_evidence])
        db.flush()
        db.add_all([
            Action(
                id="own-action",
                person_id=person_id,
                source_evidence_id=own_evidence.id,
                source_passage="Call the clinic this week",
                source_authority="caregiver",
                normalized_action="Call the clinic this week",
                original_text="Call the clinic this week",
                created_by_caregiver_id=caregiver_id,
                status="pending_confirmation",
                deadline=datetime.now(timezone.utc) + timedelta(days=2),
            ),
            Action(
                id="other-action",
                person_id=person_id,
                source_evidence_id=other_evidence.id,
                source_passage="Private instruction",
                source_authority="caregiver",
                normalized_action="PRIVATE OTHER CAREGIVER ACTION",
                original_text="PRIVATE OTHER CAREGIVER ACTION",
                created_by_caregiver_id=other_caregiver.id,
                status="pending_confirmation",
            ),
        ])
        db.add(CareEvent(
            id="recent-event",
            person_id=person_id,
            event_type="care_observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=2),
            title="Recent recorded event",
            description="Reported change to discuss",
            created_by_caregiver_id=caregiver_id,
        ))
        db.add(Appointment(
            id="next-appointment",
            person_id=person_id,
            title="Clinic appointment",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
        ))
        db.commit()
    finally:
        db.close()

    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    references = {item["source_id"]: item for item in body["source_references"]}
    assert "recent-event" in references
    assert "next-appointment" in references
    assert "own-action" in references
    assert "other-action" not in references
    assert "PRIVATE OTHER CAREGIVER ACTION" not in response.text
    assert references["recent-event"]["epistemic_status"] == "not_recorded"
    assert references["recent-event"]["record_status"] == "recorded"
    assert references["next-appointment"]["event_at"] is not None
    assert references["next-appointment"]["recorded_at"] is not None
    assert references["next-appointment"]["event_at"] != references["next-appointment"]["recorded_at"]
    assert references["own-action"]["due_at"] is not None
    assert references["own-action"]["record_status"] == "pending_confirmation"
    assert body["source_statuses"]["appointments"]["status"] == "available"
    assert body["source_statuses"]["care_events"]["status"] == "available"
    assert body["source_statuses"]["actions"]["status"] == "available"


def test_current_lcr_fact_outside_time_window_is_retained_with_separate_effective_and_recorded_times(client):
    test_client, session = client
    person_id = session["person"]["id"]
    token = session["access_token"]
    with engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value,
                 epistemic_status, effective_at, recorded_at, created_at, superseded_by_fact_id)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value,
                        :epistemic_status, :effective_at, :recorded_at, :created_at, NULL)"""),
            {
                "id": "longstanding-current-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "subject_id": person_id,
                "fact_type": "communication_preference",
                "fact_value": "Allow extra time to respond",
                "epistemic_status": "reported",
                "effective_at": "2020-01-01 00:00:00",
                "recorded_at": "2020-01-01 00:00:00",
                "created_at": "2020-01-01 00:00:00",
            },
        )
        connection.execute(
            text("""INSERT INTO care_facts
                (id, person_id, subject_type, subject_id, fact_type, fact_value,
                 epistemic_status, effective_at, recorded_at, created_at, superseded_by_fact_id)
                VALUES (:id, :person_id, :subject_type, :subject_id, :fact_type, :fact_value,
                        :epistemic_status, :effective_at, :recorded_at, :created_at, :superseded_by_fact_id)"""),
            {
                "id": "old-superseded-fact",
                "person_id": person_id,
                "subject_type": "care_recipient",
                "subject_id": person_id,
                "fact_type": "communication_preference",
                "fact_value": "Old communication preference",
                "epistemic_status": "reported",
                "effective_at": "2019-01-01 00:00:00",
                "recorded_at": "2019-01-01 00:00:00",
                "created_at": "2019-01-01 00:00:00",
                "superseded_by_fact_id": "longstanding-current-fact",
            },
        )
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {token}"},
        json={"purpose": "family_transition", "window_days": 7},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    fact = next(item for item in body["source_references"] if item["source_id"] == "longstanding-current-fact")
    assert all(item["source_id"] != "old-superseded-fact" for item in body["source_references"])
    assert fact["event_at"].startswith("2020-01-01")
    assert fact["recorded_at"].startswith("2020-01-01")
    assert fact["stale_warning"] is True
    assert fact["details"]["fact_lifecycle"] == "current_in_lcr"


@pytest.mark.parametrize("window_days", [0, -1, 91, 999])
def test_draft_rejects_unbounded_time_window(client, window_days):
    test_client, session = client
    response = test_client.post(
        "/api/v1/handoff-drafts/prepare",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"window_days": window_days},
    )
    assert response.status_code == 422, response.text
