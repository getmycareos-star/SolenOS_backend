"""Authenticated appointment-assist acceptance against a real Qwen model.

All synthetic identity and appointment rows live in an isolated PostgreSQL database.
No production care record is read or written.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import uuid
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import get_db
from app.models.care import CareEvent, Evidence
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.care_continuity import CareAssertion
from app.models.time_location import Appointment
from app.main import app
import app.main as main_module

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://"))
if os.getenv("SOLENOS_RUN_APPOINTMENT_ASSIST_LIVE_EVAL") == "1" and not POSTGRES_DATABASE:
    raise RuntimeError(
        "Live appointment-assist Qwen acceptance requires PostgreSQL; refusing a skipped SQLite run."
    )
pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_APPOINTMENT_ASSIST_LIVE_EVAL") != "1" or not POSTGRES_DATABASE,
    reason="requires the dedicated live-Qwen appointment-assist workflow and migrated PostgreSQL",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if POSTGRES_DATABASE else None
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine is not None else None


def override_db():
    if TestingSessionLocal is None:
        raise RuntimeError("Live appointment-assist acceptance requires PostgreSQL.")
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_authenticated_appointment_assist_uses_real_qwen_and_remains_read_only(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={
                "name": "Synthetic appointment evaluator",
                "caregiver_name": "Synthetic live-Qwen caregiver",
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        token = session["access_token"]
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]

        appointment = Appointment(
            person_id=person_id,
            title="Neurology review",
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
            scheduled_at_timezone="UTC",
            status="scheduled",
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        )
        care_event_detail = "Caregiver reported waking around 3 a.m. and needing help settling again."
        care_event = CareEvent(
            person_id=person_id,
            event_type="daily_care_entry",
            status="recorded",
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=8),
            title="Sleep update",
            description=care_event_detail,
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        )
        with TestingSessionLocal() as db:
            db.add(appointment)
            db.add(care_event)
            db.commit()
            db.refresh(appointment)
            db.refresh(care_event)
            appointment_id = appointment.id
            care_event_id = care_event.id

        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "workflow": "appointment",
                "appointment_id": appointment_id,
                "text": "Help prepare for this appointment using the saved sleep history and any unanswered questions.",
            },
        )

        print("APPOINTMENT_ASSIST_HTTP_STATUS", response.status_code)
        print("APPOINTMENT_ASSIST_HTTP_BODY", response.text)
        print("APPOINTMENT_ASSIST_SERVER_LOGS", caplog.text)

        assert response.status_code == 200, (
            "Authenticated appointment-assist returned a non-success response. "
            f"HTTP {response.status_code}; body={response.text}; server_logs={caplog.text}"
        )
        data = response.json()
        assert data["workflow"] == "appointment"
        assert data["ai_generated"] is True
        assert data["review_required"] is True
        assert data["written_to_lcr"] is False
        assert data["persistence"] == "read_only_assistance"
        assert data["authority"] == "solenos_care_record_and_caregiver"
        assert appointment_id in data["source_appointment_ids"]
        assert care_event_id in data["source_event_ids"], data
        assert isinstance(data["reply"], str) and data["reply"].strip()
        assert "scheduled" in data["reply"].casefold(), f"Qwen did not ground its response in the saved appointment status: {data['reply']!r}"
        assert any(term in data["reply"].casefold() for term in ("sleep", "waking", "night")), (
            f"Qwen did not retrieve the saved Daily Care sleep history for appointment preparation: {data['reply']!r}"
        )
        assert appointment_id not in data["reply"], "Caregiver-facing Qwen output must not expose internal appointment IDs"
        assert not __import__("re").search(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            data["reply"],
            __import__("re").IGNORECASE,
        ), "Caregiver-facing Qwen output must not expose UUIDs"
        assert len(data["reply"].split()) <= 55
        assert not __import__("re").search(
            r"\b(?:is|was|has been)\s+confirmed\b",
            data["reply"].casefold(),
        ), f"Qwen upgraded a scheduled appointment to confirmed: {data['reply']!r}"

        with TestingSessionLocal() as db:
            events_after = db.query(CareEvent).filter_by(person_id=person_id).all()
            assert [row.id for row in events_after] == [care_event_id]
            assert events_after[0].description == care_event_detail
            assert db.query(Evidence).filter_by(person_id=person_id).count() == 0
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == 0
            assert db.query(ConversationMessage).join(
                ConversationSession,
                ConversationMessage.session_id == ConversationSession.id,
            ).filter(
                ConversationSession.person_id == person_id,
                ConversationSession.caregiver_id == caregiver_id,
            ).count() == 0
            persisted = db.query(Appointment).filter_by(id=appointment_id, person_id=person_id).one()
            assert persisted.status == "scheduled"
            assert persisted.title == "Neurology review"
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()

def test_after_appointment_organizes_clinician_note_and_preserves_source_without_lcr_write(monkeypatch):
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    from app.api import collaboration as collaboration_api

    original_qwen_chat = collaboration_api.qwen_chat
    captured_messages = []

    def capture_real_qwen(messages, **kwargs):
        captured_messages.append(messages)
        return original_qwen_chat(messages, **kwargs)

    monkeypatch.setattr(collaboration_api, "qwen_chat", capture_real_qwen)
    client = TestClient(app)
    try:
        created = client.post(
            "/api/v1/sessions/anonymous",
            json={
                "name": "Synthetic after-appointment evaluator",
                "caregiver_name": "Synthetic after-appointment caregiver",
            },
        )
        assert created.status_code == 201, created.text
        session = created.json()
        token = session["access_token"]
        person_id = session["person"]["id"]
        caregiver_id = session["caregiver"]["id"]

        appointment = Appointment(
            person_id=person_id,
            title="Neurology review",
            scheduled_at=datetime.now(timezone.utc) - timedelta(hours=2),
            scheduled_at_timezone="UTC",
            status="completed",
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        )
        note_text = (
            "Clinician's after-visit note: Keep a sleep diary for two weeks. "
            "Schedule a follow-up visit in six weeks. Call the clinic if the sleep "
            "disruption becomes significantly worse. "
            "Caregiver note: I understand the sleep diary part, but I am unsure "
            "whether I should call sooner if the night waking happens again."
        )
        evidence_id = str(uuid.uuid4())
        evidence = Evidence(
            id=evidence_id,
            person_id=person_id,
            type="document",
            evidence_kind="document_extraction",
            epistemic_status="document_extracted",
            source_text=note_text,
            uploaded_by_caregiver_id=caregiver_id,
            evidence_status="pending_review",
            source_type="clinician_after_visit_note",
            original_author="Dr. Asha Patel, Neurologist",
            reported_by_label="Daughter",
            reported_by_caregiver_id=caregiver_id,
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        with TestingSessionLocal() as db:
            db.add_all([appointment, evidence])
            db.commit()
            db.refresh(appointment)
            appointment_id = appointment.id
            before_event_count = db.query(CareEvent).filter_by(person_id=person_id).count()
            before_assertion_count = db.query(CareAssertion).filter_by(person_id=person_id).count()

        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "workflow": "after_appointment",
                "appointment_id": appointment_id,
                "evidence_ids": [evidence_id],
                "text": (
                    "Organize the clinician's after-visit note. Separate what the clinician "
                    "said, what I understood, what remains unclear, and the reported next steps."
                ),
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data.get("workflow") == "after_appointment", data
        assert data.get("ai_generated") is True, data
        assert data.get("provider_status") == "available", data
        assert data.get("review_required") is True, data
        assert data.get("written_to_lcr") is False, data
        assert data.get("persistence") == "read_only_assistance", data
        assert appointment_id in data.get("source_appointment_ids", []), data
        assert evidence_id in data.get("source_evidence_ids", []), data
        assert captured_messages, "The real Qwen call was not observed."
        supplied_prompt = "\n".join(str(message.get("content", "")) for message in captured_messages[-1])
        assert "Dr. Asha Patel, Neurologist" in supplied_prompt
        assert "clinician_after_visit_note" in supplied_prompt
        assert "uploader is not necessarily the source author" in supplied_prompt
        assert "Original source text" in supplied_prompt
        reply = data.get("reply", "")
        assert "sleep diary" in reply.casefold(), reply
        assert "six weeks" in reply.casefold(), reply
        assert "call if sleep worsens significantly" in reply.casefold(), reply
        assert not __import__("re").search(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            reply,
            __import__("re").IGNORECASE,
        ), reply

        with TestingSessionLocal() as db:
            persisted_appointment = db.query(Appointment).filter_by(
                id=appointment_id, person_id=person_id
            ).one()
            persisted_evidence = db.query(Evidence).filter_by(
                id=evidence_id, person_id=person_id
            ).one()
            assert persisted_appointment.status == "completed"
            assert persisted_evidence.source_text == note_text
            assert persisted_evidence.source_text_sha256
            assert db.query(CareEvent).filter_by(person_id=person_id).count() == before_event_count
            assert db.query(CareAssertion).filter_by(person_id=person_id).count() == before_assertion_count
    finally:
        client.close()
        app.dependency_overrides.clear()
        engine.dispose()

