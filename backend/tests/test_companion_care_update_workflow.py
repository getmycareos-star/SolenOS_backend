"""End-to-end Companion proposal review tests using isolated persistence."""
import importlib
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base, get_db
from app.models.ai_output_governance import AIOutputLCRDecision, AIOutputLCRProposal
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage

main_module = importlib.import_module("app.main")
collaboration = importlib.import_module("app.api.collaboration")
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
    monkeypatch.setattr(collaboration, "extract_companion_care_candidates", lambda source: extraction(source))
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


def test_companion_message_persists_source_and_only_acceptance_creates_assertion(monkeypatch):
    source = "The caregiver reports a new change during the morning routine."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}] if text == source else []})
    try:
        session = create_session(client, "Caregiver one")
        response = send(client, session, source)
        assert response.status_code == 200, response.text
        proposal_data = response.json()["care_update_candidates"][0]
        assert proposal_data["status"] == "pending_authorization"
        assert proposal_data["proposed_change"]["asserted_value"] == source
        db = TestingSessionLocal()
        try:
            original = db.query(ConversationMessage).filter_by(id=proposal_data["source_message_id"], role="user").one()
            proposal = db.query(AIOutputLCRProposal).filter_by(id=proposal_data["id"]).one()
            assert original.content == source
            assert proposal.source_message_id == original.id
            assert proposal.source_conversation_id == response.json()["session_id"]
            assert db.query(CareAssertion).filter_by(person_id=session["person"]["id"]).count() == 0
        finally:
            db.close()
        accepted = review(client, session, proposal_data["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        assertion_id = accepted.json()["assertion_id"]
        assert assertion_id
        replay = review(client, session, proposal_data["id"], {"decision": "accept"})
        assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
        current_care = client.get("/api/v1/care-continuity/current-care", headers={"Authorization": f"Bearer {session['access_token']}"})
        assert current_care.status_code == 200, current_care.text
        assert any(item.get("id") == assertion_id for item in current_care.json().get("items", []))
        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.source_message_id == proposal_data["source_message_id"]
            assert assertion.source_conversation_id == response.json()["session_id"]
            assert assertion.source_proposal_id == proposal_data["id"]
            assert assertion.asserted_value == source
            assert assertion.created_by_caregiver_id == session["caregiver"]["id"]
            assert assertion.assertion_status == "caregiver_reviewed"
            decision = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal_data["id"]).one()
            assert decision.decision == "accepted"
            assert decision.reviewer_caregiver_id == session["caregiver"]["id"]
        finally:
            db.close()
    finally:
        teardown()


def test_correction_preserves_original_and_requires_separate_acceptance(monkeypatch):
    source = "A caregiver recorded an observation during today's visit."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}] if text == source else []})
    try:
        session = create_session(client, "Caregiver correction")
        proposal = send(client, session, source).json()["care_update_candidates"][0]
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
        proposal = send(client, session, source).json()["care_update_candidates"][0]
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
        proposal = send(client, first, source).json()["care_update_candidates"][0]
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
