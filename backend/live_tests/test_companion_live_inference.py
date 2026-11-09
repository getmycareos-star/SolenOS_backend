"""Real Qwen inference through authenticated Companion and PostgreSQL.

The fixture is created through the public Daily Care workflow. The database is
created exclusively by the repository's Alembic migration chain.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

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


def test_live_qwen_companion_uses_authenticated_postgres_lcr_and_preserves_identity(caplog):
    """Genuine configured Qwen must answer from real workflow-created care evidence."""
    with TestClient(app) as client:
        caregiver = _create_session(client, "Live Qwen PostgreSQL evaluator")
        event_id = _record_daily_care(
            client,
            caregiver,
            "Caregiver reported waking several times overnight and restless sleep.",
        )

        stage_log_start = len(caplog.records)
        request_started = time.perf_counter()
        direct = _ask(client, caregiver, "What did we record about sleep?")
        total_request_ms = round((time.perf_counter() - request_started) * 1000, 2)
        assert direct.status_code == 200, direct.text
        answer = direct.json()

        stage_events = []
        for record in caplog.records[stage_log_start:]:
            if record.name != "app.api.collaboration":
                continue
            message = record.getMessage()
            if not message.startswith("solenos_companion_stage "):
                continue
            try:
                stage_events.append(json.loads(message.split(" ", 1)[1]))
            except (TypeError, ValueError):
                continue
        retrieval = next((item for item in stage_events if item.get("event") == "companion_retrieval_complete"), None)
        inference = next((item for item in stage_events if item.get("event") == "companion_inference_complete"), None)
        assert retrieval is not None, stage_events
        assert inference is not None, stage_events
        assert retrieval.get("request_id") == inference.get("request_id"), stage_events
        assert isinstance(retrieval.get("duration_ms"), (int, float)) and retrieval["duration_ms"] >= 0, retrieval
        assert isinstance(inference.get("duration_ms"), (int, float)) and inference["duration_ms"] >= 0, inference
        assert inference.get("retrieval_duration_ms") == retrieval["duration_ms"], stage_events
        print("COMPANION_STAGE_TIMING=" + json.dumps({
            "request_id": retrieval["request_id"],
            "retrieval_ms": retrieval["duration_ms"],
            "inference_ms": inference["duration_ms"],
            "total_http_ms": total_request_ms,
            "scope": "disposable_postgresql_live_qwen_ci",
        }, separators=(",", ":")))
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
        other_auth = {"Authorization": f"Bearer {other['access_token']}"}
        other_proposals = client.get("/api/v1/companion/care-updates", headers=other_auth)
        assert other_proposals.status_code == 200, other_proposals.text
        assert other_proposals.json().get("items") == [], other_proposals.json()
        isolated = _ask(client, other, "What did we record about sleep?")
        assert isolated.status_code == 200, isolated.text
        isolated_data = isolated.json()
        assert event_id not in isolated_data.get("source_event_ids", []), isolated_data
        assert isolated_data.get("written_to_lcr") is False, isolated_data
