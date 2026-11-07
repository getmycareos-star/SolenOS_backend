"""Authenticated PostgreSQL appointment workspace write benchmark.

All records are synthetic and created only in the disposable CI PostgreSQL database.
"""
import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
import app.main as main_module

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires the disposable PostgreSQL integration database",
)


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)

    def at(p: float) -> float:
        position = (len(ordered) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99)}


def test_postgres_appointment_workspace_create_update_retry_and_isolation(monkeypatch):
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
    sample_count = 20
    create_ms: list[float] = []
    update_ms: list[float] = []
    record_ids: list[str] = []

    try:
        with TestClient(app) as client:
            owner = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic appointment benchmark owner"})
            other = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic appointment benchmark other"})
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}

            for index in range(sample_count):
                payload = {
                    "status": "preparation",
                    "payload": {
                        "visitPurpose": "Synthetic follow-up visit",
                        "caregiverReport": f"Synthetic caregiver report {index}",
                        "unanswered": ["What follow-up is required?"],
                    },
                    "change_reason": "Synthetic benchmark initial save",
                    "idempotency_key": f"appt-create-{suffix}-{index}",
                }
                started = time.perf_counter()
                created = client.post("/api/v1/appointment-workspaces", headers=headers, json=payload)
                create_ms.append((time.perf_counter() - started) * 1000)
                assert created.status_code == 201, created.text
                record_id = created.json()["id"]
                record_ids.append(record_id)
                assert created.json()["version_number"] == 1

                revision_payload = {
                    "expected_version": 1,
                    "status": "follow_up_pending",
                    "payload": {
                        **payload["payload"],
                        "followUps": [{
                            "action": f"Call clinic for synthetic result {index}",
                            "owner": "Caregiver",
                            "status": "open",
                        }],
                    },
                    "change_reason": "Synthetic benchmark post-appointment update",
                    "idempotency_key": f"appt-update-{suffix}-{index}",
                }
                started = time.perf_counter()
                updated = client.patch(
                    f"/api/v1/appointment-workspaces/{record_id}",
                    headers=headers,
                    json=revision_payload,
                )
                update_ms.append((time.perf_counter() - started) * 1000)
                assert updated.status_code == 200, updated.text
                assert updated.json()["version_number"] == 2

                replay = client.patch(
                    f"/api/v1/appointment-workspaces/{record_id}",
                    headers=headers,
                    json=revision_payload,
                )
                assert replay.status_code == 200, replay.text
                assert replay.json()["version_number"] == 2

                changed_retry = client.patch(
                    f"/api/v1/appointment-workspaces/{record_id}",
                    headers=headers,
                    json={
                        **revision_payload,
                        "payload": {
                            **revision_payload["payload"],
                            "caregiverReport": "changed",
                        },
                    },
                )
                assert changed_retry.status_code == 409, changed_retry.text

            denied = client.get(
                f"/api/v1/appointment-workspaces/{record_ids[0]}",
                headers=other_headers,
            )
            assert denied.status_code == 404, denied.text
            denied_update = client.patch(
                f"/api/v1/appointment-workspaces/{record_ids[0]}",
                headers=other_headers,
                json={
                    "expected_version": 2,
                    "payload": {"private": "must not change"},
                    "change_reason": "cross-person attempt",
                },
            )
            assert denied_update.status_code == 404, denied_update.text

            print("POSTGRES_APPOINTMENT_HTTP_BENCHMARK=" + json.dumps({
                "sample_count": sample_count,
                "create_post_duration_ms": _percentiles(create_ms),
                "update_patch_duration_ms": _percentiles(update_ms),
                "idempotent_update_replay": "PASS",
                "changed_payload_key_reuse": "409",
                "cross_person_read_and_write": "404",
                "scope": "disposable_postgresql_ci_only",
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()



def test_postgres_appointment_outcome_review_retrieves_only_accepted_assertions_in_fresh_session(monkeypatch):
    from app.models.care_continuity import CareAssertion
    from app.models.collaboration import ConversationMessage, ConversationSession
    import app.api.collaboration as collaboration

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
    try:
        with TestClient(app) as client:
            owner = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "PostgreSQL appointment owner"},
            )
            other = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "PostgreSQL appointment other"},
            )
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
            person_id = owner.json()["person"]["id"]

            created = client.post(
                "/api/v1/appointment-workspaces",
                headers=headers,
                json={
                    "status": "outcome_pending",
                    "payload": {
                        "caregiverReport": "Caregiver reports that dressing required two-person assistance this morning.",
                        "clinicianFindings": "Caregiver recorded that the clinician requested a call next Tuesday.",
                        "decisions": "Family plans to ask about transport availability.",
                    },
                    "change_reason": "PostgreSQL appointment outcome retrieval test",
                    "idempotency_key": "pg-appointment-outcome-" + uuid.uuid4().hex,
                },
            )
            assert created.status_code == 201, created.text
            record_id = created.json()["id"]

            proposals_response = client.post(
                f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals",
                headers=headers,
            )
            assert proposals_response.status_code == 200, proposals_response.text
            proposals = proposals_response.json()["items"]
            assert len(proposals) == 3

            accepted = client.post(
                f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[0]['id']}/review",
                headers=headers,
                json={"decision": "accept"},
            )
            assert accepted.status_code == 200, accepted.text
            accepted_assertion_id = accepted.json()["assertion_id"]
            assert accepted.json()["written_to_lcr"] is True

            rejected = client.post(
                f"/api/v1/appointment-workspaces/{record_id}/outcome-proposals/{proposals[2]['id']}/review",
                headers=headers,
                json={"decision": "reject"},
            )
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["written_to_lcr"] is False

            denied = client.get(
                f"/api/v1/appointment-workspaces/{record_id}",
                headers=other_headers,
            )
            assert denied.status_code == 404, denied.text

            refreshed = client.post("/api/v1/sessions/refresh", headers=headers)
            assert refreshed.status_code == 200, refreshed.text
            fresh_headers = {"Authorization": "Bearer " + refreshed.json()["access_token"]}
            assert refreshed.json()["person_id"] == person_id

            captured = {}

            def fake_ollama(base, model, messages, options):
                captured["messages"] = messages
                return "The caregiver reported that dressing required two-person assistance this morning."

            monkeypatch.setattr(collaboration, "_ollama_chat", fake_ollama)
            monkeypatch.setattr(
                collaboration,
                "process_companion_care_update_message",
                lambda *args, **kwargs: None,
            )
            companion = client.post(
                "/api/v1/conversation/message",
                headers=fresh_headers,
                json={
                    "person_id": person_id,
                    "text": "What did the caregiver report about dressing?",
                },
            )
            assert companion.status_code == 200, companion.text
            body = companion.json()
            assert body["source_assertion_ids"] == [accepted_assertion_id]
            assert body["written_to_lcr"] is False
            assert body["persistence"] == "conversation_only"
            prompt = "\\n".join(
                item["content"]
                for item in captured["messages"]
                if item.get("role") in {"system", "user"}
            )
            assert "two-person assistance this morning" in prompt
            assert "requested a call next Tuesday" not in prompt
            assert "ask about transport availability" not in prompt

            with test_session_local() as db:
                assertions = db.query(CareAssertion).filter(
                    CareAssertion.person_id == person_id
                ).all()
                assert len(assertions) == 1
                assert assertions[0].id == accepted_assertion_id
                assert assertions[0].assertion_status == "caregiver_reviewed"
                conversation = db.query(ConversationMessage).join(
                    ConversationSession,
                    ConversationMessage.session_id == ConversationSession.id,
                ).filter(
                    ConversationSession.person_id == person_id,
                    ConversationSession.caregiver_id == owner.json()["caregiver"]["id"],
                ).all()
                assert len(conversation) == 2
                assert {item.role for item in conversation} == {"user", "assistant"}
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
