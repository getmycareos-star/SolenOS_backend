"""Authenticated Daily Capture HTTP write benchmark on disposable PostgreSQL.

Only server-issued CI fixture sessions and synthetic notes are used. Optional
model extraction is deliberately disabled so this measures persistence rather
than inference.
"""
import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

DATABASE_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")),
    reason="requires disposable PostgreSQL DATABASE_URL",
)


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)

    return {"p50": percentile(0.50), "p95": percentile(0.95), "p99": percentile(0.99)}


def test_daily_capture_authenticated_http_write_latency_idempotency_and_scope(monkeypatch, capsys):
    from app.api import v1 as api_v1

    # The route must durably save the caregiver event even when optional model
    # extraction is unavailable. Do not make live Qwen calls in this benchmark.
    def unavailable_extraction(*args, **kwargs):
        raise RuntimeError("CI benchmark intentionally disables optional model extraction")

    monkeypatch.setattr(api_v1, "extract_review_candidates", unavailable_extraction)
    suffix = uuid.uuid4().hex
    sample_count = 20
    write_ms: list[float] = []
    saved_ids: list[str] = []

    with TestClient(app) as client:
        owner = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Daily Capture PostgreSQL fixture"})
        other = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": "Other Daily Capture fixture"})
        assert owner.status_code == 201, owner.text
        assert other.status_code == 201, other.text

        owner_session = owner.json()
        other_session = other.json()
        owner_headers = {"Authorization": "Bearer " + owner_session["access_token"]}
        other_headers = {"Authorization": "Bearer " + other_session["access_token"]}
        person_id = owner_session["person"]["id"]
        caregiver_id = owner_session["caregiver"]["id"]

        payloads = []
        for index in range(sample_count):
            payload = {
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "capture_idempotency_key": f"daily-capture-{suffix}-{index}",
                "medications": [],
                "vitals": [],
                "scales": [],
                "notes": f"Synthetic PostgreSQL benchmark note {index}; no real care data.",
            }
            payloads.append(payload)
            started = time.perf_counter()
            response = client.post("/api/v1/daily/log", headers=owner_headers, json=payload)
            write_ms.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200, response.text
            receipt = response.json()
            assert receipt["success"] is True
            assert receipt["event_type"] == "structured_daily_capture"
            assert receipt["qwen_written_to_lcr"] is False
            saved_ids.append(receipt["id"])

        # A retry with the same key and same body must return the original event.
        replay = client.post("/api/v1/daily/log", headers=owner_headers, json=payloads[0])
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == saved_ids[0]
        assert replay.json()["idempotent_replay"] is True

        # A reused key with different content must fail rather than create or overwrite.
        changed_payload = {**payloads[0], "notes": "Different content under the same idempotency key."}
        changed = client.post("/api/v1/daily/log", headers=owner_headers, json=changed_payload)
        assert changed.status_code == 409, changed.text

        # The bearer session cannot write into another person's care record.
        cross_person = client.post("/api/v1/daily/log", headers=other_headers, json=payloads[0])
        assert cross_person.status_code == 403, cross_person.text

        # Verify the durable event count and person-scoped readback.
        events = client.get(f"/api/v1/events/{person_id}", headers=owner_headers)
        assert events.status_code == 200, events.text
        returned_ids = {item["id"] for item in events.json()}
        assert set(saved_ids).issubset(returned_ids)

    report = {
        "event": "daily_capture_postgres_http_write_benchmark",
        "sample_count": sample_count,
        "write_http_duration_ms": _percentiles(write_ms),
        "idempotent_replay": "passed",
        "changed_payload_conflict": "passed",
        "cross_person_denial": "passed",
        "scope": "disposable_postgresql_ci_only",
    }
    print("DAILY_CAPTURE_POSTGRES_BENCHMARK=" + json.dumps(report, separators=(",", ":")))
