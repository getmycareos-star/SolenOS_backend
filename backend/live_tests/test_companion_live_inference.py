"""Real Qwen inference through authenticated Companion and PostgreSQL.

The fixture is created through the public Daily Care workflow. The database is
created exclusively by the repository's Alembic migration chain.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal as TestingSessionLocal
from app.models.care import Caregiver, CareEvent, Evidence
from app.models.caregiver_intelligence import CaregiverHandoff, CaregiverProfile
from app.core.caregiver_enums import HandoffAcceptanceStatus
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion


pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") != "1",
    reason="requires the dedicated live-Qwen workflow and a running Ollama model",
)


def _create_session(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": label},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _record_daily_care(client: TestClient, session: dict, detail: str) -> str:
    response = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "capture_idempotency_key": str(uuid.uuid4()),
            "observation": detail,
            "source": "live_qwen_postgres_integration_test",
        },
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json().get("id")
    assert event_id, response.text
    return event_id


def _ask(client: TestClient, session: dict, question: str, session_id: str | None = None):
    payload = {"person_id": session["person"]["id"], "text": question}
    if session_id:
        payload["session_id"] = session_id
    return client.post(
        "/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json=payload,
    )


def _get_json_after_restart(base_url: str, path: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = body
        raise AssertionError(
            f"Restarted server denied authenticated {path}: HTTP {exc.code} {detail}"
        ) from exc


def _verify_persistence_after_real_server_restart(session: dict, other: dict, conversation_id: str, proposal_id: str, assertion_id: str, disclosure: str) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", "8013",
        ],
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base_url = "http://127.0.0.1:8013"
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise AssertionError(f"Uvicorn exited during restart persistence check: {process.returncode}")
            try:
                with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError("Fresh Uvicorn process did not become healthy against the migrated PostgreSQL database")

        token = session["access_token"]
        status, history = _get_json_after_restart(base_url, f"/api/v1/conversation/{conversation_id}", token)
        assert status == 200, history
        assert any(item.get("role") == "user" and item.get("text") == disclosure for item in history.get("messages", [])), history
        status, candidate_history = _get_json_after_restart(
            base_url, f"/api/v1/companion/care-updates/{proposal_id}/history", token
        )
        assert status == 200, candidate_history
        persisted = candidate_history.get("proposal", {})
        assert persisted.get("id") == proposal_id and persisted.get("status") == "accepted", candidate_history
        assert candidate_history.get("items"), candidate_history
        status, lcr = _get_json_after_restart(base_url, "/api/v1/care-continuity/current-care", token)
        assert status == 200, lcr
        assert any(item.get("id") == assertion_id for item in lcr.get("items", [])), lcr

        denied = urllib.request.Request(
            base_url + f"/api/v1/conversation/{conversation_id}",
            headers={"Authorization": f"Bearer {other['access_token']}"},
            method="GET",
        )
        try:
            urllib.request.urlopen(denied, timeout=10)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("A different caregiver retrieved the persisted Companion conversation after restart")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_live_qwen_companion_uses_authenticated_postgres_lcr_and_preserves_identity():
    """Genuine configured Qwen must answer from real workflow-created care evidence."""
    with TestClient(app) as client:
        caregiver = _create_session(client, "Live Qwen PostgreSQL evaluator")
        other = _create_session(client, "Independent identity-isolation evaluator")
        event_id = _record_daily_care(
            client,
            caregiver,
            "Caregiver reported waking several times overnight and restless sleep.",
        )
        auth = {"Authorization": f"Bearer {caregiver['access_token']}"}

        # Exercise the real Qwen Daily Care path against the event just persisted
        # through the public Daily Care API. The answer remains read-only.
        daily_qwen = client.post(
            "/api/v1/daily-care/intelligence/ask",
            headers=auth,
            json={"question": "What did the caregiver report about sleep?", "history_query": "sleep"},
        )
        assert daily_qwen.status_code == 200, daily_qwen.text
        daily_qwen_body = daily_qwen.json()
        assert daily_qwen_body["status"] == "completed", daily_qwen_body
        assert any(row.get("source_id") == event_id for row in daily_qwen_body.get("source_records", [])), daily_qwen_body
        assert daily_qwen_body["written_to_lcr"] is False, daily_qwen_body

        direct = _ask(client, caregiver, "What did we record about sleep?")
        assert direct.status_code == 200, direct.text
        answer = direct.json()
        assert answer.get("ai_generated") is True, answer
        assert event_id in answer.get("source_event_ids", []), answer
        assert any(
            term in answer.get("reply", "").casefold()
            for term in ("sleep", "waking", "awake", "restless", "overnight")
        ), answer
        assert answer.get("written_to_lcr") is False, answer
        assert answer.get("persistence") == "conversation_only", answer

        follow_up = _ask(
            client,
            caregiver,
            "What about that?",
            answer.get("session_id"),
        )
        assert follow_up.status_code == 200, follow_up.text
        follow_up_data = follow_up.json()
        assert event_id in follow_up_data.get("source_event_ids", []), follow_up_data
        assert follow_up_data.get("written_to_lcr") is False, follow_up_data

        missing = _ask(
            client,
            caregiver,
            "What exact blood pressure was recorded last Tuesday?",
            answer.get("session_id"),
        )
        assert missing.status_code == 200, missing.text
        missing_data = missing.json()
        missing_reply = missing_data.get("reply", "").casefold()
        assert any(
            marker in missing_reply
            for marker in (
                "not recorded", "not in the record", "not documented",
                "not available", "cannot establish", "can't establish",
                "no blood pressure", "not specified", "not enough information",
            )
        ), missing_data
        assert missing_data.get("written_to_lcr") is False, missing_data

        # Real inference must create a reviewable proposal; only explicit caregiver
        # approval may persist it to the PostgreSQL Living Care Record.
        new_observation = (
            "I want to add this to the care record: this morning Mum left most "
            "of her breakfast untouched and said she felt nauseated."
        )
        capture = _ask(client, caregiver, new_observation)
        assert capture.status_code == 200, capture.text
        assert capture.json().get("ai_generated") is True, capture.json()
        assert capture.json().get("written_to_lcr") is False, capture.json()

        auth = {"Authorization": f"Bearer {caregiver['access_token']}"}
        proposals_response = client.get("/api/v1/companion/care-updates", headers=auth)
        assert proposals_response.status_code == 200, proposals_response.text
        proposals = proposals_response.json().get("items", [])
        proposal = next(
            (
                item for item in proposals
                if item.get("proposed_change", {}).get("source_quote")
                and item["proposed_change"]["source_quote"] in new_observation
            ),
            None,
        )
        assert proposal is not None, proposals_response.json()
        assert proposal["status"] == "pending_authorization", proposal

        before_approval = client.get("/api/v1/care-continuity/current-care", headers=auth)
        assert before_approval.status_code == 200, before_approval.text
        assert proposal["proposed_change"]["source_quote"] not in json.dumps(
            before_approval.json().get("items", [])
        ), before_approval.json()

        approval = client.post(
            f"/api/v1/companion/care-updates/{proposal['id']}/review",
            headers=auth,
            json={"decision": "accept"},
        )
        assert approval.status_code == 200, approval.text
        assertion_id = approval.json().get("assertion_id")
        assert assertion_id, approval.json()

        after_approval = client.get("/api/v1/care-continuity/current-care", headers=auth)
        assert after_approval.status_code == 200, after_approval.text
        assert any(item.get("id") == assertion_id for item in after_approval.json().get("items", [])), after_approval.json()

        recalled = _ask(
            client,
            caregiver,
            "What did I tell you about breakfast this morning?",
            capture.json().get("session_id"),
        )
        assert recalled.status_code == 200, recalled.text
        recalled_data = recalled.json()
        recalled_text = recalled_data.get("reply", "").casefold()
        assert any(term in recalled_text for term in ("breakfast", "nauseated", "untouched", "left most")), recalled_data
        assert recalled_data.get("written_to_lcr") is False, recalled_data

        isolated = _ask(client, other, "What did we record about sleep?")
        assert isolated.status_code == 200, isolated.text
        isolated_data = isolated.json()
        assert event_id not in isolated_data.get("source_event_ids", []), isolated_data
        assert isolated_data.get("written_to_lcr") is False, isolated_data

        # Close the in-process client, start a fresh Uvicorn process against the
        # same migrated PostgreSQL database, and prove conversation, accepted
        # candidate and LCR assertion survive a real application restart.
        conversation_id = capture.json().get("session_id")
        assert conversation_id
        client.close()
        _verify_persistence_after_real_server_restart(
            caregiver,
            other,
            conversation_id,
            proposal["id"],
            assertion_id,
            new_observation,
        )


def test_live_qwen_support_reads_shared_lcr_without_peer_document_leakage():
    """The actual Qwen support route must use canonical LCR context and stay read-only."""
    with TestClient(app) as client:
        caregiver = _create_session(client, "Live Qwen shared-LCR caregiver")
        person_id = caregiver["person"]["id"]
        caregiver_id = caregiver["caregiver"]["id"]
        peer_caregiver_id = str(uuid.uuid4())
        peer_evidence_id = str(uuid.uuid4())
        peer_analysis_id = str(uuid.uuid4())
        assertion_id = str(uuid.uuid4())

        db = TestingSessionLocal()
        try:
            db.add(Caregiver(
                id=peer_caregiver_id,
                name="Other caregiver on same care record",
                relationship="family",
                person_id=person_id,
            ))
            db.add(Evidence(
                id=peer_evidence_id,
                person_id=person_id,
                type="document",
                source_text="PRIVATE_PEER_DOCUMENT_TEXT: confidential note uploaded by another caregiver.",
                uploaded_by_caregiver_id=peer_caregiver_id,
                evidence_status="available",
                source_type="document",
            ))
            db.add(CaptureAnalysis(
                id=peer_analysis_id,
                person_id=person_id,
                caregiver_id=peer_caregiver_id,
                source_evidence_id=peer_evidence_id,
                source_text="PRIVATE_PEER_DOCUMENT_TEXT: confidential extracted document text.",
                extraction_status="available",
                candidates={"kind": "document_extraction_v1", "filename": "peer-note.pdf"},
                review_history=[],
            ))
            db.add(CareAssertion(
                id=assertion_id,
                person_id=person_id,
                subject_type="person",
                subject_id=person_id,
                field="appetite",
                assertion_kind="caregiver_reviewed_daily_care_observation",
                asserted_value="Breakfast intake was lower than usual",
                source_label="Caregiver review",
                source_text="Caregiver reviewed the breakfast observation.",
                evidence_ids=[],
                assertion_status="accepted",
                created_by_caregiver_id=caregiver_id,
            ))
            db.commit()
            assertion_count_before = db.query(CareAssertion).filter_by(person_id=person_id).count()
        finally:
            db.close()

        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {caregiver['access_token']}"},
            json={
                "workflow": "living_care_record",
                "text": "What is recorded about breakfast intake, and is any cause documented?",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("ai_generated") is True, body
        assert body.get("model") == "qwen3.5:4b", body
        assert body.get("authority") == "solenos_care_record_and_caregiver", body
        assert body.get("review_required") is True, body
        assert body.get("written_to_lcr") is False, body
        assert body.get("persistence") == "read_only_assistance", body
        assert body.get("provider_status") == "available", body
        reply = body.get("reply", "").casefold()
        assert "breakfast" in reply and any(term in reply for term in ("lower", "reduced", "less")), body
        assert "private_peer_document_text" not in reply, body
        assert "peer-note.pdf" not in reply, body
        assert body.get("source_document_analysis_ids") == [], body
        assert body.get("source_document_status") == "empty", body
        assert peer_evidence_id not in body.get("source_evidence_ids", []), body

        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == assertion_count_before
            assert db.query(CareAssertion).filter_by(id=assertion_id).count() == 1
        finally:
            db.close()


def test_live_qwen_reconstructs_three_month_sleep_history_from_paraphrased_events():
    """Three-month journey answer must retrieve and cite all relevant dated events."""
    with TestClient(app) as client:
        session = _create_session(client, "Three-month care journey evaluator")
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        now = datetime.now(timezone.utc)
        events = [
            (
                "journey-sleep-early",
                82,
                "Night-time restlessness",
                "Caregiver reported that she remained awake for long stretches and repeatedly got out of bed.",
            ),
            (
                "journey-sleep-middle",
                43,
                "Repeated overnight waking",
                "Caregiver recorded several episodes of waking during the night and difficulty settling again.",
            ),
            (
                "journey-sleep-recent",
                6,
                "Rest pattern update",
                "Caregiver reported a quieter night with fewer interruptions than previously described.",
            ),
        ]
        db = TestingSessionLocal()
        try:
            for event_id, days_ago, title, description in events:
                db.add(CareEvent(
                    id=event_id,
                    person_id=person_id,
                    event_type="daily_observation",
                    status="recorded",
                    occurred_at=now - timedelta(days=days_ago),
                    title=title,
                    description=description,
                    created_by_caregiver_id=caregiver_id,
                    time_provenance="caregiver_reported",
                ))
            db.add(Caregiver(
                id="journey-caregiver-b",
                person_id=person_id,
                name="Second caregiver",
                relationship="family",
            ))
            db.flush()
            db.add_all([
                CaregiverProfile(
                    id="journey-profile-a",
                    person_id=person_id,
                    session_caregiver_id=caregiver_id,
                    name="First caregiver",
                    relationship="family",
                ),
                CaregiverProfile(
                    id="journey-profile-b",
                    person_id=person_id,
                    session_caregiver_id="journey-caregiver-b",
                    name="Second caregiver",
                    relationship="family",
                ),
            ])
            db.flush()
            db.add(CaregiverHandoff(
                id="journey-sleep-handoff",
                person_id=person_id,
                from_caregiver_id="journey-profile-a",
                to_caregiver_id="journey-profile-b",
                responsibility_type="Sleep observations",
                scope="Overnight waking history",
                timestamp=now - timedelta(days=30),
                effective_time=now - timedelta(days=30),
                context="First caregiver handed over repeated overnight waking observations to the second caregiver.",
                acceptance_status=HandoffAcceptanceStatus.ACCEPTED,
                acceptance_evidence="Second caregiver accepted the handoff.",
                is_completed=True,
            ))
            db.commit()
        finally:
            db.close()

        response = _ask(
            client,
            session,
            "Give me the history of her sleep and explain what changed over the last three months.",
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result.get("ai_generated") is True, result
        assert result.get("written_to_lcr") is False, result
        cited = set(result.get("source_event_ids", []))
        expected = {event[0] for event in events}
        source_refs = {
            (item.get("record_type"), item.get("record_id"))
            for item in result.get("source_record_refs", [])
        }
        selected_event_refs = {record_id for record_type, record_id in source_refs if record_type == "events"}
        assert selected_event_refs == expected, source_refs
        assert cited == expected, {
            "expected_all_three_month_events": sorted(expected),
            "cited_event_ids": sorted(cited),
            "response": result,
        }
        coverage = result.get("lcr_context_coverage") or {}
        assert coverage.get("full_source_scan_requested") is True, coverage
        assert coverage.get("history_coverage") == "complete_history_scan_ranked_subset", coverage
        event_coverage = coverage.get("sources", {}).get("events", {})
        assert event_coverage.get("candidate_scan_complete") is True, coverage
        assert event_coverage.get("candidate_count", 0) >= 3, coverage
        assert event_coverage.get("selection_truncated") is False, coverage
        assert coverage.get("prompt_context_complete") is True, coverage

        handoff_response = _ask(
            client,
            session,
            "Give me the history of sleep handoffs over the last three months.",
        )
        assert handoff_response.status_code == 200, handoff_response.text
        handoff_refs = {
            (item.get("record_type"), item.get("record_id"))
            for item in handoff_response.json().get("source_record_refs", [])
        }
        assert ("handoffs", "journey-sleep-handoff") in handoff_refs, handoff_refs


def test_live_qwen_companion_retrieves_a_paraphrased_event_from_the_second_history_page():
    """A six-month-old paraphrased event must survive the 50-record page boundary."""
    client = TestClient(app)
    session = _create_session(client, "Six-month pagination caregiver")
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]
    now = datetime.now(timezone.utc)
    oldest_event_id = "sleep-six-month-oldest"
    db = TestingSessionLocal()
    try:
        db.add_all([
            CareEvent(
                id=oldest_event_id if index == 50 else f"sleep-six-month-{index:03d}",
                person_id=person_id,
                event_type="daily_observation",
                status="recorded",
                occurred_at=now - timedelta(days=index * 3),
                recorded_at=now,
                created_at=now,
                title=f"Sleep note {index}",
                description=(
                    "Caregiver reported remaining awake for several hours overnight."
                    if index == 50
                    else f"Caregiver recorded a sleep observation {index}."
                ),
                created_by_caregiver_id=caregiver_id,
            )
            for index in range(51)
        ])
        db.commit()
    finally:
        db.close()

    response = _ask(
        client,
        session,
        "Give me the history of sleep over the last six months, including older changes.",
    )
    assert response.status_code == 200, response.text
    result = response.json()
    coverage = result.get("lcr_context_coverage", {})
    event_coverage = coverage.get("sources", {}).get("events", {})
    assert coverage.get("pages_fetched") == 2, coverage
    assert coverage.get("page_limit_reached") is False, coverage
    assert event_coverage.get("candidate_count") == 51, event_coverage
    assert event_coverage.get("returned_count") == 51, event_coverage
    assert event_coverage.get("has_more") is False, event_coverage
    assert oldest_event_id in set(result.get("source_event_ids", [])), result.get("source_event_ids")
    assert any(
        item.get("record_id") == oldest_event_id and item.get("record_type") == "events"
        for item in result.get("source_record_refs", [])
    ), result.get("source_record_refs")
