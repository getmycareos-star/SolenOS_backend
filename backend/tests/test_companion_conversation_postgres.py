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

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
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


def test_companion_retrieval_and_qwen_source_gate_exclude_caregiver_deleted_evidence():
    from fastapi import HTTPException

    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Deleted evidence recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    active_id = f"active-evidence-{suffix}"
    deleted_id = f"deleted-evidence-{suffix}"
    active_text = "Caregiver reported that Mum felt dizzy this morning."
    deleted_text = "Caregiver reported that Mum fell yesterday."

    with Session() as db:
        db.add_all([
            Evidence(
                id=active_id,
                person_id=person_id,
                type="note",
                source_text=active_text,
                uploaded_by_caregiver_id=caregiver_id,
            ),
            Evidence(
                id=deleted_id,
                person_id=person_id,
                type="note",
                source_text=deleted_text,
                uploaded_by_caregiver_id=caregiver_id,
                evidence_status="deleted_by_caregiver",
            ),
        ])
        db.commit()

        visible = collaboration._active_person_evidence_query(db, person_id).all()
        visible_ids = {str(item.id) for item in visible}
        assert active_id in visible_ids
        assert deleted_id not in visible_ids

        collaboration._require_owned_qwen_source(
            db, person_id, caregiver_id, active_id, active_text,
        )
        with pytest.raises(HTTPException) as deleted:
            collaboration._require_owned_qwen_source(
                db, person_id, caregiver_id, deleted_id, deleted_text,
            )
        assert deleted.value.status_code == 404



def test_companion_follow_up_uses_only_same_caregivers_prior_session_history(monkeypatch):
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation continuity recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    prior_session_id = f"prior-conversation-{suffix}"
    other_caregiver_id = f"other-caregiver-{suffix}"
    other_session_id = f"other-conversation-{suffix}"
    captured = {}

    with Session() as db:
        now = datetime.now(timezone.utc)
        db.add(Caregiver(
            id=other_caregiver_id,
            name="Other authorised caregiver",
            relationship="Son",
            person_id=person_id,
        ))
        db.flush()
        db.add_all([
            ConversationSession(
                id=prior_session_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                title="Earlier caregiver conversation",
                status="completed",
                created_at=now,
                updated_at=now,
            ),
            ConversationSession(
                id=other_session_id,
                person_id=person_id,
                caregiver_id=other_caregiver_id,
                title="Other caregiver conversation",
                status="active",
                created_at=now,
                updated_at=now,
            ),
        ])
        db.flush()
        db.add_all([
            ConversationMessage(
                id=f"prior-user-{suffix}",
                session_id=prior_session_id,
                role="user",
                content="Mum was awake repeatedly between midnight and 3 AM.",
                created_at=now,
            ),
            ConversationMessage(
                id=f"prior-assistant-{suffix}",
                session_id=prior_session_id,
                role="assistant",
                content="The record does not yet establish a recurring pattern.",
                created_at=now,
            ),
            ConversationMessage(
                id=f"other-user-{suffix}",
                session_id=other_session_id,
                role="user",
                content="PRIVATE OTHER CAREGIVER DISCLOSURE",
                created_at=now,
            ),
            ConversationMessage(
                id=f"other-assistant-{suffix}",
                session_id=other_session_id,
                role="assistant",
                content="PRIVATE OTHER CAREGIVER RESPONSE",
                created_at=now,
            ),
        ])
        db.commit()

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        captured["messages"] = messages
        return "I can help you look at the earlier note."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    response = client.post(
        "/api/v1/conversation/message",
        headers=headers,
        json={"person_id": person_id, "text": "What about that?"},
    )
    assert response.status_code == 200, response.text
    serialized = json.dumps(captured["messages"])
    assert "Mum was awake repeatedly between midnight and 3 AM." in serialized
    assert "The record does not yet establish a recurring pattern." in serialized
    assert "PRIVATE OTHER CAREGIVER DISCLOSURE" not in serialized
    assert "PRIVATE OTHER CAREGIVER RESPONSE" not in serialized



@pytest.mark.parametrize(
    ("message_text", "model_reply"),
    [("Thank you.", "You're welcome."), ("Goodbye.", "Goodbye for now.")],
)
def test_companion_acknowledgements_and_farewells_exclude_patient_context_and_lcr_sources(
    monkeypatch, message_text, model_reply,
):
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Social turn recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    captured = {}

    with Session() as db:
        db.add(CareEvent(
            id=f"private-social-event-{suffix}",
            person_id=person_id,
            event_type="observation",
            occurred_at=datetime.now(timezone.utc),
            title="PRIVATE CARE EVENT TITLE",
            description="PRIVATE CARE EVENT DETAILS MUST NOT REACH A COURTESY RESPONSE",
            created_by_caregiver_id=caregiver_id,
        ))
        db.commit()

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        captured["messages"] = messages
        captured["audit_context"] = audit_context
        return model_reply

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    response = client.post(
        "/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {created['access_token']}"},
        json={"person_id": person_id, "text": message_text},
    )
    assert response.status_code == 200, response.text
    serialized_prompt = json.dumps(captured["messages"])
    assert "PRIVATE CARE EVENT TITLE" not in serialized_prompt
    assert "PRIVATE CARE EVENT DETAILS" not in serialized_prompt
    assert response.json()["source_event_ids"] == []
    assert response.json()["source_evidence_ids"] == []
    assert response.json()["written_to_lcr"] is False
    assert captured["audit_context"]["source_event_ids"] == []
    assert captured["audit_context"]["source_evidence_ids"] == []



def test_lcr_context_excludes_other_caregivers_private_observations_perspectives_and_context():
    from app.core.caregiver_enums import ClaimType, InformationVisibility
    from app.models.caregiver_intelligence import (
        CaregiverContext,
        CaregiverObservation,
        CaregiverPerspective,
        CaregiverProfile,
    )
    from app.services.lcr_context import build_lcr_context

    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Private caregiver context recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    other_caregiver_id = f"private-context-other-{suffix}"

    with Session() as db:
        other_caregiver = Caregiver(
            id=other_caregiver_id,
            name="Other caregiver",
            relationship="daughter",
            person_id=person_id,
        )
        db.add(other_caregiver)
        db.flush()
        current_profile = CaregiverProfile(
            person_id=person_id,
            session_caregiver_id=caregiver_id,
            name="Current caregiver",
            relationship="daughter",
        )
        other_profile = CaregiverProfile(
            person_id=person_id,
            session_caregiver_id=other_caregiver_id,
            name="Other caregiver",
            relationship="daughter",
        )
        db.add_all([current_profile, other_profile])
        db.flush()

        db.add_all([
            CaregiverObservation(
                id=f"obs-own-private-{suffix}",
                person_id=person_id,
                caregiver_id=current_profile.id,
                observation_content="CURRENT CAREGIVER PRIVATE OBSERVATION",
                information_visibility=InformationVisibility.PRIVATE,
                is_promoted_to_shared=False,
            ),
            CaregiverObservation(
                id=f"obs-other-private-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                observation_content="OTHER CAREGIVER PRIVATE OBSERVATION",
                information_visibility=InformationVisibility.PRIVATE,
                is_promoted_to_shared=False,
            ),
            CaregiverObservation(
                id=f"obs-other-shared-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                observation_content="OTHER CAREGIVER EXPLICITLY SHARED OBSERVATION",
                information_visibility=InformationVisibility.SHARED,
                is_promoted_to_shared=True,
                promoted_by_caregiver_id=other_profile.id,
            ),
            CaregiverPerspective(
                id=f"perspective-own-private-{suffix}",
                person_id=person_id,
                caregiver_id=current_profile.id,
                claim_type=ClaimType.OBSERVATION,
                claim_content="CURRENT CAREGIVER PRIVATE PERSPECTIVE",
                information_visibility=InformationVisibility.PRIVATE,
                is_promoted_to_shared=False,
            ),
            CaregiverPerspective(
                id=f"perspective-other-private-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                claim_type=ClaimType.OBSERVATION,
                claim_content="OTHER CAREGIVER PRIVATE PERSPECTIVE",
                information_visibility=InformationVisibility.PRIVATE,
                is_promoted_to_shared=False,
            ),
            CaregiverPerspective(
                id=f"perspective-other-shared-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                claim_type=ClaimType.OBSERVATION,
                claim_content="OTHER CAREGIVER EXPLICITLY SHARED PERSPECTIVE",
                information_visibility=InformationVisibility.SHARED,
                is_promoted_to_shared=True,
                promoted_by_caregiver_id=other_profile.id,
            ),
            CaregiverContext(
                id=f"context-own-{suffix}",
                person_id=person_id,
                caregiver_id=current_profile.id,
                reported_by_caregiver_id=caregiver_id,
                availability_notes="CURRENT CAREGIVER PRIVATE CONTEXT",
            ),
            CaregiverContext(
                id=f"context-other-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                reported_by_caregiver_id=other_caregiver_id,
                availability_notes="OTHER CAREGIVER PRIVATE CONTEXT",
            ),
        ])
        db.commit()

        context = build_lcr_context(
            db,
            person_id,
            caregiver_id=caregiver_id,
            limit_per_source=50,
        )
        observations = context["sources"]["caregiver_observations"]["items"]
        perspectives = context["sources"]["caregiver_perspectives"]["items"]
        caregiver_contexts = context["sources"]["caregiver_contexts"]["items"]

        observation_text = " ".join(str(item) for item in observations)
        perspective_text = " ".join(str(item) for item in perspectives)
        caregiver_context_text = " ".join(str(item) for item in caregiver_contexts)
        assert "CURRENT CAREGIVER PRIVATE OBSERVATION" in observation_text
        assert "OTHER CAREGIVER EXPLICITLY SHARED OBSERVATION" in observation_text
        assert "OTHER CAREGIVER PRIVATE OBSERVATION" not in observation_text
        assert "CURRENT CAREGIVER PRIVATE PERSPECTIVE" in perspective_text
        assert "OTHER CAREGIVER EXPLICITLY SHARED PERSPECTIVE" in perspective_text
        assert "OTHER CAREGIVER PRIVATE PERSPECTIVE" not in perspective_text
        assert "CURRENT CAREGIVER PRIVATE CONTEXT" in caregiver_context_text
        assert "OTHER CAREGIVER PRIVATE CONTEXT" not in caregiver_context_text
