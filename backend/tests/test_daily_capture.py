import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import CareEvent, Caregiver, Evidence, Person

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_db():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_db():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def seed_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-daily-test", name="Daily Capture Test Person")
    caregiver = Caregiver(
        id="caregiver-daily-test",
        name="Daily Capture Test Caregiver",
        email="daily-capture-test@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()




def session_headers(person_id: str, caregiver_id: str) -> dict[str, str]:
    token = f"daily-test-token-{person_id}-{caregiver_id}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db = TestingSessionLocal()
    existing = db.query(AnonymousSession).filter_by(token_hash=token_hash).one_or_none()
    if existing is None:
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
    else:
        existing.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()
    db.close()
    return {"Authorization": f"Bearer {token}"}


def test_daily_capture_persists_for_linked_caregiver():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
                "general_notes": "User-recorded note",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True





def test_quick_daily_checkin_persists_selected_scope_and_caregiver_attribution():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": (
                    "Caregiver reports no significant change from usual in: Sleep. "
                    "Other areas were not assessed by this check-in."
                ),
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-retry-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True

        replay = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json=json.loads(response.request.content),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == response.json()["id"]
        assert replay.json()["idempotent_replay"] is True

        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(event_type="quick_daily_checkin").count() == 1
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert event.person_id == "person-daily-test"
        assert event.created_by_caregiver_id == "caregiver-daily-test"
        assert event.event_type == "quick_daily_checkin"
        assert event.title == "Quick daily check-in"
        assert detail["quick_check_in"]["kind"] == "same_as_usual"
        assert detail["quick_check_in"]["selected_areas"] == ["sleep"]
        assert detail["quick_check_in"]["scope"] == "selected_areas_only"
        assert detail["quick_check_in"]["epistemic_status"] == "caregiver_reported"
        assert "Other areas were not assessed" in detail["notes"]
        db.close()
    finally:
        teardown_db()



def test_quick_daily_checkin_rejects_unrecognized_or_unscoped_areas():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver quick update",
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": ["diagnosis"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "note": None,
                },
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "quick_check_in must contain valid, unique selected areas"

        db = TestingSessionLocal()
        assert db.query(CareEvent).filter_by(event_type="quick_daily_checkin").count() == 0
        db.close()
    finally:
        teardown_db()


def test_daily_capture_rejects_unknown_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "missing-person",
                "caregiver_id": "caregiver-daily-test",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This session is not authorised for that care record"
    finally:
        teardown_db()


def test_daily_capture_rejects_unlinked_caregiver():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily-care/entries",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "not-linked-caregiver",
                "mood": "Calm",
            },
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Caregiver attribution does not match this session"
    finally:
        teardown_db()



def seed_second_person_and_caregiver():
    db = TestingSessionLocal()
    person = Person(id="person-other-test", name="Other Test Person")
    caregiver = Caregiver(
        id="caregiver-other-test",
        name="Other Test Caregiver",
        email="other-test-caregiver@example.invalid",
        relationship="Family",
        person_id=person.id,
    )
    db.add_all([person, caregiver])
    db.commit()
    db.close()


def test_event_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/events",
            json={
                "person_id": "person-daily-test",
                "event_type": "observation",
                "occurred_at": "2026-10-22T00:00:00Z",
                "title": "Ownership test",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in {
            "Caregiver is not linked to this care record",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()


def test_appointment_write_rejects_caregiver_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/appointments",
            json={
                "person_id": "person-daily-test",
                "title": "Ownership test appointment",
                "scheduled_at": "2026-10-23T10:00:00Z",
                "created_by_caregiver_id": "caregiver-other-test",
            },
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] in {
            "Caregiver is not linked to this care record",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()



def test_caregiver_creation_rejects_unknown_person():
    setup_db()
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/caregivers",
            json={
                "name": "Unlinked Caregiver",
                "email": "unknown-person@example.invalid",
                "relationship": "Family",
                "person_id": "missing-person",
            },
        )
        # The authorization middleware may reject the unknown care-record ID
        # before the endpoint can return its more specific 404.
        assert response.status_code in {403, 404}, response.text
        assert response.json()["detail"] in {
            "Person not found",
            "You are not a member of this care circle.",
        }
    finally:
        teardown_db()


def test_insight_rejects_evidence_from_another_person():
    setup_db()
    try:
        seed_person_and_caregiver()
        seed_second_person_and_caregiver()
        db = TestingSessionLocal()
        foreign_evidence = Evidence(
            id="foreign-evidence-test",
            person_id="person-other-test",
            type="note",
            source_text="Evidence belongs to a different care record",
            uploaded_by_caregiver_id="caregiver-other-test",
        )
        db.add(foreign_evidence)
        db.commit()
        db.close()

        client = TestClient(app)
        response = client.post(
            "/api/v1/insights",
            json={
                "person_id": "person-daily-test",
                "title": "Cross-record evidence test",
                "description": "This should be rejected",
                "insight_type": "observation",
                "confidence": 0.5,
                "evidence_ids": ["foreign-evidence-test"],
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Every evidence item must exist and belong to the same care record"
    finally:
        teardown_db()


def test_readiness_rejects_incomplete_schema():
    incomplete_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.tables["persons"].create(bind=incomplete_engine)
    IncompleteSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=incomplete_engine
    )

    def override_incomplete_db():
        db = IncompleteSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_incomplete_db
    try:
        response = TestClient(app).get("/ready")
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "Database schema is incomplete"
    finally:
        app.dependency_overrides.clear()
        incomplete_engine.dispose()


def test_quick_daily_checkin_persists_uncertainty_without_inference():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Caregiver is unsure about: Sleep. No status is inferred for these areas.",
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "uncertain",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-uncertain-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert detail["quick_check_in"]["kind"] == "uncertain"
        assert detail["quick_check_in"]["selected_areas"] == ["sleep"]
        assert "No status is inferred" in detail["notes"]
        db.close()
    finally:
        teardown_db()


def test_quick_daily_checkin_is_visible_as_caregiver_report_in_lcr_projection(monkeypatch):
    from app.api import care_continuity as care_continuity_api

    setup_db()
    db = TestingSessionLocal()
    try:
        report_time = "2026-11-03T00:00:00+01:00"
        event = {
            "id": "quick-checkin-lcr-event",
            "event_type": "quick_daily_checkin",
            "status": "recorded",
            "title": "Quick daily check-in",
            "description": json.dumps({
                "notes": "Caregiver reports no significant change in: Sleep.",
                "quick_check_in": {
                    "kind": "same_as_usual",
                    "selected_areas": ["sleep"],
                    "scope": "selected_areas_only",
                    "caregiver_reported_at": report_time,
                    "epistemic_status": "caregiver_reported",
                },
            }),
            "occurred_at": "2026-11-02T23:00:00+00:00",
            "recorded_at": "2026-11-02T23:00:01+00:00",
            "created_by_caregiver_id": "caregiver-daily-test",
            "evidence_ids": [],
        }
        monkeypatch.setattr(
            care_continuity_api,
            "_scoped_identity",
            lambda request, session: ("person-daily-test", None),
        )
        monkeypatch.setattr(
            care_continuity_api,
            "build_lcr_context",
            lambda session, person_id, limit_per_source: {
                "person_id": person_id,
                "as_of": report_time,
                "history_coverage": "bounded_subset",
                "schema_status": "complete",
                "missing_required_sources": [],
                "missing_optional_sources": [],
                "sources": {"events": {"items": [event]}},
            },
        )

        projection = care_continuity_api.current_care_projection(
            request=None, limit_per_source=10, db=db
        )
        item = next(
            item for item in projection["items"]
            if item["id"] == "quick-checkin-lcr-event"
        )
        assert item["source_collection"] == "events"
        assert item["kind"] == "Daily care entry"
        assert item["epistemic_status"] == "caregiver_reported"
        assert item["source_label"] == "Caregiver report"
        assert item["occurred_at"] == "2026-11-02T23:00:00+00:00"
        assert item["reported_at"] == report_time
    finally:
        db.close()
        teardown_db()


def test_same_as_usual_can_save_overall_impression_without_marking_domains_checked():
    setup_db()
    try:
        seed_person_and_caregiver()
        client = TestClient(app)
        response = client.post(
            "/api/v1/daily/log",
            headers=session_headers("person-daily-test", "caregiver-daily-test"),
            json={
                "person_id": "person-daily-test",
                "caregiver_id": "caregiver-daily-test",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": (
                    "Caregiver reports no significant change in their overall impression of care. "
                    "No individual care areas were assessed by this check-in."
                ),
                "quick_check_in": {
                    "schema_version": 1,
                    "kind": "same_as_usual",
                    "selected_areas": [],
                    "scope": "overall_impression_only",
                    "caregiver_reported_at": "2026-11-03T00:00:00+01:00",
                    "epistemic_status": "caregiver_reported",
                    "idempotency_key": "daily-checkin-overall-001",
                    "note": None,
                },
            },
        )
        assert response.status_code == 200, response.text
        db = TestingSessionLocal()
        event = db.query(CareEvent).filter_by(id=response.json()["id"]).one()
        detail = json.loads(event.description)
        assert detail["quick_check_in"]["selected_areas"] == []
        assert detail["quick_check_in"]["scope"] == "overall_impression_only"
        assert "No individual care areas were assessed" in detail["notes"]
        db.close()
    finally:
        teardown_db()
