from app.services.claim_provenance import build_claim_provenance


def test_exact_statement_is_linked_only_to_the_source_that_contains_it():
    result = build_claim_provenance(
        "She felt unsteady near the kitchen.",
        [
            {
                "record_type": "care_event",
                "record_id": "event-1",
                "label": "Care entry",
                "source_text": "She felt unsteady near the kitchen.",
                "occurred_at": "2026-11-02T08:00:00+00:00",
            },
            {
                "record_type": "care_event",
                "record_id": "event-2",
                "label": "Care entry",
                "source_text": "She slept well last night.",
            },
        ],
    )
    claim = result["claims"][0]
    assert claim["support_status"] == "matched_saved_wording"
    assert [item["record_id"] for item in claim["source_references"]] == ["event-1"]


def test_unrelated_evidence_is_never_attached_to_a_claim():
    result = build_claim_provenance(
        "Your mother had three incidents of unsteadiness this month.",
        [
            {
                "record_type": "care_event",
                "record_id": "unrelated",
                "source_text": "Your mother attended an appointment this month.",
            }
        ],
    )
    claim = result["claims"][0]
    assert claim["support_status"] == "not_matched_to_saved_wording"
    assert claim["source_references"] == []
    assert result["unmatched_statement_count"] == 1


def test_paraphrase_is_not_misrepresented_as_an_exact_source_match():
    result = build_claim_provenance(
        "She was unstable while walking.",
        [{"record_type": "care_evidence", "record_id": "e1", "source_text": "She felt unsteady while walking."}],
    )
    assert result["claims"][0]["support_status"] == "not_matched_to_saved_wording"


def test_duplicate_sources_are_not_counted_as_independent_corroboration():
    source = {"record_type": "care_evidence", "label": "Saved note", "source_text": "She felt dizzy."}
    result = build_claim_provenance("She felt dizzy.", [
        {**source, "record_id": "e1"},
        {**source, "record_id": "e2"},
    ])
    assert len(result["claims"][0]["source_references"]) == 2
    assert "independent" in result["matching_limit"]



def test_matched_event_keeps_its_original_supporting_document_details():
    result = build_claim_provenance(
        "She felt dizzy after lunch.",
        [{
            "record_type": "care_event",
            "record_id": "event-with-document",
            "source_text": "She felt dizzy after lunch.",
            "related_evidence_ids": ["document-1"],
            "related_evidence": [{
                "record_id": "document-1",
                "label": "discharge-summary.pdf",
                "source_excerpt": "The patient reported dizziness after lunch.",
            }],
        }],
    )
    reference = result["claims"][0]["source_references"][0]
    assert reference["related_evidence"][0]["record_id"] == "document-1"
    assert reference["related_evidence"][0]["label"] == "discharge-summary.pdf"

def test_corrected_or_withdrawn_source_is_retained_as_history_not_current_support():
    result = build_claim_provenance(
        "She felt dizzy after lunch.",
        [{
            "record_type": "care_event",
            "record_id": "corrected-event",
            "source_text": "She felt dizzy after lunch.",
            "record_status": "superseded",
        }],
    )
    claim = result["claims"][0]
    assert claim["support_status"] == "matched_historical_only"
    assert claim["source_references"][0]["source_state_label"] == "Earlier or corrected record; not treated as current"
    assert result["unmatched_statement_count"] == 1

def test_claim_reference_carries_reviewed_source_relationship_and_duplicate_status():
    result = build_claim_provenance(
        "She felt dizzy after lunch.",
        [{
            "record_type": "care_evidence",
            "record_id": "evidence-1",
            "source_text": "She felt dizzy after lunch.",
            "source_independence_status": "shared_origin",
            "exact_wording_duplicate_count": 2,
        }],
    )
    reference = result["claims"][0]["source_references"][0]
    assert reference["source_independence_status"] == "shared_origin"
    assert reference["exact_wording_duplicate_count"] == 2

def test_correction_trace_keeps_the_prior_wording_and_reason():
    result = build_claim_provenance(
        "The updated note says she felt steady after lunch.",
        [{
            "record_type": "evidence_observation",
            "record_id": "corrected-observation",
            "source_text": "The updated note says she felt steady after lunch.",
            "record_status": "recorded",
            "correction_note": "This saved detail replaces an earlier version.",
            "correction_rationale": "The caregiver corrected the time of the observation.",
            "previous_source_excerpt": "She felt unsteady after lunch.",
        }],
    )
    reference = result["claims"][0]["source_references"][0]
    assert reference["correction_note"] == "This saved detail replaces an earlier version."
    assert reference["correction_rationale"] == "The caregiver corrected the time of the observation."
    assert reference["previous_source_excerpt"] == "She felt unsteady after lunch."

from datetime import datetime, timedelta, timezone

from app.services.claim_provenance import (
    build_distinct_event_aggregate,
    context_lines_to_source_records,
)


def test_three_incident_statement_is_supported_only_by_three_distinct_dated_events():
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 13, tzinfo=timezone.utc)
    events = [
        {
            "id": "incident-1", "event_type": "mobility", "status": "recorded",
            "occurred_at": datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
            "title": "Unsteadiness near the kitchen", "description": "She felt unsteady.",
            "evidence_ids": ["source-note-1"],
        },
        {
            "id": "incident-2", "event_type": "mobility", "status": "recorded",
            "occurred_at": datetime(2026, 11, 6, 9, tzinfo=timezone.utc),
            "title": "Lost balance while standing", "description": "She lost balance.",
            "evidence_ids": ["source-note-2"],
        },
        {
            "id": "incident-3", "event_type": "mobility", "status": "completed",
            "occurred_at": datetime(2026, 11, 10, 10, tzinfo=timezone.utc),
            "title": "Stumbled near the bathroom", "description": "She stumbled.",
            "evidence_ids": ["source-note-3"],
        },
        {
            "id": "zz-duplicate-of-incident-1", "event_type": "mobility", "status": "recorded",
            "occurred_at": datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
            "title": "Unsteadiness near the kitchen", "description": "She felt unsteady.",
            "evidence_ids": ["source-note-1"],
        },
        {
            "id": "pending-not-an-incident", "event_type": "mobility", "status": "pending",
            "occurred_at": datetime(2026, 11, 11, 8, tzinfo=timezone.utc),
            "title": "Unsteady while walking", "description": "Pending review.",
            "evidence_ids": ["source-note-4"],
        },
    ]
    aggregate = build_distinct_event_aggregate(
        events, topic="unsteadiness", start_at=start, end_at=end,
        window_label="this calendar month",
    )
    assert aggregate["status"] == "validated"
    assert aggregate["count"] == 3
    assert aggregate["duplicate_event_ids"] == ["zz-duplicate-of-incident-1"]
    assert [item["record_id"] for item in aggregate["source_references"]] == [
        "incident-1", "incident-2", "incident-3",
    ]
    provenance = build_claim_provenance(
        "Your mother has had three incidents of unsteadiness this month.",
        [],
        [aggregate],
    )
    claim = provenance["claims"][0]
    assert claim["support_status"] == "matched_validated_aggregate"
    assert len(claim["source_references"]) == 3
    assert provenance["unmatched_statement_count"] == 0


def test_numeric_claim_is_not_supported_when_its_count_does_not_match_the_events():
    aggregate = build_distinct_event_aggregate(
        [{
            "id": "incident-one", "event_type": "mobility", "status": "recorded",
            "occurred_at": datetime(2026, 11, 2, tzinfo=timezone.utc),
            "title": "Unsteadiness near the kitchen", "description": "",
        }],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
    )
    provenance = build_claim_provenance(
        "Your mother has had three incidents of unsteadiness this month.",
        [],
        [aggregate],
    )
    assert provenance["claims"][0]["support_status"] == "not_matched_to_saved_wording"
    assert provenance["claims"][0]["source_references"] == []


def test_incomplete_aggregate_never_supports_a_numeric_claim():
    aggregate = build_distinct_event_aggregate(
        [],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
        truncated=True,
    )
    provenance = build_claim_provenance(
        "Your mother has had zero incidents of unsteadiness this month.",
        [],
        [aggregate],
    )
    assert aggregate["status"] == "incomplete_source_window"
    assert provenance["claims"][0]["support_status"] == "not_matched_to_saved_wording"


def test_qwen_context_sources_are_tied_to_their_own_record_reference():
    records = context_lines_to_source_records([
        "Recorded care event [event-1]: She felt unsteady near the kitchen.",
        "Recorded appointment [appointment-1]: Routine visit scheduled.",
        "No matching source was found.",
    ])
    assert [(item["record_type"], item["record_id"]) for item in records] == [
        ("care_event", "event-1"),
        ("appointment", "appointment-1"),
    ]
    provenance = build_claim_provenance("She felt unsteady near the kitchen.", records)
    assert provenance["claims"][0]["source_references"][0]["record_id"] == "event-1"

def test_same_day_identical_reports_with_different_times_are_not_guessed_as_one_or_two_incidents():
    aggregate = build_distinct_event_aggregate(
        [
            {
                "id": "same-day-a", "event_type": "mobility", "status": "recorded",
                "occurred_at": datetime(2026, 11, 4, 8, tzinfo=timezone.utc),
                "title": "Unsteadiness near the kitchen", "description": "She felt unsteady.",
            },
            {
                "id": "same-day-b", "event_type": "mobility", "status": "recorded",
                "occurred_at": datetime(2026, 11, 4, 10, tzinfo=timezone.utc),
                "title": "Unsteadiness near the kitchen", "description": "She felt unsteady.",
            },
        ],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
    )
    assert aggregate["status"] == "ambiguous_duplicate_candidates"
    assert aggregate["count"] is None
    assert aggregate["source_references"] == []


def test_reviewed_shared_origin_evidence_does_not_inflate_distinct_incident_count():
    aggregate = build_distinct_event_aggregate(
        [
            {
                "id": "shared-origin-a", "event_type": "mobility", "status": "recorded",
                "occurred_at": datetime(2026, 11, 3, 8, tzinfo=timezone.utc),
                "title": "Unsteadiness reported by family", "description": "She felt unsteady.",
                "evidence_ids": ["note-original"],
            },
            {
                "id": "shared-origin-b", "event_type": "mobility", "status": "recorded",
                "occurred_at": datetime(2026, 11, 3, 8, tzinfo=timezone.utc),
                "title": "Unsteadiness copied into another note", "description": "A copied report.",
                "evidence_ids": ["note-copy"],
            },
        ],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
        shared_origin_evidence_pairs={frozenset(("note-original", "note-copy"))},
    )
    assert aggregate["status"] == "validated"
    assert aggregate["count"] == 1
    assert aggregate["duplicate_event_ids"] == ["shared-origin-b"]
