"""10,000 PostgreSQL-backed uncertainty-analysis API journeys.

Every case crosses the real FastAPI route, server-issued bearer-session
authorization, PostgreSQL persistence, and a subsequent HTTP retrieval request.
The corpus qualifies missing-evidence handling only; it is not clinical
accuracy validation or a substitute for full capture-to-LCR qualification.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL benchmark database",
)

SCENARIO_COUNT = 10_000
DOMAINS = ("appointment", "medication", "mobility", "nutrition", "sleep")


def test_10000_real_api_scenarios_persist_and_retrieve_unknown_care_threads():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    suffix = uuid.uuid4().hex[:10]

    try:
        with TestClient(app) as client:
            # Use the production session-bootstrap route so all relational
            # identities and FK dependencies are created by the real code path.
            created_response = client.post(
                "/api/v1/sessions/anonymous",
                json={
                    "name": f"Synthetic API benchmark person {suffix}",
                    "caregiver_name": f"Synthetic API benchmark caregiver {suffix}",
                },
            )
            assert created_response.status_code == 201, created_response.text
            created = created_response.json()
            person_id = created["person"]["id"]
            headers = {"Authorization": f"Bearer {created['access_token']}"}

            for index in range(SCENARIO_COUNT):
                subject_id = f"api-thread-{index:05d}"
                response = client.post(
                    "/api/v1/uncertainty/analyze",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": DOMAINS[index % len(DOMAINS)],
                        "subject_id": subject_id,
                        "field": "current_status",
                        "claim": f"Independently labelled missing-status scenario {index:05d}.",
                        "task": "recommendation",
                        "evidence_ids": [],
                        "required_fields": ["current_status"],
                    },
                )
                assert response.status_code == 200, (
                    f"scenario={index:04d} status={response.status_code} body={response.text}"
                )
                payload = response.json()
                assert payload["analysis"]["epistemic_state"] == "unknown", f"scenario={index:04d}"
                assert payload["analysis"]["conclusion_confidence"] is None, f"scenario={index:04d}"
                assert payload["analysis"]["review_required"] is True, f"scenario={index:04d}"

            # A new HTTP request obtains a new dependency-managed SQLAlchemy session.
            listed = client.get(
                f"/api/v1/uncertainty/assessments/{person_id}",
                headers=headers,
            )
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            actual_subjects = {row["subject_id"] for row in rows}
            expected_subjects = {f"api-thread-{index:05d}" for index in range(SCENARIO_COUNT)}
            assert expected_subjects <= actual_subjects
            assert len(expected_subjects & actual_subjects) == SCENARIO_COUNT
            for row in rows:
                if row["subject_id"] in expected_subjects:
                    assert row["epistemic_state"] == "unknown"
                    assert row.get("confidence") is None

            # A second server-issued identity cannot read the first person's records.
            other_response = client.post(
                "/api/v1/sessions/anonymous",
                json={
                    "name": f"Isolated synthetic person {suffix}",
                    "caregiver_name": f"Isolated synthetic caregiver {suffix}",
                },
            )
            assert other_response.status_code == 201, other_response.text
            other_token = other_response.json()["access_token"]
            denied = client.get(
                f"/api/v1/uncertainty/assessments/{person_id}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert denied.status_code == 403, denied.text

        print(f"MESSY_CARE_POSTGRES_API_SCENARIOS_EXECUTED={SCENARIO_COUNT}")
        print(f"MESSY_CARE_POSTGRES_API_ASSESSMENTS_RETRIEVED={SCENARIO_COUNT}")
        print("MESSY_CARE_POSTGRES_API_FRESH_HTTP_SESSION=PASS")
        print("MESSY_CARE_POSTGRES_API_CROSS_PERSON_ISOLATION=PASS")
    finally:
        app.dependency_overrides.clear()
        main_module.SessionLocal = original_session_local
        engine.dispose()
