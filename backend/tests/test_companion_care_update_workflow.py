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


def test_companion_message_is_conversation_only_and_never_creates_lcr_proposal(monkeypatch):
    source = "The caregiver reports a new change during the morning routine."
    client = setup_client(monkeypatch, lambda text: (_ for _ in ()).throw(AssertionError("Companion must not run care-update extraction")))
    try:
        session = create_session(client, "Conversation only")
        response = send(client, session, source)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["care_update_candidates"] == []
        assert body["written_to_lcr"] is False
        assert body["persistence"] == "conversation_only"
        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).filter_by(role="user", content=source).count() == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=session["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        finally:
            db.close()
    finally:
        teardown()


def create_legacy_proposal_for_review(session, response, source):
    db = TestingSessionLocal()
    try:
        original = db.query(ConversationMessage).filter_by(
            session_id=response.json()["session_id"], role="user", content=source,
        ).one()
        proposals = care_updates.persist_companion_candidates(
            db,
            person_id=session["person"]["id"],
            caregiver_id=session["caregiver"]["id"],
            conversation_id=response.json()["session_id"],
            message_id=original.id,
            source_text=source,
            extraction={"observations": [{"source_quote": source}]},
        )
        assert proposals
        return proposals[0]
    finally:
        db.close()


def test_correction_preserves_original_and_requires_separate_acceptance(monkeypatch):
    source = "A caregiver recorded an observation during today's visit."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}] if text == source else []})
    try:
        session = create_session(client, "Caregiver correction")
        send_response = send(client, session, source)
        assert send_response.status_code == 200, send_response.text
        proposal = create_legacy_proposal_for_review(session, send_response, source)
        corrected_text = "A caregiver recorded a revised observation during today's visit."
        correction_context = "The caregiver clarifies the revised observation: " + corrected_text
        corrected = review(client, session, proposal["id"], {
            "decision": "correct", "corrected_text": corrected_text, "correction_context": correction_context,
        })
        assert corrected.status_code == 200, corrected.text
        body = corrected.json()
        correction_replay = review(client, session, proposal["id"], {
            "decision": "correct", "corrected_text": corrected_text, "correction_context": correction_context,
        })
        assert correction_replay.status_code == 200, correction_replay.text
        assert correction_replay.json()["idempotent_replay"] is True
        assert body["proposal"]["status"] == "corrected"
        new = body["corrected_proposal"]
        assert new["status"] == "pending_authorization" and new["proposal_version"] == 2
        assert new["proposed_change"]["asserted_value"] == corrected_text
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
            old = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            assert old.proposed_change_json["asserted_value"] == source
            assert db.query(AIOutputLCRDecision).filter_by(proposal_id=old.id).one().decision == "corrected"
        finally:
            db.close()
        accepted = review(client, session, new["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(source_proposal_id=new["id"]).one()
            assert assertion.asserted_value == corrected_text
            assert source in assertion.source_text and correction_context in assertion.source_text
        finally:
            db.close()
    finally:
        teardown()


def test_rejection_never_creates_assertion_or_allows_later_acceptance(monkeypatch):
    source = "The caregiver reports an event that should not enter the record."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}] if text == source else []})
    try:
        session = create_session(client, "Caregiver rejection")
        send_response = send(client, session, source)
        assert send_response.status_code == 200, send_response.text
        proposal = pending(client, session)[0]
        rejected = review(client, session, proposal["id"], {"decision": "reject"})
        assert rejected.status_code == 200 and rejected.json()["written_to_lcr"] is False
        rejection_replay = review(client, session, proposal["id"], {"decision": "reject"})
        assert rejection_replay.status_code == 200 and rejection_replay.json()["idempotent_replay"] is True
        retry = review(client, session, proposal["id"], {"decision": "accept"})
        assert retry.status_code == 409
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
            stored = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            assert stored.status == "rejected"
            decisions = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal["id"]).all()
            assert len(decisions) == 1 and decisions[0].decision == "rejected"
        finally:
            db.close()
    finally:
        teardown()


def test_proposals_are_person_scoped_and_candidate_inference_failure_keeps_message(monkeypatch):
    source = "A caregiver records an observation for their own care recipient."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}] if text == source else []})
    try:
        first = create_session(client, "First caregiver")
        second = create_session(client, "Second caregiver")
        send_response = send(client, first, source)
        assert send_response.status_code == 200, send_response.text
        proposal = create_legacy_proposal_for_review(first, send_response, source)
        foreign = review(client, second, proposal["id"], {"decision": "accept"})
        assert foreign.status_code == 404
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()
    finally:
        teardown()

    failing = setup_client(monkeypatch, lambda text: (_ for _ in ()).throw(RuntimeError("Qwen unavailable")))
    try:
        session = create_session(failing, "Inference failure")
        response = send(failing, session, "A caregiver shares a detail about an actual care experience.")
        assert response.status_code == 200, response.text
        assert response.json()["care_update_candidates"] == []
        assert response.json()["persistence"] == "conversation_only"
        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).filter_by(role="user").count() == 1
            assert db.query(AIOutputLCRProposal).count() == 0
        finally:
            db.close()
    finally:
        teardown()



def test_ordinary_conversation_and_ungrounded_model_output_create_no_candidate(monkeypatch):
    source = "What is dementia, and what should I ask at the appointment?"
    client = setup_client(
        monkeypatch,
        lambda text: {"observations": [{"source_quote": "A fabricated fall occurred yesterday."}]},
    )
    try:
        session = create_session(client, "Caregiver source validation")
        response = send(client, session, source)
        assert response.status_code == 200, response.text
        assert response.json()["care_update_candidates"] == []
        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).filter_by(role="user", content=source).count() == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=session["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        finally:
            db.close()
    finally:
        teardown()



def test_idempotent_accept_replay_does_not_report_rejected_assertion_as_active_lcr_write(monkeypatch):
    source = "The caregiver reports a new observation that is later withdrawn."
    client = setup_client(
        monkeypatch,
        lambda text: {"observations": [{"source_quote": source}] if text == source else []},
    )
    try:
        session = create_session(client, "Caregiver replay status")
        response = send(client, session, source)
        assert response.status_code == 200, response.text
        proposal = create_legacy_proposal_for_review(session, response, source)
        accepted = review(client, session, proposal["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        assertion_id = accepted.json()["assertion_id"]
        assert assertion_id

        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assertion.assertion_status = "rejected"
            db.commit()
        finally:
            db.close()

        replay = review(client, session, proposal["id"], {"decision": "accept"})
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["written_to_lcr"] is False
        assert replay.json()["assertion_id"] is None
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
