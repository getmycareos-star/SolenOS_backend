"""Real-model acceptance for every enabled Qwen workflow.

This suite deliberately uses the configured Ollama/Qwen model. It does not patch
or replace qwen_chat. Synthetic care data is created through the public API and
is isolated in the CI PostgreSQL database.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.qwen_background_worker import ENABLED_WORKFLOWS

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"))
if os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") == "1" and not POSTGRES_DATABASE:
    raise RuntimeError("Live Qwen workflow acceptance requires PostgreSQL; refusing a skipped non-PostgreSQL run.")

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


def _record_fixture(client: TestClient, session: dict, detail: str = "Caregiver reported that the person slept poorly last night.") -> str:
    response = client.post(
        "/api/v1/daily/log",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={
            "person_id": session["person"]["id"],
            "caregiver_id": session["caregiver"]["id"],
            "capture_idempotency_key": str(uuid.uuid4()),
            "observation": detail,
            "source": "live_qwen_workflow_acceptance",
        },
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json().get("id")
    assert event_id
    return str(event_id), [str(value) for value in response.json().get("evidence_ids", [])]


def test_enabled_workflow_contract_is_complete():
    assert len(ENABLED_WORKFLOWS) == 39, ENABLED_WORKFLOWS
    assert set(WORKFLOW_REQUESTS) == set(ENABLED_WORKFLOWS)


def test_real_qwen_passes_all_ten_specialist_acceptance_scenarios_through_shared_gateway():
    from app.api.collaboration import (
        _production_qwen_answer_checks,
        _production_qwen_evaluation_cases,
    )
    from app.services.qwen_gateway import qwen_chat
    from app.services.qwen_specialized_prompts import (
        WORKFLOW_SPECIALISTS,
        apply_specialized_intelligence,
        select_specialized_domains,
    )
    from app.services.qwen_system_prompt import apply_solenos_system_contract

    cases = _production_qwen_evaluation_cases()
    assert len(cases) == 10
    results = []

    # Do not abort on the first behavioural failure. Every controlled scenario
    # must execute independently so the artifact contains all ten real outputs.
    for case in cases:
        messages = [
            {"role": "system", "content": case["system"]},
            {
                "role": "user",
                "content": case["context"] + "\n\nQuestion: " + case["question"],
            },
        ]
        result = {
            "criterion": case["criterion"],
            "input_messages": messages,
            "assembled_system_context": None,
            "workflow": None,
            "selected_specialists": [],
            "answer": None,
            "checks": {},
            "passed": False,
        }
        try:
            workflow_match = re.search(
                r"SOLENOS_WORKFLOW_ID:\s*([a-z0-9_]+)",
                case["system"],
                flags=re.IGNORECASE,
            )
            if not workflow_match:
                raise AssertionError("scenario_missing_workflow_id")
            workflow_id = workflow_match.group(1).casefold()
            result["workflow"] = workflow_id
            expected_specialists = WORKFLOW_SPECIALISTS.get(workflow_id)
            if expected_specialists is None:
                raise AssertionError("scenario_workflow_has_no_specialist_mapping")
            selected = select_specialized_domains(messages)
            result["selected_specialists"] = list(selected)
            result["routing_passed"] = selected == expected_specialists

            # Capture the exact assembled system contract sent by qwen_chat.
            # qwen_chat applies these same shared functions immediately before
            # transport; the original messages below still go through that path.
            assembled = apply_solenos_system_contract(
                apply_specialized_intelligence(messages)
            )
            result["assembled_system_context"] = assembled[0]["content"]

            answer = qwen_chat(
                messages,
                timeout=180.0,
                options={"temperature": 0, "num_predict": 128, "num_ctx": 4096},
            )
            result["answer"] = answer
            checks = _production_qwen_answer_checks(case, answer)
            result["checks"] = checks
            result["passed"] = bool(
                result["routing_passed"] and checks and all(checks.values())
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc)[:1200]}"
        results.append(result)

    artifact = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
        "scenario_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "failed_count": sum(1 for item in results if not item["passed"]),
        "results": results,
    }
    artifact_path = Path(os.getenv("RUNNER_TEMP", "/tmp")) / "solenos-qwen-ten-area-acceptance.json"
    artifact_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    failures = [
        {
            "criterion": item["criterion"],
            "workflow": item["workflow"],
            "selected_specialists": item["selected_specialists"],
            "checks": item["checks"],
            "error": item.get("error"),
            "answer": (item.get("answer") or "")[:1200],
        }
        for item in results
        if not item["passed"]
    ]
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)



def test_real_qwen_generates_family_and_appointment_briefings_only_from_lcr(live_qwen_context):
    client = live_qwen_context["client"]
    session = live_qwen_context["session"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    person_id = session["person"]["id"]
    caregiver_id = session["caregiver"]["id"]

    appointment_response = client.post(
        "/api/v1/appointments",
        headers=headers,
        json={
            "person_id": person_id,
            "title": "Primary care follow-up",
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            "scheduled_at_timezone": "UTC",
            "provider": "Primary care clinician",
            "preparation_notes": "Bring the current care notes.",
            "medication_questions": ["Confirm the current medication list is accurate."],
            "created_by_caregiver_id": caregiver_id,
        },
    )
    assert appointment_response.status_code == 200, appointment_response.text
    appointment_id = appointment_response.json()["id"]

    def prepare(payload: dict, request_id: str) -> dict:
        response = client.post(
            "/api/v1/handoff-drafts/prepare",
            headers={**headers, "x-request-id": request_id},
            json=payload,
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["generation_status"] == "generated", draft
        assert draft["validation_status"] == "exact_quote_reference_validation_passed", draft
        assert draft["source_of_truth"] == "living_care_record"
        assert draft["source_freshness"]["status"] == "current"
        assert len(draft["source_snapshot_sha256"]) == 64
        generation = draft["generation"]
        assert generation.get("provider") == "ollama", generation
        assert "qwen" in str(generation.get("model", "")).lower(), generation
        sections = generation.get("sections") or []
        assert sections, draft
        source_refs = {
            f"{item.get('source_table')}:{item.get('source_id')}"
            for item in draft.get("source_references", [])
            if item.get("source_table") and item.get("source_id")
        }
        for section in sections:
            assert section.get("source_ref") in source_refs, section
            assert section.get("supporting_quote"), section
            assert section.get("semantic_entailment_status") == "caregiver_review_required", section
        return draft

    family = prepare(
        {"purpose": "family_transition", "audience": "family", "window_days": 30},
        "qwen-live-family-briefing-" + str(uuid.uuid4()),
    )
    assert family["audience"] == "family"
    assert family["purpose"] == "family_transition"

    appointment = prepare(
        {
            "purpose": "professional_appointment",
            "audience": "professional",
            "window_days": 30,
            "appointment_id": appointment_id,
        },
        "qwen-live-appointment-briefing-" + str(uuid.uuid4()),
    )
    assert appointment["audience"] == "professional"
    assert appointment["purpose"] == "professional_appointment"
    assert appointment["appointment_id"] == appointment_id
    assert appointment["selected_appointment"]["id"] == appointment_id
    assert any(
        item.get("source_table") == "appointments" and item.get("source_id") == appointment_id
        for item in appointment.get("source_references", [])
    )


@pytest.fixture(scope="module")
def live_qwen_context():
    """Reuse one real PostgreSQL-backed client and source fixture across all live Qwen checks.

    Recreating TestClient for every workflow restarts the application lifespan,
    its background workers, and the database fixture setup 39 times. A single
    isolated synthetic care record keeps the real Qwen calls and source checks
    intact while avoiding repeated worker startup and duplicate capture jobs.
    """
    with TestClient(app) as client:
        session = _new_session(client)
        event_id, _ = _record_fixture(client, session)
        medication_event_id, medication_evidence_ids = _record_fixture(
            client,
            session,
            "Medication observation: caregiver reported that the morning medication was taken.",
        )
        _, handoff_evidence_ids = _record_fixture(
            client,
            session,
            "Caregiver reported that the person prefers a quiet room after lunch.",
        )
        care_understanding_report = (
            "Caregiver reported that the person slept poorly last night. "
            "The nurse observed that the person walked with a frame this morning. "
            "The caregiver said the person prefers a quiet room after lunch."
        )
        _, care_understanding_evidence_ids = _record_fixture(
            client,
            session,
            care_understanding_report,
        )
        evidence_response = client.get(
            f"/api/v1/evidence/{session['person']['id']}",
            headers={"Authorization": f"Bearer {session['access_token']}"},
            params={"ids": care_understanding_evidence_ids[0]},
        )
        assert evidence_response.status_code == 200, evidence_response.text
        saved_evidence = evidence_response.json()
        assert len(saved_evidence) == 1, saved_evidence
        care_understanding_source = saved_evidence[0]["source_text"]
        assert care_understanding_report in care_understanding_source
        yield {
            "client": client,
            "session": session,
            "event_id": event_id,
            "medication_event_id": medication_event_id,
            "medication_evidence_ids": medication_evidence_ids,
            "handoff_evidence_ids": handoff_evidence_ids,
            "care_understanding_evidence_id": care_understanding_evidence_ids[0],
            "care_understanding_source": care_understanding_source,
        }


def test_real_qwen_claim_extraction_runs_two_independent_passes_without_lcr_write(live_qwen_context, monkeypatch):
    from app.api import collaboration as collaboration_api

    original_qwen_chat = collaboration_api.qwen_chat
    calls = []

    def capture_qwen_chat(messages, *args, **kwargs):
        calls.append(messages)
        return original_qwen_chat(messages, *args, **kwargs)

    monkeypatch.setattr(collaboration_api, "qwen_chat", capture_qwen_chat)
    client = live_qwen_context["client"]
    session = live_qwen_context["session"]
    source = live_qwen_context["care_understanding_source"]
    evidence_id = live_qwen_context["care_understanding_evidence_id"]
    response = client.post(
        "/api/v1/assistant/claim-extraction",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"evidence_id": evidence_id, "raw_text": source},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(calls) == 2, f"claim extraction must make two independent real-Qwen calls; observed {len(calls)}"
    first_system = " ".join(str(item.get("content", "")) for item in calls[0] if item.get("role") == "system")
    second_system = " ".join(str(item.get("content", "")) for item in calls[1] if item.get("role") == "system")
    assert "First independent pass" in first_system
    assert "Second independent pass" in second_system
    assert data.get("provider") == "private_ollama_qwen", data
    assert data.get("evidence_id") == evidence_id, data
    assert data.get("written_to_lcr") is False, data
    assert data.get("review_required") is True, data
    runs = [data.get("run_a"), data.get("run_b")]
    assert all(isinstance(run, dict) and isinstance(run.get("claims"), list) for run in runs), data
    claims = [claim for run in runs for claim in run["claims"]]
    assert claims, "real Qwen returned no source claims for an explicit multi-observation source"
    for claim in claims:
        span = claim.get("source_span")
        if span is not None:
            assert source[span["start_offset"]:span["end_offset"]] == span["text"], claim
            assert span["text"] in source, claim


def test_real_qwen_care_understanding_is_source_grounded_and_review_only(live_qwen_context, monkeypatch):
    from app.api import collaboration as collaboration_api

    original_qwen_chat = collaboration_api.qwen_chat
    calls = []

    def capture_qwen_chat(messages, *args, **kwargs):
        calls.append(messages)
        return original_qwen_chat(messages, *args, **kwargs)

    monkeypatch.setattr(collaboration_api, "qwen_chat", capture_qwen_chat)
    client = live_qwen_context["client"]
    session = live_qwen_context["session"]
    source = live_qwen_context["care_understanding_source"]
    evidence_id = live_qwen_context["care_understanding_evidence_id"]
    response = client.post(
        "/api/v1/assistant/care-understanding",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"evidence_id": evidence_id, "raw_text": source},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(calls) == 1, f"care understanding must use one real-Qwen inference; observed {len(calls)}"
    assert data.get("provider") == "private_ollama_qwen", data
    assert data.get("evidence_id") == evidence_id, data
    assert data.get("written_to_lcr") is False, data
    assert data.get("review_required") is True, data
    understanding = data.get("understanding")
    assert isinstance(understanding, dict), data
    assert any(understanding.get(key) for key in ("observations", "events", "decisions", "outcomes")), data
    for key, items in understanding.items():
        assert isinstance(items, list), (key, items)
        for item in items:
            if key == "possible_links":
                assert item.get("causation_claimed") is False, item
                assert item.get("text") in source, item
            else:
                assert item.get("raw_fragment") in source, (key, item)


@pytest.mark.parametrize("workflow", ENABLED_WORKFLOWS)
def test_each_enabled_workflow_uses_real_qwen_and_returns_reviewable_grounded_output(workflow, live_qwen_context, monkeypatch):
    # Test-only capture: if a real-model draft is rejected, expose the synthetic
    # draft and repair output in the CI assertion so the failing validator rule
    # can be diagnosed. Nothing is added to production logs or caregiver data.
    from app.api import collaboration as collaboration_api

    original_qwen_chat = collaboration_api.qwen_chat
    from app.services import qwen_gateway
    original_identity_contract = qwen_gateway.apply_solenos_system_contract
    identity_contracts = []

    def capture_identity_contract(messages):
        final_messages = original_identity_contract(messages)
        identity_contracts.append(final_messages[0]["content"])
        return final_messages

    monkeypatch.setattr(qwen_gateway, "apply_solenos_system_contract", capture_identity_contract)
    generated_drafts = []

    def capture_qwen_chat(*args, **kwargs):
        draft = original_qwen_chat(*args, **kwargs)
        generated_drafts.append(str(draft)[:2000])
        return draft

    monkeypatch.setattr(collaboration_api, "qwen_chat", capture_qwen_chat)
    client = live_qwen_context["client"]
    session = live_qwen_context["session"]
    event_id = live_qwen_context["event_id"]
    medication_event_id = live_qwen_context["medication_event_id"]
    medication_evidence_ids = live_qwen_context["medication_evidence_ids"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}
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
        f"HTTP {response.status_code} {response.text}; "
        f"synthetic Qwen drafts/repair outputs={generated_drafts!r}"
    )
    data = response.json()
    assert data.get("workflow") == workflow, data
    assert data.get("ai_generated") is True, data
    assert data.get("provider_status") == "available", data
    assert isinstance(data.get("reply"), str) and data["reply"].strip(), data
    assert data.get("review_required") is True, data
    assert data.get("written_to_lcr") is False, data
    assert data.get("persistence") == "read_only_assistance", data
    assert identity_contracts, f"{workflow} did not pass through the shared identity contract"
    for contract in identity_contracts:
        assert "SOLENOS COMPANION — PRODUCT IDENTITY" in contract
        assert "SolenOS was founded by David Abuchi" in contract
        assert "The model is replaceable." in contract
        assert "SOLENOS COMPANION — THOUGHTFUL CARE PARTNER" in contract
    assert data.get("authority") == "solenos_care_record_and_caregiver", data
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        data["reply"],
        re.IGNORECASE,
    ), f"{workflow} exposed an internal record identifier: {data['reply']!r}"
    assert isinstance(data.get("source_event_ids"), list), data
    assert isinstance(data.get("source_evidence_ids"), list), data
    if workflow == "medication_day":
        assert medication_evidence_ids and set(medication_evidence_ids).intersection(data["source_evidence_ids"]), (
            f"{workflow} did not retrieve the authorized medication source evidence: {data}"
        )
        assert medication_event_id in data["source_event_ids"], (
            f"{workflow} did not retrieve the medication-related care event: {data}"
        )
        assert event_id not in data["source_event_ids"], (
            f"{workflow} included an unrelated sleep observation: {data}"
        )
    elif workflow in EVENT_WORKFLOWS:
        assert event_id in data["source_event_ids"], (
            f"{workflow} did not retrieve the authorized fixture event: {data}"
        )
def test_handoff_draft_generation_uses_real_qwen_and_exact_saved_sources(live_qwen_context, monkeypatch):
    """Exercise the separate handoff-draft generator, not only the handoff assist workflow."""
    from app.services import qwen_gateway
    original_identity_contract = qwen_gateway.apply_solenos_system_contract
    identity_contracts = []

    def capture_identity_contract(messages):
        final_messages = original_identity_contract(messages)
        identity_contracts.append(final_messages[0]["content"])
        return final_messages

    monkeypatch.setattr(qwen_gateway, "apply_solenos_system_contract", capture_identity_contract)
    client = live_qwen_context["client"]
    session = live_qwen_context["session"]
    evidence_ids = live_qwen_context["handoff_evidence_ids"]
    question_response = client.post(
        "/api/v1/uncertainty/questions",
        headers={
            "Authorization": f"Bearer {session['access_token']}",
            "X-Request-ID": str(uuid.uuid4()),
        },
        json={
            "person_id": session["person"]["id"],
            "question": "What transfer support is required for the next handoff?",
            "subject_type": "person",
            "subject_id": session["person"]["id"],
            "status": "open",
            "necessity": "necessary",
            "necessity_rationale": "The receiving caregiver needs the current support level to safely plan transfers.",
            "intended_answerer_role": "family_caregiver",
            "intended_answerer_label": "Receiving caregiver",
            "created_by_caregiver_id": session["caregiver"]["id"],
            "evidence_ids": [evidence_ids[0]],
            "context": [{
                "source_kind": "caregiver_report",
                "source_id": evidence_ids[0],
                "source_record_refs": [{"record_type": "evidence", "record_id": evidence_ids[0]}],
                "necessity_assessment": {
                    "necessity_rationale": "The receiving caregiver needs the current support level to safely plan transfers.",
                    "decision_dependency": "The receiving caregiver cannot prepare the transfer plan without the current support level.",
                    "evidence_ids": [evidence_ids[0]],
                },
            }],
        },
    )
    assert question_response.status_code == 200, question_response.text
    open_question_id = question_response.json()["id"]
    headers = {
        "Authorization": f"Bearer {session['access_token']}",
        "X-Request-ID": str(uuid.uuid4()),
    }
    response = client.post(
        "/api/v1/handoff-drafts/prepare",
        headers=headers,
        json={
            "purpose": "family_transition",
            "audience": "family",
            "window_days": 30,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data.get("draft_persisted") is True, data
    assert data.get("generation_status") == "generated", data
    assert data.get("validation_status") == "exact_quote_reference_validation_passed", data
    assert data.get("review_status") == "pending_review", data
    assert data.get("read_only") is True, data
    assert data.get("requires_caregiver_review") is True, data
    assert data.get("creates_handoff") is False, data
    assert data.get("updates_lcr") is False, data
    assert identity_contracts, "handoff draft generation bypassed the shared identity contract"
    assert all("SolenOS was founded by David Abuchi" in item for item in identity_contracts)
    assert all("SOLENOS COMPANION — THOUGHTFUL CARE PARTNER" in item for item in identity_contracts)

    generation = data.get("generation") or {}
    assert generation.get("provider") == "ollama", generation
    assert "qwen" in str(generation.get("model", "")).lower(), generation
    sections = generation.get("sections") or []
    assert sections, generation

    source_refs = {
        f"{item.get('source_table')}:{item.get('source_id')}"
        for item in data.get("source_references", [])
        if item.get("source_table") and item.get("source_id")
    }
    assert all(item.get("source_ref") in source_refs for item in sections), data
    open_question_ref = f"open_questions:{open_question_id}"
    assert open_question_ref in source_refs, data
    assert any(item.get("source_ref") == open_question_ref for item in sections), data
    section_text = " ".join(
        str(item.get("text") or item.get("summary") or "")
        for item in sections
    ).casefold()
    assert "transfer support" in section_text, data
    assert any(term in section_text for term in ("unresolved", "not recorded", "confirm")), data

    provenance = data.get("claim_provenance") or {}
    assert provenance.get("coverage") == "all_sections_linked", provenance
    assert provenance.get("unmatched_statement_count") == 0, provenance
    referenced_evidence = {
        str(evidence_id)
        for item in data.get("source_references", [])
        for evidence_id in (item.get("evidence_ids") or [])
    }
    assert set(evidence_ids).intersection(referenced_evidence), data
