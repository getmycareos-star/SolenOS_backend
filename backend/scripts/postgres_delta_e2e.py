"""PostgreSQL/API integration test for source-grounded Delta reconciliation.

The model transport is deterministic and stubbed in-process; this validates the real
HTTP authorization, PostgreSQL persistence, provenance, open-loop creation, and
fail-closed parsing without requiring a paid model endpoint in CI.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from fastapi.testclient import TestClient

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing Delta E2E: DATABASE_URL must be PostgreSQL.")

os.environ.setdefault("OLLAMA_BASE_URL", "http://model.internal:11434")
os.environ.setdefault("OLLAMA_MODEL", "ci-fake-model")

from app.main import app
from app.core.database import SessionLocal
from app.models.care_continuity import CareDeltaRun, CareAssertion
from app.models.uncertainty import InformationGap, OpenQuestion
from app.services import delta_engine


VALID_DELTA = {
    "event_summary": "MRI was ordered; no result is present in the supplied event.",
    "medications": [],
    "conditions": [],
    "symptoms": [{
        "change_type": "new",
        "name": "fatigue",
        "previous": None,
        "current": "reported",
        "evidence_excerpt": "Reports fatigue",
        "certainty": "supported",
    }],
    "tests": [{
        "name": "MRI",
        "status": "ordered",
        "ordered_date": "2026-10-24",
        "result_present": False,
        "evidence_excerpt": "MRI ordered",
    }],
    "follow_ups": [{
        "action": "Book follow-up",
        "status": "pending",
        "due_date": None,
        "owner": None,
        "evidence_excerpt": "follow-up in two weeks",
    }],
    "contradictions": [],
    "clinical_gaps": [{
        "gap": "MRI result is not present",
        "why_it_matters": "The supplied event records an order, not a result.",
        "next_question": "Has the MRI been completed and reviewed?",
        "evidence_excerpt": "MRI ordered",
    }],
    "caregiver_summary": "MRI ordered; the result is not present in this supplied event. Follow-up remains open.",
    "clinician_summary": "MRI order reported; no result was present in the supplied event.",
}


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


class FakeModelClient:
    responses = []

    def __init__(self, timeout=60):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        if not self.responses:
            raise AssertionError("Unexpected Delta model call")
        return FakeResponse(self.responses.pop(0))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    client = TestClient(app)
    original_client_class = delta_engine.httpx.Client
    try:
        status = client.get("/ready")
        require(status.status_code == 200, f"PostgreSQL readiness failed: {status.status_code}")

        response = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Delta E2E person A", "caregiver_name": "Delta E2E caregiver A"},
        )
        require(response.status_code == 201, f"Delta owner session failed: {response.status_code} {response.text}")
        owner = response.json()
        token_a = owner["access_token"]
        person_a = owner["person"]["id"]
        caregiver_a = owner["caregiver"]["id"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        response = client.post(
            "/api/v1/sessions/anonymous",
            json={"name": "Delta E2E person B", "caregiver_name": "Delta E2E caregiver B"},
        )
        require(response.status_code == 201, f"Delta second session failed: {response.status_code} {response.text}")
        token_b = response.json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        FakeModelClient.responses = [json.dumps(VALID_DELTA), "not valid JSON"]
        delta_engine.httpx.Client = FakeModelClient

        response = client.post(
            "/api/v1/care-delta/reconcile",
            headers=headers_a,
            json={
                "person_id": person_a,
                "event_date": datetime.now(timezone.utc).isoformat(),
                "source": "CI visit note",
                "content": "Reports fatigue. MRI ordered. follow-up in two weeks.",
                "evidence_ids": [],
                "persist": True,
            },
        )
        require(response.status_code == 200, f"Delta reconcile failed: {response.status_code} {response.text}")
        result = response.json()
        run_id = result.get("provenance", {}).get("delta_run_id")
        require(run_id, "Delta response did not include a durable run ID")
        require(result["provenance"]["created_by_caregiver_id"] == caregiver_a, "Delta run caregiver attribution mismatch")
        require(result["provenance"]["persisted"] is True, "Delta result incorrectly reports non-persisted state")
        require(result["tests"][0]["result_present"] is False, "Missing result was incorrectly promoted to present")
        require(result["follow_ups"][0]["status"] == "pending", "Open follow-up was incorrectly marked complete")

        response = client.get(f"/api/v1/care-delta/runs/{person_a}", headers=headers_a)
        require(response.status_code == 200, f"Delta run list failed: {response.status_code} {response.text}")
        runs = response.json()
        require(any(row["id"] == run_id for row in runs), "Persisted Delta run not returned by scoped history")
        saved = next(row for row in runs if row["id"] == run_id)
        require(saved["created_by_caregiver_id"] == caregiver_a, "Persisted Delta attribution mismatch")
        require(saved["delta"]["provenance"]["persisted"] is True, "Persisted JSON does not match API result")

        db = SessionLocal()
        try:
            require(db.query(CareDeltaRun).filter_by(id=run_id, person_id=person_a).count() == 1, "PostgreSQL Delta run row missing")
            require(db.query(CareAssertion).filter_by(person_id=person_a).count() >= 1, "Delta assertions were not persisted")
            require(db.query(InformationGap).filter_by(person_id=person_a).count() >= 1, "Delta information gaps were not persisted")
            require(db.query(OpenQuestion).filter_by(person_id=person_a, status="open").count() >= 1, "Delta open questions were not persisted")
            before = db.query(CareDeltaRun).filter_by(person_id=person_a).count()
        finally:
            db.close()

        # Malformed model output must fail closed and must not create a second run.
        response = client.post(
            "/api/v1/care-delta/reconcile",
            headers=headers_a,
            json={
                "person_id": person_a,
                "source": "CI malformed model test",
                "content": "A second event with deliberately invalid model output.",
                "persist": True,
            },
        )
        require(response.status_code == 502, f"Malformed Delta output should return 502, got {response.status_code}: {response.text}")
        db = SessionLocal()
        try:
            after = db.query(CareDeltaRun).filter_by(person_id=person_a).count()
            require(after == before, f"Malformed model output persisted a Delta run: before={before}, after={after}")
        finally:
            db.close()

        response = client.get(f"/api/v1/care-delta/runs/{person_a}", headers=headers_b)
        require(response.status_code == 403, f"Cross-record Delta history access was not denied: {response.status_code}")
        response = client.post(
            "/api/v1/care-delta/reconcile",
            headers=headers_b,
            json={"person_id": person_a, "source": "foreign", "content": "Must not persist."},
        )
        require(response.status_code == 403, f"Cross-record Delta reconciliation was not denied: {response.status_code}")

        print("PASS: PostgreSQL Delta reconciliation persistence, caregiver provenance, open-loop creation,")
        print("      missing-result semantics, malformed-model fail-closed behavior, and cross-record isolation.")
    finally:
        delta_engine.httpx.Client = original_client_class
        client.close()


if __name__ == "__main__":
    main()
