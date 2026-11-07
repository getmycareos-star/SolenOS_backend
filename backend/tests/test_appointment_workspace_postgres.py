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
