"""End-to-end decision/outcome/follow-up continuity on disposable PostgreSQL."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module
from app.models.care import Evidence

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires the disposable PostgreSQL integration database",
)


def test_decision_outcome_revision_follow_up_fresh_session_and_person_isolation(monkeypatch):
    database_url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", test_session_local)
    suffix = uuid.uuid4().hex
    try:
        with TestClient(app) as client:
            owner = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "Decision continuity PostgreSQL owner"},
            )
            other = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "Decision continuity other person"},
            )
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
            person_id = owner.json()["person"]["id"]
            caregiver_id = owner.json()["caregiver"]["id"]

            created = client.post(
                "/api/v1/care-continuity/decisions",
                headers=headers,
                json={
                    "title": "Synthetic support decision " + suffix,
                    "decision_text": "Family agreed to trial evening home support.",
                    "decision_status": "recorded",
                    "context": "Caregiver reported that evenings had become difficult.",
                    "evidence_ids": [],
                },
            )
            assert created.status_code == 201, created.text
            decision_id = created.json()["id"]

            first_read = client.get("/api/v1/care-continuity/decisions", headers=headers)
            assert first_read.status_code == 200, first_read.text
            assert any(row["id"] == decision_id for row in first_read.json())

            outcome = client.post(
                "/api/v1/care-continuity/decision-outcomes",
                headers=headers,
                json={
                    "decision_id": decision_id,
                    "outcome_status": "observed",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "description": "Caregiver later reported that evening support began.",
                    "evidence_ids": [],
                    "is_intended_outcome": False,
                },
            )
            assert outcome.status_code == 201, outcome.text
            assert outcome.json()["decision_id"] == decision_id

            revised = client.patch(
                f"/api/v1/care-continuity/decisions/{decision_id}",
                headers=headers,
                json={"decision_text": "Family agreed to trial evening home support three days each week."},
            )
            assert revised.status_code == 200, revised.text
            assert revised.json()["decision_text"].endswith("three days each week.")

            revisions = client.get("/api/v1/care-continuity/decision-revisions", headers=headers)
            assert revisions.status_code == 200, revisions.text
            history = [row for row in revisions.json() if row.get("decision_id") == decision_id]
            assert len(history) >= 2, history
            assert any("trial evening home support." in str(row.get("previous_payload")) for row in history)

            follow_up = client.post(
                "/api/v1/care-continuity/follow-up-links",
                headers=headers,
                json={
                    "source_type": "decision",
                    "source_id": decision_id,
                    "target_type": "decision",
                    "target_id": decision_id,
                    "follow_up_kind": "check_in",
                    "status": "open",
                    "due_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
                    "notes": "Check whether the evening support arrangement is working.",
                    "evidence_ids": [],
                },
            )
            assert follow_up.status_code == 201, follow_up.text
            follow_up_id = follow_up.json()["id"]

            rejected_completion = client.patch(
                f"/api/v1/care-continuity/follow-up-links/{follow_up_id}",
                headers=headers,
                json={"status": "completed", "completed_at": datetime.now(UTC).isoformat()},
            )
            assert rejected_completion.status_code == 422, rejected_completion.text

            evidence_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            with Session(engine) as db:
                db.add(Evidence(
                    id=evidence_id,
                    person_id=person_id,
                    type="note",
                    evidence_kind="caregiver_report",
                    epistemic_status="caregiver_reported",
                    source_text="Caregiver recorded that the evening support check-in was completed.",
                    uploaded_by_caregiver_id=caregiver_id,
                    reported_by_caregiver_id=caregiver_id,
                    collected_by_caregiver_id=caregiver_id,
                    reported_by_label="Family caregiver",
                    source_type="caregiver_report",
                    occurred_at=now,
                ))
                db.commit()

            completed = client.patch(
                f"/api/v1/care-continuity/follow-up-links/{follow_up_id}",
                headers=headers,
                json={
                    "status": "completed",
                    "completed_at": now.isoformat(),
                    "evidence_ids": [evidence_id],
                },
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "completed"
            assert completed.json()["evidence_ids"] == [evidence_id]

            hidden = client.get("/api/v1/care-continuity/decisions", headers=other_headers)
            assert hidden.status_code == 200, hidden.text
            assert all(row["id"] != decision_id for row in hidden.json())
            denied_update = client.patch(
                f"/api/v1/care-continuity/decisions/{decision_id}",
                headers=other_headers,
                json={"decision_text": "Unauthorized change"},
            )
            assert denied_update.status_code == 404, denied_update.text

        # A new HTTP client/session using the same authenticated capability sees the same PostgreSQL rows.
        with TestClient(app) as refreshed_client:
            fresh_read = refreshed_client.get(
                "/api/v1/care-continuity/decisions",
                headers=headers,
            )
            assert fresh_read.status_code == 200, fresh_read.text
            assert any(row["id"] == decision_id for row in fresh_read.json())
            fresh_outcomes = refreshed_client.get(
                "/api/v1/care-continuity/decision-outcomes",
                headers=headers,
            )
            assert any(row["id"] == outcome.json()["id"] for row in fresh_outcomes.json())
            fresh_followups = refreshed_client.get(
                "/api/v1/care-continuity/follow-up-links",
                headers=headers,
            )
            assert any(row["id"] == follow_up_id and row["status"] == "completed" for row in fresh_followups.json())
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
