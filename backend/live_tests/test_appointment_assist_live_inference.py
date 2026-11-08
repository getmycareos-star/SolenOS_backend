"""Authenticated appointment-assist acceptance against a real Qwen model.

All synthetic identity and appointment rows live in an disposable PostgreSQL database.
No production care record is read or written.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
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

pytestmark = pytest.mark.skipif(
    os.getenv("SOLENOS_RUN_APPOINTMENT_ASSIST_LIVE_EVAL") != "1" or not POSTGRES_DATABASE,
    reason="requires the dedicated live-Qwen appointment-assist workflow and migrated PostgreSQL",
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTGRES_DATABASE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://", "postgresql+psycopg://"))
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if POSTGRES_DATABASE else None
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) if engine is not None else None


def override_db():
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
        with TestingSessionLocal() as db:
            db.add(appointment)
            db.commit()
            db.refresh(appointment)
            appointment_id = appointment.id

        response = client.post(
            "/api/v1/assistant/assist",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "workflow": "appointment",
                "appointment_id": appointment_id,
                "text": "Help organize the available appointment details and unanswered questions.",
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
        assert isinstance(data["reply"], str) and data["reply"].strip()
        assert "scheduled" in data["reply"].casefold(), f"Qwen did not ground its response in the saved appointment status: {data['reply']!r}"
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
            assert db.query(CareEvent).filter_by(person_id=person_id).count() == 0
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
