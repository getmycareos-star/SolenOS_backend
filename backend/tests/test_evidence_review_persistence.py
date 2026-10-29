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
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    db = SessionLocal()
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
    evidence_a = Evidence(
        id="review-evidence-a", person_id=person_a.id, type="caregiver_report",
        source_text="Caregiver reports that the appointment occurred.",
        uploaded_by_caregiver_id=caregiver_a.id, source_type="caregiver_report",
        time_provenance="reported_by_caregiver",
    )
    evidence_b = Evidence(
        id="review-evidence-b", person_id=person_b.id, type="caregiver_report",
        source_text="Separate care record evidence.",
        uploaded_by_caregiver_id=caregiver_b.id, source_type="caregiver_report",
    )
    db.add_all([evidence_a, evidence_b])
    db.commit()
    db.close()
    yield issue_client(person_a.id), issue_client(person_b.id)
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
