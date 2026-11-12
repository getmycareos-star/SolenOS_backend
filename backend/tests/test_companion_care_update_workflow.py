import json
from datetime import datetime, timezone
"""End-to-end Companion proposal review tests using isolated persistence."""
import importlib
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care_continuity import CareAssertion
from app.models.care import CareEvent
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenSafetyEvaluation
from app.models.collaboration import ConversationMessage

main_module = importlib.import_module("app.main")
collaboration = importlib.import_module("app.api.collaboration")
care_updates = importlib.import_module("app.services.companion_care_updates")
app = main_module.app
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_client(monkeypatch, extraction):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", lambda *a, **k: "I understand. I can help you keep the care information clear.")
    monkeypatch.setattr(care_updates, "extract_companion_care_candidates", lambda source: extraction(source))
    return TestClient(app)


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_session(client, name):
    response = client.post("/api/v1/sessions/anonymous", json={"caregiver_name": name})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, session, message):
    return client.post("/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        json={"person_id": session["person"]["id"], "text": message})


def review(client, session, proposal_id, payload):
    return client.post(f"/api/v1/companion/care-updates/{proposal_id}/review",
        headers={"Authorization": f"Bearer {session['access_token']}"}, json=payload)


def pending(client, session):
    response = client.get(
        "/api/v1/companion/care-updates",
        headers={"Authorization": f"Bearer {session['access_token']}"},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_companion_messages_remain_conversation_only(monkeypatch):
    extracted = []
    client = setup_client(
        monkeypatch,
        lambda text: extracted.append(text) or {"observations": [{"source_quote": text}]},
    )
    try:
        session = create_session(client, "Conversation-only caregiver")
        source = "The caregiver reports a change during the morning routine."
        response = send(client, session, source)
        assert response.status_code == 200, response.text
        assert response.json()["care_update_candidates"] == []
        assert pending(client, session) == []
        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).filter_by(role="user", content=source).count() == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=session["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        finally:
            db.close()
        assert extracted == []
    finally:
        teardown()


def test_companion_chat_does_not_trigger_background_care_extraction(monkeypatch):
    extracted = []
    client = setup_client(monkeypatch, lambda text: extracted.append(text) or {"observations": []})
    try:
        session = create_session(client, "Ordinary conversation")
        response = send(client, session, "What should I ask at the next appointment?")
        assert response.status_code == 200, response.text
        assert response.json()["persistence"] == "conversation_only"
        assert response.json()["written_to_lcr"] is False
        assert response.json()["care_update_candidates"] == []
        assert extracted == []
    finally:
        teardown()


def test_source_backed_qwen_summary_requires_review_and_reconciles_all_sources(monkeypatch):
    client = setup_client(monkeypatch, lambda text: {"observations": []})
    try:
        session = create_session(client, "Qwen summary review")
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]
        event_ids = ["qwen-source-event-1", "qwen-source-event-2"]
        job_id = "qwen-summary-job-1"
        output_id = "qwen-summary-output-1"
        proposal_id = "qwen-summary-proposal-1"
        generated = "The saved care record describes two caregiver-reported changes over the past week."

        db = TestingSessionLocal()
        try:
            now = datetime.now(timezone.utc)
            for index, event_id in enumerate(event_ids, start=1):
                db.add(CareEvent(
                    id=event_id,
                    person_id=person_id,
                    event_type="daily_care",
                    status="recorded",
                    occurred_at=now,
                    title=f"Saved source event {index}",
                    description=f"Caregiver-reported source event {index}.",
                    evidence_ids=[],
                    created_by_caregiver_id=caregiver_id,
                ))
            db.add(QwenBackgroundJob(
                id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                status="completed",
                task_text="Summarize the saved care record.",
                context_text="",
                workflow="living_care_record",
                result_text=generated,
                source_event_ids_json=json.dumps(event_ids),
                source_evidence_ids_json="[]",
            ))
            db.add(QwenModelOutput(
                id=output_id,
                job_id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                workflow="living_care_record",
                model_name="qwen-test",
                output_text=generated,
                source_event_ids_json=json.dumps(event_ids),
                source_evidence_ids_json="[]",
            ))
            db.add(QwenSafetyEvaluation(
                output_id=output_id,
                job_id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                risk_class="unassessed",
                decision="review_required",
                human_review_required=True,
            ))
            db.add(AIOutputLCRProposal(
                id=proposal_id,
                output_id=output_id,
                job_id=job_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                proposal_type="qwen_assistance_summary",
                proposed_change_json={
                    "asserted_value": generated,
                    "information_status": "ai_generated_interpretation_pending_caregiver_review",
                },
                source_event_ids_json=event_ids,
                source_evidence_ids_json=[],
                rationale="Source-backed Qwen interpretation pending caregiver review.",
                uncertainty_json={"epistemic_state": "inferred", "caregiver_approval_required": True},
                status="pending_authorization",
            ))
            db.commit()
        finally:
            db.close()

        headers = {"Authorization": f"Bearer {session['access_token']}"}
        before = client.get("/api/v1/care-continuity/current-care", headers=headers)
        assert before.status_code == 200, before.text
        assert generated not in before.text

        accepted = client.post(
            f"/api/v1/companion/care-updates/qwen/{proposal_id}/review",
            headers=headers,
            json={"decision": "accept"},
        )
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["written_to_lcr"] is True
        assert body["epistemic_state"] == "inferred"
        assert body["reconciliation"]["stages"]["reconciliation"] == "committed"
        assert body["reconciliation"]["source_event_ids"] == event_ids
        assert body["reconciliation"]["source_caregiver_id"] == caregiver_id
        assertion_id = body["assertion_id"]

        after = client.get("/api/v1/care-continuity/current-care", headers=headers)
        assert after.status_code == 200, after.text
        assert any(item.get("id") == assertion_id for item in after.json().get("items", []))

        replay = client.post(
            f"/api/v1/companion/care-updates/qwen/{proposal_id}/review",
            headers=headers,
            json={"decision": "accept"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True

        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.epistemic_state == "inferred"
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.source_proposal_id == proposal_id
            proposal = db.query(AIOutputLCRProposal).filter_by(id=proposal_id).one()
            assert proposal.status == "accepted"
            output = db.query(QwenModelOutput).filter_by(id=output_id).one()
            assert output.review_status == "accepted"
            assert output.written_to_lcr is True
            decisions = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal_id).all()
            assert len(decisions) == 1
            assert decisions[0].decision == "accepted"
        finally:
            db.close()
    finally:
        teardown()
