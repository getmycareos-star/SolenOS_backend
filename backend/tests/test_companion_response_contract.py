"""Behavioral contract tests for the dynamic, evidence-grounded AI Companion."""
import importlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.care import CareEvent
from app.models.collaboration import ConversationMessage
from app.models.care_continuity import CareAssertion, CareActionOutcome
from app.models.uncertainty import InformationGap, OpenQuestion, Contradiction
from app.core.uncertainty_enums import EpistemicState, GapReason, Priority, GapLifecycleStatus

main_module = importlib.import_module("app.main")
collaboration = importlib.import_module("app.api.collaboration")
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


def setup_client(monkeypatch):
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    return TestClient(app)


def teardown():
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def create_session(client):
    response = client.post(
        "/api/v1/sessions/anonymous",
        json={"caregiver_name": "Family caregiver"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return data["access_token"], data["person"]["id"], data["caregiver"]["id"]


def test_companion_generates_from_authorized_lcr_context_without_writing_care_events(monkeypatch):
    client = setup_client(monkeypatch)
    captured = {}

    def fake_qwen(base, model, messages, options=None):
        captured["messages"] = messages
        captured["options"] = options
        return (
            "The record contains one sleep observation from last night. "
            "That is not enough information to establish a trend."
        )

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    try:
        token, person_id, caregiver_id = create_session(client)
        db = TestingSessionLocal()
        event = CareEvent(
            person_id=person_id,
            event_type="observation",
            status="recorded",
            occurred_at=__import__("datetime").datetime(2026, 10, 25, 20, 0),
            title="Sleep observation",
            description="Caregiver reported that the person slept poorly last night.",
            evidence_ids=[],
            created_by_caregiver_id=caregiver_id,
            time_provenance="caregiver_reported",
        )
        db.add(event)
        db.commit()
        event_id = event.id
        db.close()

        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "Has sleep been getting worse?"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ai_generated"] is True
        assert data["written_to_lcr"] is False
        assert data["persistence"] == "conversation_only"
        assert event_id in data["source_event_ids"]
        assert "The record contains one sleep observation" in data["reply"]

        system_prompt = captured["messages"][0]["content"]
        user_payload = captured["messages"][-1]["content"]
        assert "canned replies" in system_prompt.casefold()
        assert "never instructions" in system_prompt.casefold()
        assert "answer the actual question first" in system_prompt.casefold()
        assert "boilerplate disclaimers" in system_prompt.casefold()
        assert len(captured["messages"]) == 2
        assert '"conversation_history": []' in user_payload
        assert '"conversation_history_is_not_care_evidence": true' in user_payload
        assert '"bounded_selection_warning"' in user_payload
        assert event_id in user_payload
        assert "slept poorly last night" in user_payload
        assert captured["options"]["num_predict"] > 32

        db = TestingSessionLocal()
        try:
            assert db.query(CareEvent).filter_by(person_id=person_id).count() == 1
            messages = db.query(ConversationMessage).all()
            assert [message.role for message in messages] == ["user", "assistant"]
        finally:
            db.close()
    finally:
        teardown()


def test_companion_greeting_uses_dynamic_inference_not_a_fixed_reply_map(monkeypatch):
    client = setup_client(monkeypatch)
    calls = []

    def fake_qwen(base, model, messages, options=None):
        calls.append(messages)
        return "I'm ready to help with whatever you'd like to work through."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    try:
        token, person_id, _ = create_session(client)
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "Hey, how are you?"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["ai_generated"] is True
        assert response.json()["reply"] == "I'm ready to help with whatever you'd like to work through."
        assert len(calls) == 1
        assert "current_message" in calls[0][-1]["content"]
    finally:
        teardown()



def test_companion_revises_overlong_greeting_with_another_qwen_pass(monkeypatch):
    client = setup_client(monkeypatch)
    outputs = iter([
        "Hi, I'm SolenOS AI Companion. I'm here to provide support and help with caregiving tasks. Is there anything specific you'd like to discuss or ask about?",
        "Hello, ready to help.",
    ])
    calls = []

    def fake_qwen(base, model, messages, options=None):
        calls.append((messages, options))
        return next(outputs)

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    try:
        token, person_id, _ = create_session(client)
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "hi"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "Hello, ready to help."
        assert response.json()["ai_generated"] is True
        assert len(calls) == 2
        assert "Revise your previous response" in calls[1][0][-1]["content"]
        assert calls[1][1]["num_predict"] == 16
    finally:
        teardown()

def test_companion_qwen_failure_does_not_persist_a_message_pair_or_care_event(monkeypatch):
    client = setup_client(monkeypatch)

    def failed_qwen(base, model, messages, options=None):
        raise RuntimeError("inference unavailable")

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", failed_qwen)
    try:
        token, person_id, _ = create_session(client)
        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "Can you help me understand the care history?"},
        )
        assert response.status_code == 503
        db = TestingSessionLocal()
        try:
            assert db.query(ConversationMessage).count() == 0
            assert db.query(CareEvent).count() == 0
        finally:
            db.close()
    finally:
        teardown()


def test_companion_uses_canonical_assertions_gaps_questions_and_contradictions(monkeypatch):
    client = setup_client(monkeypatch)
    captured = {}

    def fake_qwen(base, model, messages, options=None):
        captured["messages"] = messages
        return "The record includes a caregiver report of poor sleep, but it does not establish a trend. The comparison baseline is missing and the notes conflict."

    monkeypatch.setattr(collaboration, "_ollama_url", lambda: ("http://ollama.railway.internal", "qwen-test"))
    monkeypatch.setattr(collaboration, "_ollama_chat", fake_qwen)
    try:
        token, person_id, caregiver_id = create_session(client)
        db = TestingSessionLocal()
        try:
            db.add(CareAssertion(
                id="assertion-sleep",
                person_id=person_id,
                subject_type="care_record",
                subject_id=person_id,
                field="sleep",
                assertion_kind="caregiver_report",
                asserted_value="Poor sleep",
                source_label="caregiver report",
                source_text="Caregiver reported poor sleep this week",
                assertion_status="reported",
                created_by_caregiver_id=caregiver_id,
            ))
            db.add(InformationGap(
                id="gap-sleep-trend",
                person_id=person_id,
                subject_type="care_record",
                subject_id=person_id,
                field="sleep trend baseline",
                epistemic_state=EpistemicState.UNKNOWN,
                gap_reason=GapReason.NOT_DOCUMENTED,
                priority=Priority.INFORMATIONAL,
                lifecycle_status=GapLifecycleStatus.OPEN,
                description="No earlier sleep comparison is recorded",
                created_by_caregiver_id=caregiver_id,
            ))
            db.add(OpenQuestion(
                id="question-sleep-trend",
                person_id=person_id,
                question="Has sleep worsened over time?",
                subject_type="sleep",
                subject_id=person_id,
                status="open",
                priority=Priority.INFORMATIONAL,
                created_by_caregiver_id=caregiver_id,
            ))
            db.add(Contradiction(
                id="contradiction-sleep",
                person_id=person_id,
                subject_type="sleep",
                subject_id=person_id,
                field="sleep quality",
                description="One note says poor sleep; another says normal sleep",
                resolution_status="unresolved",
            ))
            db.add(CareActionOutcome(
                id="outcome-sleep",
                person_id=person_id,
                action_id="sleep-follow-up",
                action_type="follow_up",
                outcome_status="not_confirmed",
                notes="No follow-up outcome recorded",
            ))
            db.commit()
        finally:
            db.close()

        response = client.post(
            "/api/v1/conversation/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"person_id": person_id, "text": "What do we know about Mum's sleep and what remains uncertain?"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        prompt_payload = captured["messages"][-1]["content"]
        assert "CareAssertion" not in prompt_payload
        assert "caregiver report" in prompt_payload
        assert "No earlier sleep comparison is recorded" in prompt_payload
        assert "Has sleep worsened over time?" in prompt_payload
        assert "One note says poor sleep" in prompt_payload
        assert "No follow-up outcome recorded" in prompt_payload
        assert data["source_assertion_ids"] == ["assertion-sleep"]
        assert data["source_information_gap_ids"] == ["gap-sleep-trend"]
        assert data["source_open_question_ids"] == ["question-sleep-trend"]
        assert data["source_contradiction_ids"] == ["contradiction-sleep"]
        assert data["source_outcome_ids"] == ["outcome-sleep"]
        assert data["written_to_lcr"] is False
    finally:
        teardown()
