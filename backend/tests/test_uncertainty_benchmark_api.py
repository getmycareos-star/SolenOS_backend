"""API/database integration contracts for the uncertainty benchmark.

These tests use a real SQLAlchemy schema and FastAPI request path against isolated SQLite.
They do not claim PostgreSQL production parity; PostgreSQL migration/E2E checks remain separate.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db, SessionLocal as DefaultSessionLocal
import app.main as main_module
from app.main import app
from app.models.care import Caregiver, Person
from app.models.care_continuity import CareAssertion
from app.models.anonymous_session import AnonymousSession


@pytest.fixture
def uncertainty_api_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = TestingSessionLocal
    try:
        with TestingSessionLocal() as db:
            for suffix in ("a", "b"):
                person = Person(id=f"benchmark-person-{suffix}", name=f"Benchmark Person {suffix}")
                caregiver = Caregiver(
                    id=f"benchmark-caregiver-{suffix}",
                    name=f"Benchmark Caregiver {suffix}",
                    email=f"benchmark-{suffix}@example.invalid",
                    relationship="Family",
                    person_id=person.id,
                )
                db.add_all([person, caregiver])
            db.commit()
        yield TestingSessionLocal
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = DefaultSessionLocal
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _client_for(TestingSessionLocal, suffix):
    token = f"benchmark-token-{suffix}"
    with TestingSessionLocal() as db:
        db.add(
            AnonymousSession(
                person_id=f"benchmark-person-{suffix}",
                caregiver_id=f"benchmark-caregiver-{suffix}",
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        db.commit()
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.mark.session_security
def test_uncertainty_api_denies_cross_person_gap_reads(uncertainty_api_db):
    client_a = _client_for(uncertainty_api_db, "a")
    response = client_a.get("/api/v1/uncertainty/gaps/benchmark-person-b")
    assert response.status_code == 403
    assert "not authorised" in response.json()["detail"].lower()


@pytest.mark.session_security
def test_unknown_assessment_persists_and_projects_as_unknown(uncertainty_api_db):
    client = _client_for(uncertainty_api_db, "a")
    created = client.post(
        "/api/v1/uncertainty/assessments",
        json={
            "person_id": "benchmark-person-a",
            "subject_type": "medication",
            "subject_id": "medication-1",
            "field": "current_status",
            "epistemic_state": "unknown",
            "gap_reason": "not_documented",
            "known_value": None,
        },
    )
    assert created.status_code == 200, created.text
    created_data = created.json()
    assert created_data["epistemic_state"] == "unknown"
    assert created_data.get("confidence") is None

    # A second HTTP request uses a fresh dependency-managed DB session.
    listed = client.get("/api/v1/uncertainty/assessments/benchmark-person-a")
    assert listed.status_code == 200, listed.text
    matching = [
        item for item in listed.json()
        if item.get("subject_id") == "medication-1" and item.get("field") == "current_status"
    ]
    assert len(matching) == 1
    assert matching[0]["epistemic_state"] == "unknown"
    assert matching[0].get("confidence") is None

    view = client.get(
        "/api/v1/uncertainty/view/benchmark-person-a/medication/medication-1"
    )
    assert view.status_code == 200, view.text
    assert view.json()["epistemic_state"] == "unknown"
    assert view.json().get("known_value") is None


@pytest.mark.session_security
def test_analysis_propagates_missing_evidence_into_persisted_view_and_open_questions(
    uncertainty_api_db,
):
    client = _client_for(uncertainty_api_db, "a")
    response = client.post(
        "/api/v1/uncertainty/analyze",
        json={
            "person_id": "benchmark-person-a",
            "subject_type": "appointment",
            "subject_id": "appointment-unknown-1",
            "field": "attendance_status",
            "claim": "The care recipient attended the appointment.",
            "task": "recommendation",
            "evidence_ids": [],
            "required_fields": ["attendance_status"],
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["analysis"]["epistemic_state"] == "unknown"
    assert payload["analysis"]["conclusion_confidence"] is None
    assert payload["analysis"]["review_required"] is True

    persisted = client.get(
        "/api/v1/uncertainty/assessments/benchmark-person-a",
        params={"subject_type": "appointment", "subject_id": "appointment-unknown-1"},
    )
    assert persisted.status_code == 200, persisted.text
    rows = persisted.json()
    assert len(rows) == 1
    assert rows[0]["epistemic_state"] == "unknown"
    assert rows[0].get("confidence") is None

    view = client.get(
        "/api/v1/uncertainty/view/benchmark-person-a/appointment/appointment-unknown-1"
    )
    assert view.status_code == 200, view.text
    view_data = view.json()
    assert view_data["epistemic_state"] == "unknown"
    assert view_data.get("known_value") is None

    questions = client.get(
        "/api/v1/uncertainty/questions/benchmark-person-a",
        params={"subject_type": "appointment", "subject_id": "appointment-unknown-1"},
    )
    assert questions.status_code == 200, questions.text
    assert any(item.get("status") == "open" for item in questions.json())


@pytest.mark.session_security
def test_handoff_draft_preserves_active_unknown_assessment_and_excludes_superseded(
    uncertainty_api_db,
):
    from app.models.uncertainty import UncertaintyAssessment
    from app.core.uncertainty_enums import EpistemicState, GapReason

    client = _client_for(uncertainty_api_db, "a")
    with uncertainty_api_db() as db:
        db.add_all([
            UncertaintyAssessment(
                id="assessment-current-unknown",
                person_id="benchmark-person-a",
                subject_type="medication",
                subject_id="medication-1",
                field="current_status",
                epistemic_state=EpistemicState.UNKNOWN,
                confidence=None,
                known_value=None,
                gap_reason=GapReason.NOT_DOCUMENTED,
                evidence_ids=[],
                is_active=True,
            ),
            UncertaintyAssessment(
                id="assessment-superseded",
                person_id="benchmark-person-a",
                subject_type="medication",
                subject_id="medication-1",
                field="current_status",
                epistemic_state=EpistemicState.KNOWN,
                confidence=0.99,
                known_value="taken",
                gap_reason=None,
                evidence_ids=[],
                is_active=False,
                superseded_by_assessment_id="assessment-current-unknown",
            ),
        ])
        db.commit()

    response = client.post(
        "/api/v1/handoff-drafts/prepare",
        json={"purpose": "professional_appointment", "audience": "professional", "window_days": 30},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    status = payload["source_statuses"]["uncertainty_assessments"]
    assert status["status"] == "available"
    refs = [row for row in payload["source_references"] if row["source_table"] == "uncertainty_assessments"]
    assert len(refs) == 1
    assert refs[0]["source_id"] == "assessment-current-unknown"
    assert refs[0]["epistemic_status"].lower().endswith("unknown")
    assert refs[0]["details"]["confidence"] is None or "confidence" not in refs[0]["details"]
    assert "uncertainty_assessments" in next(
        section for section in payload["template_sections"] if section["key"] == "problems_uncertainty"
    )["source_tables"]


@pytest.mark.session_security
def test_claim_dependency_graph_persists_and_rejects_cross_record_and_cycles(uncertainty_api_db):
    client = _client_for(uncertainty_api_db, "a")
    with uncertainty_api_db() as db:
        for claim_id, person_id, value in (
            ("claim-a", "benchmark-person-a", "Appetite changed"),
            ("claim-b", "benchmark-person-a", "Breakfast intake recorded"),
            ("claim-c", "benchmark-person-a", "Usual breakfast baseline"),
            ("claim-other", "benchmark-person-b", "Unrelated person's claim"),
        ):
            db.add(CareAssertion(
                id=claim_id,
                person_id=person_id,
                subject_type="daily_care",
                subject_id="breakfast",
                field="intake",
                assertion_kind="reported_state",
                asserted_value=value,
                source_label="caregiver report",
                source_text=value,
                evidence_ids=[],
                assertion_status="reported",
            ))
        db.commit()

    def add_dependency(claim_id, depends_on_claim_id):
        return client.post("/api/v1/uncertainty/claims/dependencies", json={
            "person_id": "benchmark-person-a",
            "claim_id": claim_id,
            "depends_on_claim_id": depends_on_claim_id,
            "dependency_type": "derived_from",
            "rationale": "Declared relationship for dependency-graph regression test.",
        })

    first = add_dependency("claim-a", "claim-b")
    assert first.status_code == 200, first.text
    second = add_dependency("claim-b", "claim-c")
    assert second.status_code == 200, second.text

    graph = client.get("/api/v1/uncertainty/claims/claim-b/dependencies")
    assert graph.status_code == 200, graph.text
    graph_data = graph.json()
    assert any(row["depends_on_claim_id"] == "claim-c" for row in graph_data["dependencies"])
    assert any(row["claim_id"] == "claim-a" for row in graph_data["dependents"])
    assert "do not prove semantic entailment" in graph_data["notice"]

    cycle = add_dependency("claim-c", "claim-a")
    assert cycle.status_code == 422, cycle.text
    assert "cycle" in cycle.json()["detail"].lower()

    cross_record = add_dependency("claim-a", "claim-other")
    assert cross_record.status_code == 404, cross_record.text
