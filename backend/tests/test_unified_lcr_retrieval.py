"""Tests for unified Companion retrieval scope and epistemic boundaries."""
from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import Session

from app.models.collaboration import ConversationMessage, ConversationSession
from app.services.unified_lcr_retrieval import _conversation_context


def _conversation_db():
    metadata = ConversationSession.metadata
    if "persons" not in metadata.tables:
        from sqlalchemy import Table, MetaData
        Table("persons", metadata, Column("id", String, primary_key=True))
    if "caregivers" not in metadata.tables:
        from sqlalchemy import Table
        Table("caregivers", metadata, Column("id", String, primary_key=True))
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(
        engine,
        tables=[
            metadata.tables["persons"],
            metadata.tables["caregivers"],
            ConversationSession.__table__,
            ConversationMessage.__table__,
        ],
    )
    return engine


def test_companion_history_is_person_and_caregiver_scoped_and_not_evidence():
    engine = _conversation_db()
    try:
        with Session(engine) as db:
            own = ConversationSession(
                id="own-session", person_id="person-a", caregiver_id="caregiver-a", status="active"
            )
            other_caregiver = ConversationSession(
                id="other-caregiver-session", person_id="person-a",
                caregiver_id="caregiver-b", status="active"
            )
            other_person = ConversationSession(
                id="other-person-session", person_id="person-b",
                caregiver_id="caregiver-a", status="active"
            )
            db.add_all([own, other_caregiver, other_person])
            db.flush()
            db.add_all([
                ConversationMessage(
                    id="own-message", session_id=own.id, role="user",
                    content="Caregiver reported repeated waking overnight",
                    evidence_ids_json="[]",
                ),
                ConversationMessage(
                    id="assistant-message", session_id=own.id, role="assistant",
                    content="Unsupported assistant summary about waking",
                    evidence_ids_json="[]",
                ),
                ConversationMessage(
                    id="other-caregiver-message", session_id=other_caregiver.id, role="user",
                    content="Private waking detail from another caregiver",
                    evidence_ids_json="[]",
                ),
                ConversationMessage(
                    id="other-person-message", session_id=other_person.id, role="user",
                    content="Private waking detail for another person",
                    evidence_ids_json="[]",
                ),
            ])
            db.commit()

            result = _conversation_context(
                db,
                person_id="person-a",
                caregiver_id="caregiver-a",
                query="sleep waking",
                limit=8,
            )

        assert result["returned_count"] == 2
        by_id = {item["id"]: item for item in result["items"]}
        assert set(by_id) == {"own-message", "assistant-message"}
        assert all(item["information_status"] == "conversation_context_only" for item in by_id.values())
        assert all(item["is_lcr_assertion"] is False for item in by_id.values())
        assert all(item["is_care_evidence"] is False for item in by_id.values())
        assert by_id["assistant-message"]["role"] == "assistant"
        assert "Private waking detail" not in str(result)
    finally:
        engine.dispose()


def test_companion_history_empty_query_is_bounded_across_prior_sessions():
    engine = _conversation_db()
    try:
        with Session(engine) as db:
            active = ConversationSession(
                id="active-session", person_id="person-a",
                caregiver_id="caregiver-a", status="active"
            )
            inactive = ConversationSession(
                id="inactive-session", person_id="person-a",
                caregiver_id="caregiver-a", status="archived"
            )
            db.add_all([active, inactive])
            db.flush()
            db.add_all([
                ConversationMessage(
                    id=f"active-{index}", session_id=active.id, role="user",
                    content=f"Caregiver note {index}", evidence_ids_json="[]"
                )
                for index in range(10)
            ])
            db.add(ConversationMessage(
                id="archived-message", session_id=inactive.id, role="user",
                content="Archived caregiver note", evidence_ids_json="[]"
            ))
            db.commit()
            result = _conversation_context(
                db,
                person_id="person-a",
                caregiver_id="caregiver-a",
                query=None,
                limit=3,
            )
        assert result["returned_count"] == 3
        assert result["has_more"] is True
        assert all(item["information_status"] == "conversation_context_only" for item in result["items"])
        assert all(item["session_id"] in {"active-session", "inactive-session"} for item in result["items"])
    finally:
        engine.dispose()
