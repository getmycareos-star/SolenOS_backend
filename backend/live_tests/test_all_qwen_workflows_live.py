"""Real-model acceptance for every enabled Qwen workflow.

This suite deliberately uses the configured Ollama/Qwen model. It does not patch
or replace qwen_chat. Synthetic care data is created through the public API and
is isolated in the CI PostgreSQL database.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.qwen_background_worker import ENABLED_WORKFLOWS

pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") != "1",
    reason="requires the dedicated real-Qwen workflow and migrated PostgreSQL",
)

EVENT_WORKFLOWS = {
    "add_record", "living_care_record", "care_history_search", "care_situation_change",
    "delta_engine", "evidence_provenance", "uncertainty_contradictions",
    "returning_caregiver", "appointment", "after_appointment", "handoff",
    "professional_summary", "open_loops", "two_week_review", "decision_outcome",
    "medication_day", "care_coordination", "caregiver_feedback", "sharing_export",
    "audit_review", "care_transition", "incident_follow_through", "emergency_readiness",
    "care_coverage_gap", "care_instruction_change_tracker",
    "critical_information_completeness", "personal_preferences_distress_context",
    "caregiver_strain_support", "document_validity_renewal",
}

WORKFLOW_REQUESTS = {
    workflow: (
        "Review the available information for this task. Separate recorded details "
        "from unknown information. Do not infer a cause, clinical conclusion, owner, "
        "deadline, completed action or medication change."
    )
    for workflow in ENABLED_WORKFLOWS
}


def _new_session(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": "Synthetic Qwen workflow evaluator"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _record_fixture(client: TestClient, session: dict) -> str:
    response = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "capture_idempotency_key": str(uuid.uuid4()),
            "observation": "Caregiver reported that the person slept poorly last night.",
            "source": "live_qwen_workflow_acceptance",
        },
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json().get("id")
    assert event_id
    return str(event_id)


def test_every_enabled_workflow_uses_real_qwen_and_returns_reviewable_grounded_output():
    assert len(ENABLED_WORKFLOWS) == 39, ENABLED_WORKFLOWS
    assert set(WORKFLOW_REQUESTS) == set(ENABLED_WORKFLOWS)

    with TestClient(app) as client:
        session = _new_session(client)
        event_id = _record_fixture(client, session)
        headers = {"Authorization": f"Bearer {session['access_token']}"}

        for workflow in ENABLED_WORKFLOWS:
            response = client.post(
                "/api/v1/assistant/assist",
                headers=headers,
                json={
                    "workflow": workflow,
                    "text": WORKFLOW_REQUESTS[workflow],
                },
            )
            assert response.status_code == 200, (
                f"Real Qwen inference failed for {workflow}: "
                f"HTTP {response.status_code} {response.text}"
            )
            data = response.json()
            assert data.get("workflow") == workflow, data
            assert data.get("ai_generated") is True, data
            assert data.get("provider_status") == "available", data
            assert isinstance(data.get("reply"), str) and data["reply"].strip(), data
            assert data.get("review_required") is True, data
            assert data.get("written_to_lcr") is False, data
            assert data.get("persistence") == "read_only_assistance", data
            assert data.get("authority") == "solenos_care_record_and_caregiver", data
            assert not re.search(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
                data["reply"],
                re.IGNORECASE,
            ), f"{workflow} exposed an internal record identifier: {data['reply']!r}"
            assert isinstance(data.get("source_event_ids"), list), data
            assert isinstance(data.get("source_evidence_ids"), list), data
            if workflow in EVENT_WORKFLOWS:
                assert event_id in data["source_event_ids"], (
                    f"{workflow} did not retrieve the authorized fixture event: {data}"
                )
