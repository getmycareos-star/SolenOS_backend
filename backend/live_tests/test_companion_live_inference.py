"""Real Qwen inference through authenticated Companion and PostgreSQL.

The fixture is created through the public Daily Care workflow. The database is
created exclusively by the repository's Alembic migration chain.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
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

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"))
if os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") == "1" and not POSTGRES_DATABASE:
    raise RuntimeError("Live Companion Qwen acceptance requires PostgreSQL; refusing a skipped non-PostgreSQL run.")
from app.models.care import Caregiver, CareEvent, Evidence
from app.models.caregiver_intelligence import CaregiverHandoff, CaregiverProfile
from app.core.caregiver_enums import HandoffAcceptanceStatus
from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.uncertainty import Contradiction
from app.services.care_continuity import next_best_questions


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



def test_live_qwen_lcr_orientation_distinguishes_new_returning_and_unknown_history_without_writes(monkeypatch):
    """Real Qwen must orient from persisted LCR state and leave both records unchanged."""
    with TestClient(app) as client:
        new_caregiver = _create_session(client, "Live Qwen new caregiver orientation")
        returning_caregiver = _create_session(client, "Live Qwen returning caregiver orientation")
        event_id = _record_daily_care(
            client,
            returning_caregiver,
            "Caregiver reported that Mum woke repeatedly overnight and needed help settling again.",
        )

        db = TestingSessionLocal()
        try:
            new_event_count = db.query(CareEvent).filter_by(
                person_id=new_caregiver["person"]["id"]
            ).count()
            returning_event_count = db.query(CareEvent).filter_by(
                person_id=returning_caregiver["person"]["id"]
            ).count()

            new_conversation_count = db.query(ConversationSession).filter_by(
                person_id=new_caregiver["person"]["id"],
                caregiver_id=new_caregiver["caregiver"]["id"],
            ).count()
            returning_conversation_count = db.query(ConversationSession).filter_by(
                person_id=returning_caregiver["person"]["id"],
                caregiver_id=returning_caregiver["caregiver"]["id"],
            ).count()
            new_message_count = (
                db.query(ConversationMessage)
                .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .filter(ConversationSession.person_id == new_caregiver["person"]["id"])
                .count()
            )
            returning_message_count = (
                db.query(ConversationMessage)
                .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .filter(ConversationSession.person_id == returning_caregiver["person"]["id"])
                .count()
            )
        finally:
            db.close()

        new_response = client.post(
            "/api/lcr/orientation",
            headers={"Authorization": f"Bearer {new_caregiver['access_token']}"},
            json={"raw_input": "Good morning, SolenOS!"},
        )
        assert new_response.status_code == 200, new_response.text
        new_body = new_response.json()
        assert new_body.get("ai_generated") is True, new_body
        assert new_body.get("provider_status") == "available", new_body
        assert new_body.get("written_to_lcr") is False, new_body
        new_reply = new_body.get("reply", "")
        assert 1 <= len(new_reply.split()) <= 40, new_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", new_reply.strip()) if part]) <= 2, new_reply
        assert not re.search(
            r"\b(?:welcome back|your previous updates|as we discussed|i remember everything)\b",
            new_reply,
            re.IGNORECASE,
        ), new_reply
        assert not re.search(r"\bhow can i help\b", new_reply, re.IGNORECASE), new_reply
        assert re.search(r"\bsolenos\b", new_reply, re.IGNORECASE), new_reply
        assert re.search(r"\b(?:care story|care record|care history)\b", new_reply, re.IGNORECASE), new_reply
        assert re.search(r"\b(?:start|starting|begin|beginning|today|welcome)\b", new_reply, re.IGNORECASE), new_reply
        assert len(new_reply.split()) >= 8, new_reply

        returning_response = client.post(
            "/api/lcr/orientation",
            headers={"Authorization": f"Bearer {returning_caregiver['access_token']}"},
            json={"raw_input": "Hi"},
        )
        assert returning_response.status_code == 200, returning_response.text
        returning_body = returning_response.json()
        assert returning_body.get("ai_generated") is True, returning_body
        assert returning_body.get("provider_status") == "available", returning_body
        assert returning_body.get("written_to_lcr") is False, returning_body
        returning_reply = returning_body.get("reply", "")
        assert 1 <= len(returning_reply.split()) <= 40, returning_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", returning_reply.strip()) if part]) <= 2, returning_reply
        assert not re.search(
            r"\b(?:first time here|new caregiver|starting from scratch)\b",
            returning_reply,
            re.IGNORECASE,
        ), returning_reply
        assert re.search(r"\bsolenos\b", returning_reply, re.IGNORECASE), returning_reply
        assert re.search(r"\b(?:care story|care record|care history)\b", returning_reply, re.IGNORECASE), returning_reply
        assert re.search(r"\b(?:welcome back|already underway|continue|continuing|pick up|ongoing)\b", returning_reply, re.IGNORECASE), returning_reply
        assert len(returning_reply.split()) >= 8, returning_reply

        from app import main as main_module

        def assert_orientation(response, expected_stage):
            assert response.status_code == 200, response.text
            body = response.json()
            assert body.get("ai_generated") is True, body
            assert body.get("provider_status") == "available", body
            assert body.get("written_to_lcr") is False, body
            assert body.get("care_record_unchanged") is True, body
            assert body.get("companion_history_unchanged") is True, body
            reply = body.get("reply", "")
            assert 8 <= len(reply.split()) <= 40, reply
            assert reply.count("?") <= 1, reply
            assert not re.search(r"(?i)\bhow can i help\b", reply), reply
            assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", reply), reply
            assert len([part for part in re.split(r"(?<=[.!?])\s+", reply.strip()) if part]) <= 2, reply
            assert re.search(r"\bsolenos\b", reply, re.IGNORECASE), reply
            assert re.search(r"\b(?:care story|care record|care history)\b", reply, re.IGNORECASE), reply
            if expected_stage == "new":
                assert re.search(r"\b(?:start|starting|begin|beginning|today|welcome)\b", reply, re.IGNORECASE), reply
                assert not re.search(r"\b(?:welcome back|already underway|your previous updates)\b", reply, re.IGNORECASE), reply
            elif expected_stage == "returning":
                assert re.search(r"\b(?:welcome back|already underway|continue|continuing|pick up|ongoing)\b", reply, re.IGNORECASE), reply
                assert not re.search(r"\b(?:first time here|new caregiver|starting from scratch)\b", reply, re.IGNORECASE), reply
            else:
                assert not re.search(
                    r"\b(?:welcome back|already underway|as we discussed|your previous updates|last time we|"
                    r"we talked about|first time here|new caregiver|starting from scratch)\b",
                    reply,
                    re.IGNORECASE,
                ), reply

        # Repeat known-history cases against the real persisted LCR. The unknown
        # retrieval override is deliberately scoped below and cannot contaminate
        # these new/returning trials.
        for trial_session, greeting, expected_stage in (
            (new_caregiver, "Good evening", "new"),
            (returning_caregiver, "Hello again", "returning"),
        ):
            repeated = client.post(
                "/api/lcr/orientation",
                headers={"Authorization": f"Bearer {trial_session['access_token']}"},
                json={"raw_input": greeting},
            )
            assert_orientation(repeated, expected_stage)

        # Missing required history must remain unknown on two independent calls.
        with monkeypatch.context() as scoped_patch:
            scoped_patch.setattr(
                main_module,
                "build_lcr_context",
                lambda *args, **kwargs: {"sources": {}, "missing_required_sources": ["events"]},
            )
            scoped_patch.setattr(
                main_module,
                "format_lcr_context_for_prompt",
                lambda context: "Some required care history sources are unavailable.",
            )
            unknown_response = client.post(
                "/api/lcr/orientation",
                headers={"Authorization": f"Bearer {new_caregiver['access_token']}"},
                json={"raw_input": "Hello"},
            )
            assert_orientation(unknown_response, "unknown")
            repeated_unknown = client.post(
                "/api/lcr/orientation",
                headers={"Authorization": f"Bearer {new_caregiver['access_token']}"},
                json={"raw_input": "Hello again"},
            )
            assert_orientation(repeated_unknown, "unknown")

        db = TestingSessionLocal()
        try:
            assert db.query(CareEvent).filter_by(
                person_id=new_caregiver["person"]["id"]
            ).count() == new_event_count
            assert db.query(CareEvent).filter_by(
                person_id=returning_caregiver["person"]["id"]
            ).count() == returning_event_count
            assert db.query(CareEvent).filter_by(
                id=event_id,
                person_id=returning_caregiver["person"]["id"],
            ).count() == 1

            assert db.query(ConversationSession).filter_by(
                person_id=new_caregiver["person"]["id"],
                caregiver_id=new_caregiver["caregiver"]["id"],
            ).count() == new_conversation_count
            assert db.query(ConversationSession).filter_by(
                person_id=returning_caregiver["person"]["id"],
                caregiver_id=returning_caregiver["caregiver"]["id"],
            ).count() == returning_conversation_count
            assert (
                db.query(ConversationMessage)
                .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .filter(ConversationSession.person_id == new_caregiver["person"]["id"])
                .count()
            ) == new_message_count
            assert (
                db.query(ConversationMessage)
                .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
                .filter(ConversationSession.person_id == returning_caregiver["person"]["id"])
                .count()
            ) == returning_message_count
        finally:
            db.close()


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


def test_live_qwen_greetings_use_and_persist_the_unified_companion_conversation():
    """Greetings and thanks must use the same real-model, persisted conversation path."""
    with TestClient(app) as client:
        session = _create_session(client, "Returning greeting acceptance evaluator")
        greeting = _ask(client, session, "Good morning.")
        assert greeting.status_code == 200, greeting.text
        greeting_data = greeting.json()
        conversation_id = greeting_data.get("session_id")
        assert conversation_id, greeting_data
        assert greeting_data.get("ai_generated") is True, greeting_data
        assert greeting_data.get("written_to_lcr") is False, greeting_data
        greeting_reply = greeting_data.get("reply", "")
        assert 0 < len(greeting_reply.split()) <= 30, greeting_reply
        assert greeting_reply.count("?") == 0, greeting_reply

        with TestingSessionLocal() as db:
            saved = db.query(ConversationMessage).filter(
                ConversationMessage.session_id == conversation_id
            ).order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc()).all()
            assert {(item.role, item.content) for item in saved} == {
                ("user", "Good morning."),
                ("assistant", greeting_reply),
            }

        thanks = _ask(client, session, "Thank you.", conversation_id)
        assert thanks.status_code == 200, thanks.text
        thanks_data = thanks.json()
        assert thanks_data.get("session_id") == conversation_id, thanks_data
        assert thanks_data.get("ai_generated") is True, thanks_data
        assert thanks_data.get("written_to_lcr") is False, thanks_data
        with TestingSessionLocal() as db:
            saved = db.query(ConversationMessage).filter(
                ConversationMessage.session_id == conversation_id
            ).order_by(ConversationMessage.created_at.asc(), ConversationMessage.id.asc()).all()
            assert len(saved) == 4
            assert ("user", "Thank you.") in {(item.role, item.content) for item in saved}
            assert any(item.role == "assistant" and item.content.strip() for item in saved)


def test_live_qwen_respects_low_capacity_caregiver_response_contract():
    """Real inference must stay concise and avoid turning exhaustion into a checklist."""
    client = TestClient(app)
    session = _create_session(client, "Low-capacity caregiver")
    response = _ask(
        client,
        session,
        "I'm exhausted. I don't know what to do anymore. Please tell me just one thing to focus on.",
    )
    assert response.status_code == 200, response.text
    result = response.json()
    reply = result.get("reply", "")
    assert result.get("ai_generated") is True, result
    assert result.get("written_to_lcr") is False, result
    assert len(reply.split()) <= 80, reply
    assert len([part for part in __import__("re").split(r"(?<=[.!?])\s+", reply.strip()) if part]) <= 3, reply
    assert reply.count("?") <= 1, reply
    assert not __import__("re").search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", reply), reply


def test_live_qwen_adapts_to_overload_pushback_and_distress_without_fabricating_or_reasking():
    """Real Qwen must lower caregiver effort, retrieve prior facts, and stop when asked."""
    with TestClient(app) as client:
        session = _create_session(client, "Caregiver behavior acceptance evaluator")
        event_id = _record_daily_care(
            client,
            session,
            "Caregiver reported that Mum woke repeatedly overnight on three nights this week and was restless before settling.",
        )
        session_id = None

        overwhelmed = _ask(
            client,
            session,
            "I'm exhausted. I don't know what to do anymore. Mum has been waking repeatedly overnight. What should I focus on?",
        )
        assert overwhelmed.status_code == 200, overwhelmed.text
        overwhelmed_data = overwhelmed.json()
        session_id = overwhelmed_data.get("session_id")
        assert session_id, overwhelmed_data
        assert overwhelmed_data.get("ai_generated") is True, overwhelmed_data
        assert event_id in overwhelmed_data.get("source_event_ids", []), overwhelmed_data
        assert overwhelmed_data.get("written_to_lcr") is False, overwhelmed_data
        overwhelmed_reply = overwhelmed_data.get("reply", "")
        assert len(overwhelmed_reply.split()) <= 100, overwhelmed_reply
        assert overwhelmed_reply.count("?") <= 1, overwhelmed_reply
        assert any(
            term in overwhelmed_reply.casefold()
            for term in ("waking", "sleep", "overnight", "restless")
        ), overwhelmed_reply

        pushback = _ask(
            client,
            session,
            "You keep asking me the same questions. I've already told you this. This is getting ridiculous.",
            session_id,
        )
        assert pushback.status_code == 200, pushback.text
        pushback_data = pushback.json()
        assert event_id in pushback_data.get("source_event_ids", []), pushback_data
        assert pushback_data.get("written_to_lcr") is False, pushback_data
        pushback_reply = pushback_data.get("reply", "")
        assert len(pushback_reply.split()) <= 100, pushback_reply
        assert pushback_reply.count("?") == 0, pushback_reply
        assert any(
            term in pushback_reply.casefold()
            for term in ("sorry", "right", "repeat", "record", "already shared", "information")
        ), pushback_reply

        distress = _ask(
            client,
            session,
            "I'm angry and exhausted. I just want a break.",
            session_id,
        )
        assert distress.status_code == 200, distress.text
        distress_data = distress.json()
        assert distress_data.get("written_to_lcr") is False, distress_data
        distress_reply = distress_data.get("reply", "")
        assert len(distress_reply.split()) <= 70, distress_reply
        assert distress_reply.count("?") <= 1, distress_reply
        assert not re.search(
            r"\b(?:checklist|step 1|step 2|first,|second,|third,)\b",
            distress_reply,
            re.IGNORECASE,
        ), distress_reply

        pause = _ask(
            client,
            session,
            "I can't continue right now. Please stop asking me questions.",
            session_id,
        )
        assert pause.status_code == 200, pause.text
        pause_data = pause.json()
        assert pause_data.get("ai_generated") is True, pause_data
        assert pause_data.get("written_to_lcr") is False, pause_data
        pause_reply = pause_data.get("reply", "")
        assert 0 < len(pause_reply.split()) <= 45, pause_reply
        assert pause_reply.count("?") == 0, pause_reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", pause_reply), pause_reply

        sadness = _ask(
            client,
            session,
            "I feel so sad and alone. I've been crying this morning.",
            session_id,
        )
        assert sadness.status_code == 200, sadness.text
        sadness_data = sadness.json()
        assert sadness_data.get("written_to_lcr") is False, sadness_data
        sadness_reply = sadness_data.get("reply", "")
        assert 0 < len(sadness_reply.split()) <= 70, sadness_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", sadness_reply.strip()) if part]) <= 2, sadness_reply
        assert sadness_reply.count("?") <= 1, sadness_reply
        assert not re.search(
            r"\b(?:i'?m here for you|everything will be okay|you are not alone|you're not alone)\b",
            sadness_reply,
            re.IGNORECASE,
        ), sadness_reply
        assert not re.search(
            r"(?m)^\s*(?:[-*•]|\d+[.)])\s+",
            sadness_reply,
        ), sadness_reply


        fear = _ask(
            client,
            session,
            "I'm frightened about what the future holds as Mum's dementia changes. I don't know what to expect.",
            session_id,
        )
        assert fear.status_code == 200, fear.text
        fear_data = fear.json()
        assert fear_data.get("ai_generated") is True, fear_data
        assert fear_data.get("written_to_lcr") is False, fear_data
        fear_reply = fear_data.get("reply", "")
        assert 0 < len(fear_reply.split()) <= 70, fear_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", fear_reply.strip()) if part]) <= 2, fear_reply
        assert fear_reply.count("?") <= 1, fear_reply
        assert not re.search(
            r"\b(?:guaranteed|inevitable|will definitely|will certainly|everything will be okay)\b",
            fear_reply,
            re.IGNORECASE,
        ), fear_reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", fear_reply), fear_reply

        grief = _ask(
            client,
            session,
            "I miss her so much. Some days I feel like I'm losing her even though she's still here.",
            session_id,
        )
        assert grief.status_code == 200, grief.text
        grief_data = grief.json()
        assert grief_data.get("ai_generated") is True, grief_data
        assert grief_data.get("written_to_lcr") is False, grief_data
        grief_reply = grief_data.get("reply", "")
        assert 0 < len(grief_reply.split()) <= 70, grief_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", grief_reply.strip()) if part]) <= 2, grief_reply
        assert not re.search(r"\b(?:after she died|since she died|when she passed away)\b", grief_reply, re.IGNORECASE), grief_reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", grief_reply), grief_reply

        guilt = _ask(
            client,
            session,
            "I lost my temper with Mum today. I feel like I'm failing her.",
            session_id,
        )
        assert guilt.status_code == 200, guilt.text
        guilt_data = guilt.json()
        assert guilt_data.get("ai_generated") is True, guilt_data
        assert guilt_data.get("written_to_lcr") is False, guilt_data
        guilt_reply = guilt_data.get("reply", "")
        assert 0 < len(guilt_reply.split()) <= 70, guilt_reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", guilt_reply.strip()) if part]) <= 2, guilt_reply
        assert not re.search(r"\b(?:you are a bad caregiver|you failed her|this is your fault)\b", guilt_reply, re.IGNORECASE), guilt_reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", guilt_reply), guilt_reply

        positive = _ask(
            client,
            session,
            "What a relief. Mum had a calm morning and we enjoyed breakfast together.",
            session_id,
        )
        assert positive.status_code == 200, positive.text
        positive_data = positive.json()
        assert positive_data.get("ai_generated") is True, positive_data
        assert positive_data.get("written_to_lcr") is False, positive_data
        positive_reply = positive_data.get("reply", "")
        assert 0 < len(positive_reply.split()) <= 50, positive_reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", positive_reply), positive_reply
        assert any(term in positive_reply.casefold() for term in ("glad", "relief", "good", "calm")), positive_reply


def test_live_qwen_overwhelm_response_after_mixed_care_history():
    """Reproduce the production-like multi-domain history that previously returned HTTP 503."""
    with TestClient(app) as client:
        session = _create_session(client, "Mixed-history overwhelmed caregiver")
        for report in (
            "Mum slept poorly last night and seemed more unsettled this morning.",
            "Mum barely touched her lunch today. She drank some water, though.",
            "I think the doctor changed Mum's medication yesterday, but I can't remember the new dose.",
            "Mum was calmer this evening. She ate a little more at dinner and is now resting.",
        ):
            _record_daily_care(client, session, report)

        response = _ask(
            client,
            session,
            "I'm exhausted. I can't keep up with all these updates and I don't know what to do.",
        )
        assert response.status_code == 200, response.text
        result = response.json()
        reply = result.get("reply", "")
        assert result.get("ai_generated") in (True, False), result
        assert result.get("written_to_lcr") is False, result
        if result.get("ai_generated") is False:
            assert result.get("response_mode") == "deterministic_low_capacity_support_fallback", result
            assert "couldn't confirm a reliable answer" in (result.get("safety_note") or "").casefold(), result
            assert result.get("source_event_ids") == [], result
            assert result.get("source_evidence_ids") == [], result
        assert 0 < len(reply.split()) <= 80, reply
        assert len([part for part in re.split(r"(?<=[.!?])\s+", reply.strip()) if part]) <= 2, reply
        assert reply.count("?") <= 1, reply
        assert not re.search(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", reply), reply


def test_live_qwen_handoff_draft_selects_an_exact_daily_care_lcr_source():
    """Real Qwen may select sources, but every generated handoff section must be verbatim and linked."""
    detail = "Caregiver reported waking around 3 a.m. and needing help settling again."
    with TestClient(app) as client:
        session = _create_session(client, "Live Qwen handoff source evaluator")
        event_id = _record_daily_care(client, session, detail)
        response = client.post(
            "/api/v1/handoff-drafts/prepare",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            json={
                "purpose": "family_transition",
                "audience": "family",
                "window_days": 30,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["generation_status"] == "generated", payload
    assert payload["validation_status"] == "exact_quote_reference_validation_passed", payload
    generation = payload["generation"]
    assert generation["provider"] == "ollama"
    assert generation["sections"]
    target_ref = f"care_events:{event_id}"
    selected = [item for item in generation["sections"] if item.get("source_ref") == target_ref]
    assert selected, {
        "expected_daily_care_source": target_ref,
        "selected_source_refs": [item.get("source_ref") for item in generation["sections"]],
        "source_statuses": payload.get("source_statuses"),
    }
    provenance = payload["claim_provenance"]
    assert provenance["coverage"] == "all_sections_linked", provenance
    assert provenance["unmatched_statement_count"] == 0, provenance
    assert any(
        claim.get("support_status") == "matched_saved_wording"
        and any(reference.get("source_ref") == target_ref for reference in claim.get("source_references", []))
        for claim in provenance["claims"]
    ), provenance

def test_live_qwen_surfaces_only_the_source_validated_professional_question():
    """Real Qwen must route a source-backed medication clarification to the professional, not the caregiver."""
    with TestClient(app) as client:
        session = _create_session(client, "Live Qwen professional-question evaluator")
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        db = TestingSessionLocal()
        try:
            evidence_a = Evidence(
                id=f"live-medication-report-a-{uuid.uuid4()}",
                person_id=person_id,
                type="note",
                source_text="Caregiver report: the medication instruction was described as 5 mg.",
                uploaded_by_caregiver_id=caregiver_id,
                source_type="caregiver_report",
                evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported",
                time_provenance="caregiver_reported",
            )
            evidence_b = Evidence(
                id=f"live-medication-report-b-{uuid.uuid4()}",
                person_id=person_id,
                type="note",
                source_text="A separate caregiver report described the medication instruction as 10 mg.",
                uploaded_by_caregiver_id=caregiver_id,
                source_type="caregiver_report",
                evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported",
                time_provenance="caregiver_reported",
            )
            db.add_all([evidence_a, evidence_b])
            db.flush()
            conflict = Contradiction(
                id=f"live-medication-conflict-{uuid.uuid4()}",
                person_id=person_id,
                subject_type="medication",
                subject_id="recorded-medication",
                field="current medication instruction",
                description="Two caregiver reports disagree about the current instruction.",
                evidence_ids=[evidence_a.id, evidence_b.id],
                resolution_status="unresolved",
            )
            db.add(conflict)
            db.commit()
            expected_question = (
                "Can the prescribing clinician or pharmacist confirm the current instruction for this medication?"
            )
            candidates = next_best_questions(db, person_id, persist=True)
            assert any(
                item.get("question") == expected_question
                and item.get("intended_answerer_role") == "professional"
                and item.get("persisted_question_id")
                for item in candidates
            ), candidates
            event_count_before = db.query(CareEvent).filter(CareEvent.person_id == person_id).count()
        finally:
            db.close()

        response = _ask(
            client,
            session,
            "What should I ask the doctor about the medication?",
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        reply = payload.get("reply", "")
        assert payload.get("ai_generated") is True, payload
        assert payload.get("written_to_lcr") is False, payload
        assert re.search(r"(?i)prescribing clinician|pharmacist|doctor", reply), reply
        assert re.search(r"(?i)confirm|clarify|current instruction", reply), reply
        assert len(reply.split()) <= 100, reply
        assert reply.count("?") <= 1, reply

        db = TestingSessionLocal()
        try:
            event_count_after = db.query(CareEvent).filter(CareEvent.person_id == person_id).count()
            assert event_count_after == event_count_before
        finally:
            db.close()

def test_live_qwen_grief_self_blame_and_positive_moments_remain_caregiver_led():
    """Configured live inference handles distinct emotional disclosures without creating care facts."""
    with TestClient(app) as client:
        session = _create_session(client, "Live emotional interaction evaluator")
        auth = {"Authorization": f"Bearer {session['access_token']}"}
        baseline = client.get("/api/v1/care-continuity/current-care", headers=auth)
        assert baseline.status_code == 200, baseline.text
        baseline_ids = {
            item.get("id")
            for item in baseline.json().get("items", [])
            if item.get("id")
        }

        grief = _ask(client, session, "I miss her so much. Some days I still expect her to walk into the room.")
        assert grief.status_code == 200, grief.text
        grief_data = grief.json()
        grief_reply = grief_data.get("reply", "")
        assert grief_data.get("ai_generated") is True, grief_data
        assert grief_data.get("written_to_lcr") is False, grief_data
        assert grief_data.get("persistence") == "conversation_only", grief_data
        assert 0 < len(grief_reply.split()) <= 90, grief_reply
        assert not re.search(r"(?m)^\\s*(?:[-*•]|\\d+[.)])\\s+", grief_reply), grief_reply
        assert not re.search(r"(?i)everything will be okay|at least|you should be grateful", grief_reply), grief_reply

        self_blame = _ask(
            client, session,
            "I lost my temper today. I feel guilty, and I feel like I'm failing her as a caregiver.",
            grief_data.get("session_id"),
        )
        assert self_blame.status_code == 200, self_blame.text
        self_blame_data = self_blame.json()
        self_blame_reply = self_blame_data.get("reply", "")
        assert self_blame_data.get("ai_generated") is True, self_blame_data
        assert self_blame_data.get("written_to_lcr") is False, self_blame_data
        assert 0 < len(self_blame_reply.split()) <= 90, self_blame_reply
        assert not re.search(r"(?i)you are a bad caregiver|you failed her|you should have", self_blame_reply), self_blame_reply

        positive = _ask(
            client, session,
            "What a relief. I'm grateful we had a calm afternoon together and she smiled.",
            self_blame_data.get("session_id"),
        )
        assert positive.status_code == 200, positive.text
        positive_data = positive.json()
        positive_reply = positive_data.get("reply", "")
        assert positive_data.get("ai_generated") is True, positive_data
        assert positive_data.get("written_to_lcr") is False, positive_data
        assert 0 < len(positive_reply.split()) <= 70, positive_reply
        assert not re.search(r"(?m)^\\s*(?:[-*•]|\\d+[.)])\\s+", positive_reply), positive_reply

        after = client.get("/api/v1/care-continuity/current-care", headers=auth)
        assert after.status_code == 200, after.text
        after_ids = {
            item.get("id")
            for item in after.json().get("items", [])
            if item.get("id")
        }
        assert after_ids == baseline_ids, {"before": baseline_ids, "after": after_ids}

def test_live_qwen_concurrent_authenticated_companion_requests_remain_person_scoped():
    """Concurrent real inference must not mix two caregivers' saved evidence."""
    with TestClient(app) as client:
        caregiver_a = _create_session(client, "Concurrent Qwen caregiver A")
        caregiver_b = _create_session(client, "Concurrent Qwen caregiver B")
        event_a = _record_daily_care(
            client,
            caregiver_a,
            "Caregiver reported that Mum's blue cardigan was placed in the hallway basket before lunch.",
        )
        event_b = _record_daily_care(
            client,
            caregiver_b,
            "Caregiver reported that Dad's green scarf was left on the kitchen chair after breakfast.",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(
                _ask, client, caregiver_a, "What did we record about the blue cardigan?"
            )
            future_b = executor.submit(
                _ask, client, caregiver_b, "What did we record about the green scarf?"
            )
            response_a = future_a.result(timeout=210)
            response_b = future_b.result(timeout=210)

        assert response_a.status_code == 200, response_a.text
        assert response_b.status_code == 200, response_b.text
        answer_a = response_a.json()
        answer_b = response_b.json()

        for answer in (answer_a, answer_b):
            assert answer.get("ai_generated") is True, answer
            assert answer.get("written_to_lcr") is False, answer
            assert answer.get("persistence") == "conversation_only", answer

        assert event_a in answer_a.get("source_event_ids", []), answer_a
        assert event_b not in answer_a.get("source_event_ids", []), answer_a
        assert event_b in answer_b.get("source_event_ids", []), answer_b
        assert event_a not in answer_b.get("source_event_ids", []), answer_b
        assert answer_a.get("session_id") != answer_b.get("session_id"), (
            answer_a,
            answer_b,
        )

