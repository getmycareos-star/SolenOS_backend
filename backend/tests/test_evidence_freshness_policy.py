"""API tests for explicit, versioned, person-scoped evidence freshness policies."""
from datetime import datetime, timedelta, timezone
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.anonymous_session import AnonymousSession
from app.models.care import Person, Caregiver, Evidence

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


def setup_records():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    people = []
    for suffix in ("a", "b"):
        person = Person(id=f"freshness-person-{suffix}", name=f"Person {suffix}")
        caregiver = Caregiver(
            id=f"freshness-caregiver-{suffix}",
            name=f"Caregiver {suffix}",
            relationship="family",
            person_id=person.id,
        )
        people.append((person, caregiver))
        db.add_all([person, caregiver])
    db.flush()
    db.add(Evidence(
        id="freshness-evidence-a",
        person_id="freshness-person-a",
        type="document",
        source_text="Documented care evidence with an explicit event timestamp.",
        uploaded_by_caregiver_id="freshness-caregiver-a",
        occurred_at=datetime.now(timezone.utc) - timedelta(days=45),
        collected_at=datetime.now(timezone.utc) - timedelta(days=44),
    ))
    db.add(Evidence(
        id="freshness-evidence-no-time",
        person_id="freshness-person-a",
        type="report",
        source_text="Evidence without any event or collection timestamp.",
        uploaded_by_caregiver_id="freshness-caregiver-a",
    ))
    db.commit()
    # Clear the ORM upload timestamp as well: an upload time is a valid
    # freshness reference and must not be mistaken for absent source time.
    db.query(Evidence).filter(Evidence.id == "freshness-evidence-no-time").update(
        {Evidence.uploaded_at: None}, synchronize_session=False,
    )
    db.commit()
    for _, caregiver in people:
        token = f"freshness-token-{caregiver.person_id}"
        db.add(AnonymousSession(
            person_id=caregiver.person_id,
            caregiver_id=caregiver.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        ))
    db.commit()
    db.close()


def client_for(person_id: str) -> TestClient:
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer freshness-token-{person_id}"})
    return client


def teardown_records():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_explicit_domain_policy_persists_assessment_history_and_never_guesses_missing_time():
    setup_records()
    try:
        app.dependency_overrides[get_db] = override_get_db
        client = client_for("freshness-person-a")
        rejected = client.post("/api/v1/care-continuity/evidence-freshness/policies", json={
            "domain_key": "medication_history",
            "policy_version": "v1",
            "rationale": "A threshold must be explicit.",
        })
        assert rejected.status_code == 422

        policy_response = client.post("/api/v1/care-continuity/evidence-freshness/policies", json={
            "domain_key": "medication_history",
            "policy_version": "v1",
            "max_age_days": 30,
            "historical_after_days": 180,
            "rationale": "Caregiver-selected review interval for this domain; not a clinical rule.",
        })
        assert policy_response.status_code == 201, policy_response.text
        policy = policy_response.json()
        assert policy["domain_key"] == "medication_history"
        assert policy["max_age_days"] == 30
        assert policy["policy_version"] == "v1"
        assert policy["immutable"] is True

        duplicate = client.post("/api/v1/care-continuity/evidence-freshness/policies", json={
            "domain_key": "medication_history",
            "policy_version": "v1",
            "max_age_days": 7,
            "rationale": "Must not overwrite the earlier policy.",
        })
        assert duplicate.status_code == 409

        assessed = client.post("/api/v1/care-continuity/evidence-freshness/assessments", json={
            "evidence_id": "freshness-evidence-a",
            "policy_id": policy["id"],
        })
        assert assessed.status_code == 201, assessed.text
        result = assessed.json()
        assert result["freshness_status"] == "needs_review"
        assert result["policy_version"] == "v1"
        assert result["historical_record"] is True

        missing_time = client.post("/api/v1/care-continuity/evidence-freshness/assessments", json={
            "evidence_id": "freshness-evidence-no-time",
            "policy_id": policy["id"],
        })
        assert missing_time.status_code == 201, missing_time.text
        assert missing_time.json()["freshness_status"] == "unknown_source_time"
        assert missing_time.json()["age_days"] is None

        history = client.get("/api/v1/care-continuity/evidence-freshness/assessments/freshness-evidence-a")
        assert history.status_code == 200
        assert len(history.json()["items"]) == 1
        assert history.json()["items"][0]["policy_version"] == "v1"

        listed = client.get("/api/v1/care-continuity/evidence-freshness/policies")
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1
    finally:
        teardown_records()


def test_freshness_policy_and_evidence_are_scoped_to_authorized_person():
    setup_records()
    try:
        app.dependency_overrides[get_db] = override_get_db
        client_a = client_for("freshness-person-a")
        client_b = client_for("freshness-person-b")
        policy = client_a.post("/api/v1/care-continuity/evidence-freshness/policies", json={
            "domain_key": "daily_function",
            "policy_version": "v1",
            "max_age_days": 14,
            "rationale": "Explicit caregiver-selected review window.",
        })
        assert policy.status_code == 201, policy.text

        foreign_policy = client_b.post("/api/v1/care-continuity/evidence-freshness/assessments", json={
            "evidence_id": "freshness-evidence-a",
            "policy_id": policy.json()["id"],
        })
        assert foreign_policy.status_code == 404

        foreign_evidence = client_b.get("/api/v1/care-continuity/evidence-freshness/assessments/freshness-evidence-a")
        assert foreign_evidence.status_code == 404
        assert client_b.get("/api/v1/care-continuity/evidence-freshness/policies").json()["items"] == []
    finally:
        teardown_records()



def test_freshness_policy_and_assessment_history_are_immutable_in_the_orm():
    from app.models.care_continuity import EvidenceFreshnessPolicy, EvidenceFreshnessAssessment

    setup_records()
    try:
        app.dependency_overrides[get_db] = override_get_db
        client = client_for("freshness-person-a")
        policy_response = client.post("/api/v1/care-continuity/evidence-freshness/policies", json={
            "domain_key": "sleep_observation",
            "policy_version": "v1",
            "max_age_days": 7,
            "rationale": "Explicit review boundary for this test domain.",
        })
        assert policy_response.status_code == 201, policy_response.text
        policy_id = policy_response.json()["id"]
        assessment = client.post("/api/v1/care-continuity/evidence-freshness/assessments", json={
            "evidence_id": "freshness-evidence-a",
            "policy_id": policy_id,
        })
        assert assessment.status_code == 201, assessment.text

        db = TestingSessionLocal()
        try:
            policy = db.query(EvidenceFreshnessPolicy).filter_by(id=policy_id).one()
            policy.rationale = "Attempted in-place policy rewrite"
            with pytest.raises(ValueError, match="append-only"):
                db.commit()
            db.rollback()

            record = db.query(EvidenceFreshnessAssessment).filter_by(id=assessment.json()["id"]).one()
            record.rationale = "Attempted assessment rewrite"
            with pytest.raises(ValueError, match="append-only"):
                db.commit()
            db.rollback()
        finally:
            db.close()
    finally:
        teardown_records()
