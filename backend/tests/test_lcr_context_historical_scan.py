from datetime import datetime, timedelta, timezone
from uuid import UUID
import json

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from app.services.lcr_context import (
    MAX_TEXT_CHARS,
    _load_source_schema,
    _safe_value,
    build_lcr_context,
    format_lcr_context_for_prompt,
)


def test_reflected_uuid_values_are_normalized_for_lcr_sources():
    identifier = UUID("9a8d72d2-49f1-4d78-a8c9-5f1a5f0b1b9c")
    assert _safe_value(identifier) == str(identifier)


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



def test_lcr_context_includes_scoped_evidence_reviews_and_assessment_history():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    evidence = Table(
        "evidence", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("type", String),
        Column("evidence_kind", String),
        Column("epistemic_status", String),
        Column("source_text", String),
        Column("uploaded_at", DateTime),
        Column("uploaded_by_caregiver_id", String),
        Column("evidence_status", String),
        Column("source_text_sha256", String),
        Column("created_at", DateTime),
    )
    reviews = Table(
        "evidence_review_records", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("evidence_id", String, nullable=False),
        Column("review_type", String),
        Column("outcome", String),
        Column("rationale", String),
        Column("criteria", String),
        Column("policy_version", String),
        Column("source_attribution_snapshot", String),
        Column("reviewed_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    claim_assessments = Table(
        "evidence_claim_assessments", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("claim_id", String, nullable=False),
        Column("claim_version", Integer),
        Column("status", String),
        Column("rationale", String),
        Column("policy_version", String),
        Column("evidence_links", String),
        Column("reviewed_by_caregiver_id", String),
        Column("supersedes_assessment_id", String),
        Column("superseded_by_claim_id", String),
        Column("created_at", DateTime),
    )
    freshness = Table(
        "evidence_freshness_assessments", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("evidence_id", String, nullable=False),
        Column("policy_id", String),
        Column("policy_version", String),
        Column("freshness_status", String),
        Column("source_time", DateTime),
        Column("source_time_kind", String),
        Column("assessed_at", DateTime),
        Column("age_days", Integer),
        Column("rationale", String),
        Column("assessed_by_caregiver_id", String),
    )
    completeness = Table(
        "evidence_completeness_assessments", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("policy_id", String),
        Column("policy_version", String),
        Column("subject_type", String),
        Column("subject_id", String),
        Column("assessment_status", String),
        Column("required_fields", String),
        Column("satisfied_fields", String),
        Column("missing_fields", String),
        Column("field_evidence_json", String),
        Column("evidence_ids", String),
        Column("rationale", String),
        Column("assessment_basis", String),
        Column("semantic_sufficiency", String),
        Column("assessed_by_caregiver_id", String),
        Column("assessed_at", DateTime),
    )
    freshness_policies = Table(
        "evidence_freshness_policies", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("domain_key", String),
        Column("policy_version", String),
        Column("max_age_days", Integer),
        Column("historical_after_days", Integer),
        Column("rationale", String),
        Column("created_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    completeness_policies = Table(
        "evidence_completeness_policies", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("domain_key", String),
        Column("purpose_key", String),
        Column("policy_version", String),
        Column("required_fields", String),
        Column("rationale", String),
        Column("created_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(evidence.insert(), [
                {"id": "evidence-a", "person_id": "person-a", "type": "document",
                 "evidence_kind": "document", "epistemic_status": "reported",
                 "source_text": "Discharge note says follow-up is needed.", "uploaded_at": now,
                 "uploaded_by_caregiver_id": "caregiver-a", "evidence_status": "active",
                 "source_text_sha256": "hash-a", "created_at": now},
            ])
            connection.execute(reviews.insert(), [
                {"id": "review-a", "person_id": "person-a", "evidence_id": "evidence-a",
                 "review_type": "reliability", "outcome": "contested",
                 "rationale": "The author and source context still need confirmation.",
                 "criteria": "[]", "policy_version": "evidence-review-v1",
                 "source_attribution_snapshot": "{}", "reviewed_by_caregiver_id": "caregiver-a",
                 "created_at": now},
                {"id": "review-b", "person_id": "person-b", "evidence_id": "evidence-a",
                 "review_type": "reliability", "outcome": "supported",
                 "rationale": "Must not cross care-record scope.", "criteria": "[]",
                 "policy_version": "evidence-review-v1", "source_attribution_snapshot": "{}",
                 "reviewed_by_caregiver_id": "caregiver-b", "created_at": now},
            ])
            connection.execute(claim_assessments.insert(), [{
                "id": "assessment-a", "person_id": "person-a", "claim_id": "claim-a",
                "claim_version": 2, "status": "unverified",
                "rationale": "The linked caregiver report has not been independently verified.",
                "policy_version": "claim-assessment-v1", "evidence_links": "[]",
                "reviewed_by_caregiver_id": "caregiver-a",
                "supersedes_assessment_id": "assessment-old", "created_at": now,
            }])
            connection.execute(freshness.insert(), [{
                "id": "freshness-a", "person_id": "person-a", "evidence_id": "evidence-a",
                "policy_id": "freshness-policy-a", "policy_version": "freshness-v1",
                "freshness_status": "stale", "source_time": now, "source_time_kind": "document_date",
                "assessed_at": now, "age_days": 90, "rationale": "The explicit review interval has elapsed.",
                "assessed_by_caregiver_id": "caregiver-a",
            }])
            connection.execute(freshness_policies.insert(), [{
                "id": "freshness-policy-a", "person_id": "person-a", "domain_key": "document_history",
                "policy_version": "freshness-v1", "max_age_days": 30, "historical_after_days": 180,
                "rationale": "Caregiver-selected source review boundary.",
                "created_by_caregiver_id": "caregiver-a", "created_at": now,
            }])
            connection.execute(completeness_policies.insert(), [{
                "id": "completeness-policy-a", "person_id": "person-a", "domain_key": "document_history",
                "purpose_key": "professional_handoff", "policy_version": "completeness-v1",
                "required_fields": json.dumps(["author", "date"]),
                "rationale": "Required source fields for this briefing purpose.",
                "created_by_caregiver_id": "caregiver-a", "created_at": now,
            }])
            connection.execute(completeness.insert(), [{
                "id": "completeness-a", "person_id": "person-a", "policy_id": "completeness-policy-a",
                "policy_version": "completeness-v1", "subject_type": "evidence", "subject_id": "evidence-a",
                "assessment_status": "partial", "required_fields": json.dumps(["author", "date"]),
                "satisfied_fields": json.dumps(["date"]), "missing_fields": json.dumps(["author"]),
                "field_evidence_json": "{}", "evidence_ids": json.dumps(["evidence-a"]),
                "rationale": "The source author is not identified.",
                "assessment_basis": "linked_source_coverage_only",
                "semantic_sufficiency": "not_automatically_verified",
                "assessed_by_caregiver_id": "caregiver-a", "assessed_at": now,
            }])
        with Session(engine) as db:
            context = build_lcr_context(db, "person-a", limit_per_source=20, include_all_history=True)
        assert [item["id"] for item in context["sources"]["evidence_reviews"]["items"]] == ["review-a"]
        assert context["sources"]["evidence_reviews"]["items"][0]["information_status"] == "evidence_review_reliability_contested"
        assert context["sources"]["claim_assessments"]["items"][0]["information_status"] == "claim_assessment_unverified"
        assert context["sources"]["evidence_freshness_assessments"]["items"][0]["information_status"] == "evidence_freshness_stale"
        completeness_item = context["sources"]["evidence_completeness_assessments"]["items"][0]
        assert completeness_item["information_status"] == "evidence_completeness_partial"
        assert completeness_item["semantic_sufficiency"] == "not_automatically_verified"
        assert context["sources"]["evidence_freshness_policies"]["items"][0]["information_status"] == "evidence_freshness_policy_defined"
        assert context["sources"]["evidence_completeness_policies"]["items"][0]["information_status"] == "evidence_completeness_policy_defined"
        prompt = format_lcr_context_for_prompt(context, max_chars=12000)
        assert "[evidence_reviews;" in prompt
        assert "contested" in prompt
        assert "[claim_assessments;" in prompt
        assert "[evidence_freshness_assessments;" in prompt
        assert "[evidence_completeness_assessments;" in prompt
        assert "[evidence_freshness_policies;" in prompt
        assert "[evidence_completeness_policies;" in prompt
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()

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
            # Keep every executemany row's key set identical. SQLAlchemy derives
            # the INSERT column list from the first mapping in this batch.
            "answer_provenance": None,
            "context": None,
            "answered_by_caregiver_id": None,
            "updated_at": asked + timedelta(hours=2),
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
        "context": '[{"source_field":"current_instruction","source_id":"older-restless-sleep-conflict","necessity_assessment":{"necessity":"necessary"}}]',
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
        assert source["items"][0]["context"][0]["source_field"] == "current_instruction"
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


def test_lcr_context_includes_person_scoped_care_revisions_and_prior_change_analyses():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    revisions = Table(
        "care_state_revisions", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("target_type", String),
        Column("target_id", String),
        Column("revision_number", Integer),
        Column("change_kind", String),
        Column("before_state", String),
        Column("after_state", String),
        Column("evidence_ids", String),
        Column("changed_by_caregiver_id", String),
        Column("changed_at", DateTime),
    )
    analyses = Table(
        "care_delta_runs", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("source", String),
        Column("event_date", DateTime),
        Column("source_event_id", String),
        Column("evidence_ids", String),
        Column("delta_json", String),
        Column("model_name", String),
        Column("created_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(revisions.insert(), [
                {
                    "id": "sleep-revision-person-a",
                    "person_id": "person-a",
                    "target_type": "care_assertions",
                    "target_id": "sleep-assertion-a",
                    "revision_number": 2,
                    "change_kind": "updated",
                    "before_state": json.dumps({"field": "sleep", "asserted_value": "Poor sleep was reported."}),
                    "after_state": json.dumps({"field": "sleep", "asserted_value": "Caregiver corrected the report to say waking occurred twice."}),
                    "evidence_ids": json.dumps(["sleep-evidence-a"]),
                    "changed_by_caregiver_id": "caregiver-a",
                    "changed_at": now,
                },
                {
                    "id": "sleep-revision-person-b",
                    "person_id": "person-b",
                    "target_type": "care_assertions",
                    "target_id": "sleep-assertion-b",
                    "revision_number": 2,
                    "change_kind": "updated",
                    "before_state": json.dumps({"field": "sleep", "asserted_value": "Private other-person detail."}),
                    "after_state": json.dumps({"field": "sleep", "asserted_value": "Must never cross care-recipient scope."}),
                    "evidence_ids": json.dumps(["sleep-evidence-b"]),
                    "changed_by_caregiver_id": "caregiver-b",
                    "changed_at": now,
                },
            ])
            connection.execute(analyses.insert(), [
                {
                    "id": "sleep-analysis-person-a",
                    "person_id": "person-a",
                    "source": "daily_care",
                    "event_date": now - timedelta(days=2),
                    "source_event_id": "sleep-event-a",
                    "evidence_ids": json.dumps(["sleep-evidence-a"]),
                    "delta_json": json.dumps({
                        "topic": "sleep",
                        "summary": "Two caregiver reports describe waking overnight.",
                        "interpretation_limit": "This is a recorded comparison, not a clinical finding.",
                    }),
                    "model_name": "qwen-test",
                    "created_by_caregiver_id": "caregiver-a",
                    "created_at": now,
                },
                {
                    "id": "sleep-analysis-person-b",
                    "person_id": "person-b",
                    "source": "daily_care",
                    "event_date": now - timedelta(days=2),
                    "source_event_id": "sleep-event-b",
                    "evidence_ids": json.dumps(["sleep-evidence-b"]),
                    "delta_json": json.dumps({"summary": "Private other-person analysis."}),
                    "model_name": "qwen-test",
                    "created_by_caregiver_id": "caregiver-b",
                    "created_at": now,
                },
            ])
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=10, query="sleep",
            )

        revision_source = context["sources"]["care_state_revisions"]
        assert revision_source["status"] == "available"
        assert revision_source["candidate_scan_complete"] is True
        assert [item["id"] for item in revision_source["items"]] == ["sleep-revision-person-a"]
        revision = revision_source["items"][0]
        assert revision["information_status"] == "care_state_revision_history"
        assert revision["before_state"]["asserted_value"] == "Poor sleep was reported."
        assert revision["after_state"]["asserted_value"] == "Caregiver corrected the report to say waking occurred twice."
        assert revision["evidence_ids"] == ["sleep-evidence-a"]

        analysis_source = context["sources"]["change_analyses"]
        assert analysis_source["status"] == "available"
        assert analysis_source["candidate_scan_complete"] is True
        assert [item["id"] for item in analysis_source["items"]] == ["sleep-analysis-person-a"]
        analysis = analysis_source["items"][0]
        assert analysis["information_status"] == "recorded_change_analysis"
        assert analysis["delta_json"]["interpretation_limit"] == "This is a recorded comparison, not a clinical finding."
        assert analysis["model_name"] == "qwen-test"
        assert analysis["source_event_id"] == "sleep-event-a"

        prompt = format_lcr_context_for_prompt(context, max_chars=12000)
        assert "[care_state_revisions;" in prompt
        assert "[change_analyses;" in prompt
        assert "not that the person's real-world condition changed" in prompt
        assert "not an independently verified fact or diagnosis" in prompt
        assert "Private other-person" not in prompt
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()


def test_large_json_change_analysis_is_bounded_before_parsing():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    analyses = Table(
        "care_delta_runs", metadata,
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
        Column("source", String),
        Column("event_date", DateTime),
        Column("source_event_id", String),
        Column("evidence_ids", String),
        Column("delta_json", String),
        Column("model_name", String),
        Column("created_by_caregiver_id", String),
        Column("created_at", DateTime),
    )
    metadata.create_all(engine)
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(analyses.insert(), [{
                "id": "oversized-sleep-analysis",
                "person_id": "person-a",
                "source": "daily_care",
                "event_date": now,
                "source_event_id": "sleep-event-a",
                "evidence_ids": "[]",
                "delta_json": json.dumps({"topic": "sleep", "summary": "x" * 4000}),
                "model_name": "qwen-test",
                "created_by_caregiver_id": "caregiver-a",
                "created_at": now,
            }])
        with Session(engine) as db:
            context = build_lcr_context(
                db, "person-a", limit_per_source=5, query="sleep",
            )
        value = context["sources"]["change_analyses"]["items"][0]["delta_json"]
        assert isinstance(value, str)
        assert value.endswith("… [truncated]")
        assert len(value) == MAX_TEXT_CHARS + len("… [truncated]")
    finally:
        _load_source_schema.cache_clear()
        engine.dispose()



def test_companion_source_caveats_are_bounded_and_visible_in_prompt_metadata():
    context = {
        "schema_status": "complete",
        "history_coverage": "complete",
        "retrieval_scan_complete": True,
        "retrieval_projection_complete": True,
        "missing_required_sources": [],
        "missing_optional_sources": [],
        "sources": {
            "evidence": {
                "status": "available",
                "returned_count": 22,
                "candidate_count": 22,
                "candidate_scan_complete": True,
                "has_more": False,
                "items": [
                    {
                        "id": f"evidence-{index}",
                        "source_context": {
                            "authority": "A" * 5000,
                            "document_candidate": {
                                "negation_status": "N" * 5000,
                            },
                        },
                    }
                    for index in range(22)
                ],
            },
        },
    }

    prompt = format_lcr_context_for_prompt(context, max_chars=12000)
    metadata_line = prompt.splitlines()[0]
    metadata = json.loads(metadata_line.removeprefix("LCR context metadata: "))

    caveats = metadata["source_caveats"]
    assert len(caveats) == 20
    assert caveats[0] == {
        "source_type": "evidence",
        "source_id": "evidence-0",
        "authority": "A" * 120,
        "negation_status": "N" * 60,
    }
    assert caveats[-1]["source_id"] == "evidence-19"
    assert len(caveats[-1]["authority"]) == 120
    assert len(caveats[-1]["negation_status"]) == 60
    assert "source_caveats" in metadata_line


def test_document_extraction_metadata_detects_legacy_pdf_page_gaps():
    from app.services.lcr_context import _document_extraction_metadata, _information_status

    metadata = _document_extraction_metadata(
        {
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "processed_page_numbers": [1, 3],
            "document_intelligence": {"status": "available", "observations": []},
        }
    )

    assert metadata["unprocessed_page_numbers"] == [2]
    status, explanation = _information_status(
        "document_extractions",
        {"extraction_status": "available", "extraction_metadata": metadata},
    )
    assert status == "document_extraction_partial"
    assert "Pages not yet processed: 2." in explanation


def test_document_extraction_metadata_derives_pdf_gaps_without_processed_manifest():
    from app.services.lcr_context import _document_extraction_metadata

    metadata = _document_extraction_metadata(
        {
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
        },
        "[Page 1 OCR]\nFirst page.\n[Page 3 OCR]\nThird page.",
    )

    assert metadata["unprocessed_page_numbers"] == [2]


def test_document_extraction_metadata_does_not_invent_gaps_for_standalone_scan():
    from app.services.lcr_context import _document_extraction_metadata

    metadata = _document_extraction_metadata(
        {
            "filename": "scan.jpg",
            "mime_type": "image/jpeg",
            "page_count": 1,
            "processed_page_numbers": [3],
        },
    )

    assert metadata.get("unprocessed_page_numbers", []) == []


def test_document_extraction_metadata_does_not_trust_stale_pdf_manifest():
    from app.services.lcr_context import _document_extraction_metadata

    metadata = _document_extraction_metadata(
        {
            "filename": "legacy-care-plan.pdf",
            "mime_type": "application/pdf",
            "page_count": 3,
            "processed_page_numbers": [1, 2, 3],
        },
        "[Page 1 OCR]\nFirst page.\n[Page 3 OCR]\nThird page.",
    )

    assert metadata["unprocessed_page_numbers"] == [2]


def test_unfiltered_full_history_uses_exact_count_and_ordered_page():
    _load_source_schema.cache_clear()
    engine = create_engine("sqlite:///:memory:")
    events = _care_events_schema(engine)
    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(events.insert(), [
            {
                "id": f"event-{index:03d}",
                "person_id": "person-a",
                "event_type": "daily_capture",
                "status": "recorded",
                "occurred_at": now - timedelta(minutes=index),
                "recorded_at": now - timedelta(minutes=index),
                "created_at": now - timedelta(minutes=index),
                "title": f"Care update {index}",
                "description": f"Saved care update {index}",
                "evidence_ids": "[]",
            }
            for index in range(260)
        ])

    with Session(engine) as db:
        context = build_lcr_context(
            db,
            "person-a",
            limit_per_source=10,
            include_all_history=True,
        )

    source = context["sources"]["events"]
    assert source["candidate_count"] == 260
    assert source["candidate_scan_complete"] is True
    assert source["returned_count"] == 10
    assert source["has_more"] is True
    assert source["selection"] == "full_history_exact_count_ordered_page"
    assert context["retrieval_strategy"] == "person_scoped_exact_count_ordered_history"
    assert source["items"][0]["id"] == "event-000"
