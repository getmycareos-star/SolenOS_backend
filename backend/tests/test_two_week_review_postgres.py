"""PostgreSQL lifecycle and authorization tests for persisted two-week reviews."""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires migrated PostgreSQL DATABASE_URL",
)

from app.main import app
from app.models.care import CareEvent, Evidence
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenJobEvent

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def _new_anonymous(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": label, "caregiver_name": label},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_two_week_review_decisions_survive_new_login_and_remain_caregiver_scoped():
    """Review receipts and source references survive independent PostgreSQL sessions."""
    suffix = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)

    with TestClient(app) as client:
        anonymous = _new_anonymous(client, f"Two-week review caregiver {suffix}")
        person_id = anonymous["person"]["id"]
        caregiver_id = anonymous["caregiver"]["id"]
        anonymous_headers = {"Authorization": f"Bearer {anonymous['access_token']}"}

        email = f"two-week-review-{suffix}@example.test"
        password = f"two-week-review-password-{suffix}"
        account = client.post(
            "/api/v1/accounts",
            headers=anonymous_headers,
            json={"email": email, "password": password},
        )
        assert account.status_code == 201, account.text
        login = client.post("/api/v1/accounts/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert login.json()["person_id"] == person_id
        assert login.json()["caregiver_id"] == caregiver_id

        event_id = f"two-week-event-{suffix}"
        evidence_id = f"two-week-evidence-{suffix}"
        job_id = f"two-week-job-{suffix}"
        output_id = f"two-week-output-{suffix}"
        with Session() as db:
            db.add(Evidence(
                id=evidence_id,
                person_id=person_id,
                type="caregiver_note",
                source_text="The caregiver reported several interrupted nights of sleep.",
                uploaded_by_caregiver_id=caregiver_id,
                reported_by_caregiver_id=caregiver_id,
                epistemic_status="caregiver_reported",
            ))
            db.flush()
            db.add(CareEvent(
                id=event_id,
                person_id=person_id,
                event_type="daily_care_entry",
                status="recorded",
                occurred_at=now,
                title="Sleep update",
                description="The caregiver reported several interrupted nights of sleep.",
                evidence_ids=[evidence_id],
                created_by_caregiver_id=caregiver_id,
            ))
            db.flush()
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                task_text="Review care events from the past fourteen days.",
                context_text="",
                evidence_ids_json=json.dumps([evidence_id]),
                workflow="two_week_review",
                status="completed",
                source_event_ids_json=json.dumps([event_id]),
                source_evidence_ids_json=json.dumps([evidence_id]),
                completed_at=now,
            ))
            db.flush()
            db.add(QwenModelOutput(
                id=output_id,
                job_id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                workflow="two_week_review",
                model_name="postgresql-review-fixture",
                output_text="A source-grounded two-week summary for this PostgreSQL lifecycle test.",
                output_metadata={"source_event_ids": [event_id], "source_evidence_ids": [evidence_id]},
                source_event_ids_json=json.dumps([event_id]),
                source_evidence_ids_json=json.dumps([evidence_id]),
                review_status="pending_review",
                review_required=True,
                written_to_lcr=False,
                created_at=now,
            ))
            db.commit()

        other = _new_anonymous(client, f"Separate two-week review caregiver {suffix}")
        other_headers = {"Authorization": f"Bearer {other['access_token']}"}

        reviewed = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=headers,
            json={"decision": "reviewed"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["review_status"] == "reviewed"
        assert reviewed.json()["review_required"] is False
        assert reviewed.json()["written_to_lcr"] is False
        assert reviewed.json()["idempotent_replay"] is False

        replay = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=headers,
            json={"decision": "reviewed"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True

        follow_up = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=headers,
            json={"decision": "needs_follow_up"},
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["review_status"] == "needs_follow_up"
        assert follow_up.json()["review_required"] is False
        assert follow_up.json()["written_to_lcr"] is False
        assert [entry["decision"] for entry in follow_up.json()["review_history"]] == [
            "reviewed", "needs_follow_up"
        ]

        status = client.get(f"/api/v1/assistant/jobs/{job_id}", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["output_review_status"] == "needs_follow_up"
        assert status.json()["review_required"] is False
        assert status.json()["written_to_lcr"] is False
        assert status.json()["review_history"] == follow_up.json()["review_history"]
        assert status.json()["source_event_ids"] == [event_id]
        assert status.json()["source_evidence_ids"] == [evidence_id]

        history = client.get("/api/v1/assistant/reviews/two-week?limit=10", headers=headers)
        assert history.status_code == 200, history.text
        assert history.json()["count"] == 1
        item = history.json()["items"][0]
        assert item["job_id"] == job_id
        assert item["output_id"] == output_id
        assert item["source_event_ids"] == [event_id]
        assert item["source_evidence_ids"] == [evidence_id]
        assert [entry["decision"] for entry in item["review_history"]] == [
            "reviewed", "needs_follow_up"
        ]

        denied_status = client.get(f"/api/v1/assistant/jobs/{job_id}", headers=other_headers)
        assert denied_status.status_code == 404
        denied_review = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=other_headers,
            json={"decision": "reviewed"},
        )
        assert denied_review.status_code == 404
        other_history = client.get(
            "/api/v1/assistant/reviews/two-week?limit=10",
            headers=other_headers,
        )
        assert other_history.status_code == 200, other_history.text
        assert other_history.json()["items"] == []

    # Independent SQLAlchemy sessions prove that both the current decision and
    # its ordered audit history were committed, not held in request memory.
    with Session() as db:
        saved = db.query(QwenModelOutput).filter_by(id=output_id).one()
        assert saved.review_status == "needs_follow_up"
        assert saved.review_required is False
        assert saved.written_to_lcr is False
        events = db.query(QwenJobEvent).filter_by(
            job_id=job_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            event_type="caregiver_review_decision",
        ).order_by(QwenJobEvent.created_at.asc(), QwenJobEvent.id.asc()).all()
        assert [event.to_status for event in events] == ["reviewed", "needs_follow_up"]
        assert all(event.caregiver_id == caregiver_id for event in events)
        assert all(event.metadata_json["written_to_lcr"] is False for event in events)
        assert db.query(CareEvent).filter_by(id=event_id, person_id=person_id).count() == 1
        assert db.query(Evidence).filter_by(id=evidence_id, person_id=person_id).count() == 1

    engine.dispose()
