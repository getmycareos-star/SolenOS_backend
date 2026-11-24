"""Real-Qwen acceptance for the Delta Engine model-enrichment service path.

The public reconcile API intentionally uses deterministic change intelligence
unless model enrichment is explicitly requested. This test exercises the real
Qwen-enabled service path, strict output parsing, provenance, and PostgreSQL
persistence without mocking qwen_chat.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.main import app
from app.models.care import CareEvent
from app.models.care_continuity import CareAssertion
from app.services import delta_engine, qwen_gateway
from app.services.delta_engine import reconcile_event
from app.services.qwen_gateway import _estimate_prompt_tokens

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(
    ("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")
)
if os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") == "1" and not POSTGRES_DATABASE:
    raise RuntimeError("Live Delta Engine Qwen acceptance requires PostgreSQL; refusing a skipped non-PostgreSQL run.")
pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_LIVE_QWEN_EVAL") != "1" or not POSTGRES_DATABASE,
    reason="requires real Qwen inference and migrated PostgreSQL",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if POSTGRES_DATABASE else None
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine is not None else None


def override_db():
    if TestingSessionLocal is None:
        raise RuntimeError("Live Delta Engine acceptance requires PostgreSQL.")
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_real_qwen_delta_reconciliation_persists_grounded_result_within_budget(monkeypatch):
    app.dependency_overrides[get_db] = override_db
    source_text = (
        "Caregiver reported that Mary ate half of breakfast this morning."
    )
    event_id = str(uuid.uuid4())
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": "Synthetic Delta Engine evaluator"},
            )
            assert created.status_code == 201, created.text
            session = created.json()
            person_id = session["person"]["id"]
            caregiver_id = session["caregiver"]["id"]
            headers = {"Authorization": f"Bearer {session['access_token']}"}

            occurred_at = datetime.now(timezone.utc)
            with TestingSessionLocal() as db:
                db.add(CareEvent(
                    id=event_id,
                    person_id=person_id,
                    event_type="daily_observation",
                    status="recorded",
                    occurred_at=occurred_at,
                    title="Breakfast observation",
                    description=source_text,
                    evidence_ids=[],
                    created_by_caregiver_id=caregiver_id,
                ))
                db.commit()

            qwen_calls = []
            assembled_requests = []
            original_qwen_chat = delta_engine.qwen_chat
            original_apply_contract = qwen_gateway.apply_solenos_system_contract
            def capture_contract(messages):
                assembled = original_apply_contract(messages)
                assembled_requests.append(assembled)
                return assembled
            def capture_qwen_chat(messages, **kwargs):
                qwen_calls.append(kwargs)
                return original_qwen_chat(messages, **kwargs)
            monkeypatch.setattr(qwen_gateway, "apply_solenos_system_contract", capture_contract)
            monkeypatch.setattr(delta_engine, "qwen_chat", capture_qwen_chat)

            started = time.perf_counter()
            with TestingSessionLocal() as db:
                result = reconcile_event(
                    db,
                    person_id=person_id,
                    event_date=occurred_at,
                    source="live_qwen_delta_acceptance",
                    content=source_text,
                    source_event_id=event_id,
                    evidence_ids=[],
                    persist=True,
                    created_by_caregiver_id=caregiver_id,
                    model_enrichment=True,
                )
                db.commit()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            assert qwen_calls and assembled_requests, "The Delta model-enrichment path did not invoke the shared Qwen gateway."
            assert len(qwen_calls) == len(assembled_requests)
            for assembled_messages, kwargs in zip(assembled_requests, qwen_calls):
                options = kwargs.get("options") or {}
                num_ctx = int(options.get("num_ctx", 4096))
                num_predict = int(options.get("num_predict", 512))
                estimate = _estimate_prompt_tokens(assembled_messages)
                input_budget = num_ctx - num_predict - 128
                assert estimate <= input_budget, {
                    "estimated_prompt_tokens": estimate,
                    "input_budget": input_budget,
                }
                assembled_prompt = "\n".join(
                    str(message.get("content") or "")
                    for message in assembled_messages
                    if message.get("role") == "system"
                )
                for required_contract in (
                    "Version: 1.1",
                    "Information stewardship — applies to every workflow",
                    "NON-OVERRIDABLE SHARED EVIDENCE, SAFETY AND AUTHORITY RULES",
                    "# SOLENOS COMPANION — THOUGHTFUL CARE PARTNER",
                    "# SOLENOS COMPANION — PRODUCT IDENTITY",
                    "ACTIVE SOLENOS SPECIALIST RESPONSIBILITIES",
                ):
                    assert required_contract in assembled_prompt, required_contract
            assert result.get("event_summary", "").strip(), result
            assert result.get("caregiver_summary", "").strip(), result
            assert result.get("clinician_summary", "").strip(), result
            assert all(
                len(result[key]) <= 140
                for key in ("event_summary", "caregiver_summary", "clinician_summary")
            ), result
            assert result.get("conditions") == [], result
            assert result.get("symptoms") == [], result
            assert result.get("follow_ups") == [], result
            assert result.get("clinical_gaps") == [], result
            caregiver_facing_text = " ".join(
                result[key] for key in ("event_summary", "caregiver_summary", "clinician_summary")
            ).casefold()
            assert not any(
                unsupported in caregiver_facing_text
                for unsupported in ("dehydration", "reduced appetite", "illness", "diagnosis")
            ), result
            assert "breakfast" in (
                result["event_summary"] + " " + result["caregiver_summary"]
            ).casefold(), result
            provenance = result.get("provenance") or {}
            assert provenance.get("model_provider") == "configured_local_ollama", provenance
            assert provenance.get("model") == os.getenv("OLLAMA_MODEL", "qwen3.5:4b"), provenance
            assert provenance.get("person_id") == person_id, provenance
            assert provenance.get("source_event_id") == event_id, provenance
            assert provenance.get("persisted") is True, provenance

            claim_provenance = result.get("claim_provenance") or {}
            assert claim_provenance.get("version") == "delta-claim-provenance-v1", claim_provenance
            claims = claim_provenance.get("claims")
            assert isinstance(claims, list), claim_provenance
            summary_claims = [claim for claim in claims if claim.get("claim_type") == "summary"]
            for summary_field in ("event_summary", "caregiver_summary", "clinician_summary"):
                assert any(claim.get("summary_field") == summary_field for claim in summary_claims), claim_provenance
            for claim in summary_claims:
                assert claim.get("text", "").strip(), claim
                if claim.get("support_status") == "matched_exact_source_wording":
                    assert claim.get("source_references"), claim
                else:
                    assert claim.get("support_status") in {"historical_only", "not_matched_to_source"}, claim

            delta_run_id = provenance.get("delta_run_id")
            assert delta_run_id, provenance

            runs_response = client.get(
                f"/api/v1/care-delta/runs/{person_id}",
                headers=headers,
            )
            assert runs_response.status_code == 200, runs_response.text
            runs = runs_response.json()
            persisted = next((row for row in runs if row.get("id") == delta_run_id), None)
            assert persisted is not None, runs
            assert persisted.get("source_event_id") == event_id, persisted
            assert persisted.get("model_name") == provenance["model"], persisted
            assert persisted.get("delta") == result, persisted

            with TestingSessionLocal() as db:
                original = db.query(CareEvent).filter(
                    CareEvent.id == event_id,
                    CareEvent.person_id == person_id,
                    CareEvent.created_by_caregiver_id == caregiver_id,
                ).one()
                assert original.description == source_text
                assert original.status == "recorded"
                accepted_assertions = db.query(CareAssertion).filter(
                    CareAssertion.person_id == person_id,
                    CareAssertion.assertion_status == "accepted",
                ).all()
                assert accepted_assertions == [], (
                    "Live Qwen Delta reconciliation must not create accepted LCR assertions "
                    "without caregiver review.",
                    [row.id for row in accepted_assertions],
                )
                source_assertions = db.query(CareAssertion).filter(
                    CareAssertion.person_id == person_id,
                    CareAssertion.source_event_id == event_id,
                ).all()
                assert all(
                    row.assertion_status != "accepted" and row.epistemic_state != "verified"
                    for row in source_assertions
                ), [
                    {"id": row.id, "status": row.assertion_status, "epistemic_state": row.epistemic_state}
                    for row in source_assertions
                ]

            assert elapsed_ms <= 150_000, (
                f"Real Delta Engine reconciliation took {elapsed_ms}ms; "
                "the acceptance budget is 150000ms."
            )
            print(
                "LIVE_QWEN_DELTA_RECONCILIATION="
                f"PASS model={provenance['model']} elapsed_ms={elapsed_ms} "
                f"persisted_run={delta_run_id} source_event={event_id}"
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
