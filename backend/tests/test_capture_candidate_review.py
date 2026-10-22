"""Authenticated review of Qwen candidates preserves provenance and never writes LCR facts."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.care import CareEvent
from app.models.capture_analysis import CaptureAnalysis

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


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
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


def create_owner(client: TestClient, suffix: str):
    signup = client.post("/api/v1/auth/signup", json={
        "email": f"{suffix}@example.com",
        "password": "a-long-test-password-2026",
        "display_name": "Review Test Caregiver",
    })
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={
        "email": f"{suffix}@example.com",
        "password": "a-long-test-password-2026",
    })
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    context = client.post(
        "/api/v1/auth/care-contexts",
        headers=headers,
        json={"name": "Review Test Care Recipient"},
    )
    assert context.status_code == 201, context.text
    return headers, context.json()


def create_analysis(person_id: str, caregiver_id: str, analysis_id: str = "review-analysis"):
    with TestingSessionLocal() as db:
        event = CareEvent(
            id=f"{analysis_id}-event",
            person_id=person_id,
            event_type="observation",
            status="recorded",
            occurred_at=datetime.now(timezone.utc),
            title="Caregiver observation",
            description="Slept only two hours last night.",
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
        )
        analysis = CaptureAnalysis(
            id=analysis_id,
            care_event_id=event.id,
            person_id=person_id,
            source_evidence_ids=[],
            provider_status="connected",
            status="pending_review",
            model_name="qwen-test",
            candidate_data={
                "observations": [{
                    "subject": "sleep",
                    "observation": "Caregiver reports reduced sleep.",
                    "source_quote": "Slept only two hours last night.",
                    "evidence_ids": [],
                    "review_status": "pending_review",
                }],
                "open_questions": [],
            },
        )
        db.add_all([event, analysis])
        db.commit()


def test_caregiver_correction_persists_and_preserves_original_candidate():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, context = create_owner(client, "candidate-review-owner")
            with TestingSessionLocal() as db:
                from app.models.care import Caregiver
                caregiver = db.query(Caregiver).filter_by(person_id=context["person_id"]).one()
                caregiver_id = caregiver.id
            create_analysis(context["person_id"], caregiver_id)

            response = client.post(
                "/api/v1/capture/analyses/review-analysis/review",
                headers=headers,
                json={
                    "candidate_index": 0,
                    "decision": "correct",
                    "corrected_observation": "Caregiver reports approximately two hours of sleep.",
                    "note": "Corrected wording to match the source report.",
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["authoritative_lcr_write"] is False
            assert body["candidate"]["review_status"] == "corrected_by_caregiver"
            assert body["candidate"]["observation"] == "Caregiver reports approximately two hours of sleep."
            assert "not changed" in body["message"]

            with TestingSessionLocal() as db:
                saved = db.query(CaptureAnalysis).filter_by(id="review-analysis").one()
                data = saved.candidate_data
                assert data["observations"][0]["observation"] == "Caregiver reports approximately two hours of sleep."
                assert data["review_history"][0]["original_candidate"]["observation"] == "Caregiver reports reduced sleep."
                assert data["review_history"][0]["note"] == "Corrected wording to match the source report."
    finally:
        teardown_db()


def test_candidate_review_rejects_correction_without_corrected_content():
    setup_db()
    try:
        with TestClient(app) as client:
            headers, context = create_owner(client, "candidate-review-invalid")
            with TestingSessionLocal() as db:
                from app.models.care import Caregiver
                caregiver_id = db.query(Caregiver).filter_by(person_id=context["person_id"]).one().id
            create_analysis(context["person_id"], caregiver_id, "review-analysis-invalid")
            response = client.post(
                "/api/v1/capture/analyses/review-analysis-invalid/review",
                headers=headers,
                json={"candidate_index": 0, "decision": "correct"},
            )
            assert response.status_code == 422, response.text
    finally:
        teardown_db()


def test_another_account_cannot_review_a_candidate_for_an_ungranted_record():
    setup_db()
    try:
        with TestClient(app) as client:
            owner_headers, owner_context = create_owner(client, "candidate-review-owner-two")
            with TestingSessionLocal() as db:
                from app.models.care import Caregiver
                caregiver_id = db.query(Caregiver).filter_by(person_id=owner_context["person_id"]).one().id
            create_analysis(owner_context["person_id"], caregiver_id, "review-analysis-private")
            other_headers, _ = create_owner(client, "candidate-review-other")
            response = client.post(
                "/api/v1/capture/analyses/review-analysis-private/review",
                headers=other_headers,
                json={"candidate_index": 0, "decision": "reject"},
            )
            assert response.status_code == 403, response.text
    finally:
        teardown_db()
