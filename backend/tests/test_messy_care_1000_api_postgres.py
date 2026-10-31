"""1,000 end-to-end PostgreSQL uncertainty-analysis API journeys.

Each labelled scenario crosses the real FastAPI route, bearer-session authorization,
SQLAlchemy persistence, and a second HTTP retrieval request. No handler or database
mock is used. The corpus tests missing-evidence behavior only; it is not clinical
accuracy validation and does not replace the 10,000-record persistence corpus.
"""
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver, Person

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL") or os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")),
    reason="requires disposable PostgreSQL benchmark database",
)

SCENARIO_COUNT = 1_000


def test_1000_real_api_scenarios_persist_and_retrieve_unknown_care_threads():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    person_id = f"api10kp{suffix}"
    caregiver_id = f"api10kc{suffix}"
    token = f"api10k-token-{suffix}"
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    original_session_local = main_module.SessionLocal
    app.dependency_overrides[get_db] = override_get_db
    main_module.SessionLocal = Session
    try:
        with Session() as db:
            db.add(Person(id=person_id, name="Synthetic API benchmark person"))
            db.flush()
            db.add(Caregiver(
                id=caregiver_id,
                name="Synthetic API benchmark caregiver",
                relationship="family",
                person_id=person_id,
            ))
            db.flush()
            db.add(AnonymousSession(
                id=f"api10ks{suffix}",
                person_id=person_id,
                caregiver_id=caregiver_id,
                token_hash=token_hash,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
            ))
            db.commit()

        headers = {"Authorization": f"Bearer {token}"}
        with TestClient(app) as client:
            for index in range(SCENARIO_COUNT):
                subject_id = f"api-thread-{index:04d}"
                response = client.post(
                    "/api/v1/uncertainty/analyze",
                    headers=headers,
                    json={
                        "person_id": person_id,
                        "subject_type": ("appointment", "medication", "mobility", "nutrition", "sleep")[index % 5],
                        "subject_id": subject_id,
                        "field": "current_status",
                        "claim": f"Independently labelled missing-status scenario {index:04d}.",
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
            expected_subjects = {f"api-thread-{index:04d}" for index in range(SCENARIO_COUNT)}
            assert expected_subjects <= actual_subjects
            assert len(expected_subjects & actual_subjects) == SCENARIO_COUNT
            for row in rows:
                if row["subject_id"] in expected_subjects:
                    assert row["epistemic_state"] == "unknown"
                    assert row.get("confidence") is None

            # A second identity cannot read the benchmark person's assessment set.
            other_person_id = f"api10ko{suffix}"
            other_caregiver_id = f"api10kd{suffix}"
            other_token = f"api10k-other-{suffix}"
            with Session() as db:
                db.add(Person(id=other_person_id, name="Isolated synthetic person"))
                db.flush()
                db.add(Caregiver(
                    id=other_caregiver_id,
                    name="Isolated synthetic caregiver",
                    relationship="family",
                    person_id=other_person_id,
                ))
                db.flush()
                db.add(AnonymousSession(
                    id=f"api10ko-session-{suffix}",
                    person_id=other_person_id,
                    caregiver_id=other_caregiver_id,
                    token_hash=hashlib.sha256(other_token.encode()).hexdigest(),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=3),
                ))
                db.commit()
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
