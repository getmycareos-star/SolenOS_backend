from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from app.services.lcr_context import (
    _load_source_schema,
    build_lcr_context,
    format_lcr_context_for_prompt,
)


def _care_events_schema(engine):
    metadata = MetaData()
    events = Table(
        "care_events",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("event_type", String),
        Column("status", String),
        Column("occurred_at", DateTime),
        Column("recorded_at", DateTime),
        Column("created_at", DateTime),
        Column("title", String),
        Column("description", String),
        Column("evidence_ids", String),
        Column("time_provenance", String),
        Column("location_provenance", String),
        Column("created_by_caregiver_id", String),
        Column("significance_verdict", String),
        Column("attention_candidate", String),
        Column("follow_up_candidate", String),
        Column("situation_id", String),
    )
    metadata.create_all(engine)
    return events


def test_query_retrieval_scans_beyond_old_250_candidate_ceiling_and_finds_older_direct_match():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _care_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)

    rows = []
    for index in range(300):
        happened = now - timedelta(days=index)
        rows.append({
            "id": f"recent-sleep-{index:03d}",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": happened,
            "recorded_at": happened,
            "created_at": happened,
            "title": "Sleep observation",
            "description": "Caregiver reported poor sleep overnight.",
        })
    older = now - timedelta(days=190)
    rows.append({
        "id": "older-waking-event",
        "person_id": "person-a",
        "event_type": "daily_observation",
        "status": "recorded",
        "occurred_at": older,
        "recorded_at": older,
        "created_at": older,
        "title": "Overnight waking",
        "description": "Caregiver reported repeated waking during the night.",
    })

    try:
        with engine.begin() as connection:
            connection.execute(events.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=1, query="waking",
            )

        source = context["sources"]["events"]
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 301
        assert source["has_more"] is True
        assert [item["id"] for item in source["items"]] == ["older-waking-event"]
        assert context["history_coverage"] == "query_scanned_ranked_subset"
        # The whole query scan can be complete while the overall LCR schema is
        # partial. Those are deliberately separate claims.
        assert context["retrieval_scan_complete"] is True
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()


def _open_questions_schema(engine):
    metadata = MetaData()
    questions = Table(
        "open_questions",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("question", String),
        Column("subject_type", String),
        Column("subject_id", String),
        Column("claim_id", String),
        Column("intended_answerer_role", String),
        Column("intended_answerer_label", String),
        Column("necessity", String),
        Column("necessity_rationale", String),
        Column("status", String),
        Column("priority", String),
        Column("evidence_ids", String),
        Column("context", String),
        Column("asked_at", DateTime),
        Column("answered_at", DateTime),
        Column("answer", String),
        Column("answer_provenance", String),
        Column("answered_by_caregiver_id", String),
        Column("closed_at", DateTime),
        Column("closed_reason", String),
        Column("created_by_caregiver_id", String),
        Column("updated_at", DateTime),
    )
    metadata.create_all(engine)
    return questions

def _open_question_answer_events_schema(engine):
    metadata = MetaData()
    events = Table(
        "open_question_answer_events",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("question_id", String, nullable=False),
        Column("answer", String),
        Column("answer_provenance", String),
        Column("evidence_ids", String),
        Column("answer_version", Integer),
        Column("answered_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    return events

def test_query_retrieval_finds_older_answered_question_over_newer_related_history():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    questions = _open_questions_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for index in range(300):
        asked = now - timedelta(days=index)
        rows.append({
            "id": f"recent-sleep-question-{index:03d}",
            "person_id": "person-a",
            "question": f"How was sleep on day {index}?",
            "subject_type": "sleep",
            "subject_id": "person-a",
            "status": "answered",
            "priority": "low",
            "asked_at": asked,
            "answered_at": asked + timedelta(hours=1),
            "answer": "The caregiver recorded that sleep was usual.",
        })
    older = now - timedelta(days=500)
    rows.append({
        "id": "older-restless-sleep-answer",
        "person_id": "person-a",
        "question": "Did restless sleep continue after the medication change?",
        "subject_type": "sleep",
        "subject_id": "person-a",
        "status": "answered",
        "priority": "high",
        "asked_at": older,
        "answered_at": older + timedelta(hours=1),
        "answer": "The caregiver recorded repeated waking on two nights.",
        "answer_provenance": "Daughter reported the answer after speaking with the care team.",
        "answered_by_caregiver_id": "caregiver-a",
        "updated_at": older + timedelta(hours=2),
    })

    try:
        with engine.begin() as connection:
            connection.execute(questions.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=1, query="restless sleep",
            )
        source = context["sources"]["open_questions"]
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 301
        assert source["has_more"] is True
        assert [item["id"] for item in source["items"]] == ["older-restless-sleep-answer"]
        assert source["items"][0]["status"] == "answered"
        assert "repeated waking on two nights" in source["items"][0]["answer"]
        assert source["items"][0]["answered_by_caregiver_id"] == "caregiver-a"
        assert context["history_coverage"] == "query_scanned_ranked_subset"
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()

def test_query_retrieval_finds_latest_relevant_answer_revision_beyond_recent_history():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _open_question_answer_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for index in range(300):
        created = now - timedelta(days=index)
        rows.append({
            "id": f"recent-answer-{index:03d}",
            "person_id": "person-a",
            "question_id": f"recent-question-{index:03d}",
            "answer": "Sleep was usual.",
            "answer_provenance": "Caregiver recorded the usual sleep pattern.",
            "answer_version": 1,
            "answered_by_caregiver_id": "caregiver-a",
            "created_at": created,
        })
    older = now - timedelta(days=500)
    rows.extend([
        {
            "id": "older-restless-sleep-answer-v1",
            "person_id": "person-a",
            "question_id": "older-restless-sleep-question",
            "answer": "The caregiver initially recorded that sleep was usual.",
            "answer_provenance": "Daughter first reported the usual sleep pattern.",
            "answer_version": "1",
            "answered_by_caregiver_id": "caregiver-a",
            "created_at": older + timedelta(hours=1),
        },
        {
            "id": "older-restless-sleep-answer-v2",
            "person_id": "person-a",
            "question_id": "older-restless-sleep-question",
            "answer": "The caregiver recorded repeated waking on two nights.",
            "answer_provenance": "Daughter corrected the earlier report after reviewing the care-team note about restless sleep.",
            "answer_version": 2,
            "answered_by_caregiver_id": "caregiver-a",
            "created_at": older + timedelta(hours=2),
        },
    ])

    try:
        with engine.begin() as connection:
            connection.execute(events.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=1, query="restless sleep",
            )
        source = context["sources"]["open_question_answer_events"]
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 302
        assert source["has_more"] is True
        assert [item["id"] for item in source["items"]] == ["older-restless-sleep-answer-v2"]
        assert source["items"][0]["answer_version"] == 2
        assert source["items"][0]["answered_by_caregiver_id"] == "caregiver-a"
        assert "corrected the earlier report" in source["items"][0]["answer_provenance"]
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()

def _open_question_review_events_schema(engine):
    metadata = MetaData()
    reviews = Table(
        "open_question_review_events",
        metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("question_id", String, nullable=False),
        Column("answer_event_id", String, nullable=False),
        Column("answer_version", Integer),
        Column("decision", String),
        Column("rationale", String),
        Column("reviewer_caregiver_id", String),
        Column("claim_assessment_id", String),
        Column("resulting_claim_id", String),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    return reviews

def test_query_retrieval_finds_older_answer_review_that_limits_reliance_on_it():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    reviews = _open_question_review_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for index in range(300):
        created = now - timedelta(days=index)
        rows.append({
            "id": f"recent-review-{index:03d}",
            "person_id": "person-a",
            "question_id": f"recent-question-{index:03d}",
            "answer_event_id": f"recent-answer-{index:03d}",
            "answer_version": 1,
            "decision": "accepted",
            "rationale": "The review confirms the usual sleep pattern.",
            "reviewer_caregiver_id": "caregiver-a",
            "created_at": created,
        })
    older = now - timedelta(days=500)
    rows.append({
        "id": "older-restless-sleep-review",
        "person_id": "person-a",
        "question_id": "older-restless-sleep-question",
        "answer_event_id": "older-restless-sleep-answer-v2",
        "answer_version": 2,
        "decision": "needs_clarification",
        "rationale": "The corrected report still does not establish whether restless sleep continued.",
        "reviewer_caregiver_id": "caregiver-a",
        "created_at": older,
    })

    try:
        with engine.begin() as connection:
            connection.execute(reviews.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=1, query="restless sleep",
            )
        source = context["sources"]["open_question_review_events"]
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 301
        assert source["has_more"] is True
        assert [item["id"] for item in source["items"]] == ["older-restless-sleep-review"]
        assert source["items"][0]["decision"] == "needs_clarification"
        assert "does not establish whether restless sleep continued" in source["items"][0]["rationale"]
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()

def test_unqueried_current_care_projection_remains_explicitly_bounded():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _care_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for index in range(5):
        happened = now - timedelta(days=index)
        rows.append({
            "id": f"event-{index}",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": happened,
            "recorded_at": happened,
            "created_at": happened,
            "title": f"Observation {index}",
            "description": "Caregiver observation.",
        })

    try:
        with engine.begin() as connection:
            connection.execute(events.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(db, "person-a", limit_per_source=2)

        source = context["sources"]["events"]
        assert source["returned_count"] == 2
        assert source["candidate_scan_complete"] is False
        assert source["has_more"] is True
        assert context["history_coverage"] == "bounded_recent_subset"
        assert context["retrieval_scan_complete"] is False
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()


def test_full_care_journey_scan_does_not_require_lexical_match_and_reports_remaining_history():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _care_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for index in range(60):
        happened = now - timedelta(days=index)
        rows.append({
            "id": f"journey-event-{index:03d}",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": happened,
            "recorded_at": happened,
            "created_at": happened,
            "title": f"Care entry {index}",
            "description": f"Recorded care detail {index}.",
        })

    try:
        with engine.begin() as connection:
            connection.execute(events.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=50,
                query="tell me the complete care journey",
                include_all_history=True,
            )

        source = context["sources"]["events"]
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 60
        assert source["returned_count"] == 50
        assert source["has_more"] is True
        assert source["returned_history_complete"] is False
        assert source["next_offset"] == 50
        assert context["history_coverage"] == "complete_history_scan_ranked_subset"
        assert context["retrieval_scan_complete"] is True
        assert context["retrieval_projection_complete"] is False
        with Session(engine) as db:
            second_page = build_lcr_context(
                db, "person-a", limit_per_source=50,
                query="tell me the complete care journey",
                include_all_history=True,
                source_offsets={"events": 50},
            )
        second_source = second_page["sources"]["events"]
        assert second_source["candidate_count"] == 60
        assert second_source["offset"] == 50
        assert second_source["returned_count"] == 10
        assert second_source["has_more"] is False
        assert second_source["returned_history_complete"] is False
        assert {item["id"] for item in second_source["items"]} == {
            f"journey-event-{index:03d}" for index in range(50, 60)
        }
        prompt = format_lcr_context_for_prompt(context)
        assert '"retrieval_projection_complete":false' in prompt
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()



def test_prompt_budget_is_shared_across_sources_instead_of_starving_later_care_domains():
    """A large event section must not consume the prompt before evidence is represented."""
    context = {
        "schema_status": "complete",
        "history_coverage": "complete_history_scan_ranked_subset",
        "retrieval_scan_complete": True,
        "missing_required_sources": [],
        "missing_optional_sources": [],
        "evidence_lineage": {"status": "assessed"},
        "sources": {
            "events": {
                "status": "available",
                "returned_count": 4,
                "candidate_count": 4,
                "candidate_scan_complete": True,
                "has_more": False,
                "items": [
                    {"id": f"event-{index}", "description": "x" * 420}
                    for index in range(4)
                ],
            },
            "evidence": {
                "status": "available",
                "returned_count": 1,
                "candidate_count": 1,
                "candidate_scan_complete": True,
                "has_more": False,
                "items": [{"id": "source-evidence-1", "source_text": "Hospital discharge note"}],
            },
        },
    }

    prompt = format_lcr_context_for_prompt(context, max_chars=1500)

    assert len(prompt) <= 1500
    assert "event-0" in prompt
    assert "source-evidence-1" in prompt
    assert "\n[events;" in prompt
    assert "\n[evidence;" in prompt
    assert "Context truncated" in prompt


def test_temporal_retrieval_keeps_delayed_entries_when_either_event_or_recording_time_is_in_window():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _care_events_schema(engine)
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    window_start = now - timedelta(days=7)

    rows = [
        {
            "id": "late-recorded-entry",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": now - timedelta(days=20),
            "recorded_at": now - timedelta(days=1),
            "created_at": now - timedelta(days=1),
            "title": "Delayed report",
            "description": "A caregiver entered this event late.",
        },
        {
            "id": "event-in-window",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": now - timedelta(days=2),
            "recorded_at": now - timedelta(days=2),
            "created_at": now - timedelta(days=2),
            "title": "Recent event",
            "description": "An event happened during the requested period.",
        },
        {
            "id": "outside-window",
            "person_id": "person-a",
            "event_type": "daily_observation",
            "status": "recorded",
            "occurred_at": now - timedelta(days=30),
            "recorded_at": now - timedelta(days=25),
            "created_at": now - timedelta(days=25),
            "title": "Older event",
            "description": "Both timestamps are outside the requested period.",
        },
    ]

    try:
        with engine.begin() as connection:
            connection.execute(events.insert(), rows)
        with Session(engine) as db:
            context = build_lcr_context(
                db,
                "person-a",
                limit_per_source=10,
                temporal_window={
                    "start": window_start.isoformat(),
                    "end": now.isoformat(),
                    "basis": "explicit_user_temporal_expression",
                },
            )

        source = context["sources"]["events"]
        returned_ids = {item["id"] for item in source["items"]}
        assert source["candidate_scan_complete"] is True
        assert source["candidate_count"] == 2
        assert returned_ids == {"late-recorded-entry", "event-in-window"}
        delayed = next(item for item in source["items"] if item["id"] == "late-recorded-entry")
        assert delayed["occurred_at"] < window_start.isoformat()
        assert delayed["recorded_at"] >= window_start.isoformat()
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()
