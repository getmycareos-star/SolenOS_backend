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
            with test_session_local() as db:
                provisional = db.query(CareAssertion).filter_by(person_id=person_id).all()
                assert len(provisional) == 3
                assert all(item.assertion_status == "reported" for item in provisional)
                provisional_ids = {item.id for item in provisional}
            initial_lcr = client.get("/api/v1/care-continuity/current-care?limit_per_source=50", headers=headers)
            assert initial_lcr.status_code == 200, initial_lcr.text
            visible_ids = {item.get("id") for item in initial_lcr.json().get("items", [])}
            assert provisional_ids.issubset(visible_ids)

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
            with test_session_local() as db:
                pending_assertion_id = db.query(CareAssertion.id).filter_by(source_proposal_id=proposals[1]["id"]).scalar()
                rejected_assertion_id = db.query(CareAssertion.id).filter_by(source_proposal_id=proposals[2]["id"]).scalar()

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

            monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.test", "qwen-test"))
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
            assert accepted_assertion_id in body["source_assertion_ids"]
            assert pending_assertion_id not in body["source_assertion_ids"]
            assert rejected_assertion_id not in body["source_assertion_ids"]
            assert body["written_to_lcr"] is False
            assert body["persistence"] == "conversation_only"
            prompt = "\n".join(
                item["content"]
                for item in captured["messages"]
                if item.get("role") in {"system", "user"}
            )
            assert "two-person assistance this morning" in prompt
            assert "requested a call next Tuesday" not in prompt
            assert "ask about transport availability" not in prompt

            # Appointment follow-ups use the shared Open Loop store, with a
            # stable source reference, explicit owner/deadline provenance and
            # caregiver-reported completion kept distinct from verified closure.
            follow_up_source_id = record_id + ":follow-up-1"
            follow_up_payload = {
                "loop_type": "follow_up",
                "title": "Call the clinic for the test result",
                "description": "Caregiver-recorded appointment follow-up; result is not yet known.",
                "source_type": "appointment_workspace",
                "source_id": follow_up_source_id,
                "owner_role": "caregiver",
                "owner_label": "Family caregiver",
                "deadline_at": "2026-11-10T12:00:00+00:00",
                "deadline_source": "explicit_user",
                "deadline_timezone": "UTC",
                "idempotency_key": "appointment-follow-up:" + follow_up_source_id,
            }
            follow_up = client.post("/api/v1/open-loops", headers=fresh_headers, json=follow_up_payload)
            assert follow_up.status_code == 201, follow_up.text
            loop_id = follow_up.json()["id"]
            assert follow_up.json()["source_type"] == "appointment_workspace"
            assert follow_up.json()["source_id"] == follow_up_source_id
            assert follow_up.json()["owner_label"] == "Family caregiver"
            assert follow_up.json()["deadline_timezone"] == "UTC"
            assert follow_up.json()["status"] == "open"

            follow_up_replay = client.post("/api/v1/open-loops", headers=fresh_headers, json=follow_up_payload)
            assert follow_up_replay.status_code == 201, follow_up_replay.text
            assert follow_up_replay.json()["id"] == loop_id

            follow_up_update = client.patch(
                f"/api/v1/open-loops/{loop_id}",
                headers=fresh_headers,
                json={
                    "title": "Call the clinic and ask for the result",
                    "description": "Caregiver updated the wording; the result remains unconfirmed.",
                    "owner_role": "caregiver",
                    "owner_label": "Family caregiver",
                    "deadline_at": "2026-11-11T12:00:00+00:00",
                    "deadline_source": "explicit_user",
                    "deadline_timezone": "UTC",
                    "reason": "Caregiver updated appointment follow-up details",
                    "idempotency_key": "appointment-follow-up-update:" + follow_up_source_id,
                },
            )
            assert follow_up_update.status_code == 200, follow_up_update.text
            assert follow_up_update.json()["title"] == "Call the clinic and ask for the result"

            reported_done = client.post(
                f"/api/v1/open-loops/{loop_id}/outcomes",
                headers=fresh_headers,
                json={
                    "text": "Caregiver marked the task done; the result has not been independently verified.",
                    "evidence": [],
                    "idempotency_key": "appointment-follow-up-done:" + follow_up_source_id,
                },
            )
            assert reported_done.status_code == 200, reported_done.text
            assert reported_done.json()["outcome_status"] == "outcome_reported"
            assert reported_done.json()["status"] == "open"

            # Closing the appointment workspace with outstanding work must
            # never close or erase the independently persisted Open Loop.
            workspace_state = client.get(
                f"/api/v1/appointment-workspaces/{record_id}",
                headers=fresh_headers,
            )
            assert workspace_state.status_code == 200, workspace_state.text
            workspace_version = workspace_state.json()["version_number"]
            workspace_payload = workspace_state.json()["payload"]
            for next_status, key in (
                ("follow_up_pending", "pg-appointment-follow-up-stage"),
                ("closed_with_outstanding_items", "pg-appointment-close-outstanding"),
            ):
                closed = client.patch(
                    f"/api/v1/appointment-workspaces/{record_id}",
                    headers=fresh_headers,
                    json={
                        "expected_version": workspace_version,
                        "status": next_status,
                        "payload": workspace_payload,
                        "change_reason": "PostgreSQL acceptance: retain outstanding follow-up independently",
                        "idempotency_key": key,
                    },
                )
                assert closed.status_code == 200, closed.text
                workspace_version = closed.json()["version_number"]
                assert closed.json()["status"] == next_status

            still_open = client.get(f"/api/v1/open-loops/{loop_id}", headers=fresh_headers)
            assert still_open.status_code == 200, still_open.text
            assert still_open.json()["loop"]["status"] == "open"

            foreign_follow_up = client.get(f"/api/v1/open-loops/{loop_id}", headers=other_headers)
            assert foreign_follow_up.status_code == 404, foreign_follow_up.text

            with test_session_local() as db:
                assertions = db.query(CareAssertion).filter(
                    CareAssertion.person_id == person_id
                ).all()
                assert len(assertions) == 3
                accepted_assertion = next(item for item in assertions if item.id == accepted_assertion_id)
                assert accepted_assertion.assertion_status == "caregiver_reviewed"
                assert any(item.assertion_status == "reported" for item in assertions)
                assert any(item.assertion_status == "rejected" for item in assertions)
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


def test_postgres_daily_capture_http_write_latency_idempotency_and_isolation(monkeypatch):
    """Benchmark the real Daily Capture write route with intelligence isolated.

    Qwen extraction and Delta reconciliation are stubbed so this reports the
    durable capture write path only. Synthetic records exist only in disposable
    PostgreSQL CI; no production caregiver data is created.
    """
    from app.api import v1 as v1_module

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
    monkeypatch.setattr(v1_module, "extract_review_candidates", lambda *args, **kwargs: {"observations": [], "open_questions": []})
    monkeypatch.setattr(v1_module, "reconcile_person_change_intelligence", lambda *args, **kwargs: None)
    suffix = uuid.uuid4().hex
    sample_count = 20
    write_ms: list[float] = []
    event_ids: list[str] = []

    try:
        with TestClient(app) as client:
            owner = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic Daily Capture benchmark owner"})
            other = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic Daily Capture benchmark other"})
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            owner_headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            other_headers = {"Authorization": "Bearer " + other.json()["access_token"]}
            person_id = owner.json()["person"]["id"]
            caregiver_id = owner.json()["caregiver"]["id"]

            for index in range(sample_count):
                payload = {
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "medications": [],
                    "vitals": [],
                    "scales": [],
                    "notes": f"Synthetic Daily Capture benchmark observation {index} ({suffix}).",
                    "capture_idempotency_key": f"daily-capture-{suffix}-{index}",
                }
                started = time.perf_counter()
                response = client.post("/api/v1/daily/log", headers=owner_headers, json=payload)
                write_ms.append((time.perf_counter() - started) * 1000)
                assert response.status_code == 200, response.text
                receipt = response.json()
                assert receipt["success"] is True
                assert receipt["idempotent_replay"] is False
                assert receipt["qwen_written_to_lcr"] is False
                event_ids.append(receipt["id"])

            replay_payload = {
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": f"Synthetic Daily Capture benchmark observation 0 ({suffix}).",
                "capture_idempotency_key": f"daily-capture-{suffix}-0",
            }
            replay = client.post("/api/v1/daily/log", headers=owner_headers, json=replay_payload)
            assert replay.status_code == 200, replay.text
            assert replay.json()["id"] == event_ids[0]
            assert replay.json()["idempotent_replay"] is True

            changed = client.post(
                "/api/v1/daily/log",
                headers=owner_headers,
                json={**replay_payload, "notes": "Changed payload must not overwrite the original capture."},
            )
            assert changed.status_code == 409, changed.text

            cross_person = client.post(
                "/api/v1/daily/log",
                headers=other_headers,
                json={**replay_payload, "capture_idempotency_key": f"cross-person-{suffix}"},
            )
            assert cross_person.status_code == 403, cross_person.text

            print("POSTGRES_DAILY_CAPTURE_HTTP_BENCHMARK=" + json.dumps({
                "sample_count": sample_count,
                "daily_log_post_duration_ms": _percentiles(write_ms),
                "idempotent_replay": "PASS",
                "changed_payload_key_reuse": "409",
                "cross_person_visibility": "DENIED",
                "qwen_and_delta": "stubbed_to_isolate_durable_write_path",
                "scope": "disposable_postgresql_ci_only",
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
