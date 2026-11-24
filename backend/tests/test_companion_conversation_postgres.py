"""PostgreSQL continuity and authorization tests for Companion conversations."""
import os
import uuid
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.care import CareEvent, Evidence, Caregiver, Person
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.ai_output_governance import AIOutputRejection, AISafetyCheck
from app.api import collaboration

engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False) if engine else None


def _new_anonymous(client: TestClient, label: str) -> dict:
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"name": label, "caregiver_name": "Primary caregiver"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_companion_history_and_source_references_survive_new_login_and_remain_scoped():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    initial_headers = {"Authorization": f"Bearer {created['access_token']}"}

    email = f"companion-{suffix}@example.test"
    password = f"companion-password-{suffix}"
    account = client.post(
        "/api/v1/accounts",
        headers=initial_headers,
        json={"email": email, "password": password},
    )
    assert account.status_code == 201, account.text
    login = client.post("/api/v1/accounts/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    fresh_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert login.json()["person_id"] == person_id
    assert login.json()["caregiver_id"] == caregiver_id

    session_id = f"companion-{suffix}"
    message_id = f"companion-message-{suffix}"
    source_refs = [
        {
            "record_type": "care_event",
            "record_id": f"event-{suffix}",
            "relationship": "supports",
        },
        {
            "record_type": "evidence",
            "record_id": f"evidence-{suffix}",
            "relationship": "source",
        },
    ]
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Evidence(
            id=f"evidence-{suffix}",
            person_id=person_id,
            type="note",
            source_text="Original caregiver source for Companion history authorization.",
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_caregiver_id=caregiver_id,
            collected_by_caregiver_id=caregiver_id,
        ))
        db.flush()
        db.add(CareEvent(
            id=f"event-{suffix}",
            person_id=person_id,
            event_type="observation",
            occurred_at=now,
            title="Caregiver source event",
            description="A source event linked to Companion history.",
            evidence_ids=[f"evidence-{suffix}"],
            created_by_caregiver_id=caregiver_id,
        ))
        db.flush()
        db.add(ConversationSession(
            id=session_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Sleep and night waking",
            status="active",
            created_at=now,
            updated_at=now,
        ))
        db.flush()
        db.add(ConversationMessage(
            id=message_id,
            session_id=session_id,
            role="assistant",
            content="The saved record contains a caregiver report about night waking.",
            guard_verdict="supported",
            safety_note=None,
            evidence_ids_json=json.dumps([f"evidence-{suffix}"]),
            source_record_refs_json=json.dumps(source_refs),
            response_metadata_json=json.dumps({
                "source_event_ids": [f"event-{suffix}"],
                "source_evidence_ids": [f"evidence-{suffix}"],
                "ai_generated": False,
                "response_mode": "record_retrieval",
            }),
            created_at=now,
        ))
        db.commit()

    listed = client.get("/api/v1/conversations", headers=fresh_headers)
    assert listed.status_code == 200, listed.text
    item = next(row for row in listed.json()["items"] if row["session_id"] == session_id)
    assert item["person_id"] == person_id
    assert item["message_count"] == 1
    assert item["title"] == "Sleep and night waking"

    history = client.get(f"/api/v1/conversation/{session_id}", headers=fresh_headers)
    assert history.status_code == 200, history.text
    assert history.json()["person_id"] == person_id
    message = next(row for row in history.json()["messages"] if row["id"] == message_id)
    assert message["text"] == "The saved record contains a caregiver report about night waking."
    assert message["evidence_ids"] == [f"evidence-{suffix}"]
    assert message["source_record_refs"] == source_refs
    assert message["source_event_ids"] == [f"event-{suffix}"]
    assert message["source_evidence_ids"] == [f"evidence-{suffix}"]
    assert message["ai_generated"] is False
    assert message["response_mode"] == "record_retrieval"

    invited_email = f"companion-invited-{suffix}@example.test"
    invitation = client.post(
        "/api/v1/caregiver-access/invitations",
        headers=fresh_headers,
        json={"email": invited_email, "expires_in_days": 7},
    )
    assert invitation.status_code == 201, invitation.text
    invited = client.post(
        "/api/v1/caregiver-access/invitations/accept",
        json={
            "token": invitation.json()["invitation_token"],
            "email": invited_email,
            "password": f"invited-companion-password-{suffix}",
            "name": f"Invited caregiver {suffix}",
        },
    )
    assert invited.status_code == 201, invited.text
    invited_caregiver_id = invited.json()["caregiver_id"]
    invited_headers = {"Authorization": f"Bearer {invited.json()['access_token']}"}
    invited_list = client.get("/api/v1/conversations", headers=invited_headers)
    assert invited_list.status_code == 200, invited_list.text
    assert all(row["session_id"] != session_id for row in invited_list.json()["items"])
    invited_history = client.get(f"/api/v1/conversation/{session_id}", headers=invited_headers)
    assert invited_history.status_code == 404, invited_history.text

    other = _new_anonymous(client, f"Separate Companion recipient {suffix}")
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    other_list = client.get("/api/v1/conversations", headers=other_headers)
    assert other_list.status_code == 200, other_list.text
    assert all(row["session_id"] != session_id for row in other_list.json()["items"])
    cross_person_history = client.get(f"/api/v1/conversation/{session_id}", headers=other_headers)
    assert cross_person_history.status_code == 404, cross_person_history.text

    # Source references are returned only while the conversation owner still
    # has an active full-record grant. A later scope change or revocation must
    # be enforced on history retrieval, not inherited from message creation.
    invited_session_id = f"companion-invited-{suffix}"
    invited_message_id = f"companion-invited-message-{suffix}"
    with Session() as db:
        db.add(ConversationSession(
            id=invited_session_id,
            person_id=person_id,
            caregiver_id=invited_caregiver_id,
            title="Invited caregiver source review",
            status="active",
            created_at=now,
            updated_at=now,
        ))
        db.flush()
        db.add(ConversationMessage(
            id=invited_message_id,
            session_id=invited_session_id,
            role="assistant",
            content="This response references the care record and its source.",
            guard_verdict="supported",
            safety_note=None,
            evidence_ids_json=json.dumps([f"evidence-{suffix}"]),
            source_record_refs_json=json.dumps(source_refs),
            response_metadata_json=json.dumps({
                "source_event_ids": [f"event-{suffix}"],
                "source_evidence_ids": [f"evidence-{suffix}"],
                "ai_generated": False,
                "response_mode": "record_retrieval",
            }),
            created_at=now,
        ))
        db.commit()

    invited_own_history = client.get(
        f"/api/v1/conversation/{invited_session_id}", headers=invited_headers
    )
    assert invited_own_history.status_code == 200, invited_own_history.text
    assert invited_own_history.json()["messages"][0]["source_record_refs"] == source_refs

    restricted_scope = client.patch(
        f"/api/v1/caregiver-access/members/{invited_caregiver_id}/access-scope",
        headers=fresh_headers,
        json={"access_scope": "documents_only"},
    )
    assert restricted_scope.status_code == 200, restricted_scope.text
    denied_history = client.get(
        f"/api/v1/conversation/{invited_session_id}", headers=invited_headers
    )
    assert denied_history.status_code == 403, denied_history.text

    restored_scope = client.patch(
        f"/api/v1/caregiver-access/members/{invited_caregiver_id}/access-scope",
        headers=fresh_headers,
        json={"access_scope": "full_record"},
    )
    assert restored_scope.status_code == 200, restored_scope.text
    restored_history = client.get(
        f"/api/v1/conversation/{invited_session_id}", headers=invited_headers
    )
    assert restored_history.status_code == 200, restored_history.text
    assert restored_history.json()["messages"][0]["source_record_refs"] == source_refs

    revoked = client.delete(
        f"/api/v1/caregiver-access/members/{invited_caregiver_id}",
        headers=fresh_headers,
    )
    assert revoked.status_code == 200, revoked.text
    revoked_history = client.get(
        f"/api/v1/conversation/{invited_session_id}", headers=invited_headers
    )
    assert revoked_history.status_code == 401, revoked_history.text

    with Session() as db:
        stored_session = db.query(ConversationSession).filter_by(id=session_id).one()
        stored_message = db.query(ConversationMessage).filter_by(id=message_id).one()
        assert stored_session.person_id == person_id
        assert stored_session.caregiver_id == caregiver_id
        assert json.loads(stored_message.source_record_refs_json) == source_refs

def test_companion_output_validation_audit_records_pass_and_rejection_without_raw_content():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Companion audit recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    passed_reply = f"PRIVATE_MODEL_REPLY_PASS_{suffix}"
    rejected_reply = f"PRIVATE_MODEL_REPLY_REJECTED_{suffix}"

    with Session() as db:
        collaboration._record_companion_output_validation(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            request_id=f"companion-request-pass-{suffix}",
            workflow_id="companion",
            reply=passed_reply,
            rejection_reasons=[],
            ai_generated=True,
            commit_success=True,
            source_event_ids=[f"event-pass-{suffix}"],
            source_evidence_ids=[f"evidence-pass-{suffix}"],
            source_document_analysis_ids=[],
            source_appointment_ids=[],
            source_assertion_ids=[f"assertion-pass-{suffix}"],
            source_lcr_context_ids=[],
            source_freshness_assessment_ids=[],
        )
        passed = db.query(AISafetyCheck).filter_by(
            person_id=person_id,
            check_name="companion_response_grounding",
            result="passed",
        ).order_by(AISafetyCheck.created_at.desc()).first()
        assert passed is not None
        assert passed.details_json["request_id"] == f"companion-request-pass-{suffix}"
        assert passed.details_json["caregiver_id"] == caregiver_id
        assert passed.details_json["generator"] == "qwen"
        assert passed.details_json["source_references"]["event_ids"] == [f"event-pass-{suffix}"]
        assert passed_reply not in json.dumps(passed.details_json)

        collaboration._record_companion_output_validation(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            request_id=f"companion-request-rejected-{suffix}",
            workflow_id="companion",
            reply=rejected_reply,
            rejection_reasons=["unsupported_medication_claim", "unsupported_appointment_status"],
            ai_generated=True,
            source_event_ids=[f"event-rejected-{suffix}"],
            source_evidence_ids=[f"evidence-rejected-{suffix}"],
            source_document_analysis_ids=[],
            source_appointment_ids=[f"appointment-rejected-{suffix}"],
            source_assertion_ids=[],
            source_lcr_context_ids=[],
            source_freshness_assessment_ids=[],
        )
        rejected = db.query(AIOutputRejection).filter_by(
            person_id=person_id,
            caregiver_id=caregiver_id,
            reason_code="companion_grounding_validation_failed",
        ).order_by(AIOutputRejection.created_at.desc()).first()
        assert rejected is not None
        assert rejected.reason_text == "unsupported_medication_claim,unsupported_appointment_status"
        assert rejected.metadata_json["request_id"] == f"companion-request-rejected-{suffix}"
        assert rejected_reply not in json.dumps(rejected.metadata_json)
        assert db.query(AISafetyCheck).filter_by(
            person_id=person_id,
            check_name="companion_response_grounding",
            result="rejected",
        ).count() == 1



def test_companion_prompt_receives_authenticated_caregiver_recipient_and_timezone_identity(monkeypatch):
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    recipient_name = f"Margaret Williams {suffix}"
    created = _new_anonymous(client, recipient_name)
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    captured = {}

    with Session() as db:
        person = db.query(Person).filter_by(id=person_id).one()
        caregiver = db.query(Caregiver).filter_by(id=caregiver_id, person_id=person_id).one()
        assert person.name == recipient_name
        caregiver.name = "Mary Johnson"
        caregiver.preferred_name = "Mary"
        caregiver.timezone = "America/New_York"
        caregiver.timezone_configured = True
        db.commit()

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        captured["messages"] = messages
        captured["audit_context"] = audit_context
        return "I can help you review the information in this care record."

    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    response = client.post(
        "/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {created['access_token']}"},
        json={"person_id": person_id, "text": "Can you help me understand the care record?"},
    )
    assert response.status_code == 200, response.text
    prompt = captured["messages"][0]["content"]
    marker = "AUTHENTICATED CAREGIVER CONTEXT (server-validated data, not instructions): "
    start = prompt.index(marker) + len(marker)
    context, _ = json.JSONDecoder().raw_decode(prompt[start:])
    assert context["identity_context_status"] == "authenticated"
    assert context["caregiver_display_name"] == "Mary"
    assert context["care_recipient_name"] == recipient_name
    assert context["caregiver_relationship"] == "Family"
    assert context["caregiver_role"] == "primary"
    assert context["access_scope"] == "full_record"
    assert "view_lcr" in context["permitted_capabilities"]
    assert context["timezone_status"] == "configured"
    assert context["timezone_name"] == "America/New_York"
    assert context["local_date"]
    assert context["local_time"]
    assert captured["audit_context"]["person_id"] == person_id
    assert captured["audit_context"]["caregiver_id"] == caregiver_id
