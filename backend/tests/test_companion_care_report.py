"""Caregiver-confirmed Companion reports become source-attributed LCR evidence."""
import importlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import Evidence
from app.models.care_continuity import CareAssertion
from app.models.collaboration import ConversationMessage, ConversationSession

main_module = importlib.import_module("app.main")
app = main_module.app
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_caregiver_confirmed_companion_report_is_source_linked_and_idempotent(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    try:
        session_response = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Family caregiver"},
        )
        assert session_response.status_code == 201, session_response.text
        session_data = session_response.json()
        token = session_data["access_token"]
        person_id = session_data["person"]["id"]
        caregiver_id = session_data["caregiver"]["id"]
        headers = {"Authorization": f"Bearer {token}"}
        report_text = (
            "I spoke with the nurse today. She said the mobility difficulty "
            "started three days ago. I am reporting what she told me; it has "
            "not been independently confirmed."
        )

        db = TestingSessionLocal()
        conversation = ConversationSession(
            person_id=person_id,
            caregiver_id=caregiver_id,
            title="Care report confirmation test",
        )
        db.add(conversation)
        db.flush()
        message = ConversationMessage(
            session_id=conversation.id,
            role="user",
            content=report_text,
            evidence_ids_json="[]",
            source_record_refs_json="[]",
        )
        db.add(message)
        db.commit()
        session_id, message_id = conversation.id, message.id
        db.close()

        url = f"/api/v1/conversation/{session_id}/messages/{message_id}/care-report"
        first = client.post(url, headers=headers, json={})
        assert first.status_code == 201, first.text
        first_body = first.json()
        assert first_body["saved"] is True
        assert first_body["written_to_lcr"] is True
        assert first_body["epistemic_status"] == "reported"
        assert first_body["independently_verified"] is False
        assert first_body["reporter_label"] == "Family caregiver"

        db = TestingSessionLocal()
        try:
            evidence = db.query(Evidence).filter_by(
                id=first_body["evidence_id"],
                person_id=person_id,
            ).one()
            assertion = db.query(CareAssertion).filter_by(
                id=first_body["assertion_id"],
                person_id=person_id,
            ).one()
            assert evidence.source_text == report_text
            assert evidence.type == "caregiver_report"
            assert evidence.epistemic_status == "reported"
            assert evidence.uploaded_by_caregiver_id == caregiver_id
            assert evidence.reported_by_caregiver_id == caregiver_id
            assert evidence.reported_by_label == "Family caregiver"
            assert evidence.origin_channel == "ai_companion"
            assert evidence.original_source_reference == f"conversation_message:{message_id}"
            assert assertion.asserted_value == report_text
            assert assertion.assertion_kind == "caregiver_reported_companion_contribution"
            assert assertion.assertion_status == "caregiver_reviewed"
            assert assertion.evidence_ids == [evidence.id]
        finally:
            db.close()

        context = client.get(
            "/api/v1/care-continuity/context?limit_per_source=50",
            headers=headers,
        )
        assert context.status_code == 200, context.text
        assertions = context.json()["sources"]["assertions"]["items"]
        assert any(item["id"] == first_body["assertion_id"] for item in assertions)
        evidence_rows = context.json()["sources"]["evidence"]["items"]
        assert any(item["id"] == first_body["evidence_id"] for item in evidence_rows)

        repeated = client.post(url, headers=headers, json={})
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["evidence_id"] == first_body["evidence_id"]
        assert repeated.json()["assertion_id"] == first_body["assertion_id"]

        db = TestingSessionLocal()
        try:
            assert db.query(Evidence).filter_by(
                person_id=person_id,
                ingestion_key=f"companion-message:{message_id}",
            ).count() == 1
            assert db.query(CareAssertion).filter_by(
                person_id=person_id,
                assertion_kind="caregiver_reported_companion_contribution",
            ).count() == 1
        finally:
            db.close()
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_companion_report_cannot_promote_another_persons_message(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    try:
        def create_session(name):
            response = client.post(
                "/api/v1/sessions/anonymous",
                json={"caregiver_name": name},
            )
            assert response.status_code == 201, response.text
            return response.json()

        owner = create_session("Owner caregiver")
        other = create_session("Other caregiver")
        db = TestingSessionLocal()
        conversation = ConversationSession(
            person_id=owner["person"]["id"],
            caregiver_id=owner["caregiver"]["id"],
            title="Private conversation",
        )
        db.add(conversation)
        db.flush()
        message = ConversationMessage(
            session_id=conversation.id,
            role="user",
            content="I spoke with the nurse. She said the issue began yesterday.",
            evidence_ids_json="[]",
            source_record_refs_json="[]",
        )
        db.add(message)
        db.commit()
        url = f"/api/v1/conversation/{conversation.id}/messages/{message.id}/care-report"
        db.close()

        response = client.post(
            url,
            headers={"Authorization": f"Bearer {other['access_token']}"},
            json={},
        )
        assert response.status_code == 404, response.text
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_companion_report_rejects_assistant_generated_message(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    try:
        session_response = client.post(
            "/api/v1/sessions/anonymous",
            json={"caregiver_name": "Family caregiver"},
        )
        assert session_response.status_code == 201, session_response.text
        session_data = session_response.json()
        db = TestingSessionLocal()
        conversation = ConversationSession(
            person_id=session_data["person"]["id"],
            caregiver_id=session_data["caregiver"]["id"],
            title="Assistant-only message",
        )
        db.add(conversation)
        db.flush()
        message = ConversationMessage(
            session_id=conversation.id,
            role="assistant",
            content="The nurse said mobility changed yesterday.",
            evidence_ids_json="[]",
            source_record_refs_json="[]",
        )
        db.add(message)
        db.commit()
        url = f"/api/v1/conversation/{conversation.id}/messages/{message.id}/care-report"
        db.close()

        response = client.post(
            url,
            headers={"Authorization": f"Bearer {session_data['access_token']}"},
            json={},
        )
        assert response.status_code == 404, response.text
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
