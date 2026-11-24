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
    assert provenance["claims"][0]["support_status"] == "aggregate_count_mismatch"
    assert provenance["claims"][0]["aggregate_review"]["count"] == 1
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
    assert provenance["claims"][0]["support_status"] == "aggregate_not_validated"


def test_qwen_context_sources_are_tied_to_their_own_record_reference():
    records = context_lines_to_source_records([
        "Recorded care event [event-1]: She felt unsteady near the kitchen.",
        "Recorded appointment [appointment-1]: Routine visit scheduled.",
        "Persisted extracted document [analysis-1] from evidence [original-file-1]: extracted_text=She felt unsteady.",
        "No matching source was found.",
    ])
    assert [(item["record_type"], item["record_id"]) for item in records] == [
        ("care_event", "event-1"),
        ("appointment", "appointment-1"),
        ("document_extraction", "analysis-1"),
    ]
    document = records[2]
    assert document["related_evidence_ids"] == ["original-file-1"]
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

def test_corrected_linked_observation_blocks_a_current_incident_total():
    aggregate = build_distinct_event_aggregate(
        [{
            "id": "corrected-incident",
            "event_type": "mobility",
            "status": "recorded",
            "occurred_at": datetime(2026, 11, 2, 8, tzinfo=timezone.utc),
            "title": "Unsteadiness near the kitchen",
            "description": "Original interpretation from the note.",
            "evidence_ids": ["corrected-source"],
            "source_correction_requires_review": True,
        }],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
    )
    assert aggregate["status"] == "source_correction_requires_review"
    assert aggregate["count"] is None
    provenance = build_claim_provenance(
        "Your mother has had one incident of unsteadiness this month.",
        [],
        [aggregate],
    )
    assert provenance["claims"][0]["support_status"] == "aggregate_not_validated"
    assert "corrected" in provenance["claims"][0]["aggregate_review"]["interpretation_limit"]

def test_no_matching_events_does_not_support_a_zero_incident_claim():
    aggregate = build_distinct_event_aggregate(
        [],
        topic="unsteadiness",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        window_label="this calendar month",
    )
    assert aggregate["status"] == "no_matching_records"
    assert aggregate["count"] == 0
    provenance = build_claim_provenance(
        "Your mother had zero incidents of unsteadiness this month.",
        [],
        [aggregate],
    )
    assert provenance["claims"][0]["support_status"] == "aggregate_not_validated"
    assert "does not establish that no incident occurred" in provenance["claims"][0]["aggregate_review"]["interpretation_limit"]

def test_material_care_paraphrase_without_exact_source_match_is_rejected():
    from app.services.claim_provenance import unmatched_material_claims

    provenance = build_claim_provenance(
        "Mum's mobility has worsened.",
        [{
            "record_type": "care_evidence",
            "record_id": "mobility-source",
            "source_text": "Caregiver reported that Mum walked less steadily yesterday.",
        }],
    )
    unmatched = unmatched_material_claims(provenance)
    assert len(unmatched) == 1
    assert unmatched[0]["support_status"] == "not_matched_to_saved_wording"


def test_exact_material_care_wording_is_not_flagged_as_unmatched():
    from app.services.claim_provenance import unmatched_material_claims

    sentence = "Caregiver reported that Mum felt unsteady."
    provenance = build_claim_provenance(
        sentence,
        [{
            "record_type": "care_evidence",
            "record_id": "unsteady-source",
            "source_text": sentence,
        }],
    )
    assert unmatched_material_claims(provenance) == []


def test_unsupported_appointment_status_is_a_material_claim():
    from app.services.claim_provenance import unmatched_material_claims

    provenance = build_claim_provenance(
        "The appointment is confirmed.",
        [{
            "record_type": "appointment",
            "record_id": "appointment-1",
            "source_text": "Appointment status: scheduled.",
        }],
    )
    assert len(unmatched_material_claims(provenance)) == 1


def test_unsupported_clinical_diagnosis_is_a_material_claim():
    from app.services.claim_provenance import unmatched_material_claims

    provenance = build_claim_provenance("She has dementia.", [])
    assert len(unmatched_material_claims(provenance)) == 1


def test_questions_and_conditional_guidance_are_not_classified_as_care_assertions():
    from app.services.claim_provenance import unmatched_material_claims

    provenance = build_claim_provenance(
        "Has Mum's mobility changed?\nPlease ask the clinician whether her medication needs review.",
        [],
    )
    assert unmatched_material_claims(provenance) == []

def test_reported_evidence_requires_the_response_to_preserve_attribution():
    from app.services.claim_provenance import unmatched_material_claims

    source = {
        "record_type": "care_evidence",
        "record_id": "reported-mobility",
        "source_text": "Sarah reported that Mum felt unsteady.",
        "record_status": "caregiver_reported",
        "reported_by": "Sarah",
    }
    unqualified = build_claim_provenance("Mum felt unsteady.", [source])
    assert unqualified["claims"][0]["support_status"] == "not_matched_to_saved_wording"
    assert len(unmatched_material_claims(unqualified)) == 1

    attributed = build_claim_provenance("Sarah reported that Mum felt unsteady.", [source])
    assert attributed["claims"][0]["support_status"] == "matched_saved_wording"
    assert unmatched_material_claims(attributed) == []


def test_event_claim_inherits_report_attribution_from_linked_evidence():
    result = build_claim_provenance(
        "Mum fell.",
        [{
            "record_type": "care_event",
            "record_id": "reported-event",
            "source_text": "Mum fell.",
            "record_status": "recorded",
            "related_evidence": [{
                "record_id": "reported-evidence",
                "record_status": "caregiver_reported",
                "reported_by": "Sarah",
            }],
        }],
    )
    assert result["claims"][0]["support_status"] == "not_matched_to_saved_wording"


def test_shared_lcr_context_source_types_are_mapped_before_enrichment():
    from app.services.claim_provenance import context_lines_to_source_records

    records = context_lines_to_source_records([
        "Shared LCR context: " + json.dumps({
            "source_type": "events",
            "source_id": "event-source-map",
            "record": {
                "id": "event-source-map",
                "status": "recorded",
                "time_provenance": "caregiver_reported",
                "occurred_at": "2026-11-23T12:00:00+00:00",
            },
        })
    ])
    assert len(records) == 1
    assert records[0]["record_type"] == "care_event"
    assert records[0]["record_status"] == "recorded"
    assert records[0]["source_attribution"] == "caregiver_reported"


def test_negation_and_uncertainty_are_not_removed_from_supported_claims():
    negative = build_claim_provenance(
        "Sarah reported that Mum did not fall.",
        [{
            "record_type": "care_evidence",
            "record_id": "negative-report",
            "source_text": "Sarah reported that Mum did not fall.",
            "record_status": "caregiver_reported",
            "reported_by": "Sarah",
        }],
    )
    uncertain = build_claim_provenance(
        "Sarah reported that Mum may have fallen.",
        [{
            "record_type": "care_evidence",
            "record_id": "uncertain-report",
            "source_text": "Sarah reported that Mum may have fallen.",
            "record_status": "caregiver_reported",
            "reported_by": "Sarah",
        }],
    )
    assert negative["claims"][0]["support_status"] == "matched_saved_wording"
    assert uncertain["claims"][0]["support_status"] == "matched_saved_wording"

def test_explicitly_attributed_exact_source_quote_is_supported():
    source_text = "Caregiver reported that Mum had a quieter night last week."
    provenance = build_claim_provenance(
        "Caregiver-reported: “Mum had a quieter night last week.”",
        [{
            "record_type": "care_event",
            "record_id": "quoted-care-event",
            "source_text": source_text,
            "record_status": "recorded",
            "source_attribution": "caregiver_reported",
        }],
    )
    assert provenance["claims"][0]["support_status"] == "matched_saved_wording"
    assert provenance["claims"][0]["source_references"][0]["matched_passage"] == (
        "Mum had a quieter night last week."
    )


def test_exact_source_quote_without_attribution_is_not_supported_for_reported_evidence():
    provenance = build_claim_provenance(
        "“Mum had a quieter night last week.”",
        [{
            "record_type": "care_event",
            "record_id": "unattributed-quoted-care-event",
            "source_text": "Caregiver reported that Mum had a quieter night last week.",
            "record_status": "recorded",
            "source_attribution": "caregiver_reported",
        }],
    )
    assert provenance["claims"][0]["support_status"] == "not_matched_to_saved_wording"


def test_attributed_quote_must_exist_verbatim_in_the_same_source():
    provenance = build_claim_provenance(
        "Caregiver-reported: “Mum slept through the night.”",
        [{
            "record_type": "care_event",
            "record_id": "different-quoted-care-event",
            "source_text": "Caregiver reported that Mum had a quieter night last week.",
            "record_status": "recorded",
            "source_attribution": "caregiver_reported",
        }],
    )
    assert provenance["claims"][0]["support_status"] == "not_matched_to_saved_wording"

def test_lcr_event_evidence_prefix_resolves_to_persisted_event_type():
    from app.services.claim_provenance import context_lines_to_source_records

    records = context_lines_to_source_records([
        'LCR event evidence: {"event_id":"lcr-event-42","title":"Mobility update",'
        '"description":"Caregiver reported that Mum felt unsteady."}'
    ])
    assert records == [{
        "record_type": "care_event",
        "record_id": "lcr-event-42",
        "label": "Care update",
        "source_text": (
            'LCR event evidence: {"event_id":"lcr-event-42","title":"Mobility update",'
            '"description":"Caregiver reported that Mum felt unsteady."}'
        ),
        "source_excerpt": (
            'LCR event evidence: {"event_id":"lcr-event-42","title":"Mobility update",'
            '"description":"Caregiver reported that Mum felt unsteady."}'
        ),
        "record_status": None,
        "related_evidence_ids": [],
    }]

