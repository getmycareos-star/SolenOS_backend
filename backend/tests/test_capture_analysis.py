import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.capture_analysis import CaptureAnalysis
from app.services import capture_analysis


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
)


def setup_db():
    Base.metadata.create_all(bind=engine)


def teardown_db():
    Base.metadata.drop_all(bind=engine)


def create_event(db):
    person = Person(id="capture-person", name="Capture Test Person")
    caregiver = Caregiver(
        id="capture-caregiver",
        name="Capture Test Caregiver",
        email="capture@example.com",
        relationship="family",
        person_id=person.id,
    )
    evidence = Evidence(
        id="capture-evidence",
        person_id=person.id,
        type="note",
        source_text="Slept only two hours last night.",
        uploaded_by_caregiver_id=caregiver.id,
        confidence=1.0,
        evidence_status="reported",
        source_type="caregiver_observation",
    )
    event = CareEvent(
        id="capture-event",
        person_id=person.id,
        event_type="observation",
        status="recorded",
        occurred_at=datetime.now(timezone.utc),
        title="Sleep observation",
        description="Slept only two hours last night.",
        evidence_ids=[evidence.id],
        created_by_caregiver_id=caregiver.id,
    )
    db.add_all([person, caregiver, evidence, event])
    db.commit()
    return event


def test_capture_extraction_records_not_configured_without_fabricating_candidates(monkeypatch):
    setup_db()
    try:
        with TestingSessionLocal() as db:
            create_event(db)
            monkeypatch.setattr(
                capture_analysis,
                "get_settings",
                lambda: SimpleNamespace(ollama_configured=False, ollama_model="", ollama_base_url="", ollama_timeout_seconds=20),
            )
            result = capture_analysis.analyze_capture_with_qwen(db, "capture-event")
            assert result["qwen_extraction_status"] == "not_configured"
            assert result["qwen_candidates"] is None
            row = db.query(CaptureAnalysis).filter_by(care_event_id="capture-event").one()
            assert row.status == "not_configured"
            assert db.query(CareEvent).filter_by(id="capture-event").one().description == "Slept only two hours last night."
    finally:
        teardown_db()


def test_capture_extraction_only_keeps_candidates_with_verbatim_source_quotes(monkeypatch):
    setup_db()
    try:
        with TestingSessionLocal() as db:
            create_event(db)
            monkeypatch.setattr(
                capture_analysis,
                "get_settings",
                lambda: SimpleNamespace(
                    ollama_configured=True,
                    ollama_base_url="http://127.0.0.1:11434",
                    ollama_model="qwen-test",
                    ollama_timeout_seconds=3,
                ),
            )
            model_result = {
                "observations": [
                    {
                        "subject": "sleep",
                        "observation": "Caregiver reports reduced sleep.",
                        "time_expression": "last night",
                        "certainty": "reported",
                        "source_quote": "Slept only two hours",
                    },
                    {
                        "subject": "temperature",
                        "observation": "The person had a fever.",
                        "time_expression": "today",
                        "certainty": "reported",
                        "source_quote": "Had a fever",
                    },
                ],
                "open_questions": ["Was this different from the usual sleep pattern?"],
                "possible_safety_flags": [],
            }
            body = json.dumps({"message": {"content": json.dumps(model_result)}}).encode()
            with patch.object(capture_analysis, "urlopen", return_value=io.BytesIO(body)) as ollama_call:
                result = capture_analysis.analyze_capture_with_qwen(db, "capture-event")
            request = ollama_call.call_args.args[0]
            assert request.full_url == "http://127.0.0.1:11434/api/chat"
            assert request.get_header("Authorization") is None
            assert json.loads(request.data)["format"] == "json"
            assert result["qwen_extraction_status"] == "pending_review"
            candidates = result["qwen_candidates"]["observations"]
            assert len(candidates) == 1
            assert candidates[0]["source_quote"] == "Slept only two hours"
            assert candidates[0]["evidence_ids"] == ["capture-evidence"]
            assert candidates[0]["review_status"] == "pending_review"
            assert result["qwen_candidates"]["rejected_unverifiable_candidates"] == 1
            row = db.query(CaptureAnalysis).filter_by(care_event_id="capture-event").one()
            assert row.status == "pending_review"
    finally:
        teardown_db()
