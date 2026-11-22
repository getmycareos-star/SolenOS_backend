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
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenSafetyEvaluation, QwenJobEvent
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


def test_companion_is_conversation_only_and_never_creates_lcr_proposals(monkeypatch):
    source = "Her sleep has been worse since last Friday."
    client = setup_client(monkeypatch, lambda text: {"observations": [{"source_quote": source}]})
    captured = []

    def fake_chat(base, model, messages, options=None):
        captured.append(messages)
        return "I can help explain what is already saved. This conversation does not change the care record."

    monkeypatch.setattr(collaboration, "_ollama_chat", fake_chat)
    try:
        first = create_session(client, "Caregiver one")
        headers = {"Authorization": "Bearer " + first["access_token"]}
        payload = {"person_id": first["person"]["id"], "text": source, "client_turn_id": "companion-conversation-only-0001"}
        response = client.post("/api/v1/conversation/message", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        first_prompt = "\n".join(str(item.get("content", "")) for item in captured[0])
        assert "new observation, event, or change" in first_prompt
        assert "Daily Care" in first_prompt
        assert "Do not collect a second structured care entry in chat" in first_prompt
        body = response.json()
        assert body["care_update_candidates"] == []
        assert body["written_to_lcr"] is False
        assert body["persistence"] == "conversation_only"

        # Conversation discovery is server-backed, so a refreshed bearer or
        # a new device does not depend on the old browser's session-ID cache.
        listed = client.get("/api/v1/conversations?limit=10", headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["person_id"] == first["person"]["id"]
        assert [item["session_id"] for item in listed.json()["items"]] == [body["session_id"]]
        rotated = client.post("/api/v1/sessions/refresh", headers=headers)
        assert rotated.status_code == 200, rotated.text
        rotated_headers = {"Authorization": "Bearer " + rotated.json()["access_token"]}
        headers = rotated_headers
        restored_listing = client.get("/api/v1/conversations?limit=10", headers=rotated_headers)
        assert restored_listing.status_code == 200, restored_listing.text
        assert restored_listing.json()["items"][0]["session_id"] == body["session_id"]

        # A legacy pending proposal must not leak back into the conversation
        # response when an idempotent retry replays the saved assistant turn.
        db = TestingSessionLocal()
        try:
            user_message = db.query(ConversationMessage).filter_by(
                session_id=body["session_id"], role="user", content=source
            ).one()
            db.add(AIOutputLCRProposal(
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                proposal_type="companion_care_observation",
                proposed_change_json={"field": "sleep_pattern", "value": "worse"},
                source_event_ids_json=[],
                source_evidence_ids_json=[],
                rationale="Legacy proposal created before the conversation-only boundary.",
                uncertainty_json={},
                source_conversation_id=body["session_id"],
                source_message_id=user_message.id,
                status="pending_authorization",
            ))
            db.commit()
        finally:
            db.close()

        replay = client.post("/api/v1/conversation/message", headers=headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["care_update_candidates"] == []

        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).filter_by(session_id=body["session_id"], role="user", content=source).count() == 1
            assert db.query(AIOutputLCRProposal).filter_by(person_id=first["person"]["id"]).count() == 1
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()

        second = create_session(client, "Caregiver two")
        second_headers = {"Authorization": "Bearer " + second["access_token"]}
        other = client.post("/api/v1/conversation/message", headers=second_headers, json={
            "person_id": second["person"]["id"], "text": "What is already saved about sleep?",
        })
        assert other.status_code == 200, other.text
        second_list = client.get("/api/v1/conversations?limit=10", headers=second_headers)
        assert second_list.status_code == 200, second_list.text
        assert all(item["session_id"] != body["session_id"] for item in second_list.json()["items"])
        second_prompt = "\\n".join(str(item.get("content", "")) for item in captured[-1])
        assert source not in second_prompt
        db = TestingSessionLocal()
        try:
            assert db.query(AIOutputLCRProposal).filter_by(person_id=second["person"]["id"]).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=second["person"]["id"]).count() == 0
        finally:
            db.close()
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


def test_companion_care_report_requires_explicit_creation_and_separate_acceptance(monkeypatch):
    source = "Her sleep has been worse since last Friday."
    client = setup_client(monkeypatch, lambda text: {"observations": []})
    captured_prompts = []
    monkeypatch.setattr(
        collaboration,
        "_ollama_chat",
        lambda base, model, messages, options=None: (
            captured_prompts.append(messages)
            or "I can explain what the saved care report says."
        ),
    )
    try:
        first = create_session(client, "Caregiver one")
        headers = {"Authorization": f"Bearer {first['access_token']}"}
        response = send(client, first, source)
        assert response.status_code == 200, response.text
        body = response.json()
        message_id = body["source_message_id"]
        assert message_id
        assert body["written_to_lcr"] is False
        assert body["persistence"] == "conversation_only"

        missing_confirmation = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": False},
        )
        assert missing_confirmation.status_code == 422

        created = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert created.status_code == 200, created.text
        proposal = created.json()["proposal"]
        assert proposal["status"] == "pending_authorization"
        assert proposal["proposed_change"]["asserted_value"] == source
        assert proposal["proposed_change"]["source_quote"] == source
        assert proposal["proposed_change"]["information_status"] == "caregiver_reported_pending_review"
        assert created.json()["idempotent_replay"] is False

        replay_create = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert replay_create.status_code == 200, replay_create.text
        assert replay_create.json()["idempotent_replay"] is True
        assert replay_create.json()["proposal"]["id"] == proposal["id"]

        queued = pending(client, first)
        assert len(queued) == 1
        assert queued[0]["id"] == proposal["id"]

        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()

        second = create_session(client, "Caregiver two")
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        cross_person_create = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=second_headers,
            json={"confirm_care_report": True},
        )
        assert cross_person_create.status_code == 404
        assert pending(client, second) == []
        cross_person_review = review(client, second, proposal["id"], {"decision": "accept"})
        assert cross_person_review.status_code == 404

        accepted = review(client, first, proposal["id"], {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        accepted_body = accepted.json()
        assert accepted_body["written_to_lcr"] is True
        assert accepted_body["decision"] == "accept"
        assertion_id = accepted_body["assertion_id"]
        assert assertion_id

        replay_review = review(client, first, proposal["id"], {"decision": "accept"})
        assert replay_review.status_code == 200, replay_review.text
        assert replay_review.json()["idempotent_replay"] is True
        assert replay_review.json()["status"] == "accepted"
        assert replay_review.json()["decision"] == "accept"
        assert replay_review.json()["written_to_lcr"] is True
        assert replay_review.json()["assertion_id"] == assertion_id

        current_care = client.get("/api/v1/care-continuity/current-care", headers=headers)
        assert current_care.status_code == 200, current_care.text
        assert any(item.get("id") == assertion_id for item in current_care.json().get("items", []))

        # Rejection is also a terminal, idempotent review receipt and must never
        # create a CareAssertion.
        second_source = "She did not finish lunch yesterday."
        second_message = send(client, first, second_source)
        assert second_message.status_code == 200, second_message.text
        second_message_id = second_message.json()["source_message_id"]
        second_created = client.post(
            f"/api/v1/companion/care-updates/from-message/{second_message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert second_created.status_code == 200, second_created.text
        second_proposal_id = second_created.json()["proposal"]["id"]
        rejected = review(client, first, second_proposal_id, {"decision": "reject"})
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decision"] == "reject"
        assert rejected.json()["written_to_lcr"] is False
        rejected_replay = review(client, first, second_proposal_id, {"decision": "reject"})
        assert rejected_replay.status_code == 200, rejected_replay.text
        assert rejected_replay.json()["idempotent_replay"] is True
        assert rejected_replay.json()["status"] == "rejected"
        assert rejected_replay.json()["decision"] == "reject"
        assert rejected_replay.json()["written_to_lcr"] is False
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 1
        finally:
            db.close()

        prompt_count_before_readback = len(captured_prompts)
        follow_up = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": first["person"]["id"],
                "text": "What have I told you about her sleep?",
                # Deliberately omit session_id: retrieval must work in a new
                # conversation, not by replaying the original chat context.
                "client_turn_id": "companion-report-readback-0001",
            },
        )
        assert follow_up.status_code == 200, follow_up.text
        assert follow_up.json()["session_id"] != body["session_id"]
        assert source in follow_up.json()["reply"], (
            "when Companion inference returns no source-grounded answer, the saved caregiver report must be quoted"
        )
        assert follow_up.json()["ai_generated"] is False
        assert follow_up.json()["response_mode"] == "source_quoted_care_history"
        assert len(captured_prompts) == prompt_count_before_readback, "saved-history readback must not require model inference"

        prompt_count_before_natural_recall = len(captured_prompts)
        natural_recall = client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": first["person"]["id"],
                "text": "What did I mention about her sleep?",
                "client_turn_id": "companion-report-readback-0002",
            },
        )
        assert natural_recall.status_code == 200, natural_recall.text
        assert source in natural_recall.json()["reply"]
        assert natural_recall.json()["ai_generated"] is False
        assert natural_recall.json()["response_mode"] == "source_quoted_care_history"
        assert len(captured_prompts) == prompt_count_before_natural_recall, "natural saved-history readback must not require model inference"

        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(id=assertion_id).one()
            assert assertion.person_id == first["person"]["id"]
            assert assertion.asserted_value == source
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.epistemic_state == "reported"
            assert assertion.source_message_id == message_id
            assert assertion.source_proposal_id == proposal["id"]
            assert assertion.source_conversation_id == body["session_id"]
            saved_proposal = db.query(AIOutputLCRProposal).filter_by(id=proposal["id"]).one()
            assert saved_proposal.status == "accepted"
            decisions = db.query(AIOutputLCRDecision).filter_by(proposal_id=proposal["id"]).all()
            assert len(decisions) == 1
            assert decisions[0].decision == "accepted"
        finally:
            db.close()
    finally:
        teardown()


def test_corrected_companion_report_is_versioned_reviewable_and_audited(monkeypatch):
    client = setup_client(monkeypatch, lambda text: {"observations": []})
    try:
        first = create_session(client, "Correction reviewer")
        headers = {"Authorization": f"Bearer {first['access_token']}"}
        source = "She had trouble sleeping for three nights."
        sent = send(client, first, source)
        assert sent.status_code == 200, sent.text
        message_id = sent.json()["source_message_id"]

        created = client.post(
            f"/api/v1/companion/care-updates/from-message/{message_id}",
            headers=headers,
            json={"confirm_care_report": True},
        )
        assert created.status_code == 200, created.text
        original = created.json()["proposal"]
        original_id = original["id"]

        corrected_text = "She had trouble sleeping for two nights."
        correction_context = "The corrected report is: She had trouble sleeping for two nights. The earlier duration of three nights was incorrect."
        corrected = review(client, first, original_id, {
            "decision": "correct",
            "corrected_text": corrected_text,
            "correction_context": correction_context,
        })
        assert corrected.status_code == 200, corrected.text
        body = corrected.json()
        assert body["decision"] == "correct"
        assert body["proposal"]["status"] == "corrected"
        assert body["corrected_proposal"]["status"] == "pending_authorization"
        assert body["corrected_proposal"]["proposal_version"] == 2
        corrected_id = body["corrected_proposal"]["id"]

        # A correction creates a new pending version; it does not silently
        # write the original or corrected wording into the canonical LCR.
        pending_response = client.get("/api/v1/companion/care-updates", headers=headers)
        assert pending_response.status_code == 200, pending_response.text
        assert [item["id"] for item in pending_response.json()["items"]] == [corrected_id]
        all_versions = client.get(
            "/api/v1/companion/care-updates?include_reviewed=true",
            headers=headers,
        )
        assert all_versions.status_code == 200, all_versions.text
        versions = {item["id"]: item for item in all_versions.json()["items"]}
        assert list(versions) == [original_id]
        assert versions[original_id]["status"] == "corrected"
        assert corrected_id not in versions
        db = TestingSessionLocal()
        try:
            assert db.query(CareAssertion).filter_by(person_id=first["person"]["id"]).count() == 0
        finally:
            db.close()

        history = client.get(
            f"/api/v1/companion/care-updates/{original_id}/history",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        history_body = history.json()
        assert history_body["append_only"] is True
        assert len(history_body["items"]) == 1
        assert history_body["items"][0]["decision"] == "corrected"
        assert history_body["items"][0]["correction_context"] == correction_context

        second = create_session(client, "Unrelated reviewer")
        second_headers = {"Authorization": f"Bearer {second['access_token']}"}
        cross_record = client.get(
            f"/api/v1/companion/care-updates?include_reviewed=true",
            headers=second_headers,
        )
        assert cross_record.status_code == 200, cross_record.text
        assert cross_record.json()["items"] == []
        cross_history = client.get(
            f"/api/v1/companion/care-updates/{original_id}/history",
            headers=second_headers,
        )
        assert cross_history.status_code == 404

        accepted = review(client, first, corrected_id, {"decision": "accept"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["written_to_lcr"] is True
        db = TestingSessionLocal()
        try:
            assertion = db.query(CareAssertion).filter_by(
                id=accepted.json()["assertion_id"],
                person_id=first["person"]["id"],
            ).one()
            assert assertion.asserted_value == corrected_text
            assert assertion.epistemic_state == "reported"
            assert correction_context in assertion.source_text
        finally:
            db.close()

        corrected_history = client.get(
            f"/api/v1/companion/care-updates/{corrected_id}/history",
            headers=headers,
        )
        assert corrected_history.status_code == 200, corrected_history.text
        assert [item["decision"] for item in corrected_history.json()["items"]] == ["accepted"]
    finally:
        teardown()


def test_companion_conversation_history_returns_source_message_ids_and_enforces_scope(monkeypatch):
    client = setup_client(monkeypatch, lambda text: {"observations": []})
    try:
        first = create_session(client, "Conversation owner")
        source = "The person slept for six hours last night."
        sent = send(client, first, source)
        assert sent.status_code == 200, sent.text
        session_id = sent.json()["session_id"]
        headers = {"Authorization": f"Bearer {first['access_token']}"}

        history = client.get(f"/api/v1/conversation/{session_id}", headers=headers)
        assert history.status_code == 200, history.text
        body = history.json()
        assert body["session_id"] == session_id
        assert body["person_id"] == first["person"]["id"]
        user_messages = [item for item in body["messages"] if item["role"] == "user"]
        assistant_messages = [item for item in body["messages"] if item["role"] == "assistant"]
        assert len(user_messages) == 1
        assert user_messages[0]["text"] == source
        assert user_messages[0]["id"]
        assert user_messages[0]["source_message_id"] == user_messages[0]["id"]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["id"]
        assert assistant_messages[0]["source_message_id"] is None
        assert isinstance(assistant_messages[0]["lcr_context_coverage"], dict)
        assert isinstance(assistant_messages[0]["source_event_ids"], list)
        assert isinstance(assistant_messages[0]["source_evidence_ids"], list)

        second = create_session(client, "Unrelated caregiver")
        cross_record = client.get(
            f"/api/v1/conversation/{session_id}",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )
        assert cross_record.status_code == 404
    finally:
        teardown()

def test_two_week_review_decisions_are_scoped_and_append_only(monkeypatch):
    client = setup_client(monkeypatch, lambda text: {"observations": []})
    try:
        first = create_session(client, "Review caregiver")
        second = create_session(client, "Other caregiver")
        db = TestingSessionLocal()
        try:
            job = QwenBackgroundJob(
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                task_text="Review care information from the past fourteen days.",
                context_text="",
                workflow="two_week_review",
                status="completed",
            )
            db.add(job)
            db.flush()
            output = QwenModelOutput(
                job_id=job.id,
                person_id=first["person"]["id"],
                caregiver_id=first["caregiver"]["id"],
                workflow="two_week_review",
                model_name="qwen-test",
                output_text="A source-grounded two-week summary for testing.",
                output_metadata={"live_inference_confirmed": True},
                source_event_ids_json="[]",
                source_evidence_ids_json="[]",
                review_status="pending_review",
                review_required=True,
                written_to_lcr=False,
            )
            db.add(output)
            db.commit()
            job_id = job.id
            output_id = output.id
        finally:
            db.close()

        first_headers = {"Authorization": "Bearer " + first["access_token"]}
        second_headers = {"Authorization": "Bearer " + second["access_token"]}
        proposal_blocked = client.post(
            f"/api/v1/assistant/jobs/{job_id}/proposal",
            headers=first_headers,
            json={},
        )
        assert proposal_blocked.status_code == 409, proposal_blocked.text
        denied = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=second_headers,
            json={"decision": "reviewed"},
        )
        assert denied.status_code == 404

        reviewed = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=first_headers,
            json={"decision": "reviewed"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["review_status"] == "reviewed"
        assert reviewed.json()["review_required"] is False
        assert reviewed.json()["written_to_lcr"] is False
        assert reviewed.json()["idempotent_replay"] is False

        replay = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=first_headers,
            json={"decision": "reviewed"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent_replay"] is True

        changed = client.post(
            f"/api/v1/assistant/jobs/{job_id}/review",
            headers=first_headers,
            json={"decision": "needs_follow_up"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["review_status"] == "needs_follow_up"
        assert changed.json()["written_to_lcr"] is False

        status = client.get(
            f"/api/v1/assistant/jobs/{job_id}",
            headers=first_headers,
        )
        assert status.status_code == 200, status.text
        assert status.json()["review_required"] is False
        assert status.json()["output_review_status"] == "needs_follow_up"
        assert status.json()["written_to_lcr"] is False
        assert [entry["decision"] for entry in status.json()["review_history"]] == ["reviewed", "needs_follow_up"]

        history = client.get("/api/v1/assistant/reviews/two-week?limit=10", headers=first_headers)
        assert history.status_code == 200, history.text
        assert history.json()["count"] == 1
        assert history.json()["items"][0]["job_id"] == job_id
        assert history.json()["items"][0]["output_id"] == output_id
        assert [entry["decision"] for entry in history.json()["items"][0]["review_history"]] == ["reviewed", "needs_follow_up"]
        other_history = client.get("/api/v1/assistant/reviews/two-week?limit=10", headers=second_headers)
        assert other_history.status_code == 200, other_history.text
        assert other_history.json()["items"] == []

        db = TestingSessionLocal()
        try:
            saved = db.query(QwenModelOutput).filter_by(id=output_id).one()
            assert saved.review_status == "needs_follow_up"
            assert saved.review_required is False
            assert saved.written_to_lcr is False
            events = db.query(QwenJobEvent).filter_by(
                job_id=job_id,
                event_type="caregiver_review_decision",
            ).order_by(QwenJobEvent.created_at.asc()).all()
            assert [event.to_status for event in events] == ["reviewed", "needs_follow_up"]
            assert all(event.caregiver_id == first["caregiver"]["id"] for event in events)
            assert all(event.metadata_json["written_to_lcr"] is False for event in events)
        finally:
            db.close()
    finally:
        teardown()
