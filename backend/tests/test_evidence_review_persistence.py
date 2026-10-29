import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Evidence, Person
from app.models.care_continuity import EvidenceReviewRecord

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def issue_client(person_id: str) -> TestClient:
    db = SessionLocal()
    caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
    assert caregiver is not None
    token = f"evidence-review-token-{person_id}"
    db.add(AnonymousSession(
        person_id=person_id,
        caregiver_id=caregiver.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    ))
    db.commit()
    db.close()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
def review_records():
    # This fixture uses a shared in-memory SQLite connection; always reset it,
    # including after setup failures, so test order cannot leak records.
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    db = SessionLocal()
    try:
        person_a = Person(id="review-person-a", name="Person A")
        caregiver_a = Caregiver(
            id="review-caregiver-a", name="Caregiver A", email="review-a@example.com",
            relationship="Family", person_id=person_a.id,
        )
        person_b = Person(id="review-person-b", name="Person B")
        caregiver_b = Caregiver(
            id="review-caregiver-b", name="Caregiver B", email="review-b@example.com",
            relationship="Family", person_id=person_b.id,
        )
        db.add_all([person_a, caregiver_a, person_b, caregiver_b])
        db.flush()
        person_a_id = person_a.id
        person_b_id = person_b.id
        evidence_a = Evidence(
            id="review-evidence-a", person_id=person_a_id, type="caregiver_report",
            source_text="Caregiver reports that the appointment occurred.",
            uploaded_by_caregiver_id=caregiver_a.id, source_type="caregiver_report",
            time_provenance="reported_by_caregiver",
        )
        evidence_b = Evidence(
            id="review-evidence-b", person_id=person_b_id, type="caregiver_report",
            source_text="Separate care record evidence.",
            uploaded_by_caregiver_id=caregiver_b.id, source_type="caregiver_report",
        )
        db.add_all([evidence_a, evidence_b])
        db.commit()
    finally:
        db.close()
    try:
        yield issue_client(person_a_id), issue_client(person_b_id)
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


CRITERIA = [
    {"criterion": "source attribution", "result": "present", "rationale": "Reporter identity is recorded."},
    {"criterion": "independent corroboration", "result": "none_linked", "rationale": "No independent source is linked."},
    {"criterion": "temporal context", "result": "specified", "rationale": "The reported timing is recorded."},
    {"criterion": "evidence freshness", "result": "undetermined", "rationale": "No domain-specific review interval is configured."},
    {"criterion": "record completeness", "result": "partial", "rationale": "Only the report is present."},
]


def test_evidence_review_create_retrieve_is_persisted_and_person_scoped(review_records):
    client_a, client_b = review_records
    payload = {
        "review_type": "reliability",
        "outcome": "unverified",
        "rationale": "A caregiver report is attributed, but independent confirmation has not been reviewed.",
        "policy_version": "evidence-review-v1",
        "criteria": CRITERIA,
    }
    created = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json=payload)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["evidence_id"] == "review-evidence-a"
    assert saved["person_id"] == "review-person-a"
    assert saved["source_attribution"]["source_type"] == "caregiver_report"
    assert saved["source_attribution"]["uploaded_by_caregiver_id"] == "review-caregiver-a"

    retrieved = client_a.get("/api/v1/care-continuity/evidence/review-evidence-a/reviews")
    assert retrieved.status_code == 200, retrieved.text
    assert [item["id"] for item in retrieved.json()] == [saved["id"]]

    assert client_b.get("/api/v1/care-continuity/evidence/review-evidence-a/reviews").status_code == 404
    assert client_b.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json=payload).status_code == 404


def test_evidence_review_history_cannot_be_updated_or_deleted(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/evidence/review-evidence-a/reviews", json={
        "review_type": "quality",
        "outcome": "limited",
        "rationale": "The source is a single caregiver report.",
        "policy_version": "evidence-review-v1",
        "criteria": CRITERIA,
    })
    assert response.status_code == 201, response.text
    db = SessionLocal()
    row = db.query(EvidenceReviewRecord).filter(EvidenceReviewRecord.id == response.json()["id"]).one()
    row.rationale = "Edited after the fact"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.delete(row)
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    db.close()



def test_source_assertion_preserves_reported_status_evidence_and_event_time(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/assertions", json={
        "person_id": "review-person-b",
        "subject_type": "appointment",
        "subject_id": "appointment-42",
        "field": "attendance",
        "assertion_kind": "time_bound_observation",
        "asserted_value": "Caregiver reported attendance",
        "source_label": "Caregiver report",
        "source_text": "The caregiver said the appointment was attended.",
        "evidence_ids": ["review-evidence-a"],
        "observed_at": "2026-10-28T09:30:00Z",
        "review_after_days": 14,
        "assertion_status": "confirmed",
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["person_id"] == "review-person-a"
    assert created["assertion_status"] == "reported"
    assert created["evidence_ids"] == ["review-evidence-a"]
    assert created["observed_at"].startswith("2026-10-28T09:30:00")

    retrieved = client_a.get("/api/v1/care-continuity/assertions?limit=100")
    assert retrieved.status_code == 200, retrieved.text
    persisted = [item for item in retrieved.json() if item["id"] == created["id"]]
    assert len(persisted) == 1
    assert persisted[0]["asserted_value"] == "Caregiver reported attendance"
    assert persisted[0]["evidence_ids"] == ["review-evidence-a"]


def test_time_bound_assertion_requires_actual_event_time(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/care-continuity/assertions", json={
        "subject_type": "daily_care",
        "subject_id": "meal-1",
        "field": "appetite",
        "assertion_kind": "time_bound_observation",
        "asserted_value": "Reduced appetite",
        "source_label": "Caregiver report",
        "evidence_ids": [],
        "review_after_days": 7,
    })
    assert response.status_code == 422
    assert "actual event time" in response.json()["detail"]


def test_evidence_capture_preserves_distinct_provenance_and_hash(review_records):
    import hashlib

    client_a, _ = review_records
    source_text = "Original caregiver note: two awakenings were recorded."
    response = client_a.post("/api/v1/evidence", json={
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": source_text,
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "original_author": "Caregiver's handwritten note",
        "reported_by_label": "Caregiver A",
        "reported_by_caregiver_id": "review-caregiver-a",
        "collected_by_caregiver_id": "review-caregiver-a",
        "collected_at": "2026-10-29T08:00:00Z",
        "occurred_at": "2026-10-28T22:00:00Z",
        "occurred_at_timezone": "America/New_York",
        "origin_channel": "manual_entry",
        "original_source_reference": "family-sleep-log/2026-10-28",
        "parent_evidence_id": "review-evidence-a",
    })
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["source_text"] == source_text
    assert saved["source_text_sha256"] == hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    assert saved["ingested_at"]
    assert saved["uploaded_by_caregiver_id"] == "review-caregiver-a"
    assert saved["reported_by_caregiver_id"] == "review-caregiver-a"
    assert saved["collected_by_caregiver_id"] == "review-caregiver-a"
    assert saved["original_author"] == "Caregiver's handwritten note"
    assert saved["occurred_at"].startswith("2026-10-28T22:00:00")
    assert saved["occurred_at_timezone"] == "America/New_York"
    assert saved["parent_evidence_id"] == "review-evidence-a"


def test_evidence_provenance_rejects_reporter_from_another_care_record(review_records):
    client_a, _ = review_records
    response = client_a.post("/api/v1/evidence", json={
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": "A report with a foreign reporter identity.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
        "reported_by_caregiver_id": "review-caregiver-b",
    })
    assert response.status_code == 422, response.text
    assert "reported_by_caregiver_id" in response.text


def test_evidence_creation_requires_server_authorized_record_identity(review_records):
    from fastapi.testclient import TestClient

    client_a, _ = review_records
    payload = {
        "person_id": "review-person-a",
        "type": "caregiver_report",
        "source_text": "Evidence must be bound to the authorized session.",
        "uploaded_by_caregiver_id": "review-caregiver-a",
    }
    unauthenticated = TestClient(app)
    assert unauthenticated.post("/api/v1/evidence", json=payload).status_code == 401

    # A valid session for person A cannot create evidence in person B's record,
    # even when the request body supplies a valid caregiver B identity.
    foreign_payload = {
        **payload,
        "person_id": "review-person-b",
        "uploaded_by_caregiver_id": "review-caregiver-b",
    }
    assert client_a.post("/api/v1/evidence", json=foreign_payload).status_code == 403
