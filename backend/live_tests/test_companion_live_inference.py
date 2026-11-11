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
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


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
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _verify_persistence_after_real_server_restart(session: dict, other: dict, conversation_id: str, proposal_id: str, assertion_id: str, disclosure: str, document_analysis_id: str) -> None:
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

        status, document_history = _get_json_after_restart(base_url, "/api/v1/documents/intelligence", token)
        assert status == 200, document_history
        assert any(item.get("analysis_id") == document_analysis_id for item in document_history.get("items", [])), document_history

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

        # Exercise actual Qwen document candidate extraction, not the mocked
        # extraction boundary used by the isolated PostgreSQL persistence suite.
        document_text = (
            "Discharge note: Patient walked 20 metres with a frame on 12 October. "
            "The patient ate half of lunch during the observation period."
        )
        uploaded = client.post(
            "/api/v1/upload",
            headers=auth,
            data={
                "person_id": caregiver["person"]["id"],
                "caregiver_id": caregiver["caregiver"]["id"],
                "evidence_type": "document",
            },
            files={"file": ("live-discharge-note.txt", document_text, "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        evidence_id = uploaded.json()["evidence_id"]
        extracted = client.post(f"/api/v1/documents/{evidence_id}/extract", headers=auth, json={})
        assert extracted.status_code == 201, extracted.text
        interpreted = client.post(f"/api/v1/documents/{evidence_id}/intelligence", headers=auth)
        assert interpreted.status_code == 201, interpreted.text
        document_result = interpreted.json()
        assert document_result["intelligence_status"] == "available", document_result
        assert document_result["written_to_lcr"] is False, document_result
        assert document_result["observations"], "Live Qwen returned no reviewable candidate for explicit source observations"
        assert all(
            item.get("review_status") == "pending_review"
            and item.get("source_quote") in document_text
            for item in document_result.get("observations", [])
        ), document_result

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

        other = _create_session(client, "Independent identity-isolation evaluator")
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
            document_result["analysis_id"],
        )
