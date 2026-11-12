from datetime import datetime, timedelta, timezone
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Evidence, Person

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


def session_headers(person_id: str, caregiver_id: str):
    token = f"assertion-scope-{person_id}-{caregiver_id}"
    db = TestingSessionLocal()
    db.add(AnonymousSession(
        person_id=person_id,
        caregiver_id=caregiver_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ))
    db.commit()
    db.close()
    return {"Authorization": f"Bearer {token}"}


def setup_function():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db


def teardown_function():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_assertion_evidence_links_must_belong_to_active_care_record():
    db = TestingSessionLocal()
    for person_id, caregiver_id in (
        ("assertion-person-a", "assertion-caregiver-a"),
        ("assertion-person-b", "assertion-caregiver-b"),
    ):
        db.add(Person(id=person_id, name=person_id))
        db.add(Caregiver(
            id=caregiver_id,
            name=caregiver_id,
            email=f"{caregiver_id}@example.invalid",
            relationship="Family",
            person_id=person_id,
        ))
    db.flush()
    db.add_all([
        Evidence(
            id="assertion-evidence-a",
            person_id="assertion-person-a",
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="Caregiver observed reduced meal intake.",
            uploaded_by_caregiver_id="assertion-caregiver-a",
            reported_by_caregiver_id="assertion-caregiver-a",
            reported_by_label="Caregiver",
            source_type="caregiver_report",
        ),
        Evidence(
            id="assertion-evidence-b",
            person_id="assertion-person-b",
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text="Private note from another care record.",
            uploaded_by_caregiver_id="assertion-caregiver-b",
            reported_by_caregiver_id="assertion-caregiver-b",
            reported_by_label="Caregiver",
            source_type="caregiver_report",
        ),
    ])
    db.commit()
    db.close()

    client = TestClient(app)
    headers_a = session_headers("assertion-person-a", "assertion-caregiver-a")
    base_payload = {
        "subject_type": "care_recipient",
        "subject_id": "assertion-person-a",
        "field": "meal_intake",
        "assertion_kind": "time_bound_observation",
        "asserted_value": "Left half the meal",
        "source_label": "Caregiver observation",
        "source_text": "Half the meal remained after eating.",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }

    foreign = client.post(
        "/api/v1/assertions",
        headers=headers_a,
        json={**base_payload, "evidence_ids": ["assertion-evidence-b"]},
    )
    assert foreign.status_code == 404, foreign.text

    accepted = client.post(
        "/api/v1/assertions",
        headers=headers_a,
        json={**base_payload, "evidence_ids": ["assertion-evidence-a"], "epistemic_state": "observed"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["epistemic_state"] == "observed"
    assert accepted.json()["assertion_status"] == "reported"

    listed = client.get("/api/v1/assertions/assertion-person-a", headers=headers_a)
    assert listed.status_code == 200, listed.text
    record = next(item for item in listed.json() if item["id"] == accepted.json()["id"])
    assert record["evidence_ids"] == ["assertion-evidence-a"]
    assert record["epistemic_state"] == "observed"
