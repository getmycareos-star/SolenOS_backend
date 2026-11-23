"""PostgreSQL integration tests for unified Companion retrieval scope."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Column, DateTime, String, Table, Text, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.capture_analysis import CaptureAnalysis
from app.models.care import CareEvent, Caregiver, Person
from app.models.time_location import Appointment
from app.models.appointment_workspace import AppointmentWorkspaceRecord
from app.models.caregiver_intelligence import CaregiverHandoff, CaregiverProfile
from app.core.caregiver_enums import HandoffAcceptanceStatus
from app.models.care_continuity import CareActionOutcome, CareAssertion, CareStateRevision, EvidenceObservation
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.ai_output_governance import AIOutputLCRProposal, AIOutputLCRDecision
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput
from app.services.lcr_context import build_lcr_context, format_lcr_context_for_prompt
from app.services.unified_lcr_retrieval import (
    _conversation_context,
    _temporal_window,
    build_unified_lcr_context,
)


def _postgres_conversation_db():
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace(
            "postgresql+psycopg://", "postgresql+psycopg2://", 1
        )
    if not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("unified Companion retrieval integration requires PostgreSQL DATABASE_URL")

    schema = "test_lcr_retrieval_" + uuid.uuid4().hex
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    connection = engine.connect()
    connection.exec_driver_sql(f'SET search_path TO "{schema}"')

    metadata = ConversationSession.metadata
    if "care_facts" not in metadata.tables:
        Table(
            "care_facts", metadata,
            Column("id", String, primary_key=True),
            Column("person_id", String, nullable=False),
            Column("subject_type", String),
            Column("subject_id", String),
            Column("fact_type", String),
            Column("fact_value", Text),
            Column("epistemic_status", String),
            Column("effective_at", DateTime(timezone=True)),
            Column("recorded_at", DateTime(timezone=True)),
            Column("created_at", DateTime(timezone=True)),
            Column("superseded_by_fact_id", String),
            Column("created_by_caregiver_id", String),
            Column("review_notes", Text),
        )
    if "situations" not in metadata.tables:
        Table("situations", metadata, Column("id", String, primary_key=True))
    if "evidence" not in metadata.tables:
        Table(
            "evidence", metadata,
            Column("id", String, primary_key=True),
            Column("person_id", String, nullable=False),
        )
    if "care_fact_evidence" not in metadata.tables:
        Table(
            "care_fact_evidence", metadata,
            Column("person_id", String, nullable=False),
            Column("fact_id", String, nullable=False),
            Column("evidence_id", String, nullable=False),
            Column("evidence_role", String, nullable=False),
            Column("source_span", Text),
            Column("created_at", DateTime(timezone=True)),
        )
    connection.exec_driver_sql("CREATE TABLE situations (id VARCHAR PRIMARY KEY)")
    metadata.create_all(
        connection,
        tables=[
            Person.__table__, Caregiver.__table__, CareEvent.__table__,
            CareAssertion.__table__, CareStateRevision.__table__, CareActionOutcome.__table__, EvidenceObservation.__table__, CaptureAnalysis.__table__,
            ConversationSession.__table__, ConversationMessage.__table__,
            CaregiverProfile.__table__, CaregiverHandoff.__table__,
            metadata.tables["care_facts"], metadata.tables["evidence"],
            metadata.tables["care_fact_evidence"],
            QwenBackgroundJob.__table__, QwenModelOutput.__table__,
            AIOutputLCRProposal.__table__, AIOutputLCRDecision.__table__,
        ],
    )
    return engine, connection, schema



def _sqlite_retrieval_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    connection = engine.connect()
    metadata = ConversationSession.metadata
    if "situations" not in metadata.tables:
        Table("situations", metadata, Column("id", String, primary_key=True))
    connection.exec_driver_sql("CREATE TABLE situations (id VARCHAR PRIMARY KEY)")
    metadata.create_all(connection, tables=[
        Person.__table__, Caregiver.__table__, CareEvent.__table__,
        CareAssertion.__table__, CareStateRevision.__table__, CareActionOutcome.__table__, CaptureAnalysis.__table__,
        ConversationSession.__table__, ConversationMessage.__table__,
        QwenBackgroundJob.__table__, QwenModelOutput.__table__,
        AIOutputLCRProposal.__table__, AIOutputLCRDecision.__table__,
    ])
    connection.commit()
    return engine, connection

def _close_postgres_conversation_db(engine, connection, schema):
    connection.close()
    with engine.begin() as cleanup:
        cleanup.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    engine.dispose()


def test_lcr_fact_projection_retains_verified_person_scoped_evidence_links():
    engine, connection, schema = _postgres_conversation_db()
    try:
        metadata = ConversationSession.metadata
        with Session(bind=connection) as db:
            db.add_all([
                Person(id="fact-lineage-person", name="Care recipient"),
                Person(id="other-fact-person", name="Other care recipient"),
            ])
            db.flush()
            db.add_all([
                Caregiver(
                    id="fact-lineage-caregiver", name="Caregiver",
                    relationship="family", person_id="fact-lineage-person",
                ),
                Caregiver(
                    id="other-fact-caregiver", name="Other caregiver",
                    relationship="family", person_id="other-fact-person",
                ),
            ])
            db.flush()
            now = datetime.now(UTC)
            db.execute(
                metadata.tables["care_facts"].insert(),
                [{
                    "id": "fact-with-source", "person_id": "fact-lineage-person",
                    "subject_type": "person", "subject_id": "fact-lineage-person",
                    "fact_type": "sleep_pattern", "fact_value": "Caregiver reported overnight waking.",
                    "epistemic_status": "reported", "effective_at": now,
                    "recorded_at": now, "created_at": now,
                    "superseded_by_fact_id": None, "created_by_caregiver_id": "fact-lineage-caregiver",
                    "review_notes": None,
                }],
            )
            db.execute(metadata.tables["evidence"].insert(), [
                {
                    "id": "fact-source-evidence", "person_id": "fact-lineage-person",
                    "type": "note", "source_text": "Caregiver reported overnight waking.",
                    "uploaded_by_caregiver_id": "fact-lineage-caregiver",
                },
                {
                    "id": "other-person-evidence", "person_id": "other-fact-person",
                    "type": "note", "source_text": "Private information for another person.",
                    "uploaded_by_caregiver_id": "other-fact-caregiver",
                },
            ])
            db.execute(metadata.tables["care_fact_evidence"].insert(), [
                {
                    "person_id": "fact-lineage-person", "fact_id": "fact-with-source",
                    "evidence_id": "fact-source-evidence", "evidence_role": "supports",
                    "source_span": "Caregiver reported overnight waking.", "created_at": now,
                },
                {
                    # Even a malformed cross-person link row must not make the
                    # other person's evidence visible in this person's LCR.
                    "person_id": "fact-lineage-person", "fact_id": "fact-with-source",
                    "evidence_id": "other-person-evidence", "evidence_role": "supports",
                    "source_span": "Private information for another person.", "created_at": now,
                },
            ])
            db.commit()
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db, "fact-lineage-person", caregiver_id="fact-lineage-caregiver",
                query="sleep", include_all_history=True,
            )
            from app.services.delta_engine import _resolve_delta_source_refs
            delta_references = _resolve_delta_source_refs(
                db, "fact-lineage-person", ["fact-with-source"],
            )
        fact = next(item for item in context["sources"]["facts"]["items"] if item["id"] == "fact-with-source")
        prompt_context = format_lcr_context_for_prompt(context)
        assert "fact-source-evidence" in prompt_context
        assert fact["evidence_ids"] == ["fact-source-evidence"]
        assert fact["evidence_link_status"] == "verified_person_scoped_links"
        assert fact["evidence_links"] == [{
            "evidence_id": "fact-source-evidence",
            "evidence_role": "supports",
            "source_span": "Caregiver reported overnight waking.",
            "integrity_status": "verified_person_scoped",
        }]
        fact_reference = next(ref for ref in delta_references if ref["record_type"] == "care_fact")
        assert fact_reference["record_id"] == "fact-with-source"
        assert fact_reference["source_evidence"] == [{
            "evidence_id": "fact-source-evidence",
            "evidence_role": "supports",
            "source_span": "Caregiver reported overnight waking.",
        }]
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_full_history_projection_returns_more_than_fifty_matches_in_one_scan():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            db.add(Person(id="wide-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="wide-caregiver", name="Caregiver", relationship="family",
                person_id="wide-person",
            ))
            db.flush()
            now = datetime.now(UTC)
            db.add_all([
                CareEvent(
                    id=f"sleep-event-{index}", person_id="wide-person",
                    event_type="daily_observation", status="recorded",
                    occurred_at=now - timedelta(days=index),
                    occurred_at_timezone="Africa/Lagos",
                    location="Home",
                    title=f"Sleep history {index}",
                    description=f"Caregiver recorded sleep observation {index}.",
                    evidence_ids=[], created_by_caregiver_id="wide-caregiver",
                )
                for index in range(75)
            ])
            db.commit()
            from app.services.lcr_context import _load_source_schema
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db, "wide-person", caregiver_id="wide-caregiver",
                query="sleep", limit_per_source=200, include_all_history=True,
            )
        events = context["sources"]["events"]
        assert events["limit"] == 200
        assert events["candidate_count"] == 75
        assert events["returned_count"] == 75
        assert events["candidate_scan_complete"] is True
        assert events["has_more"] is False
        assert events["returned_history_complete"] is True
        assert events["items"][0]["occurred_at_timezone"] == "Africa/Lagos"
        assert events["items"][0]["location"] == "Home"
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_full_history_projection_discloses_more_than_two_hundred_matches():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            db.add(Person(id="partial-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="partial-caregiver", name="Caregiver", relationship="family",
                person_id="partial-person",
            ))
            db.flush()
            now = datetime.now(UTC)
            db.add_all([
                CareEvent(
                    id=f"sleep-overflow-{index}", person_id="partial-person",
                    event_type="daily_observation", status="recorded",
                    occurred_at=now - timedelta(days=index),
                    title=f"Sleep history {index}",
                    description=f"Caregiver recorded sleep observation {index}.",
                    evidence_ids=[], created_by_caregiver_id="partial-caregiver",
                )
                for index in range(205)
            ])
            db.commit()
            from app.services.lcr_context import _load_source_schema
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db, "partial-person", caregiver_id="partial-caregiver",
                query="sleep", limit_per_source=200, include_all_history=True,
            )
        events = context["sources"]["events"]
        assert events["candidate_count"] == 205
        assert events["returned_count"] == 200
        assert events["has_more"] is True
        assert events["returned_history_complete"] is False
        assert context["retrieval_projection_complete"] is False
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_companion_history_is_person_and_caregiver_scoped_and_not_evidence():
    engine, connection, schema = _postgres_conversation_db()
    metadata = ConversationSession.metadata
    try:
        with Session(bind=connection) as db:
            person = Person(id="person-a", name="Care recipient A")
            other_person_record = Person(id="person-b", name="Care recipient B")
            db.add_all([person, other_person_record])
            db.flush()
            db.add_all([
                Caregiver(id="caregiver-a", name="Caregiver A", relationship="family", person_id="person-a"),
                Caregiver(id="caregiver-b", name="Caregiver B", relationship="family", person_id="person-a"),
                Caregiver(id="caregiver-c", name="Caregiver C", relationship="family", person_id="person-b"),
            ])
            db.flush()
            db.add_all([
                CaregiverProfile(
                    id="profile-a", person_id="person-a", session_caregiver_id="caregiver-a",
                    name="Caregiver A", relationship="family",
                ),
                CaregiverProfile(
                    id="profile-b", person_id="person-a", session_caregiver_id="caregiver-b",
                    name="Caregiver B", relationship="family",
                ),
                CaregiverProfile(
                    id="profile-c", person_id="person-b", session_caregiver_id="caregiver-c",
                    name="Caregiver C", relationship="family",
                ),
            ])
            db.flush()
            db.add_all([
                CaregiverHandoff(
                    id="handoff-person-a", person_id="person-a",
                    from_caregiver_id="profile-a", to_caregiver_id="profile-b",
                    responsibility_type="Sleep observations",
                    scope="Overnight waking history",
                    context="Caregiver A handed over repeated overnight waking observations.",
                    acceptance_status=HandoffAcceptanceStatus.ACCEPTED,
                    acceptance_evidence="Caregiver B accepted the handoff.",
                    is_completed=True,
                ),
                CaregiverHandoff(
                    id="handoff-person-b", person_id="person-b",
                    from_caregiver_id="profile-c", to_caregiver_id="profile-c",
                    responsibility_type="Sleep observations",
                    scope="Private overnight waking history",
                    context="This handoff belongs to a different care recipient.",
                    acceptance_status=HandoffAcceptanceStatus.ACCEPTED,
                    is_completed=True,
                ),
            ])
            db.flush()
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
            # Insert the current fact first so PostgreSQL can enforce the
            # immediate self-referencing supersession foreign key.
            db.execute(metadata.tables["care_facts"].insert(), [
                {
                    "id": "sleep-fact-current", "person_id": "person-a",
                    "subject_type": "person", "subject_id": "person-a",
                    "fact_type": "sleep_pattern", "fact_value": "Recent sleep report described fewer interruptions.",
                    "epistemic_status": "reported", "effective_at": datetime.now(UTC) - timedelta(days=6),
                    "recorded_at": datetime.now(UTC) - timedelta(days=6),
                    "created_at": datetime.now(UTC) - timedelta(days=6),
                    "superseded_by_fact_id": None,
                    "created_by_caregiver_id": "caregiver-a",
                },
            ])
            db.execute(metadata.tables["care_facts"].insert(), [
                {
                    "id": "sleep-fact-old", "person_id": "person-a",
                    "subject_type": "person", "subject_id": "person-a",
                    "fact_type": "sleep_pattern", "fact_value": "Earlier sleep report described frequent waking.",
                    "epistemic_status": "reported", "effective_at": datetime.now(UTC) - timedelta(days=45),
                    "recorded_at": datetime.now(UTC) - timedelta(days=45),
                    "created_at": datetime.now(UTC) - timedelta(days=45),
                    "superseded_by_fact_id": "sleep-fact-current",
                    "created_by_caregiver_id": "caregiver-a",
                },
            ])
            db.commit()
            result = _conversation_context(
                db, person_id="person-a", caregiver_id="caregiver-a",
                query="sleep waking", limit=8,
            )
            from app.services.lcr_context import _load_source_schema
            _load_source_schema.cache_clear()
            lcr_context = build_unified_lcr_context(
                db, "person-a", "caregiver-a",
                query="sleep handoff", limit_per_source=20, include_all_history=True,
            )

        assert result["returned_count"] == 2
        handoffs = lcr_context["sources"]["handoffs"]
        assert handoffs["candidate_scan_complete"] is True
        assert handoffs["candidate_count"] == 1
        assert [item["id"] for item in handoffs["items"]] == ["handoff-person-a"]
        facts = {item["id"]: item["information_status"] for item in lcr_context["sources"]["facts"]["items"]}
        assert facts["sleep-fact-old"] == "superseded_fact"
        assert facts["sleep-fact-current"] != "superseded_fact"
        by_id = {item["id"]: item for item in result["items"]}
        assert set(by_id) == {"own-message", "assistant-message"}
        assert all(item["information_status"] == "conversation_context_only" for item in by_id.values())
        assert all(item["is_lcr_assertion"] is False for item in by_id.values())
        assert all(item["is_care_evidence"] is False for item in by_id.values())
        assert by_id["assistant-message"]["role"] == "assistant"
        assert by_id["own-message"]["source_attribution"] == "caregiver_companion_message"
        assert by_id["assistant-message"]["source_attribution"] == "solenos_companion_response"
        assert "Private waking detail" not in str(result)
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_companion_history_is_bounded_across_prior_sessions():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            person = Person(id="person-a", name="Care recipient A")
            db.add(person)
            db.flush()
            db.add(Caregiver(
                id="caregiver-a", name="Caregiver A", relationship="family", person_id="person-a"
            ))
            db.flush()
            active = ConversationSession(
                id="active-session", person_id="person-a",
                caregiver_id="caregiver-a", status="active"
            )
            archived = ConversationSession(
                id="archived-session", person_id="person-a",
                caregiver_id="caregiver-a", status="archived"
            )
            db.add_all([active, archived])
            db.flush()
            db.add_all([
                ConversationMessage(
                    id=f"active-{index}", session_id=active.id, role="user",
                    content=f"Caregiver note {index}", evidence_ids_json="[]"
                )
                for index in range(10)
            ])
            db.add(ConversationMessage(
                id="archived-message", session_id=archived.id, role="user",
                content="Archived caregiver note", evidence_ids_json="[]"
            ))
            db.commit()
            result = _conversation_context(
                db, person_id="person-a", caregiver_id="caregiver-a",
                query=None, limit=3,
            )
        assert result["returned_count"] == 3
        assert result["has_more"] is True
        assert all(
            item["session_id"] in {"active-session", "archived-session"}
            for item in result["items"]
        )
    finally:
        _close_postgres_conversation_db(engine, connection, schema)



def test_conversation_search_scans_past_recent_candidate_window():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            person = Person(id="person-search", name="Care recipient")
            caregiver = Caregiver(
                id="caregiver-search", name="Caregiver", relationship="family",
                person_id="person-search",
            )
            db.add(person)
            db.flush()
            db.add(caregiver)
            db.flush()
            old_session = ConversationSession(
                id="old-search-session", person_id="person-search",
                caregiver_id="caregiver-search", status="archived",
            )
            db.add(old_session)
            db.flush()
            old_message = ConversationMessage(
                id="old-relevant-message", session_id=old_session.id, role="user",
                content="We discussed sleep and the new medication after the clinic visit.",
                evidence_ids_json="[]",
            )
            db.add(old_message)
            db.flush()
            for index in range(125):
                session = ConversationSession(
                    id=f"recent-search-session-{index}", person_id="person-search",
                    caregiver_id="caregiver-search", status="archived",
                )
                db.add(session)
                db.flush()
                db.add(ConversationMessage(
                    id=f"recent-sleep-message-{index}", session_id=session.id,
                    role="user", content=f"Caregiver mentioned sleep was unchanged on routine day {index}.",
                    evidence_ids_json="[]",
                ))
            db.commit()

            result = _conversation_context(
                db, person_id="person-search", caregiver_id="caregiver-search",
                query="What did we discuss about sleep and medication?",
                limit=8,
            )
            ids = {item["id"] for item in result["items"]}
            assert "old-relevant-message" in ids
            assert result["candidate_count"] == 126
            assert result["candidate_scan_complete"] is True
            assert result["has_more"] is True
    finally:
        connection.close()
        engine.dispose()

def test_hybrid_retrieval_includes_all_four_persisted_source_pathways():
    engine, connection, schema = _postgres_conversation_db()
    try:
        now = datetime.now(UTC)
        with Session(bind=connection) as db:
            person = Person(id="person-a", name="Care recipient")
            other_person = Person(id="person-b", name="Different care recipient")
            caregiver = Caregiver(
                id="caregiver-a", name="Caregiver A", relationship="family", person_id="person-a"
            )
            caregiver_b = Caregiver(
                id="caregiver-b", name="Caregiver B", relationship="family", person_id="person-a"
            )
            caregiver_c = Caregiver(
                id="caregiver-c", name="Caregiver C", relationship="family", person_id="person-b"
            )
            # Flush parent rows before caregivers so this fixture remains valid
            # even when mapper relationship metadata is not available for ordering.
            db.add_all([person, other_person])
            db.flush()
            db.add_all([caregiver, caregiver_b, caregiver_c])
            db.flush()
            event = CareEvent(
                id="daily-care-event", person_id="person-a", event_type="daily_observation",
                status="recorded", occurred_at=now - timedelta(days=1), title="Sleep disruption",
                description="Caregiver recorded repeated waking during the night.",
                evidence_ids=[], created_by_caregiver_id="caregiver-a",
            )
            longitudinal_events = [
                CareEvent(
                    id="sleep-history-86d", person_id="person-a", event_type="daily_observation",
                    status="recorded", occurred_at=now - timedelta(days=86),
                    title="Night-time restlessness",
                    description="Caregiver reported that she remained awake for long stretches.",
                    evidence_ids=[], created_by_caregiver_id="caregiver-a",
                ),
                CareEvent(
                    id="sleep-history-43d", person_id="person-a", event_type="daily_observation",
                    status="recorded", occurred_at=now - timedelta(days=43),
                    title="Repeated overnight waking",
                    description="Caregiver recorded several episodes of waking during the night.",
                    evidence_ids=[], created_by_caregiver_id="caregiver-a",
                ),
                CareEvent(
                    id="sleep-history-6d", person_id="person-a", event_type="daily_observation",
                    status="recorded", occurred_at=now - timedelta(days=6),
                    title="Quieter night",
                    description="Caregiver reported fewer interruptions during the overnight rest period.",
                    evidence_ids=[], created_by_caregiver_id="caregiver-a",
                ),
            ]
            accepted = CareAssertion(
                id="accepted-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Wakes repeatedly overnight", source_label="Caregiver review",
                source_text="Sleep is interrupted", evidence_ids=[], assertion_status="accepted",
                source_context={
                    "kind": "caregiver_reviewed_document_candidate",
                    "authority": "caregiver_reviewed_report_not_independently_verified",
                    "document_candidate": {
                        "source_attribution": "professional_documented",
                        "negation_status": "explicitly_negated",
                        "modality": "tentative",
                        "conditionality": "conditional",
                        "temporal_context": "historical",
                        "statement_kind": "recommendation",
                        "page_number": 2,
                    },
                },
                observed_at=now - timedelta(days=1), created_by_caregiver_id="caregiver-a",
            )
            rejected = CareAssertion(
                id="rejected-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Rejected sleep claim", source_label="Caregiver rejection",
                source_text="Rejected assertion", evidence_ids=[], assertion_status="rejected",
                observed_at=now - timedelta(days=1), created_by_caregiver_id="caregiver-a",
            )
            superseded = CareAssertion(
                id="superseded-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Earlier sleep report", source_label="Earlier caregiver report",
                source_text="This report was replaced by a later caregiver correction.",
                evidence_ids=[], assertion_status="superseded",
                observed_at=now - timedelta(days=8), created_by_caregiver_id="caregiver-a",
            )
            caregiver_reviewed = CareAssertion(
                id="caregiver-reviewed-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="caregiver_reviewed_daily_care_observation",
                asserted_value="Caregiver reviewed interrupted sleep observation", source_label="Caregiver review",
                source_text="Original daily care note and caregiver correction", evidence_ids=[],
                assertion_status="caregiver_reviewed", observed_at=now - timedelta(days=1),
                created_by_caregiver_id="caregiver-a",
            )
            pending = CareAssertion(
                id="pending-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Pending sleep claim", source_label="Unreviewed model proposal",
                source_text="Sleep claim awaiting review", evidence_ids=[], assertion_status="reported",
                observed_at=now - timedelta(days=1), created_by_caregiver_id=None,
            )
            extraction = CaptureAnalysis(
                id="document-extraction", person_id="person-a", caregiver_id="caregiver-a",
                source_event_id=None, source_evidence_id="document-evidence", source_text=(
                    "Clinic letter: sleep disturbance was reported by the family."
                ), extraction_status="available", candidates={
                    "kind": "document_extraction_v1", "filename": "clinic-letter.txt",
                    "extraction_method": "server_text",
                }, review_history=[],
            )
            other_caregiver_extraction = CaptureAnalysis(
                id="other-caregiver-document", person_id="person-a", caregiver_id="caregiver-b",
                source_evidence_id="other-caregiver-evidence", source_text="Private document extraction",
                extraction_status="available", candidates={"kind": "document_extraction_v1"}, review_history=[],
            )
            failed_extraction = CaptureAnalysis(
                id="failed-document-extraction", person_id="person-a", caregiver_id="caregiver-a",
                source_evidence_id="failed-document-evidence", source_text="Must not be retrieved",
                extraction_status="failed", candidates={"kind": "document_extraction_v1"}, review_history=[],
            )
            other_person_event = CareEvent(
                id="other-person-event", person_id="person-b", event_type="daily_observation",
                status="recorded", occurred_at=now - timedelta(days=1), title="Private sleep event",
                description="Private record for another person", evidence_ids=[], created_by_caregiver_id="caregiver-c",
            )
            other_person_assertion = CareAssertion(
                id="other-person-assertion", person_id="person-b", subject_type="person",
                subject_id="person-b", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Private assertion", source_label="Other record", source_text="Private",
                evidence_ids=[], assertion_status="accepted", observed_at=now - timedelta(days=1),
            )
            other_person_extraction = CaptureAnalysis(
                id="other-person-document", person_id="person-b", caregiver_id="caregiver-c",
                source_evidence_id="other-person-evidence", source_text="Private extraction for another person",
                extraction_status="available", candidates={"kind": "document_extraction_v1"}, review_history=[],
            )
            prior_session = ConversationSession(
                id="prior-session", person_id="person-a", caregiver_id="caregiver-a", status="archived"
            )
            other_person_session = ConversationSession(
                id="other-person-session", person_id="person-b", caregiver_id="caregiver-c", status="active"
            )
            other_caregiver_session = ConversationSession(
                id="other-caregiver-session", person_id="person-a", caregiver_id="caregiver-b", status="active"
            )
            db.add_all([
                event, *longitudinal_events, accepted, pending, rejected, superseded,
                caregiver_reviewed, extraction, other_caregiver_extraction,
                failed_extraction, other_person_event, other_person_assertion,
                other_person_extraction, prior_session, other_caregiver_session, other_person_session,
            ])
            db.flush()
            db.add_all([
                ConversationMessage(
                    id="prior-session-message", session_id="prior-session", role="user",
                    content="We discussed sleep and night waking last time.", evidence_ids_json="[]",
                ),
                ConversationMessage(
                    id="other-caregiver-message", session_id="other-caregiver-session", role="user",
                    content="Private sleep details from another caregiver.", evidence_ids_json="[]",
                ),
                ConversationMessage(
                    id="other-person-message", session_id="other-person-session", role="user",
                    content="Private sleep details for another person.", evidence_ids_json="[]",
                ),
            ])
            db.commit()

            # This is intentionally a new Companion session: retrieval must find
            # the previous session's message through the shared person/caregiver scope.
            context = build_unified_lcr_context(
                db, "person-a", "caregiver-a", query="sleep", limit_per_source=20,
            )
            temporal_context = build_unified_lcr_context(
                db, "person-a", "caregiver-a", query="sleep yesterday", limit_per_source=20,
            )
            journey_context = build_unified_lcr_context(
                db, "person-a", "caregiver-a", query="Give me the history of sleep",
                limit_per_source=20, include_all_history=True,
            )

        sources = context["sources"]
        assert {row["id"] for row in sources["events"]["items"]} >= {"daily-care-event"}
        assert {row["id"] for row in sources["document_extractions"]["items"]} == {"document-extraction"}
        assert {row["id"] for row in sources["assertions"]["items"]} == {
            "accepted-assertion", "caregiver-reviewed-assertion",
            "rejected-assertion", "superseded-assertion",
        }
        historical_assertions = {
            row["id"]: row["information_status"]
            for row in sources["assertions"]["items"]
        }
        assert historical_assertions["rejected-assertion"] == "historical_assertion"
        assert historical_assertions["superseded-assertion"] == "historical_assertion"
        assert "pending-assertion" not in str(sources["assertions"])
        assert "failed-document-extraction" not in str(sources["document_extractions"])
        assert "other-caregiver-document" not in str(sources["document_extractions"])
        assert "other-person-document" not in str(sources["document_extractions"])
        assert "other-person-event" not in str(sources["events"])
        assert "other-person-assertion" not in str(sources["assertions"])
        history = sources["companion_conversation"]["items"]
        assert {row["id"] for row in history} == {"prior-session-message"}
        assert "other-person-message" not in str(history)
        assert history[0]["information_status"] == "conversation_context_only"
        assert sources["document_extractions"]["items"][0]["information_status"] == "document_extracted_unreviewed"
        active_reviewed_assertions = [
            row for row in sources["assertions"]["items"]
            if row["assertion_status"] in {"accepted", "caregiver_reviewed"}
        ]
        assert {row["id"] for row in active_reviewed_assertions} == {
            "accepted-assertion", "caregiver-reviewed-assertion",
        }
        assert all(
            item["information_status"] == "caregiver_reviewed_report"
            and item["epistemic_state"] == "reported"
            for item in active_reviewed_assertions
        ), "caregiver acceptance/review must not upgrade a reported assertion to verified"
        accepted_document = next(row for row in active_reviewed_assertions if row["id"] == "accepted-assertion")
        assert accepted_document["source_context"]["authority"] == "caregiver_reviewed_report_not_independently_verified"
        assert accepted_document["source_context"]["document_candidate"]["negation_status"] == "explicitly_negated"
        companion_prompt_context = format_lcr_context_for_prompt(context)
        assert "caregiver_reviewed_report_not_independently_verified" in companion_prompt_context
        assert '"negation_status":"explicitly_negated"' in companion_prompt_context
        assert '"page_number":2' in companion_prompt_context
        assert context["retrieval_contract"]["retrieval_strategy"] == "hybrid_lexical_semantic_temporal"
        assert temporal_context["retrieval_contract"]["temporal_window"]["basis"] == "explicit_user_temporal_expression"
        assert "daily-care-event" in {row["id"] for row in temporal_context["sources"]["events"]["items"]}
        journey_events = journey_context["sources"]["events"]
        assert journey_context["history_coverage"] == "complete_history_scan_ranked_subset"
        assert journey_events["candidate_scan_complete"] is True
        assert journey_events["candidate_count"] == 4
        assert journey_events["has_more"] is False
        assert {row["id"] for row in journey_events["items"]} == {
            "daily-care-event", "sleep-history-86d", "sleep-history-43d", "sleep-history-6d",
        }
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_temporal_retrieval_rejects_invalid_and_reversed_explicit_dates():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="Invalid calendar date"):
        _temporal_window("What happened on 2026-02-30?", now=now)
    with pytest.raises(ValueError, match="start date is after the end date"):
        _temporal_window(
            "Show care history between 2026-11-07 and 2026-11-01",
            now=now,
        )


def test_temporal_retrieval_uses_exclusive_before_and_after_date_boundaries():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    before = _temporal_window("What happened before 2026-11-06?", now=now)
    after = _temporal_window("What happened after 2026-11-05?", now=now)
    assert before is not None and after is not None
    assert before["start"] == "0002-01-01T00:00:00+00:00"
    assert before["end"].startswith("2026-11-05T23:59:59")
    assert after["start"] == "2026-11-06T00:00:00+00:00"
    assert after["end"].startswith("9998-12-31T23:59:59")


def test_temporal_retrieval_resolves_explicit_relative_windows():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    window = _temporal_window("What happened yesterday with sleep?", now=now)
    assert window is not None
    assert window["start"] == "2026-11-05T00:00:00+00:00"
    assert window["end"].startswith("2026-11-05T23:59:59")
    assert _temporal_window("Tell me about sleep", now=now) is None
    local_window = _temporal_window(
        "What happened yesterday?",
        now=datetime(2026, 11, 6, 2, tzinfo=UTC),
        timezone_name="America/Los_Angeles",
    )
    assert local_window is not None
    assert local_window["start"] == "2026-11-04T08:00:00+00:00"
    assert local_window["timezone"] == "America/Los_Angeles"


def test_hybrid_retrieval_four_sources_and_status_gate_on_local_sqlite():
    engine, connection = _sqlite_retrieval_db()
    try:
        now = datetime.now(UTC)
        with connection.begin():
            connection.execute(Person.__table__.insert(), {"id": "person-local", "name": "Care recipient"})
            connection.execute(Caregiver.__table__.insert(), {
                "id": "caregiver-local", "name": "Caregiver", "relationship": "family", "person_id": "person-local"
            })
            connection.execute(CareEvent.__table__.insert(), {
                "id": "daily-event-local", "person_id": "person-local", "event_type": "daily_observation",
                "status": "recorded", "occurred_at": now - timedelta(days=1), "title": "Sleep disruption",
                "description": "Repeated waking overnight", "evidence_ids": [], "created_by_caregiver_id": "caregiver-local",
            })
            connection.execute(CareActionOutcome.__table__.insert(), {
                "id": "action-outcome-local", "person_id": "person-local",
                "action_id": "follow-up-action-local", "action_type": "care_follow_up",
                "outcome_status": "completed", "notes": "Caregiver confirmed the sleep follow-up was completed.",
                "correction_text": None, "evidence_ids": [],
                "created_by_caregiver_id": "caregiver-local", "created_at": now,
            })
            connection.execute(CareAssertion.__table__.insert(), [
                {
                    "id": "accepted-local", "person_id": "person-local", "subject_type": "person", "subject_id": "person-local",
                    "field": "sleep_pattern", "assertion_kind": "reported_state", "asserted_value": "Repeated waking",
                    "source_label": "Caregiver review", "source_text": "Sleep interrupted", "evidence_ids": [],
                    "assertion_status": "accepted", "observed_at": now - timedelta(days=1),
                },
                {
                    "id": "pending-local", "person_id": "person-local", "subject_type": "person", "subject_id": "person-local",
                    "field": "sleep_pattern", "assertion_kind": "reported_state", "asserted_value": "Pending claim",
                    "source_label": "Model proposal", "source_text": "Unreviewed sleep claim", "evidence_ids": [],
                    "assertion_status": "reported", "observed_at": now - timedelta(days=1),
                },
                {
                    "id": "caregiver-reviewed-local", "person_id": "person-local", "subject_type": "person", "subject_id": "person-local",
                    "field": "sleep_pattern", "assertion_kind": "caregiver_reviewed_daily_care_observation",
                    "asserted_value": "Caregiver reviewed sleep observation", "source_label": "Caregiver review",
                    "source_text": "Original and corrected Daily Care values", "evidence_ids": [],
                    "assertion_status": "caregiver_reviewed", "observed_at": now - timedelta(days=1),
                    "created_by_caregiver_id": "caregiver-local",
                },
            ])
            connection.execute(CaptureAnalysis.__table__.insert(), {
                "id": "document-local", "person_id": "person-local", "caregiver_id": "caregiver-local",
                "source_evidence_id": "document-evidence-local", "source_text": "Clinic letter reports sleep disturbance.",
                "extraction_status": "available", "candidates": {"kind": "document_extraction_v1"}, "review_history": [],
            })
            connection.execute(ConversationSession.__table__.insert(), {
                "id": "old-session-local", "person_id": "person-local", "caregiver_id": "caregiver-local", "status": "archived",
            })
            connection.execute(ConversationMessage.__table__.insert(), {
                "id": "old-message-local", "session_id": "old-session-local", "role": "user",
                "content": "We discussed sleep and night waking before.", "evidence_ids_json": "[]",
                "source_record_refs_json": "[]",
            })
        with Session(bind=connection) as db:
            context = build_unified_lcr_context(db, "person-local", "caregiver-local", query="sleep")
            yesterday = build_unified_lcr_context(db, "person-local", "caregiver-local", query="sleep yesterday")
        sources = context["sources"]
        assert "daily-event-local" in {row["id"] for row in sources["events"]["items"]}
        assert {row["id"] for row in sources["action_outcomes"]["items"]} == {"action-outcome-local"}
        assert sources["action_outcomes"]["items"][0]["outcome_status"] == "completed"
        assert "Caregiver confirmed the sleep follow-up was completed." in str(sources["action_outcomes"]["items"][0])
        assert {row["id"] for row in sources["document_extractions"]["items"]} == {"document-local"}
        assert {row["id"] for row in sources["assertions"]["items"]} == {
            "accepted-local", "caregiver-reviewed-local"
        }
        assert "pending-local" not in str(sources["assertions"])
        assert {row["id"] for row in sources["companion_conversation"]["items"]} == {"old-message-local"}
        assert "daily-event-local" in {row["id"] for row in yesterday["sources"]["events"]["items"]}
        assert sources["document_extractions"]["items"][0]["information_status"] == "document_extracted_unreviewed"
    finally:
        connection.close()
        engine.dispose()


def test_last_year_uses_previous_calendar_year_in_caregiver_timezone():
    window = _temporal_window(
        "What happened last year?",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="America/Los_Angeles",
    )
    assert window is not None
    assert window["basis"] == "explicit_previous_calendar_year"
    assert window["timezone"] == "America/Los_Angeles"
    assert window["start"] == "2025-01-01T08:00:00+00:00"
    assert window["end"] == "2026-01-01T07:59:59.999999+00:00"


def test_past_year_uses_rolling_twelve_month_window_in_caregiver_timezone():
    window = _temporal_window(
        "What changed in the past year?",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="America/Los_Angeles",
    )
    assert window is not None
    assert window["basis"] == "explicit_rolling_past_year"
    assert window["timezone"] == "America/Los_Angeles"
    assert window["start"] == "2025-11-15T08:00:00+00:00"
    assert window["end"] == "2026-11-16T07:59:59.999999+00:00"


def test_past_year_clamps_leap_day_when_prior_year_is_not_leap():
    window = _temporal_window(
        "Show me the past year",
        now=datetime(2025, 2, 28, 12, tzinfo=UTC),
    )
    assert window is not None
    assert window["start"] == "2024-02-28T00:00:00+00:00"


def test_this_year_is_bounded_from_local_january_first_through_today():
    window = _temporal_window(
        "Show me what changed this year.",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="UTC",
    )
    assert window is not None
    assert window["basis"] == "explicit_current_calendar_year"
    assert window["start"] == "2026-01-01T00:00:00+00:00"
    assert window["end"].startswith("2026-11-15T23:59:59")


def test_explicit_year_query_scans_the_full_calendar_year():
    window = _temporal_window(
        "What happened in 2025?",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="UTC",
    )
    assert window is not None
    assert window["start"] == "2025-01-01T00:00:00+00:00"
    assert window["end"] == "2025-12-31T23:59:59.999999+00:00"


def test_explicit_year_range_uses_caregiver_local_timezone():
    window = _temporal_window(
        "Show me the care history during 2024.",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="America/Los_Angeles",
    )
    assert window is not None
    assert window["start"] == "2024-01-01T08:00:00+00:00"
    assert window["end"] == "2025-01-01T07:59:59.999999+00:00"


def test_calendar_year_filter_does_not_require_year_in_care_note_or_message_text():
    engine, connection = _sqlite_retrieval_db()
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    window = _temporal_window("What happened in 2025?", now=now, timezone_name="UTC")
    try:
        with Session(bind=connection) as db:
            db.add(Person(id="year-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="year-caregiver", name="Caregiver", relationship="family",
                person_id="year-person",
            ))
            db.flush()
            db.add_all([
                CareEvent(
                    id="event-in-2025", person_id="year-person",
                    event_type="daily_observation", status="recorded",
                    occurred_at=datetime(2025, 6, 10, 9, tzinfo=UTC),
                    title="Night waking", description="Caregiver recorded repeated waking.",
                    evidence_ids=[], created_by_caregiver_id="year-caregiver",
                ),
                CareEvent(
                    id="event-in-2024", person_id="year-person",
                    event_type="daily_observation", status="recorded",
                    occurred_at=datetime(2024, 12, 31, 9, tzinfo=UTC),
                    title="Night waking", description="Caregiver recorded repeated waking.",
                    evidence_ids=[], created_by_caregiver_id="year-caregiver",
                ),
            ])
            session = ConversationSession(
                id="year-session", person_id="year-person", caregiver_id="year-caregiver", status="active"
            )
            db.add(session)
            db.flush()
            db.add_all([
                ConversationMessage(
                    id="message-in-2025", session_id=session.id, role="user",
                    content="We discussed night waking.", evidence_ids_json="[]",
                    created_at=datetime(2025, 6, 10, 10, tzinfo=UTC),
                ),
                ConversationMessage(
                    id="message-in-2024", session_id=session.id, role="user",
                    content="We discussed night waking.", evidence_ids_json="[]",
                    created_at=datetime(2024, 12, 31, 10, tzinfo=UTC),
                ),
            ])
            db.commit()
            care_context = build_lcr_context(
                db, "year-person", caregiver_id="year-caregiver",
                query="What happened in 2025?", temporal_window=window,
            )
            conversation_context = _conversation_context(
                db, person_id="year-person", caregiver_id="year-caregiver",
                query="What happened in 2025?", limit=8, temporal_window=window,
            )
            exact_date_window = _temporal_window(
                "What happened on 2025-06-10?", now=now, timezone_name="UTC"
            )
            exact_date_care_context = build_lcr_context(
                db, "year-person", caregiver_id="year-caregiver",
                query="What happened on 2025-06-10?", temporal_window=exact_date_window,
            )
            exact_date_conversation_context = _conversation_context(
                db, person_id="year-person", caregiver_id="year-caregiver",
                query="What happened on 2025-06-10?", limit=8,
                temporal_window=exact_date_window,
            )
        assert {item["id"] for item in care_context["sources"]["events"]["items"]} == {"event-in-2025"}
        assert {item["id"] for item in conversation_context["items"]} == {"message-in-2025"}
        assert {item["id"] for item in exact_date_care_context["sources"]["events"]["items"]} == {"event-in-2025"}
        assert {item["id"] for item in exact_date_conversation_context["items"]} == {"message-in-2025"}
    finally:
        connection.close()
        engine.dispose()


def test_postgres_companion_temporal_query_scans_entire_window_before_returning_bounded_page():
    engine, connection, schema = _postgres_conversation_db()
    try:
        now = datetime(2026, 11, 15, 12, tzinfo=UTC)
        window = _temporal_window(
            "What happened in 2025?", now=now, timezone_name="UTC"
        )
        with Session(bind=connection) as db:
            person = Person(id="temporal-scan-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="temporal-scan-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()
            session = ConversationSession(
                id="temporal-scan-session", person_id=person.id,
                caregiver_id=caregiver.id, status="archived",
            )
            db.add(session)
            db.flush()
            db.add_all([
                ConversationMessage(
                    id=f"temporal-message-{index:02d}", session_id=session.id,
                    role="user", content=f"Care history entry {index}.",
                    evidence_ids_json="[]",
                    created_at=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=index * 25),
                )
                for index in range(12)
            ])
            db.add(ConversationMessage(
                id="outside-temporal-window", session_id=session.id,
                role="user", content="This is outside the requested year.",
                evidence_ids_json="[]",
                created_at=datetime(2024, 12, 31, tzinfo=UTC),
            ))
            db.commit()
            result = _conversation_context(
                db, person_id=person.id, caregiver_id=caregiver.id,
                query="What happened in 2025?", limit=8, temporal_window=window,
            )
        assert result["candidate_count"] == 12
        assert result["candidate_scan_complete"] is True
        assert result["has_more"] is True
        assert len(result["items"]) == 8
        assert "outside-temporal-window" not in {item["id"] for item in result["items"]}
    finally:
        _close_postgres_conversation_db(engine, connection, schema)


def test_temporal_retrieval_uses_calendar_months_not_fixed_30_day_approximation():
    # March 31 minus one calendar month clamps to February 28 in 2026.
    window = _temporal_window(
        "What happened in the last 1 month?",
        now=datetime(2026, 3, 31, 12, tzinfo=UTC),
    )
    assert window is not None
    assert window["start"] == "2026-02-28T00:00:00+00:00"
    assert window["end"].startswith("2026-03-31T23:59:59")

    # Leap-year February retains its final valid day.
    leap_window = _temporal_window(
        "What happened in the last 1 month?",
        now=datetime(2024, 3, 31, 12, tzinfo=UTC),
    )
    assert leap_window is not None
    assert leap_window["start"] == "2024-02-29T00:00:00+00:00"

def test_query_retrieval_scans_beyond_250_candidates_and_keeps_old_exact_match():
    engine, connection = _sqlite_retrieval_db()
    try:
        now = datetime.now(UTC)
        with connection.begin():
            connection.execute(Person.__table__.insert(), {
                "id": "person-wide-history", "name": "Care recipient"
            })
            connection.execute(Caregiver.__table__.insert(), {
                "id": "caregiver-wide-history", "name": "Caregiver",
                "relationship": "family", "person_id": "person-wide-history",
            })
            rows = [{
                "id": f"sleep-history-{index:03d}",
                "person_id": "person-wide-history",
                "event_type": "daily_observation",
                "status": "recorded",
                "occurred_at": now - timedelta(days=index),
                "title": "Sleep observation",
                "description": f"Caregiver recorded sleep observation number {index}.",
                "evidence_ids": [],
                "created_by_caregiver_id": "caregiver-wide-history",
            } for index in range(1, 301)]
            rows.append({
                "id": "old-exact-sleep-match",
                "person_id": "person-wide-history",
                "event_type": "daily_observation",
                "status": "recorded",
                "occurred_at": now - timedelta(days=500),
                "title": "Sleep severe worsening",
                "description": "Caregiver recorded severe worsening of sleep.",
                "evidence_ids": [],
                "created_by_caregiver_id": "caregiver-wide-history",
            })
            connection.execute(CareEvent.__table__.insert(), rows)

        with Session(bind=connection) as db:
            context = build_unified_lcr_context(
                db, "person-wide-history", "caregiver-wide-history",
                query="sleep severe worsening", limit_per_source=20,
            )

        events = context["sources"]["events"]
        assert events["candidate_count"] == 301
        assert events["candidate_scan_complete"] is True
        assert events["has_more"] is True
        assert "old-exact-sleep-match" in {
            item["id"] for item in events["items"]
        }
    finally:
        connection.close()
        engine.dispose()

def test_one_failed_care_source_does_not_hide_successful_source_reads():
    from sqlalchemy.exc import SQLAlchemyError

    engine, connection = _sqlite_retrieval_db()
    try:
        with connection.begin():
            connection.execute(Person.__table__.insert(), {"id": "person-source-failure", "name": "Care recipient"})
            connection.execute(Caregiver.__table__.insert(), {
                "id": "caregiver-source-failure",
                "name": "Caregiver",
                "relationship": "family",
                "person_id": "person-source-failure",
            })
        with Session(bind=connection) as db:
            original_execute = db.execute

            def fail_only_events(statement, *args, **kwargs):
                source_names = {
                    getattr(source, "name", None)
                    for source in statement.get_final_froms()
                }
                if "care_events" in source_names:
                    raise SQLAlchemyError("simulated care-events source outage")
                return original_execute(statement, *args, **kwargs)

            from unittest.mock import patch
            with patch.object(db, "execute", side_effect=fail_only_events):
                context = build_lcr_context(
                    db,
                    "person-source-failure",
                    caregiver_id="caregiver-source-failure",
                    include_all_history=True,
                )

        assert context["sources"]["events"]["status"] == "failed"
        assert "events" in context["failed_sources"]
        assert "events" in context["failed_required_sources"]
        assert context["schema_status"] == "partial"
        assert context["history_coverage"] == "partial_source_history"
        assert context["retrieval_scan_complete"] is False
        successful_sources = [
            source for name, source in context["sources"].items()
            if name != "events" and source["status"] in {"available", "empty"}
        ]
        assert successful_sources, "a failed source must not erase successful source results"
    finally:
        connection.close()
        engine.dispose()




def test_postgres_lcr_retrieval_keeps_partial_document_pages_and_discloses_gaps():
    """Companion must see readable pages and explicit page gaps, not an empty document source."""
    from app.services.lcr_context import _load_source_schema

    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            db.add(Person(id="partial-document-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="partial-document-caregiver",
                name="Caregiver",
                relationship="family",
                person_id="partial-document-person",
            ))
            db.flush()
            db.add(CaptureAnalysis(
                id="partial-document-analysis",
                person_id="partial-document-person",
                caregiver_id="partial-document-caregiver",
                source_evidence_id="partial-document-evidence",
                source_text=(
                    "[Page 1]\\nMedication list: take the listed tablet with breakfast.\\n"
                    "[Page 2 OCR]\\n[No readable text detected on page 2]"
                ),
                extraction_status="partial",
                candidates={
                    "kind": "document_extraction_v1",
                    "page_count": 2,
                    "processed_page_numbers": [1, 2],
                    "unreadable_page_numbers": [2],
                    "unprocessed_page_numbers": [],
                    "retryable_page_numbers": [],
                    "retry_exhausted_page_numbers": [],
                    "extraction_method": "server_pdf_ocr",
                    "document_intelligence": {
                        "status": "partial",
                        "observations": [{"source_quote": "take the listed tablet with breakfast"}],
                    },
                },
                review_history=[],
            ))
            db.add(CaptureAnalysis(
                id="partial-interpretation-analysis",
                person_id="partial-document-person",
                caregiver_id="partial-document-caregiver",
                source_evidence_id="partial-interpretation-evidence",
                source_text="Medication list: the original text was extracted successfully.",
                extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1",
                    "page_count": 2,
                    "processed_page_numbers": [1],
                    "unreadable_page_numbers": [],
                    "unprocessed_page_numbers": [2],
                    "extraction_method": "server_pdf_text",
                    "document_intelligence": {"status": "partial", "observations": []},
                },
                review_history=[],
            ))
            db.commit()
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db,
                "partial-document-person",
                caregiver_id="partial-document-caregiver",
                query="medication",
            )
            uncertainty_context = build_lcr_context(
                db,
                "partial-document-person",
                caregiver_id="partial-document-caregiver",
                query="What is uncertain or unreadable in the documents?",
            )
        documents = context["sources"]["document_extractions"]
        assert documents["returned_count"] == 2
        item = next(value for value in documents["items"] if value["id"] == "partial-document-analysis")
        assert item["information_status"] == "document_extraction_partial"
        assert item["extraction_metadata"]["unreadable_page_numbers"] == [2]
        assert item["extraction_metadata"]["interpretation_status"] == "partial"
        assert item["extraction_metadata"]["unreviewed_candidate_count"] == 1
        assert "Unreadable pages: 2." in item["interpretation_limit"]
        interpretation_only = next(
            value for value in documents["items"] if value["id"] == "partial-interpretation-analysis"
        )
        assert interpretation_only["information_status"] == "document_interpretation_partial"
        assert interpretation_only["extraction_status"] == "available"
        prompt_context = format_lcr_context_for_prompt(context)
        assert "document_extraction_partial" in prompt_context
        assert "document_interpretation_partial" in prompt_context
        assert "unreadable_page_numbers" in prompt_context
        assert '"source_quote"' not in prompt_context, (
            "unreviewed model candidate objects must not be leaked into the LCR retrieval prompt"
        )
        uncertainty_documents = uncertainty_context["sources"]["document_extractions"]
        assert any(item["id"] == "partial-document-analysis" for item in uncertainty_documents["items"]), (
            "a question about uncertainty must retrieve a partially unreadable document even when its text does not contain the word"
        )
    finally:
        _load_source_schema.cache_clear()
        _close_postgres_conversation_db(engine, connection, schema)



def test_postgres_appointment_retrieval_preserves_timezone_and_preparation_context():
    """Companion must retrieve the real appointment questions, documents and time basis."""
    from app.services.lcr_context import _load_source_schema

    engine, connection, schema = _postgres_conversation_db()
    try:
        scheduled_at = datetime(2026, 12, 2, 14, 30, tzinfo=UTC)
        with Session(bind=connection) as db:
            db.add(Person(id="appointment-context-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="appointment-context-caregiver",
                name="Family caregiver",
                relationship="family",
                person_id="appointment-context-person",
            ))
            db.flush()
            db.add(Appointment(
                id="appointment-context-record",
                person_id="appointment-context-person",
                title="Memory clinic follow-up",
                status="scheduled",
                scheduled_at=scheduled_at,
                scheduled_at_timezone="Africa/Lagos",
                location="Memory clinic",
                location_type="clinic",
                provider="Dr. Example",
                healthcare_org="Community Health Centre",
                preparation_notes="Ask about the recent sleep change and medication timing.",
                documents_to_bring=["Current medication list", "Recent sleep notes"],
                medication_questions=["Should the evening dose time change?"],
                travel_notes="Family caregiver will accompany the person.",
                time_provenance="caregiver_reported",
                location_provenance="caregiver_reported",
                evidence_ids=[],
                created_by_caregiver_id="appointment-context-caregiver",
            ))
            db.commit()
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db,
                "appointment-context-person",
                caregiver_id="appointment-context-caregiver",
                query="appointment medication",
            )
        appointments = context["sources"]["appointments"]
        assert appointments["returned_count"] == 1
        item = appointments["items"][0]
        assert item["scheduled_at_timezone"] == "Africa/Lagos"
        assert item["location"] == "Memory clinic"
        assert item["provider"] == "Dr. Example"
        assert item["healthcare_org"] == "Community Health Centre"
        assert item["preparation_notes"] == "Ask about the recent sleep change and medication timing."
        assert item["documents_to_bring"] == ["Current medication list", "Recent sleep notes"]
        assert item["medication_questions"] == ["Should the evening dose time change?"]
        assert item["travel_notes"] == "Family caregiver will accompany the person."
        prompt_context = format_lcr_context_for_prompt(context)
        assert "Africa/Lagos" in prompt_context
        assert "Current medication list" in prompt_context
        assert "Should the evening dose time change?" in prompt_context
    finally:
        _load_source_schema.cache_clear()
        _close_postgres_conversation_db(engine, connection, schema)



def test_postgres_appointment_workspace_notes_are_retrievable_without_becoming_lcr_facts():
    """Companion can retrieve visit outcomes while preserving their caregiver-recorded status."""
    from app.services.lcr_context import _load_source_schema

    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            db.add(Person(id="appointment-workspace-person", name="Care recipient"))
            db.flush()
            db.add(Caregiver(
                id="appointment-workspace-caregiver",
                name="Family caregiver",
                relationship="family",
                person_id="appointment-workspace-person",
            ))
            db.flush()
            db.add(AppointmentWorkspaceRecord(
                id="appointment-workspace-record",
                person_id="appointment-workspace-person",
                appointment_id=None,
                created_by_caregiver_id="appointment-workspace-caregiver",
                updated_by_caregiver_id="appointment-workspace-caregiver",
                status="ready_for_review",
                version_number=3,
                last_revision_reason="Caregiver saved the visit outcome",
                payload={
                    "visitPurpose": "Memory clinic follow-up",
                    "clinician": "Dr. Example",
                    "clinicianFindings": "The clinician said the sleep pattern should be tracked for two weeks.",
                    "decisions": "Continue the current routine until the follow-up.",
                    "instructions": "Bring the sleep notes to the next visit.",
                    "medicationQuestions": "Ask whether the evening dose timing should change.",
                    "unanswered": "The family is still waiting for the blood test result.",
                    "followUps": [{
                        "action": "Call the clinic about the blood test result",
                        "owner": "Family caregiver",
                        "due": "2026-12-04",
                        "status": "Open",
                        "open_loop_id": "appointment-follow-up-loop",
                    }],
                    "epistemic_boundary": {
                        "caregiver_report": "caregiver_reported",
                        "clinician_findings": "caregiver_recorded_clinician_statement_not_independently_verified",
                        "ai_suggestions_written_as_clinical_facts": False,
                        "lcr_reconciliation": "not_performed",
                    },
                },
            ))
            db.commit()
            _load_source_schema.cache_clear()
            context = build_lcr_context(
                db,
                "appointment-workspace-person",
                caregiver_id="appointment-workspace-caregiver",
                query="What did the clinician say about sleep and what remains unanswered?",
            )
        workspaces = context["sources"]["appointment_workspaces"]
        assert workspaces["returned_count"] == 1
        item = workspaces["items"][0]
        assert item["information_status"] == "appointment_workspace_caregiver_notes"
        assert item["status"] == "ready_for_review"
        assert item["version_number"] == 3
        assert item["payload"]["clinicianFindings"].startswith("The clinician said")
        assert item["payload"]["unanswered"].startswith("The family is still waiting")
        assert item["payload"]["epistemic_boundary"]["clinician_findings"] == (
            "caregiver_recorded_clinician_statement_not_independently_verified"
        )
        assert item["payload"]["followUps"][0]["open_loop_id"] == "appointment-follow-up-loop"
        prompt_context = format_lcr_context_for_prompt(context)
        assert "appointment_workspace_caregiver_notes" in prompt_context
        assert "not independently verified" in prompt_context
        assert "lcr_reconciliation" in prompt_context
    finally:
        _load_source_schema.cache_clear()
        _close_postgres_conversation_db(engine, connection, schema)
