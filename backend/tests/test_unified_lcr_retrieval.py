"""PostgreSQL integration tests for unified Companion retrieval scope."""
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Column, DateTime, String, Table, Text, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.models.capture_analysis import CaptureAnalysis
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.time_location import Appointment
from app.models.appointment_workspace import AppointmentWorkspaceRecord, AppointmentWorkspaceRevision
from app.models.caregiver_intelligence import CaregiverHandoff, CaregiverProfile
from app.core.caregiver_enums import HandoffAcceptanceStatus
from app.models.care_continuity import CareActionOutcome, CareAssertion, CareStateRevision, EvidenceObservation
from app.models.collaboration import ConversationMessage, ConversationSession
from app.models.ai_output_governance import AIOutputLCRProposal, AIOutputLCRDecision
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput
from app.services.lcr_context import _apply_text_search_filter, _expanded_search_terms, _information_status, _load_source_schema, _retrieval_score, build_lcr_context, format_lcr_context_for_prompt
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
            Person.__table__, Caregiver.__table__, CareEvent.__table__, Appointment.__table__,
            AppointmentWorkspaceRecord.__table__, AppointmentWorkspaceRevision.__table__,
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
        Person.__table__, Caregiver.__table__, CareEvent.__table__, Appointment.__table__,
        AppointmentWorkspaceRecord.__table__, AppointmentWorkspaceRevision.__table__,
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
        assert facts["sleep-fact-old"] == "historical_fact"
        assert facts["sleep-fact-current"] != "historical_fact"
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
            withdrawn = CareAssertion(
                id="withdrawn-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Previously verified sleep claim", source_label="Caregiver withdrawal",
                source_text="This previously verified sleep claim was withdrawn.",
                evidence_ids=[], assertion_status="withdrawn", epistemic_state="verified",
                observed_at=now - timedelta(days=2), created_by_caregiver_id="caregiver-a",
            )
            retracted = CareAssertion(
                id="retracted-assertion", person_id="person-a", subject_type="person",
                subject_id="person-a", field="sleep_pattern", assertion_kind="reported_state",
                asserted_value="Previously accepted sleep claim", source_label="Caregiver retraction",
                source_text="The caregiver later retracted this previously accepted claim.",
                evidence_ids=[], assertion_status="retracted", epistemic_state="verified",
                observed_at=now - timedelta(days=3), created_by_caregiver_id="caregiver-a",
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
                event, *longitudinal_events, accepted, pending, rejected, superseded, withdrawn, retracted,
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
        assert {row["id"] for row in sources["document_extractions"]["items"]} == {
            "document-extraction", "failed-document-extraction",
        }
        failed_document = next(
            row for row in sources["document_extractions"]["items"]
            if row["id"] == "failed-document-extraction"
        )
        assert failed_document["information_status"] == "document_extraction_failed"
        assert {row["id"] for row in sources["assertions"]["items"]} == {
            "accepted-assertion", "caregiver-reviewed-assertion",
            "rejected-assertion", "superseded-assertion", "withdrawn-assertion",
            "retracted-assertion",
        }
        historical_assertions = {
            row["id"]: row["information_status"]
            for row in sources["assertions"]["items"]
        }
        assert historical_assertions["rejected-assertion"] == "historical_assertion"
        assert historical_assertions["superseded-assertion"] == "historical_assertion"
        assert historical_assertions["withdrawn-assertion"] == "historical_assertion"
        assert historical_assertions["retracted-assertion"] == "historical_assertion"
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
        assert context["retrieval_contract"]["retrieval_strategy"] == "person_scoped_lexical_temporal"
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


def test_legacy_document_page_gaps_override_available_aggregate_status():
    from app.services.lcr_context import _information_status

    status, explanation = _information_status(
        "document_extractions",
        {
            "extraction_status": "available",
            "extraction_metadata": {
                "page_count": 4,
                "processed_page_numbers": [1, 2, 4],
                "unprocessed_page_numbers": [3],
                "unreadable_page_numbers": [],
            },
        },
    )

    assert status == "document_extraction_partial"
    assert "Pages not yet processed: 3." in explanation
    assert "do not treat this extraction as the complete document" in explanation


def test_temporal_retrieval_rejects_invalid_and_reversed_explicit_dates():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="Invalid calendar date"):
        _temporal_window("What happened on 2026-02-30?", now=now)
    with pytest.raises(ValueError, match="start date is after the end date"):
        _temporal_window(
            "Show care history between 2026-11-07 and 2026-11-01",
            now=now,
        )


def test_temporal_retrieval_resolves_named_month_and_day_dates():
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)

    month = _temporal_window("What happened in March 2025?", now=now)
    assert month is not None
    assert month["start"] == "2025-03-01T00:00:00+00:00"
    assert month["end"].startswith("2025-03-31T23:59:59")

    abbreviated_month = _temporal_window("Show me care from Sept 2024", now=now)
    assert abbreviated_month is not None
    assert abbreviated_month["start"] == "2024-09-01T00:00:00+00:00"
    assert abbreviated_month["end"].startswith("2024-09-30T23:59:59")

    full_date = _temporal_window("What happened on March 4th, 2025?", now=now)
    assert full_date is not None
    assert full_date["start"] == "2025-03-04T00:00:00+00:00"
    assert full_date["end"].startswith("2025-03-04T23:59:59")

    reversed_date = _temporal_window("What happened on 4 March 2025?", now=now)
    assert reversed_date is not None
    assert reversed_date["start"] == "2025-03-04T00:00:00+00:00"


def test_temporal_retrieval_rejects_invalid_named_month_dates():
    with pytest.raises(ValueError, match="Invalid calendar date"):
        _temporal_window(
            "What happened on February 30, 2025?",
            now=datetime(2026, 11, 6, 12, tzinfo=UTC),
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


def test_numbered_multi_year_window_is_rolling_and_timezone_aware():
    window = _temporal_window(
        "What changed in the last 2 years?",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
        timezone_name="America/Los_Angeles",
    )
    assert window is not None
    assert window["basis"] == "explicit_rolling_past_2_years"
    assert window["timezone"] == "America/Los_Angeles"
    assert window["start"] == "2024-11-15T08:00:00+00:00"
    assert window["end"] == "2026-11-16T07:59:59.999999+00:00"


def test_written_number_multi_year_window_is_understood():
    window = _temporal_window(
        "What happened over the last two years?",
        now=datetime(2026, 11, 15, 12, tzinfo=UTC),
    )
    assert window is not None
    assert window["basis"] == "explicit_rolling_past_2_years"
    assert window["start"] == "2024-11-15T00:00:00+00:00"


def test_numbered_multi_year_window_clamps_leap_day_and_rejects_unbounded_ranges():
    leap_window = _temporal_window(
        "Show me the past 2 years",
        now=datetime(2024, 2, 29, 12, tzinfo=UTC),
    )
    assert leap_window is not None
    assert leap_window["start"] == "2022-02-28T00:00:00+00:00"
    with pytest.raises(ValueError, match="between one and twenty years"):
        _temporal_window("Show me the last 0 years", now=datetime(2026, 11, 6, 12, tzinfo=UTC))
    with pytest.raises(ValueError, match="between one and twenty years"):
        _temporal_window("Show me the past 21 years", now=datetime(2026, 11, 6, 12, tzinfo=UTC))


def test_long_relative_windows_are_not_silently_clamped_to_one_year():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    months = _temporal_window("What changed in the last 18 months?", now=now)
    days = _temporal_window("What changed in the past 500 days?", now=now)
    weeks = _temporal_window("What changed in the last 80 weeks?", now=now)

    assert months is not None and months["start"] == "2025-05-15T00:00:00+00:00"
    assert days is not None and days["start"] == "2025-07-04T00:00:00+00:00"
    assert weeks is not None and weeks["start"] == "2025-05-05T00:00:00+00:00"


def test_unsupported_long_relative_windows_fail_instead_of_silently_narrowing():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    with pytest.raises(ValueError, match="between one day and twenty years"):
        _temporal_window("What happened in the last 8000 days?", now=now)
    with pytest.raises(ValueError, match="between one week and twenty years"):
        _temporal_window("What happened in the last 1100 weeks?", now=now)
    with pytest.raises(ValueError, match="between one month and twenty years"):
        _temporal_window("What happened in the last 241 months?", now=now)
    with pytest.raises(ValueError, match="between one and twenty years"):
        _temporal_window("What happened in the past 100 years?", now=now)


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


def test_withdrawn_or_superseded_care_facts_remain_historical():
    for item in (
        {"superseded_by_fact_id": "replacement-fact", "epistemic_status": "verified"},
        {"superseded_by_fact_id": None, "epistemic_status": "withdrawn"},
        {"superseded_by_fact_id": None, "epistemic_status": "superseded"},
    ):
        status, explanation = _information_status("facts", item)
        assert status == "historical_fact"
        assert "do not present it as current" in explanation


def test_invalidated_or_superseded_care_events_remain_historical():
    for event_status in ("invalidated", "superseded", "withdrawn", "deleted"):
        status, explanation = _information_status("events", {"status": event_status})
        assert status == "historical_event"
        assert "do not present it as current care information" in explanation

    status, explanation = _information_status("events", {"status": "unparsed_raw"})
    assert status == "unprocessed_capture"
    assert "not an accepted care event" in explanation


def test_topic_search_keeps_failed_document_extractions_as_visible_evidence_gaps():
    table = CaptureAnalysis.__table__
    statement, searchable = _apply_text_search_filter(
        table.select(),
        table,
        "document_extractions",
        ["medication"],
    )
    sql = str(statement)
    parameters = statement.compile().params

    assert searchable is True
    assert "source_text" in sql
    assert "extraction_status IN" in sql
    assert "failed" in str(parameters)
    assert "processing" in str(parameters)


def test_withdrawn_or_superseded_evidence_never_reads_as_current():
    for item in (
        {"evidence_status": "withdrawn", "epistemic_status": "caregiver_reported"},
        {"evidence_status": "superseded", "epistemic_status": "verified"},
        {"evidence_status": "active", "epistemic_status": "withdrawn"},
    ):
        status, explanation = _information_status("evidence", item)
        assert status == "historical_evidence"
        assert "do not treat it as current evidence" in explanation


def test_postgres_document_topic_retrieval_returns_verbatim_passage_beyond_preview():
    engine, connection, schema = _postgres_conversation_db()
    try:
        exact_passage = (
            "Page 4: The clinic letter says the medication was held until Friday, "
            "according to the discharge note."
        )
        source_text = ("Page 1: Routine administrative information. " * 180) + "\n" + exact_passage
        with Session(bind=connection) as db:
            person = Person(id="document-passage-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="document-passage-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()
            db.add(CaptureAnalysis(
                id="long-document-passage",
                person_id=person.id,
                caregiver_id=caregiver.id,
                source_evidence_id="long-document-evidence",
                source_text=source_text,
                extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1",
                    "filename": "long-clinic-letter.txt",
                },
                review_history=[],
            ))
            db.flush()
            context = build_lcr_context(
                db,
                person.id,
                caregiver_id=caregiver.id,
                query="What does the clinic letter say about medication?",
            )

        extraction = next(
            item for item in context["sources"]["document_extractions"]["items"]
            if item["id"] == "long-document-passage"
        )
        assert extraction["source_text"].endswith("… [truncated]")
        assert any(exact_passage in item["text"] for item in extraction["retrieved_source_passages"])
        prompt = format_lcr_context_for_prompt(context, max_chars=12000)
        assert exact_passage in prompt
    finally:
        connection.close()
        engine.dispose()


def test_postgres_lcr_preserves_exact_document_quotation_for_companion():
    engine, connection, schema = _postgres_conversation_db()
    try:
        quote = "The exact wording from the scanned clinic letter. " * 75
        with Session(bind=connection) as db:
            person = Person(id="exact-quote-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="exact-quote-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()
            db.add(CareAssertion(
                id="exact-document-quote-assertion",
                person_id=person.id,
                subject_type="person",
                subject_id=person.id,
                field="clinic_letter_statement",
                assertion_kind="document_reviewed_observation",
                asserted_value="A source-backed statement",
                source_label="Caregiver-reviewed document",
                source_text=quote,
                evidence_ids=["exact-document-evidence"],
                assertion_status="accepted",
                epistemic_state="reported",
                created_by_caregiver_id=caregiver.id,
                updated_by_caregiver_id=caregiver.id,
            ))
            db.flush()
            context = build_lcr_context(
                db, person.id, caregiver_id=caregiver.id, include_all_history=True,
            )
        assertion = next(
            item for item in context["sources"]["assertions"]["items"]
            if item["id"] == "exact-document-quote-assertion"
        )
        assert assertion["source_text"] == quote
        prompt = format_lcr_context_for_prompt(context, max_chars=12000)
        assert quote in prompt
    finally:
        connection.close()
        engine.dispose()


def test_postgres_lcr_keeps_failed_and_pending_document_extractions_visible():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            person = Person(id="document-status-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="document-status-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()
            db.add_all([
                CaptureAnalysis(
                    id="failed-document-status",
                    person_id=person.id,
                    caregiver_id=caregiver.id,
                    source_evidence_id="failed-document-evidence",
                    source_text="",
                    extraction_status="failed",
                    candidates={
                        "kind": "document_extraction_v1",
                        "filename": "unreadable-clinic-scan.pdf",
                    },
                    review_history=[],
                ),
                CaptureAnalysis(
                    id="pending-document-status",
                    person_id=person.id,
                    caregiver_id=caregiver.id,
                    source_evidence_id="pending-document-evidence",
                    source_text="",
                    extraction_status="processing",
                    candidates={
                        "kind": "document_extraction_v1",
                        "filename": "processing-clinic-scan.pdf",
                    },
                    review_history=[],
                ),
            ])
            db.flush()
            context = build_lcr_context(
                db,
                person.id,
                caregiver_id=caregiver.id,
                query="What does the clinic letter say about medication?",
            )

        extractions = {
            item["id"]: item
            for item in context["sources"]["document_extractions"]["items"]
        }
        assert extractions["failed-document-status"]["information_status"] == "document_extraction_failed"
        assert extractions["pending-document-status"]["information_status"] == "document_extraction_pending"
        assert "failed-document-status" in str(context["sources"]["document_extractions"])
        assert "pending-document-status" in str(context["sources"]["document_extractions"])
    finally:
        connection.close()
        engine.dispose()


def test_postgres_lcr_marks_superseded_observation_versions_as_historical():
    engine, connection, schema = _postgres_conversation_db()
    try:
        with Session(bind=connection) as db:
            person = Person(id="observation-correction-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="observation-correction-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()
            db.add(Evidence(
                id="observation-correction-evidence",
                person_id=person.id,
                type="care_note",
                source_text="Caregiver correction source note.",
                uploaded_by_caregiver_id=caregiver.id,
                reported_by_caregiver_id=caregiver.id,
                evidence_status="active",
            ))
            db.flush()
            original = EvidenceObservation(
                id="observation-original",
                person_id=person.id,
                evidence_id="observation-correction-evidence",
                observation_kind="caregiver_report",
                epistemic_status="reported",
                subject_label="Care recipient",
                attribute="falls",
                value_text="One fall was reported.",
                source_passage="I saw one fall yesterday.",
                source_span_start=0,
                source_span_end=23,
                extraction_method="caregiver_report",
                extraction_version="1",
                created_by_caregiver_id=caregiver.id,
            )
            corrected = EvidenceObservation(
                id="observation-corrected",
                person_id=person.id,
                evidence_id="observation-correction-evidence",
                supersedes_observation_id=original.id,
                correction_rationale="The caregiver corrected the count after checking the original note.",
                observation_kind="caregiver_report",
                epistemic_status="reported",
                subject_label="Care recipient",
                attribute="falls",
                value_text="Two falls were reported.",
                source_passage="I saw two falls yesterday.",
                source_span_start=0,
                source_span_end=24,
                extraction_method="caregiver_correction",
                extraction_version="1",
                created_by_caregiver_id=caregiver.id,
            )
            db.add_all([original, corrected])
            db.flush()
            context = build_lcr_context(
                db, person.id, caregiver_id=caregiver.id, include_all_history=True,
            )

        observations = {
            item["id"]: item
            for item in context["sources"]["evidence_observations"]["items"]
        }
        assert observations["observation-original"]["information_status"] == "historical_observation"
        assert observations["observation-original"]["superseded_by_observation_ids"] == ["observation-corrected"]
        assert observations["observation-corrected"]["information_status"] != "historical_observation"
        assert observations["observation-corrected"]["supersedes_observation_id"] == "observation-original"
    finally:
        connection.close()
        engine.dispose()


def test_postgres_historical_scan_includes_delayed_report_and_recording_timestamps():
    engine, connection, schema = _postgres_conversation_db()
    try:
        window = _temporal_window(
            "What happened in March 2025?",
            now=datetime(2026, 11, 15, 12, tzinfo=UTC),
            timezone_name="UTC",
        )
        assert window is not None
        with Session(bind=connection) as db:
            person = Person(id="delayed-history-person", name="Care recipient")
            db.add(person)
            db.flush()
            caregiver = Caregiver(
                id="delayed-history-caregiver", name="Caregiver",
                relationship="family", person_id=person.id,
            )
            db.add(caregiver)
            db.flush()

            # Exercise the production temporal predicate against PostgreSQL
            # using the separate event-time, report-time and upload-time clocks.
            db.add_all([
                Evidence(
                    id="reported-in-window", person_id=person.id, type="care_note",
                    source_text="Caregiver reported this event in March.",
                    uploaded_by_caregiver_id=caregiver.id,
                    reported_by_caregiver_id=caregiver.id,
                    reported_at=datetime(2025, 3, 12, 10, tzinfo=UTC),
                    uploaded_at=datetime(2026, 1, 2, 10, tzinfo=UTC),
                    evidence_status="active",
                ),
                Evidence(
                    id="uploaded-in-window", person_id=person.id, type="care_note",
                    source_text="This source was uploaded in March.",
                    uploaded_by_caregiver_id=caregiver.id,
                    reported_by_caregiver_id=caregiver.id,
                    uploaded_at=datetime(2025, 3, 14, 10, tzinfo=UTC),
                    evidence_status="active",
                ),
                Evidence(
                    id="outside-window", person_id=person.id, type="care_note",
                    source_text="This source is outside the requested window.",
                    uploaded_by_caregiver_id=caregiver.id,
                    reported_by_caregiver_id=caregiver.id,
                    reported_at=datetime(2024, 12, 20, 10, tzinfo=UTC),
                    uploaded_at=datetime(2026, 1, 2, 10, tzinfo=UTC),
                    evidence_status="active",
                ),
                Evidence(
                    id="deleted-in-window", person_id=person.id, type="care_note",
                    source_text="This source was deleted by the caregiver.",
                    uploaded_by_caregiver_id=caregiver.id,
                    reported_by_caregiver_id=caregiver.id,
                    reported_at=datetime(2025, 3, 13, 10, tzinfo=UTC),
                    uploaded_at=datetime(2025, 3, 13, 10, tzinfo=UTC),
                    evidence_status="deleted_by_caregiver",
                ),
            ])
            db.flush()

            db.add_all([
                CareEvent(
                    id="recorded-in-window", person_id=person.id,
                    event_type="daily_observation", status="recorded",
                    occurred_at=datetime(2024, 12, 20, 9, tzinfo=UTC),
                    recorded_at=datetime(2025, 3, 15, 9, tzinfo=UTC),
                    title="Delayed care entry",
                    description="The caregiver entered this earlier event in March.",
                    evidence_ids=[], created_by_caregiver_id=caregiver.id,
                ),
                CareEvent(
                    id="outside-recording-window", person_id=person.id,
                    event_type="daily_observation", status="recorded",
                    occurred_at=datetime(2024, 12, 19, 9, tzinfo=UTC),
                    recorded_at=datetime(2024, 12, 20, 9, tzinfo=UTC),
                    title="Earlier care entry",
                    description="This entry was recorded before March.",
                    evidence_ids=[], created_by_caregiver_id=caregiver.id,
                ),
            ])
            db.flush()
            context = build_lcr_context(
                db, person.id, caregiver_id=caregiver.id,
                temporal_window=window, include_all_history=True,
            )

        evidence_ids = {item["id"] for item in context["sources"]["evidence"]["items"]}
        event_ids = {item["id"] for item in context["sources"]["events"]["items"]}
        assert evidence_ids == {"reported-in-window", "uploaded-in-window"}
        assert event_ids == {"recorded-in-window"}
        assert context["sources"]["evidence"]["candidate_scan_complete"] is True
        assert context["sources"]["events"]["candidate_scan_complete"] is True
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
                    "[Page 1]\nMedication list: take the listed tablet with breakfast.\n"
                    "[Page 2 OCR]\n[No readable text detected on page 2]"
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
                source_text=(
                    "[Page 1 OCR]\nMedication list: the original text was extracted successfully.\n"
                    "[Page 2 OCR]\nThe second page was also extracted successfully."
                ),
                extraction_status="available",
                candidates={
                    "kind": "document_extraction_v1",
                    "page_count": 2,
                    "processed_page_numbers": [1, 2],
                    "unreadable_page_numbers": [],
                    "unprocessed_page_numbers": [],
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
            passage_context = build_unified_lcr_context(
                db,
                "partial-document-person",
                "partial-document-caregiver",
                query="tablet breakfast",
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
        passage_item = next(
            value for value in passage_context["sources"]["document_extractions"]["items"]
            if value["id"] == "partial-document-analysis"
        )
        retrieved_passages = passage_item["retrieved_source_passages"]
        assert retrieved_passages
        assert any(
            "take the listed tablet with breakfast" in passage["text"]
            and passage["source_character_start"] <= passage_item["source_text"].find("take the listed tablet with breakfast") < passage["source_character_end"]
            and passage["page_number"] == 1
            and passage["page_label"] == "[Page 1]"
            for passage in retrieved_passages
        ), "Companion retrieval must return the exact matching document passage with source offsets"
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
            workspace_record = AppointmentWorkspaceRecord(
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
            )
            db.add(workspace_record)
            db.flush()
            db.add(CareStateRevision(
                id="appointment-workspace-later-care-change",
                person_id="appointment-workspace-person",
                target_type="care_event",
                target_id="later-care-event",
                revision_number=1,
                change_kind="updated",
                before_state="{}",
                after_state="{\"status\": \"changed\"}",
                evidence_ids=[],
                changed_by_caregiver_id="appointment-workspace-caregiver",
                changed_at=workspace_record.updated_at + timedelta(seconds=1),
            ))
            db.add(AppointmentWorkspaceRevision(
                id="appointment-workspace-revision-2",
                record_id="appointment-workspace-record",
                person_id="appointment-workspace-person",
                revision_number=2,
                payload={
                    "clinicianFindings": "The clinician previously asked the family to track sleep for one week.",
                    "decisions": "Review the sleep notes at the next visit.",
                    "epistemic_boundary": {
                        "clinician_findings": "caregiver_recorded_clinician_statement_not_independently_verified",
                    },
                },
                change_reason="Updated the tracking period after the visit",
                changed_by_caregiver_id="appointment-workspace-caregiver",
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
        assert item["information_status"] == "appointment_workspace_stale"
        assert item["source_freshness"]["status"] == "stale"
        assert item["status"] == "ready_for_review"
        assert item["version_number"] == 3
        assert item["payload"]["clinicianFindings"].startswith("The clinician said")
        assert item["payload"]["unanswered"].startswith("The family is still waiting")
        assert item["payload"]["epistemic_boundary"]["clinician_findings"] == (
            "caregiver_recorded_clinician_statement_not_independently_verified"
        )
        assert item["payload"]["followUps"][0]["open_loop_id"] == "appointment-follow-up-loop"
        revisions = context["sources"]["appointment_workspace_revisions"]
        assert revisions["returned_count"] == 1
        prior = revisions["items"][0]
        assert prior["revision_number"] == 2
        assert prior["information_status"] == "appointment_workspace_revision_history"
        assert "one week" in prior["payload"]["clinicianFindings"]
        prompt_context = format_lcr_context_for_prompt(context)
        assert "appointment_workspace_caregiver_notes" in prompt_context
        assert "appointment_workspace_revision_history" in prompt_context
        assert "not independently verified" in prompt_context
        assert "lcr_reconciliation" in prompt_context
        from app.api.collaboration import _context_record_refs
        typed_refs = _context_record_refs([
            json.dumps({"source_type": "appointment_workspaces", "source_id": item["id"], "record": item}),
            json.dumps({"source_type": "appointment_workspace_revisions", "source_id": prior["id"], "record": prior}),
        ])
        assert {"record_type": "appointment_workspace", "record_id": "appointment-workspace-record"} in typed_refs
        assert {"record_type": "appointment_workspace_revision", "record_id": "appointment-workspace-revision-2"} in typed_refs
    finally:
        _load_source_schema.cache_clear()
        _close_postgres_conversation_db(engine, connection, schema)


def test_explicit_multi_year_day_week_and_month_windows_are_not_truncated():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    days = _temporal_window("What changed in the last 800 days?", now=now)
    weeks = _temporal_window("What changed in the past 104 weeks?", now=now)
    months = _temporal_window("What changed in the last 24 months?", now=now)

    assert days is not None and days["start"] == "2024-09-07T00:00:00+00:00"
    assert weeks is not None and weeks["start"] == "2024-11-18T00:00:00+00:00"
    assert months is not None and months["start"] == "2024-11-15T00:00:00+00:00"
    assert days["basis"] == weeks["basis"] == months["basis"] == "explicit_user_temporal_expression"


def test_explicit_temporal_windows_reject_ranges_beyond_supported_bounds():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    with pytest.raises(ValueError, match="between one day and twenty years"):
        _temporal_window("Show the last 7301 days", now=now)
    with pytest.raises(ValueError, match="between one week and twenty years"):
        _temporal_window("Show the last 1041 weeks", now=now)
    with pytest.raises(ValueError, match="between one month and twenty years"):
        _temporal_window("Show the last 241 months", now=now)


def test_written_day_week_and_month_ranges_are_resolved_as_calendar_windows():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    ten_days = _temporal_window("What changed in the past ten days?", now=now)
    four_weeks = _temporal_window("What changed in the last four weeks?", now=now)
    two_months = _temporal_window("What changed in the past two months?", now=now)

    assert ten_days is not None and ten_days["start"] == "2026-11-06T00:00:00+00:00"
    assert four_weeks is not None and four_weeks["start"] == "2026-10-19T00:00:00+00:00"
    assert two_months is not None and two_months["start"] == "2026-09-15T00:00:00+00:00"


def test_oversized_numeric_ranges_fail_closed_instead_of_falling_back_to_recent_history():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)

    with pytest.raises(ValueError, match="between one and twenty years"):
        _temporal_window("Show the last 1000 years", now=now)
    with pytest.raises(ValueError, match="between one day and twenty years"):
        _temporal_window("Show the last 10000 days", now=now)
    with pytest.raises(ValueError, match="between one week and twenty years"):
        _temporal_window("Show the last 10000 weeks", now=now)
    with pytest.raises(ValueError, match="between one month and twenty years"):
        _temporal_window("Show the last 1000 months", now=now)


def test_retrieval_score_prefers_relevant_narrative_and_full_concept_coverage():
    query = "sleep medication"
    direct = ["sleep", "medication"]
    expanded = ["sleep", "medication", "rest", "medicine"]
    relevant = {
        "id": "relevant",
        "title": "Sleep medication change",
        "description": "Caregiver reported a new medicine affected overnight sleep.",
    }
    incidental = {
        "id": "incidental",
        "title": "Daily check-in",
        "description": "Sleep was mentioned briefly; unrelated appointment details follow.",
    }
    assert _retrieval_score(relevant, query, direct, expanded) > _retrieval_score(
        incidental, query, direct, expanded
    )


def test_retrieval_score_does_not_count_identifiers_or_nested_candidate_payloads():
    query = "sleep medication"
    direct = ["sleep", "medication"]
    expanded = direct[:]
    identifier_only = {
        "id": "sleep-medication-record",
        "candidates": [{"description": "sleep medication changed"}],
    }
    assert _retrieval_score(identifier_only, query, direct, expanded) == 0


def test_curated_sleep_paraphrase_expansion_retrieves_awake_wording():
    direct = ["sleep"]
    expanded = _expanded_search_terms(direct)
    paraphrased_record = {
        "title": "Overnight observation",
        "description": "Caregiver reports the person was awake several times overnight.",
    }
    unrelated_record = {
        "title": "Appointment update",
        "description": "Caregiver recorded a visit with the primary care office.",
    }
    assert "awake" in expanded
    assert _retrieval_score(paraphrased_record, "sleep", direct, expanded) > _retrieval_score(
        unrelated_record, "sleep", direct, expanded
    )


def test_retrieval_score_indexes_nested_descriptive_context_but_not_nested_ids():
    direct = ["overnight", "awake"]
    expanded = ["overnight", "awake", "sleep"]
    contextual = {
        "id": "record-1",
        "context": {
            "caregiver_observation": "Mum was awake repeatedly overnight.",
            "source_event_id": "awake-overnight",
        },
    }
    identifier_only = {
        "id": "awake-overnight",
        "context": {"source_event_id": "awake-overnight"},
    }
    assert _retrieval_score(contextual, "overnight awake", direct, expanded) > 0
    assert _retrieval_score(identifier_only, "overnight awake", direct, expanded) == 0
