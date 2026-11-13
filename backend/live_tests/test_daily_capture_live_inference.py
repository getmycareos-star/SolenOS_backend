"""Real-Qwen Daily Capture acceptance against disposable PostgreSQL.

This is an end-to-end model + persistence test, separate from the DB-only write
latency benchmark. It does not stub Qwen, Delta reconciliation, or persistence.
"""
from __future__ import annotations

import json
import os
import re
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
                "care_assertions_created_without_review": 0,
                "real_qwen_handoff_generated_and_reloaded": True,
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_real_qwen_natural_language_daily_care_preserves_all_clauses_and_companion_retrieves_them():
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
        "Mum slept poorly last night and seemed more unsettled this morning. "
        "She ate a little more at dinner and is now resting."
    )

    try:
        with TestClient(app) as client:
            owner = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "Synthetic natural-language Daily Care caregiver"},
            )
            assert owner.status_code == 201, owner.text
            headers = {"Authorization": "Bearer " + owner.json()["access_token"]}
            person_id = owner.json()["person"]["id"]
            caregiver_id = owner.json()["caregiver"]["id"]

            receipt = client.post(
                "/api/v1/daily-care/entries",
                headers=headers,
                json={
                    "person_id": person_id,
                    "caregiver_id": caregiver_id,
                    "capture_idempotency_key": f"live-natural-daily-care-{suffix}",
                    "capture_kind": "natural_language_update",
                    "notes": note,
                },
            )
            assert receipt.status_code == 200, receipt.text
            saved = receipt.json()
            assert saved["success"] is True
            assert saved["id"]
            assert saved["qwen_job_id"]

            deadline = time.monotonic() + 300
            latest_job = None
            while time.monotonic() < deadline:
                job_response = client.get(
                    f"/api/v1/assistant/jobs/{saved['qwen_job_id']}",
                    headers=headers,
                )
                assert job_response.status_code == 200, job_response.text
                latest_job = job_response.json()
                if latest_job["status"] == "completed":
                    break
                assert latest_job["status"] not in {"failed", "cancelled"}, latest_job
                time.sleep(1.5)
            assert latest_job and latest_job["status"] == "completed", latest_job
            capture_result = latest_job.get("daily_capture") or {}
            assert capture_result.get("projection_status") == "verified", latest_job
            assert capture_result.get("reconciliation_status") == "completed", latest_job
            assert int(capture_result.get("source_grounded_observations_projected") or 0) >= 4, latest_job

            lcr = client.get(
                "/api/v1/care-continuity/current-care?limit_per_source=50",
                headers=headers,
            )
            assert lcr.status_code == 200, lcr.text
            event_row = next(
                (item for item in lcr.json().get("items", []) if item.get("id") == saved["id"]),
                None,
            )
            assert event_row is not None, "saved natural-language Daily Care entry is missing from LCR"
            assert event_row.get("source_type") == "caregiver_report"
            assert event_row.get("evidence_ids") == saved.get("evidence_ids")
            observations = event_row.get("derived_observations") or []
            expected = {
                "poor_sleep": re.compile(r"slept poorly", re.IGNORECASE),
                "unsettled": re.compile(r"unsettled", re.IGNORECASE),
                "dinner": re.compile(r"ate a little more at dinner", re.IGNORECASE),
                "resting": re.compile(r"now resting", re.IGNORECASE),
            }
            matched = {
                name: next((item for item in observations if pattern.search(item.get("value_text", ""))), None)
                for name, pattern in expected.items()
            }
            assert all(matched.values()), {
                "missing": [name for name, item in matched.items() if item is None],
                "observations": observations,
            }
            for name, item in matched.items():
                assert item.get("source_passage") in note
                assert note[item["source_span_start"]:item["source_span_end"]] == item["source_passage"]
                assert item.get("value_text") in note
            assert matched["unsettled"].get("epistemic_status") == "uncertain"
            assert matched["unsettled"].get("hedge_marker") == "seemed"

            companion = client.post(
                "/api/v1/conversation/message",
                headers=headers,
                json={
                    "person_id": person_id,
                    "text": "What has the caregiver recorded about sleep, morning changes, dinner and resting?",
                    "client_turn_id": f"live-natural-companion-{suffix}",
                },
            )
            assert companion.status_code == 200, companion.text
            answer = companion.json()
            assert answer.get("ai_generated") is True, answer
            assert saved["id"] in (answer.get("source_event_ids") or []), answer
            assert saved["evidence_ids"][0] in (answer.get("source_evidence_ids") or []), answer
            reply = (answer.get("reply") or "").casefold()
            assert re.search(r"sleep|slept", reply), answer
            assert re.search(r"unsettled|restless", reply), answer
            assert "dinner" in reply or "ate" in reply, answer
            assert "rest" in reply, answer

            print("LIVE_QWEN_NATURAL_DAILY_CARE=" + json.dumps({
                "status": "PASS",
                "model_inference": "real_qwen",
                "database": "disposable_postgresql",
                "source_grounded_observations_projected": capture_result.get("source_grounded_observations_projected"),
                "four_independent_source_clauses_persisted": True,
                "uncertain_hedge_preserved": True,
                "companion_retrieved_exact_event_and_evidence": True,
            }, separators=(",", ":")))
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
