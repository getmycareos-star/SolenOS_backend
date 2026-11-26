"""PostgreSQL continuity and authorization tests for Companion conversations."""
import os
import uuid
import json
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires migrated PostgreSQL DATABASE_URL")

from app.main import app
from app.models.care import CareEvent, Evidence, Caregiver, Person
from app.models.collaboration import CompanionTurnLease, ConversationMessage, ConversationSession
from app.models.caregiver_access import CaregiverAccessGrant
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
            source_event_ids=[f"event-pass-{suffix}"],
            source_evidence_ids=[f"evidence-pass-{suffix}"],
            source_document_analysis_ids=[],
            source_appointment_ids=[],
            source_assertion_ids=[f"assertion-pass-{suffix}"],
            source_lcr_context_ids=[],
            source_freshness_assessment_ids=[],
        )
        # The successful audit must remain in the caller's transaction so
        # it can commit atomically with the conversation turns. A separate
        # connection must not observe it before that transaction commits.
        db.flush()
        with Session() as observer:
            assert observer.query(AISafetyCheck).filter_by(
                person_id=person_id,
                check_name="companion_response_grounding",
                result="passed",
            ).count() == 0
        db.commit()
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
    assert context["caregiver_relationship"] is None  # Generic "Family" is not a recorded specific relationship.
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
    assert "The record does not yet establish a recurring pattern." not in serialized
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
        second_session_id = f"session-only-caregiver-{suffix}"
        second_session_caregiver = Caregiver(
            id=second_session_id,
            name="Second caregiver",
            relationship="daughter",
            person_id=person_id,
        )
        db.add_all([other_caregiver, second_session_caregiver])
        db.flush()
        current_profile = db.query(CaregiverProfile).filter_by(
            person_id=person_id,
            session_caregiver_id=caregiver_id,
        ).one()
        other_profile = CaregiverProfile(
            person_id=person_id,
            session_caregiver_id=other_caregiver_id,
            name="Other caregiver",
            relationship="daughter",
        )
        db.add(other_profile)
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
                availability_notes="CURRENT CAREGIVER OWNER CONTEXT",
            ),
            CaregiverContext(
                id=f"context-own-profile-only-{suffix}",
                person_id=person_id,
                caregiver_id=current_profile.id,
                reported_by_caregiver_id=None,
                availability_notes="CURRENT CAREGIVER PROFILE ATTRIBUTION",
            ),
            CaregiverContext(
                id=f"context-own-session-only-{suffix}",
                person_id=person_id,
                caregiver_id=None,
                reported_by_caregiver_id=caregiver_id,
                availability_notes="CURRENT CAREGIVER SESSION ATTRIBUTION",
            ),
            CaregiverContext(
                id=f"context-mismatched-owner-{suffix}",
                person_id=person_id,
                caregiver_id=current_profile.id,
                reported_by_caregiver_id=other_caregiver_id,
                availability_notes="CONFLICTING CAREGIVER ATTRIBUTION",
            ),
            CaregiverContext(
                id=f"context-other-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                reported_by_caregiver_id=other_caregiver_id,
                availability_notes="OTHER CAREGIVER OWNER CONTEXT",
            ),
            CaregiverContext(
                id=f"context-session-mismatch-{suffix}",
                person_id=person_id,
                caregiver_id=other_profile.id,
                reported_by_caregiver_id=second_session_id,
                availability_notes="SECOND SESSION MISMATCH SENTINEL",
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
        assert "CURRENT CAREGIVER OWNER CONTEXT" in caregiver_context_text
        assert "CURRENT CAREGIVER PROFILE ATTRIBUTION" in caregiver_context_text
        assert "CURRENT CAREGIVER SESSION ATTRIBUTION" in caregiver_context_text
        assert "CONFLICTING CAREGIVER ATTRIBUTION" not in caregiver_context_text
        assert "OTHER CAREGIVER OWNER CONTEXT" not in caregiver_context_text

        session_only_context = build_lcr_context(
            db,
            person_id,
            caregiver_id=second_session_id,
            limit_per_source=50,
        )
        session_only_context_text = " ".join(
            str(item) for item in session_only_context["sources"]["caregiver_contexts"]["items"]
        )
        assert "SECOND SESSION MISMATCH SENTINEL" not in session_only_context_text


def test_companion_discards_inference_when_caregiver_access_is_revoked_mid_request(monkeypatch):
    """A grant revoked during model inference must block response persistence."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Revoked Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    inference_called = {"value": False}

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        inference_called["value"] = True
        # Simulate an administrator revoking access while the model is working.
        with Session() as revoke_db:
            grant = revoke_db.query(CaregiverAccessGrant).filter_by(
                person_id=person_id, caregiver_id=caregiver_id,
            ).one()
            grant.status = "revoked"
            grant.revoked_at = datetime.now(timezone.utc)
            revoke_db.commit()
        return "This answer must not be delivered after access is revoked."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    response = client.post(
        "/api/v1/conversation/message",
        headers={"Authorization": f"Bearer {created['access_token']}"},
        json={"person_id": person_id, "text": "Can you summarize the saved care information?"},
    )

    assert inference_called["value"] is True
    assert response.status_code == 403, response.text
    with Session() as db:
        assert db.query(ConversationMessage).join(
            ConversationSession, ConversationMessage.session_id == ConversationSession.id,
        ).filter(
            ConversationSession.person_id == person_id,
            ConversationSession.caregiver_id == caregiver_id,
        ).count() == 0


def test_companion_history_uses_stable_scoped_cursor_pages():
    """Older history pages neither duplicate nor omit tied recipient messages."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Paginated Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    token = created["access_token"]
    session_id = f"companion-history-{suffix}"
    now = datetime.now(timezone.utc).replace(microsecond=0)

    with Session() as db:
        db.add(ConversationSession(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id,
            title="History pagination", status="active", created_at=now,
        ))
        db.flush()
        for index in range(5):
            db.add(ConversationMessage(
                id=f"message-{index}-{suffix}", session_id=session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"history item {index}", created_at=now + timedelta(seconds=min(index, 3)),
                evidence_ids_json="[]", source_record_refs_json="[]",
            ))
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    first = client.get(f"/api/v1/conversation/{session_id}?limit=2", headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert [item["text"] for item in first_body["messages"]] == ["history item 3", "history item 4"]
    assert first_body["has_more"] is True
    assert first_body["next_before"] and first_body["next_before_id"]

    second = client.get(
        f"/api/v1/conversation/{session_id}",
        params={
            "limit": 2,
            "before": first_body["next_before"],
            "before_id": first_body["next_before_id"],
        },
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert [item["text"] for item in second_body["messages"]] == ["history item 1", "history item 2"]
    assert second_body["has_more"] is True

    third = client.get(
        f"/api/v1/conversation/{session_id}",
        params={
            "limit": 2,
            "before": second_body["next_before"],
            "before_id": second_body["next_before_id"],
        },
        headers=headers,
    )
    assert third.status_code == 200, third.text
    assert [item["text"] for item in third.json()["messages"]] == ["history item 0"]
    assert third.json()["has_more"] is False

    incomplete_cursor = client.get(
        f"/api/v1/conversation/{session_id}?before={first_body['next_before']}",
        headers=headers,
    )
    assert incomplete_cursor.status_code == 422

    other = _new_anonymous(client, f"Other pagination recipient {suffix}")
    denied = client.get(
        f"/api/v1/conversation/{session_id}",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert denied.status_code == 404


def test_companion_idempotent_replay_returns_only_the_persisted_turn_pair():
    """Retry payload size must not grow with unrelated older conversation history."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Replay bounded recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    session_id = f"companion-replay-{suffix}"
    turn_id = str(uuid.uuid4())
    source_message_id = f"source-{suffix}"
    assistant_message_id = f"assistant-{suffix}"
    now = datetime.now(timezone.utc)

    with Session() as db:
        db.add(ConversationSession(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id,
            title="Replay bounded history", status="active", created_at=now,
        ))
        db.flush()
        for index in range(25):
            db.add(ConversationMessage(
                id=f"older-{index}-{suffix}", session_id=session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"older history {index}", created_at=now + timedelta(seconds=index),
            ))
        db.add(ConversationMessage(
            id=source_message_id, session_id=session_id, role="user",
            content="Current caregiver question", created_at=now + timedelta(seconds=26),
        ))
        db.add(ConversationMessage(
            id=assistant_message_id, session_id=session_id, role="assistant",
            content="Current grounded response", client_turn_id=turn_id,
            response_metadata_json=json.dumps({
                "request_sha256": "stable-request-fingerprint",
                "source_message_id": source_message_id,
                "ai_generated": True,
            }),
            created_at=now + timedelta(seconds=26, microseconds=1),
        ))
        db.commit()

        replay = collaboration._companion_replay_response(
            db,
            client_turn_id=turn_id,
            request_sha256="stable-request-fingerprint",
            person_id=person_id,
            caregiver_id=caregiver_id,
            requested_session_id=session_id,
        )
        assert replay is not None
        assert replay["idempotent_replay"] is True
        assert [item["text"] for item in replay["messages"]] == [
            "Current caregiver question",
            "Current grounded response",
        ]


def test_concurrent_companion_retries_with_same_turn_id_run_inference_once(monkeypatch):
    """A same-turn retry waits for the original commit instead of duplicating inference."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Concurrent Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    turn_id = str(uuid.uuid4())
    calls = []
    calls_lock = threading.Lock()

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        with calls_lock:
            calls.append(messages)
        time.sleep(0.25)
        return "I cannot confirm that from the records retrieved for this response."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)

    def submit():
        return client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": person_id,
                "text": "What does the saved care information establish?",
                "client_turn_id": turn_id,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))

    assert [response.status_code for response in responses] == [200, 200], [r.text for r in responses]
    bodies = [response.json() for response in responses]
    assert bodies[0]["session_id"] == bodies[1]["session_id"]
    assert sorted(body["idempotent_replay"] for body in bodies) == [False, True]
    assert len(calls) == 1

    with Session() as db:
        assert db.query(ConversationSession).filter_by(
            person_id=person_id, caregiver_id=caregiver_id,
        ).count() == 1
        assert db.query(ConversationMessage).filter_by(
            session_id=bodies[0]["session_id"],
        ).count() == 2
        expected_lock_keys = [
            "idempotency:" + hashlib.sha256(turn_id.encode("utf-8")).hexdigest(),
            "conversation:" + hashlib.sha256(bodies[0]["session_id"].encode("utf-8")).hexdigest(),
        ]
        assert db.query(CompanionTurnLease).filter(
            CompanionTurnLease.lock_key.in_(expected_lock_keys),
        ).count() == 0


def test_distinct_companion_turns_in_one_conversation_are_serialized(monkeypatch):
    """A later turn cannot infer against a stale snapshot of the same thread."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Serialized Companion recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    session_id = f"companion-serialized-{suffix}"
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    first_started = threading.Event()
    release_first = threading.Event()
    observed_prompts = []
    prompts_lock = threading.Lock()

    with Session() as db:
        db.add(ConversationSession(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id,
            title="Serialized turns", status="active",
        ))
        db.commit()

    def fake_qwen(base, model, messages, options=None, audit_context=None):
        prompt_text = "\\n".join(str(item.get("content") or "") for item in messages)
        with prompts_lock:
            observed_prompts.append(prompt_text)
        if "FIRST TURN SENTINEL" in prompt_text:
            first_started.set()
            assert release_first.wait(timeout=10), "first turn was not released"
        return "I cannot confirm that from the records retrieved for this response."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://qwen.test", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)

    def submit(text, turn_id):
        return client.post(
            "/api/v1/conversation/message",
            headers=headers,
            json={
                "person_id": person_id,
                "text": text,
                "session_id": session_id,
                "client_turn_id": turn_id,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(submit, "FIRST TURN SENTINEL: what is saved?", str(uuid.uuid4()))
        assert first_started.wait(timeout=10), "first turn never reached model inference"
        second_future = pool.submit(submit, "SECOND TURN SENTINEL: what did I just ask?", str(uuid.uuid4()))
        time.sleep(0.25)
        with prompts_lock:
            assert not any("SECOND TURN SENTINEL" in prompt for prompt in observed_prompts), (
                "second turn reached inference before the first turn committed"
            )
        release_first.set()
        first_response = first_future.result(timeout=30)
        second_response = second_future.result(timeout=30)

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    with prompts_lock:
        second_prompts = [prompt for prompt in observed_prompts if "SECOND TURN SENTINEL" in prompt]
    assert second_prompts, "second turn did not reach inference"
    assert any("FIRST TURN SENTINEL" in prompt for prompt in second_prompts), (
        "second turn did not receive the first committed turn as prior conversation context"
    )
    with Session() as db:
        rows = db.query(ConversationMessage).filter_by(session_id=session_id).order_by(
            ConversationMessage.created_at.asc(), ConversationMessage.id.asc(),
        ).all()
        assert [row.role for row in rows] == ["user", "assistant", "user", "assistant"]
        conversation_lock_key = "conversation:" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        assert db.query(CompanionTurnLease).filter_by(lock_key=conversation_lock_key).count() == 0


def test_companion_history_requires_conversation_permission_independent_of_lcr_access():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation permission recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    session_id = f"conversation-permission-{suffix}"

    with Session() as db:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=person_id, caregiver_id=caregiver_id,
        ).one()
        grant.permissions = [
            permission for permission in (grant.permissions or [])
            if permission != "view_conversations"
        ]
        db.add(ConversationSession(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id,
            title="Restricted conversation", status="active",
        ))
        source_message_id = f"restricted-source-{suffix}"
        db.add(ConversationMessage(
            id=source_message_id, session_id=session_id, role="user",
            content="I noticed a change in sleep last night.",
        ))
        db.commit()

    listed = client.get("/api/v1/conversations", headers=headers)
    history = client.get(f"/api/v1/conversation/{session_id}", headers=headers)
    care_updates = client.get("/api/v1/companion/care-updates", headers=headers)
    create_update = client.post(
        f"/api/v1/companion/care-updates/from-message/{source_message_id}",
        headers=headers,
        json={"confirm_care_report": True},
    )
    sent = client.post(
        "/api/v1/conversation/message",
        headers=headers,
        json={
            "person_id": person_id,
            "text": "What is saved in the care record?",
            "session_id": session_id,
            "client_turn_id": str(uuid.uuid4()),
        },
    )
    assert listed.status_code == 403, listed.text
    assert history.status_code == 403, history.text
    assert care_updates.status_code == 403, care_updates.text
    assert create_update.status_code == 403, create_update.text
    assert sent.status_code == 403, sent.text

    with Session() as db:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=person_id, caregiver_id=caregiver_id,
        ).one()
        assert "view_lcr" in (grant.permissions or [])
        assert "view_conversations" not in (grant.permissions or [])


def test_companion_history_permission_does_not_grant_lcr_read_or_write():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation-only recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    session_id = f"conversation-only-{suffix}"
    source_message_id = f"conversation-only-source-{suffix}"

    with Session() as db:
        grant = db.query(CaregiverAccessGrant).filter_by(
            person_id=person_id, caregiver_id=caregiver_id,
        ).one()
        grant.permissions = [
            permission for permission in (grant.permissions or [])
            if permission != "view_lcr"
        ]
        db.add(ConversationSession(
            id=session_id, person_id=person_id, caregiver_id=caregiver_id,
            title="Conversation-only access", status="active",
        ))
        db.add(ConversationMessage(
            id=source_message_id, session_id=session_id, role="user",
            content="I noticed a change in sleep last night.",
        ))
        db.commit()

    listed = client.get("/api/v1/conversations", headers=headers)
    history = client.get(f"/api/v1/conversation/{session_id}", headers=headers)
    updates = client.get("/api/v1/companion/care-updates", headers=headers)
    proposal_response = client.post(
        f"/api/v1/companion/care-updates/from-message/{source_message_id}",
        headers=headers,
        json={"confirm_care_report": True},
    )
    assert listed.status_code == 200, listed.text
    assert history.status_code == 200, history.text
    assert updates.status_code == 200, updates.text
    assert proposal_response.status_code == 200 or proposal_response.status_code == 201, proposal_response.text
    proposal_id = proposal_response.json()["proposal"]["id"]

    accepted = client.post(
        f"/api/v1/companion/care-updates/{proposal_id}/review",
        headers=headers,
        json={"decision": "accept"},
    )
    sent = client.post(
        "/api/v1/conversation/message",
        headers=headers,
        json={
            "person_id": person_id,
            "text": "What is saved in the care record?",
            "session_id": session_id,
            "client_turn_id": str(uuid.uuid4()),
        },
    )
    assert accepted.status_code == 403, accepted.text
    assert sent.status_code == 403, sent.text


def test_companion_saved_conversation_list_paginates_with_recipient_scope():
    """Saved conversation navigation can reach older sessions without crossing records."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation list recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    token = created["access_token"]
    now = datetime.now(timezone.utc)

    with Session() as db:
        for index in range(3):
            session_id = f"conversation-list-{index}-{suffix}"
            db.add(ConversationSession(
                id=session_id, person_id=person_id, caregiver_id=caregiver_id,
                title=f"Saved conversation {index}", status="active",
                created_at=now + timedelta(seconds=index),
            ))
            db.flush()
            db.add(ConversationMessage(
                id=f"conversation-list-message-{index}-{suffix}",
                session_id=session_id, role="user", content=f"Conversation question {index}",
                created_at=now + timedelta(seconds=index),
            ))
        db.commit()

    headers = {"Authorization": f"Bearer {token}"}
    first = client.get("/api/v1/conversations?limit=2&offset=0", headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["has_more"] is True
    assert first_body["next_offset"] == 2

    second = client.get(
        "/api/v1/conversations",
        params={"limit": 2, "offset": first_body["next_offset"]},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["items"]) == 1
    assert second_body["has_more"] is False
    all_ids = [item["session_id"] for item in first_body["items"] + second_body["items"]]
    assert len(set(all_ids)) == 3

    other = _new_anonymous(client, f"Other conversation list recipient {suffix}")
    isolated = client.get(
        "/api/v1/conversations?limit=50",
        headers={"Authorization": f"Bearer {other['access_token']}"},
    )
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["items"] == []



def test_companion_conversation_list_cursor_is_stable_when_new_conversation_arrives():
    """A newly inserted latest session must not shift an already-issued page cursor."""
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation cursor recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    headers = {"Authorization": f"Bearer {created['access_token']}"}
    base_time = datetime.now(timezone.utc) - timedelta(days=1)

    with Session() as db:
        for index in range(3):
            session_id = f"cursor-page-{index}-{suffix}"
            stamp = base_time + timedelta(minutes=index)
            db.add(ConversationSession(
                id=session_id, person_id=person_id, caregiver_id=caregiver_id,
                title=f"Cursor conversation {index}", status="active", created_at=stamp,
            ))
            db.flush()
            db.add(ConversationMessage(
                id=f"cursor-page-message-{index}-{suffix}", session_id=session_id,
                role="user", content=f"Cursor question {index}", created_at=stamp,
            ))
        db.commit()

    first = client.get("/api/v1/conversations?limit=2", headers=headers)
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 2
    assert body["has_more"] is True
    cursor = body["next_cursor"]
    assert cursor and cursor["before_session_id"] == body["items"][-1]["session_id"]

    with Session() as db:
        newest = ConversationSession(
            id=f"cursor-newest-{suffix}", person_id=person_id, caregiver_id=caregiver_id,
            title="Arrived between pages", status="active", created_at=base_time + timedelta(days=1),
        )
        db.add(newest)
        db.flush()
        db.add(ConversationMessage(
            id=f"cursor-newest-message-{suffix}", session_id=newest.id,
            role="user", content="New conversation", created_at=base_time + timedelta(days=1),
        ))
        db.commit()

    second = client.get("/api/v1/conversations", params={"limit": 2, **cursor}, headers=headers)
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert [item["session_id"] for item in second_body["items"]] == [f"cursor-page-0-{suffix}"]
    assert f"cursor-newest-{suffix}" not in [item["session_id"] for item in second_body["items"]]


def test_companion_conversation_list_has_deterministic_order_when_timestamps_collide():
    suffix = uuid.uuid4().hex[:10]
    client = TestClient(app)
    created = _new_anonymous(client, f"Conversation tie-break recipient {suffix}")
    person_id = created["person"]["id"]
    caregiver_id = created["caregiver"]["id"]
    token = created["access_token"]
    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    session_ids = [f"tie-b-{suffix}", f"tie-a-{suffix}"]

    with Session() as db:
        for session_id in session_ids:
            db.add(ConversationSession(
                id=session_id,
                person_id=person_id,
                caregiver_id=caregiver_id,
                title=session_id,
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            ))
            db.flush()
            db.add(ConversationMessage(
                id=f"message-{session_id}",
                session_id=session_id,
                role="user",
                content="Same timestamp conversation",
                created_at=timestamp,
            ))
        db.commit()

    response = client.get(
        "/api/v1/conversations?limit=10",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    tied = [
        item["session_id"]
        for item in response.json()["items"]
        if item["session_id"] in set(session_ids)
    ]
    assert tied == sorted(session_ids)
