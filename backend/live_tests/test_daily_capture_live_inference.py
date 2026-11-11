"""Real-Qwen Daily Capture acceptance against disposable PostgreSQL.

This is an end-to-end model + persistence test, separate from the DB-only write
latency benchmark. It does not stub Qwen, Delta reconciliation, or persistence.
"""
from __future__ import annotations

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
from app.models.care import CareEvent, Evidence
from app.models.care_continuity import CareAssertion
from app.models.capture_analysis import CaptureAnalysis

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(
    ("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://")
)
pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") != "1" or not POSTGRES_DATABASE,
    reason="requires the dedicated live-Qwen workflow and migrated PostgreSQL",
)


def test_real_qwen_daily_capture_persists_source_and_keeps_candidates_out_of_lcr():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    test_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_db():
        db = test_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    suffix = uuid.uuid4().hex
    note = (
        "At breakfast, the caregiver observed that Mary ate only a few bites, "
        "drank about half a cup of water, and woke several times overnight. "
        "This is a caregiver report, not a clinical assessment."
    )
    payload = {
        "medications": [],
        "vitals": [],
        "scales": [],
        "notes": note,
        "capture_idempotency_key": f"live-qwen-daily-capture-{suffix}",
    }

    try:
        with TestClient(app) as client:
            owner = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "Synthetic live-Qwen Daily Capture caregiver"},
            )
            assert owner.status_code == 201, owner.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            person_id = owner.json()["person"]["id"]
            caregiver_id = owner.json()["caregiver"]["id"]
            payload["person_id"] = person_id
            payload["caregiver_id"] = caregiver_id

            started = time.perf_counter()
            response = client.post("/api/v1/daily/log", headers=headers, json=payload)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            assert response.status_code == 200, response.text
            receipt = response.json()

            assert receipt["success"] is True
            assert receipt["qwen_extraction_status"] == "available", receipt
            assert receipt["qwen_analysis_id"]
            assert receipt["qwen_review_required"] is True
            assert receipt["qwen_written_to_lcr"] is False
            assert isinstance(receipt["qwen_candidates"], dict)
            assert set(receipt["qwen_candidates"]).issuperset({"observations", "open_questions"})
            assert isinstance(receipt["qwen_candidates"]["observations"], list)
            assert receipt["qwen_candidates"]["observations"], "real Qwen did not extract a caregiver-reported observation"
            assert receipt["evidence_ids"]

            with test_session_local() as db:
                event = db.query(CareEvent).filter(
                    CareEvent.id == receipt["id"],
                    CareEvent.person_id == person_id,
                    CareEvent.created_by_caregiver_id == caregiver_id,
                ).one()
                evidence = db.query(Evidence).filter(
                    Evidence.id == receipt["evidence_ids"][0],
                    Evidence.person_id == person_id,
                    Evidence.uploaded_by_caregiver_id == caregiver_id,
                ).one()
                analysis = db.query(CaptureAnalysis).filter(
                    CaptureAnalysis.id == receipt["qwen_analysis_id"],
                    CaptureAnalysis.person_id == person_id,
                    CaptureAnalysis.caregiver_id == caregiver_id,
                    CaptureAnalysis.source_event_id == event.id,
                    CaptureAnalysis.source_evidence_id == evidence.id,
                    CaptureAnalysis.extraction_status == "available",
                ).one()
                assert analysis.candidates == receipt["qwen_candidates"]
                assert note in evidence.source_text
                assert event.id == receipt["id"]
                assert db.query(CareAssertion).filter(
                    CareAssertion.person_id == person_id,
                    CareAssertion.assertion_status.in_(("accepted", "caregiver_reviewed")),
                ).count() == 0

            handoff = client.post(
                "/api/v1/handoff-drafts/prepare",
                headers=headers,
                json={"purpose": "family_transition", "audience": "family", "window_days": 30},
            )
            assert handoff.status_code == 200, handoff.text
            draft = handoff.json()
            assert draft.get("generation_status") == "generated", draft
            assert draft.get("validation_status") == "exact_quote_reference_validation_passed", draft
            assert draft.get("review_status") == "pending_review", draft
            assert draft.get("draft_id"), draft
            assert draft.get("requires_caregiver_review") is True, draft
            assert draft.get("creates_handoff") is False and draft.get("updates_lcr") is False, draft
            generated = draft.get("generation") or {}
            sections = generated.get("sections") or []
            assert sections, draft
            assert all(section.get("source_ref") and section.get("text") for section in sections), draft

            reloaded = client.get(
                f"/api/v1/handoff-drafts/{draft['draft_id']}",
                headers=headers,
            )
            assert reloaded.status_code == 200, reloaded.text
            reloaded_draft = reloaded.json()
            assert reloaded_draft.get("draft_id") == draft["draft_id"], reloaded_draft
            assert reloaded_draft.get("generation_status") == "generated", reloaded_draft
            assert reloaded_draft.get("review_status") == "pending_review", reloaded_draft
            assert reloaded_draft.get("revision_number") == 1, reloaded_draft
            assert reloaded_draft.get("creates_handoff") is False
            assert reloaded_draft.get("updates_lcr") is False

            print("LIVE_QWEN_DAILY_CAPTURE=" + json.dumps({
                "status": "PASS",
                "model_inference": "real_qwen",
                "database": "disposable_postgresql",
                "capture_http_duration_ms": elapsed_ms,
                "source_event_persisted": True,
                "caregiver_evidence_persisted": True,
                "review_analysis_persisted": True,
                "qwen_candidates_written_to_lcr": False,
                "care_assertions_created_without_review": 0,\n                "real_qwen_handoff_generated_and_reloaded": True,
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
