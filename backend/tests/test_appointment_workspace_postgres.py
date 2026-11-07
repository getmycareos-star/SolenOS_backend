"""PostgreSQL HTTP benchmark for authenticated appointment workspace writes.

All care records in this test are synthetic and live only in the disposable CI database.
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
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestSessionLocal)
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
                        "followUps": [{"action": f"Call clinic for synthetic result {index}", "owner": "Caregiver", "status": "open"}],
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

                # A transport retry with the same key and body must not create
                # another workspace or revision, even though expected_version is stale.
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
                    json={**revision_payload, "payload": {**revision_payload["payload"], "caregiverReport": "changed"}},
                )
                assert changed_retry.status_code == 409, changed_retry.text

            denied = client.get(f"/api/v1/appointment-workspaces/{record_ids[0]}", headers=other_headers)
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


def test_postgres_daily_capture_http_write_retry_and_isolation(monkeypatch):
    """Benchmark the real authenticated Daily Capture POST using only disposable CI data."""
    from app.models.care import CareEvent, Evidence
    import app.api.v1 as api_v1

    database_url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(database_url, pool_pre_ping=True)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestSessionLocal)

    def unavailable_optional_extraction(*args, **kwargs):
        raise RuntimeError("Optional model extraction is deliberately disabled for the write-path benchmark")

    monkeypatch.setattr(api_v1, "extract_review_candidates", unavailable_optional_extraction)
    suffix = uuid.uuid4().hex
    sample_count = 20
    write_ms: list[float] = []
    replay_ms: list[float] = []
    daily_care_write_ms: list[float] = []
    daily_care_replay_ms: list[float] = []
    event_ids: list[str] = []
    daily_care_event_ids: list[str] = []

    try:
        with TestClient(app) as client:
            owner = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic Daily Capture benchmark owner"})
            other = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Synthetic Daily Capture benchmark other"})
            assert owner.status_code == 201, owner.text
            assert other.status_code == 201, other.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
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
                    "notes": f"Synthetic caregiver Daily Capture benchmark note {index}.",
                    "capture_idempotency_key": f"daily-capture-{suffix}-{index}",
                }
                started = time.perf_counter()
                saved = client.post("/api/v1/daily/log", headers=headers, json=payload)
                write_ms.append((time.perf_counter() - started) * 1000)
                assert saved.status_code == 200, saved.text
                receipt = saved.json()
                assert receipt["success"] is True
                assert receipt["idempotent_replay"] is False
                assert receipt["qwen_extraction_status"] == "unavailable"
                event_ids.append(receipt["id"])

                started = time.perf_counter()
                replay = client.post("/api/v1/daily/log", headers=headers, json=payload)
                replay_ms.append((time.perf_counter() - started) * 1000)
                assert replay.status_code == 200, replay.text
                assert replay.json()["id"] == receipt["id"]
                assert replay.json()["evidence_ids"] == receipt["evidence_ids"]
                assert replay.json()["idempotent_replay"] is True

                changed = client.post(
                    "/api/v1/daily/log",
                    headers=headers,
                    json={**payload, "notes": "Changed content under the same synthetic key."},
                )
                assert changed.status_code == 409, changed.text

            for index in range(sample_count):
                payload = {
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "medications": [],
                    "vitals": [],
                    "scales": [],
                    "notes": f"Synthetic Daily Care entry benchmark note {index}.",
                    "capture_idempotency_key": f"daily-care-entry-{suffix}-{index}",
                }
                started = time.perf_counter()
                saved = client.post("/api/v1/daily-care/entries", headers=headers, json=payload)
                daily_care_write_ms.append((time.perf_counter() - started) * 1000)
                assert saved.status_code == 200, saved.text
                receipt = saved.json()
                assert receipt["success"] is True
                assert receipt["idempotent_replay"] is False
                daily_care_event_ids.append(receipt["id"])

                started = time.perf_counter()
                replay = client.post("/api/v1/daily-care/entries", headers=headers, json=payload)
                daily_care_replay_ms.append((time.perf_counter() - started) * 1000)
                assert replay.status_code == 200, replay.text
                assert replay.json()["id"] == receipt["id"]
                assert replay.json()["idempotent_replay"] is True

                changed = client.post(
                    "/api/v1/daily-care/entries",
                    headers=headers,
                    json={**payload, "notes": "Changed Daily Care entry under the same key."},
                )
                assert changed.status_code == 409, changed.text

            denied = client.post("/api/v1/daily/log", headers=other_headers, json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Cross-person write attempt",
                "capture_idempotency_key": f"daily-capture-cross-person-{suffix}",
            })
            assert denied.status_code == 403, denied.text
            denied_daily_care = client.post("/api/v1/daily-care/entries", headers=other_headers, json={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": "Cross-person Daily Care write attempt",
                "capture_idempotency_key": f"daily-care-cross-person-{suffix}",
            })
            assert denied_daily_care.status_code == 403, denied_daily_care.text

            with TestSessionLocal() as db:
                events = db.query(CareEvent).filter(
                    CareEvent.person_id == person_id,
                    CareEvent.id.in_(event_ids),
                    CareEvent.event_type == "structured_daily_capture",
                ).all()
                assert len(events) == sample_count
                daily_care_events = db.query(CareEvent).filter(
                    CareEvent.person_id == person_id,
                    CareEvent.id.in_(daily_care_event_ids),
                    CareEvent.event_type == "daily_care_entry",
                ).all()
                assert len(daily_care_events) == sample_count
                evidence = db.query(Evidence).filter(Evidence.person_id == person_id).all()
                assert len(evidence) >= sample_count * 2

            print("POSTGRES_DAILY_CAPTURE_HTTP_BENCHMARK=" + json.dumps({
                "sample_count": sample_count,
                "daily_log_post_duration_ms": _percentiles(write_ms),
                "daily_log_idempotent_replay_post_duration_ms": _percentiles(replay_ms),
                "daily_care_entries_post_duration_ms": _percentiles(daily_care_write_ms),
                "daily_care_entries_idempotent_replay_post_duration_ms": _percentiles(daily_care_replay_ms),
                "changed_payload_key_reuse": "409",
                "cross_person_write": "403",
                "optional_model_extraction": "disabled_to_isolate_durable_write_path",
                "scope": "disposable_postgresql_ci_only",
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
