from datetime import datetime, timedelta, timezone

from app.services.care_attention_intelligence import build_care_attention_assessment

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def test_missing_or_unrecognized_open_loop_status_is_not_silently_dropped():
    result = build_care_attention_assessment(
        records=[],
        open_loops=[
            {"id": "missing-status", "source": "open_loop_records", "title": "Clinic callback"},
            {"id": "unknown-status", "source": "open_loop_records", "title": "Medication question",
             "recorded_status": "awaiting_magic"},
        ],
        uncertainties=[], missing_information=[], as_of=NOW,
    )
    by_id = {item["record_id"]: item for item in result["needs_clarification"]}
    assert set(by_id) == {"missing-status", "unknown-status"}
    assert by_id["missing-status"]["status"] == "unknown"
    assert by_id["unknown-status"]["status"] == "awaiting_magic"
    assert all("cannot safely treat it as open or resolved" in item["priority_basis"][0]
               for item in by_id.values())
    assert result["needs_attention_now"] == []
    assert result["important_to_address"] == []
    assert result["can_wait_with_documented_basis"] == []


def test_explicit_overdue_deadline_reopens_even_when_deferred():
    result = build_care_attention_assessment(
        records=[], open_loops=[{
            "id": "loop-1", "source": "open_loop_records", "title": "Follow-up",
            "recorded_status": "deferred", "deadline_source": "recorded_commitment",
            "due_at": NOW - timedelta(hours=1), "review_at": NOW + timedelta(days=2),
            "deferred_reason": "Waiting for appointment", "evidence_ids": ["ev-1"],
        }], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert [item["record_id"] for item in result["needs_attention_now"]] == ["loop-1"]
    assert result["can_wait_with_documented_basis"] == []


def test_completed_critical_loop_is_never_immediate():
    result = build_care_attention_assessment(
        records=[], open_loops=[{
            "id": "loop-2", "source": "open_loop_records", "title": "Resolved",
            "recorded_status": "completed", "priority": "critical",
            "priority_reasons": ["Critical consequence of delay was explicitly recorded"],
        }], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["needs_attention_now"] == []


def test_unexplained_high_priority_is_not_automatically_urgent():
    result = build_care_attention_assessment(
        records=[], open_loops=[{
            "id": "loop-3", "source": "open_loop_records", "title": "Review",
            "recorded_status": "open", "priority": "high", "priority_reasons": [],
        }], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["needs_attention_now"] == []
    assert result["important_to_address"] == []
    assert result["needs_clarification"][0]["category"] == "needs_clarification"


def test_documented_future_deferral_does_not_claim_safety():
    result = build_care_attention_assessment(
        records=[], open_loops=[{
            "id": "loop-4", "source": "open_loop_records", "title": "Review",
            "recorded_status": "deferred", "deferred_reason": "Waiting for scheduled visit",
            "review_at": NOW + timedelta(days=3),
        }], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = result["can_wait_with_documented_basis"][0]
    assert "not a safety assessment" in item["priority_basis"][0]


def test_repeated_structured_observations_are_grouped_with_provenance():
    result = build_care_attention_assessment(
        records=[
            {"id": "obs-1", "source": "evidence_observations", "epistemic_status": "reported", "subject_id": "person-1",
             "subject_label": "Mom", "attribute": "usual_activity", "observed_value": "walks daily",
             "occurred_at": NOW - timedelta(days=3), "evidence_ids": ["ev-1"]},
            {"id": "obs-2", "source": "evidence_observations", "epistemic_status": "reported", "subject_id": "person-1",
             "subject_label": "Mom", "attribute": "usual_activity", "observed_value": "stopped walking",
             "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-2"]},
        ], open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = result["important_to_address"][0]
    assert item["pattern_type"] == "repeated_attribute_with_changed_values"
    assert item["record_count"] == 2
    assert item["evidence_ids"] == ["ev-1", "ev-2"]
    assert len(item["records"]) == 2


def test_multi_attribute_cluster_is_reviewed_without_causal_claim():
    result = build_care_attention_assessment(
        records=[
            {"id": f"obs-{i}", "source": "evidence_observations", "epistemic_status": "reported", "subject_id": "person-2",
             "attribute": f"attribute-{i}", "observed_value": f"value-{i}",
             "occurred_at": NOW - timedelta(days=i), "evidence_ids": [f"ev-{i}"]}
            for i in (1, 2, 3)
        ], open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(item for item in result["important_to_address"]
                if item["pattern_type"] == "multi_attribute_observation_cluster")
    assert "not established" in item["reason"]


def test_unresolved_conflicts_and_gaps_need_clarification():
    result = build_care_attention_assessment(
        records=[], open_loops=[],
        uncertainties=[{"id": "conflict-1", "source": "conflicts", "title": "Reports differ",
                        "status": "unresolved", "evidence_ids": ["ev-3"]}],
        missing_information=[{"id": "gap-1", "source": "missing_information",
                              "title": "Medication instruction", "status": "open"}],
        as_of=NOW,
    )
    assert len(result["needs_clarification"]) == 2



def test_recent_observation_is_compared_with_only_explicit_personal_baseline():
    result = build_care_attention_assessment(
        records=[{
            "id": "current-1", "source": "evidence_observations", "epistemic_status": "reported", "subject_id": "person-3",
            "subject_label": "Mom", "attribute": "usual_orientation",
            "observed_value": "confused about location", "occurred_at": NOW - timedelta(days=1),
            "evidence_ids": ["ev-current"],
        }],
        baseline_items=[{
            "id": "baseline-1", "source": "evidence_observations", "epistemic_status": "reported", "baseline_basis": "explicit_persisted_baseline_marker", "subject_id": "person-3",
            "subject_label": "Mom", "attribute": "usual_orientation",
            "observed_value": "usually knows where she is", "evidence_ids": ["ev-baseline"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(item for item in result["important_to_address"]
                if item["pattern_type"] == "explicit_baseline_deviation")
    assert item["evidence_ids"] == ["ev-baseline", "ev-current"]
    assert len(item["records"]) == 2
    assert "does not establish clinical severity or cause" in item["reason"]


def test_no_implicit_baseline_is_created_from_oldest_record():
    result = build_care_attention_assessment(
        records=[{
            "id": "current-2", "source": "evidence_observations", "epistemic_status": "reported", "subject_id": "person-4",
            "attribute": "usual_orientation", "observed_value": "confused about location",
            "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-current-2"],
        }],
        baseline_items=[], open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert not any(item["pattern_type"] == "explicit_baseline_deviation"
                   for item in result["important_to_address"])



def test_deadline_within_24_hours_is_important_not_immediate_or_deferred():
    result = build_care_attention_assessment(
        records=[], open_loops=[{
            "id": "loop-5", "source": "open_loop_records", "title": "Upcoming follow-up",
            "recorded_status": "deferred", "priority": "high",
            "priority_reasons": ["Explicit follow-up deadline is within 24 hours"],
            "deadline_source": "recorded_commitment", "due_at": NOW + timedelta(hours=12),
            "deferred_reason": "Waiting for appointment", "review_at": NOW + timedelta(days=2),
        }], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["needs_attention_now"] == []
    assert result["can_wait_with_documented_basis"] == []
    item = result["important_to_address"][0]
    assert item["record_id"] == "loop-5"
    assert item["category"] == "important_to_address"



def test_uncertain_inferred_negated_and_superseded_observations_do_not_form_patterns():
    result = build_care_attention_assessment(
        records=[
            {"id": "reported", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-5", "attribute": "mobility", "observed_value": "needs help",
             "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-reported"]},
            {"id": "inferred", "source": "evidence_observations", "epistemic_status": "inferred",
             "subject_id": "person-5", "attribute": "mobility", "observed_value": "cannot walk",
             "occurred_at": NOW - timedelta(days=2), "evidence_ids": ["ev-inferred"]},
            {"id": "uncertain", "source": "evidence_observations", "epistemic_status": "uncertain",
             "subject_id": "person-5", "attribute": "mobility", "observed_value": "may need help",
             "occurred_at": NOW - timedelta(days=3), "evidence_ids": ["ev-uncertain"]},
            {"id": "negated", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-5", "attribute": "mobility", "observed_value": "walks independently",
             "negated": True, "occurred_at": NOW - timedelta(days=4), "evidence_ids": ["ev-negated"]},
        ],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["important_to_address"] == []
    clarification_ids = {item["record_id"] for item in result["needs_clarification"]}
    assert clarification_ids == {"inferred", "uncertain"}


def test_correcting_observation_is_used_and_superseded_observation_is_excluded():
    result = build_care_attention_assessment(
        records=[
            {"id": "old", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-6", "attribute": "orientation", "observed_value": "usual",
             "occurred_at": NOW - timedelta(days=2), "evidence_ids": ["ev-old"]},
            {"id": "correction", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-6", "attribute": "orientation", "observed_value": "disoriented",
             "supersedes_observation_id": "old", "correction_rationale": "Corrected after checking source note",
             "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-correction"]},
        ],
        baseline_items=[{
            "id": "baseline", "source": "evidence_observations", "epistemic_status": "reported",
            "baseline_basis": "explicit_persisted_baseline_marker",
            "subject_id": "person-6", "attribute": "orientation", "observed_value": "usually oriented",
            "evidence_ids": ["ev-baseline"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(item for item in result["important_to_address"]
                if item["pattern_type"] == "explicit_baseline_deviation")
    assert [record["record_id"] for record in item["records"]] == ["baseline", "correction"]
    assert item["evidence_ids"] == ["ev-baseline", "ev-correction"]


def test_baseline_without_explicit_persisted_marker_is_not_used():
    result = build_care_attention_assessment(
        records=[{
            "id": "current", "source": "evidence_observations", "epistemic_status": "reported",
            "subject_id": "person-7", "attribute": "sleep", "observed_value": "awake all night",
            "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-current"],
        }],
        baseline_items=[{
            "id": "unmarked", "source": "evidence_observations", "epistemic_status": "reported",
            "subject_id": "person-7", "attribute": "sleep", "observed_value": "usually sleeps",
            "evidence_ids": ["ev-baseline"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert not any(item["pattern_type"] == "explicit_baseline_deviation"
                   for item in result["important_to_address"])


def test_same_numeric_value_with_different_units_is_a_changed_observation():
    result = build_care_attention_assessment(
        records=[{
            "id": "current-units", "source": "evidence_observations", "epistemic_status": "reported",
            "subject_id": "person-8", "attribute": "fluid_amount", "observed_value": "5",
            "units": "ml", "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-current-units"],
        }],
        baseline_items=[{
            "id": "baseline-units", "source": "evidence_observations", "epistemic_status": "reported",
            "baseline_basis": "explicit_persisted_baseline_marker",
            "subject_id": "person-8", "attribute": "fluid_amount", "observed_value": "5",
            "units": "mg", "evidence_ids": ["ev-baseline-units"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(item for item in result["important_to_address"]
                if item["pattern_type"] == "explicit_baseline_deviation")
    assert item["record_count"] == 1


def test_ambiguous_observation_is_visible_for_clarification_not_pattern_review():
    result = build_care_attention_assessment(
        records=[{
            "id": "ambiguous", "source": "evidence_observations", "epistemic_status": "reported",
            "subject_id": "person-9", "attribute": "behavior", "observed_value": "worse",
            "ambiguity_note": "The source does not identify what became worse.",
            "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["ev-ambiguous"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["important_to_address"] == []
    assert result["needs_clarification"][0]["record_id"] == "ambiguous"



def test_typed_fact_comparison_excludes_superseded_fact_row():
    result = build_care_attention_assessment(
        records=[
            {"id": "fact-old", "source": "facts", "epistemic_status": "reported",
             "subject_id": "person-10", "fact_type": "sleep_duration", "attribute": "sleep_duration",
             "fact_value": "7 hours", "observed_value": "7 hours",
             "effective_at": NOW - timedelta(days=3), "superseded_by_fact_id": "fact-new",
             "evidence_ids": ["ev-fact-old"]},
            {"id": "fact-new", "source": "facts", "epistemic_status": "reported",
             "subject_id": "person-10", "fact_type": "sleep_duration", "attribute": "sleep_duration",
             "fact_value": "3 hours", "observed_value": "3 hours",
             "effective_at": NOW - timedelta(days=1), "evidence_ids": ["ev-fact-new"]},
        ],
        baseline_items=[{
            "id": "fact-baseline", "source": "facts", "epistemic_status": "reported",
            "baseline_basis": "explicit_persisted_baseline_marker",
            "subject_id": "person-10", "fact_type": "sleep_duration", "attribute": "sleep_duration",
            "fact_value": "8 hours", "observed_value": "8 hours",
            "evidence_ids": ["ev-fact-baseline"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(item for item in result["important_to_address"]
                if item["pattern_type"] == "explicit_baseline_deviation")
    assert [record["record_id"] for record in item["records"]] == ["fact-baseline", "fact-new"]


def test_free_text_events_are_not_grouped_as_structured_change_patterns():
    result = build_care_attention_assessment(
        records=[
            {"id": "event-1", "source": "events", "subject_id": "person-11",
             "event_type": "observation", "title": "A little tired", "occurred_at": NOW - timedelta(days=2),
             "evidence_ids": ["ev-event-1"]},
            {"id": "event-2", "source": "events", "subject_id": "person-11",
             "event_type": "observation", "title": "Very tired", "occurred_at": NOW - timedelta(days=1),
             "evidence_ids": ["ev-event-2"]},
        ],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["important_to_address"] == []



def test_duplicate_projections_of_one_source_do_not_create_repeated_pattern():
    result = build_care_attention_assessment(
        records=[
            {"id": "projection-a", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-12", "attribute": "sleep", "observed_value": "awake",
             "occurred_at": NOW - timedelta(days=2), "evidence_ids": ["same-source"]},
            {"id": "projection-b", "source": "evidence_observations", "epistemic_status": "reported",
             "subject_id": "person-12", "attribute": "sleep", "observed_value": "slept",
             "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["same-source"]},
        ],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["important_to_address"] == []


def test_baseline_and_current_observation_from_same_source_do_not_create_change():
    result = build_care_attention_assessment(
        records=[{
            "id": "current-same-source", "source": "evidence_observations", "epistemic_status": "reported",
            "subject_id": "person-13", "attribute": "orientation", "observed_value": "confused",
            "occurred_at": NOW - timedelta(days=1), "evidence_ids": ["same-source"],
        }],
        baseline_items=[{
            "id": "baseline-same-source", "source": "evidence_observations", "epistemic_status": "reported",
            "baseline_basis": "explicit_persisted_baseline_marker",
            "subject_id": "person-13", "attribute": "orientation", "observed_value": "usually oriented",
            "evidence_ids": ["same-source"],
        }],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert result["important_to_address"] == []



def test_persisted_change_is_important_only_with_complete_baseline_and_source_records():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-1", "title": "Mobility", "significance_verdict": "meaningful_candidate",
        "attention_candidate": True, "follow_up_candidate": True,
        "baseline_coverage_status": "covered", "current_coverage_status": "covered",
        "baseline_is_stable": True, "baseline_history_completeness": "complete",
        "expected_source_record_count": 1,
        "explanation": "Recorded mobility events increased.",
        "records": [{"source": "events", "record_id": "event-1", "title": "Fall",
                     "evidence_ids": ["ev-1"]}],
    }])
    assert len(important) == 1
    assert important[0]["source"] == "persisted_change"
    assert important[0]["evidence_ids"] == ["ev-1"]
    assert clarification == []


def test_persisted_change_remains_visible_when_baseline_coverage_is_incomplete():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-2", "title": "Sleep", "significance_verdict": "meaningful_candidate",
        "attention_candidate": True, "follow_up_candidate": True,
        "baseline_coverage_status": "unknown", "current_coverage_status": "covered",
        "baseline_is_stable": False, "baseline_history_completeness": "unknown",
        "explanation": "More sleep events were recorded.",
        "records": [{"source": "events", "record_id": "event-2", "title": "Night waking",
                     "evidence_ids": ["ev-2"]}],
    }])
    assert len(important) == 1
    assert important[0]["history_completeness"] == "partial_or_unknown"
    assert "size and timing of the change remain uncertain" in important[0]["reason"]
    assert clarification == []


def test_persisted_change_remains_visible_when_source_records_are_unavailable():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-3", "title": "Mobility", "significance_verdict": "meaningful_candidate",
        "attention_candidate": True, "follow_up_candidate": True,
        "baseline_coverage_status": "covered", "current_coverage_status": "covered",
        "baseline_is_stable": True, "baseline_history_completeness": "complete",
        "explanation": "A recorded change exists.",
        "records": [],
    }])
    assert len(important) == 1
    assert important[0]["history_completeness"] == "partial_or_unknown"
    assert important[0]["source_record_ids"] == []
    assert clarification == []


def test_follow_up_candidate_remains_visible_with_insufficient_evidence_verdict():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-needs-follow-up", "significance_verdict": "insufficient_evidence",
        "attention_candidate": False, "follow_up_candidate": True,
        "baseline_coverage_status": "unknown", "current_coverage_status": "covered",
        "baseline_is_stable": False, "baseline_history_completeness": "unknown",
        "expected_source_record_count": 1,
        "records": [{"source": "events", "record_id": "event-one", "evidence_ids": ["evidence-one"]}],
    }])
    assert len(important) == 1
    assert important[0]["care_significance"] == "follow_up_candidate"
    assert important[0]["history_completeness"] == "partial_or_unknown"
    assert important[0]["evidence_ids"] == ["evidence-one"]
    assert clarification == []



def test_persisted_change_keeps_evidence_significance_and_follow_up_dimensions_separate():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-dimensions",
        "significance_verdict": "meaningful_candidate",
        "significance_assessment_id": "assessment-dimensions",
        "significance_assessment_status": "linked",
        "evidence_status": "reported",
        "evidence_confidence": 0.42,
        "significance_confidence": 0.61,
        "assessment_dimensions": [{"name": "frequency", "score": 0.61}],
        "attention_candidate": True,
        "follow_up_candidate": True,
        "action_status": "open",
        "linked_follow_up_outcome_status": "outcome_unknown",
        "linked_follow_up_id": "loop-dimensions",
        "baseline_coverage_status": "unknown",
        "current_coverage_status": "covered",
        "baseline_is_stable": False,
        "baseline_history_completeness": "unknown",
        "expected_source_record_count": 1,
        "records": [{
            "source": "events",
            "record_id": "event-dimensions",
            "title": "Repeated waking",
            "evidence_ids": ["evidence-dimensions"],
        }],
        "explanation": "A saved sleep change needs review.",
    }])
    assert len(important) == 1
    item = important[0]
    assert item["care_significance"] == "attention_candidate"
    assert item["significance_verdict"] == "meaningful_candidate"
    assert item["significance_assessment_id"] == "assessment-dimensions"
    assert item["significance_assessment_status"] == "linked"
    assert item["evidence_status"] == "reported"
    assert item["evidence_confidence"] == 0.42
    assert item["significance_confidence"] == 0.61
    assert item["confidence_interpretation"] == "uncalibrated_score_not_probability"
    assert item["assessment_dimensions"] == [{"name": "frequency", "score": 0.61}]
    assert item["action_status"] == "open"
    assert item["linked_follow_up_outcome_status"] == "outcome_unknown"
    assert item["linked_follow_up_id"] == "loop-dimensions"
    assert item["history_completeness"] == "partial_or_unknown"
    assert clarification == []


def test_missing_significance_assessment_is_disclosed_without_hiding_saved_follow_up():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-no-assessment",
        "significance_verdict": "insufficient_evidence",
        "significance_assessment_status": "not_recorded",
        "attention_candidate": False,
        "follow_up_candidate": True,
        "action_status": "closed",
        "linked_follow_up_outcome_status": "outcome_unknown",
        "linked_follow_up_id": "loop-closed-unknown",
        "expected_source_record_count": 0,
        "records": [],
        "explanation": "A saved follow-up concern remains open.",
    }])
    assert len(important) == 1
    assert important[0]["evidence_status"] == "unknown"
    assert important[0]["significance_assessment_status"] == "not_recorded"
    assert "No separate significance assessment is linked" in important[0]["reason"]
    assert important[0]["action_status"] == "closed"
    assert important[0]["linked_follow_up_outcome_status"] == "outcome_unknown"
    assert "follow-up was closed, but its outcome was not recorded" in important[0]["reason"]
    assert important[0]["history_completeness"] == "partial_or_unknown"
    assert clarification == []

def test_rejected_or_not_meaningful_persisted_changes_are_not_surfaced():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([
        {"id": "change-rejected", "significance_verdict": "meaningful_candidate",
         "confirmation_status": "rejected", "attention_candidate": True},
        {"id": "change-not-meaningful", "significance_verdict": "not_meaningful",
         "follow_up_candidate": True},
    ])
    assert important == []
    assert clarification == []



def test_persisted_change_accepts_linked_care_event_ids_as_source_records():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-event-source", "significance_verdict": "meaningful_candidate",
        "attention_candidate": True, "baseline_coverage_status": "covered",
        "current_coverage_status": "covered", "baseline_is_stable": True,
        "baseline_history_completeness": "complete",
        "expected_source_record_count": 1,
        "records": [{"source": "events", "record_id": "care-event-1", "evidence_ids": []}],
    }])
    assert len(important) == 1
    assert important[0]["source_record_ids"] == ["care-event-1"]
    assert important[0]["evidence_ids"] == []
    assert clarification == []



def test_bounded_persisted_change_history_cannot_produce_complete_priority():
    result = build_care_attention_assessment(
        records=[], open_loops=[], uncertainties=[], missing_information=[],
        persisted_changes=[{
            "id": "bounded-change", "significance_verdict": "meaningful_candidate",
            "attention_candidate": True, "baseline_coverage_status": "covered",
            "current_coverage_status": "covered", "baseline_is_stable": True,
            "baseline_history_completeness": "complete",
            "expected_source_record_count": 1,
            "records": [{"source": "events", "record_id": "event-1", "evidence_ids": []}],
        }],
        source_coverage={"persisted_change_history_bounded": True},
        as_of=NOW,
    )
    assert [item["record_id"] for item in result["important_to_address"]] == ["bounded-change"]
    assert result["important_to_address"][0]["history_completeness"] == "bounded"
    assert "overall change list is incomplete" in result["important_to_address"][0]["reason"]
    clarification_by_id = {
        item["record_id"]: item for item in result["needs_clarification"]
    }
    assert "bounded-change" not in clarification_by_id
    assert clarification_by_id["bounded-history"]["category"] == "needs_clarification"



def test_bounded_change_history_creates_visible_clarification_notice():
    result = build_care_attention_assessment(
        records=[], open_loops=[], uncertainties=[], missing_information=[],
        source_coverage={"persisted_change_history_bounded": True},
        as_of=NOW,
    )
    item = next(
        item for item in result["needs_clarification"]
        if item["record_id"] == "bounded-history"
    )
    assert item["source"] == "changes"
    assert "list may be incomplete" in item["reason"]


def test_unavailable_persisted_change_source_is_not_silently_treated_as_no_change():
    result = build_care_attention_assessment(
        records=[], open_loops=[], uncertainties=[], missing_information=[],
        source_coverage={"persisted_change_source_available": False},
        as_of=NOW,
    )
    item = next(
        item for item in result["needs_clarification"]
        if item["record_id"] == "source-unavailable"
    )
    assert item["source"] == "changes"
    assert "cannot confirm whether there are recorded changes" in item["reason"]



def test_persisted_change_remains_visible_when_source_record_coverage_is_partial():
    from app.services.care_attention_intelligence import _persisted_change_decisions

    important, clarification = _persisted_change_decisions([{
        "id": "change-partial-source", "significance_verdict": "meaningful_candidate",
        "attention_candidate": True, "baseline_coverage_status": "covered",
        "current_coverage_status": "covered", "baseline_is_stable": True,
        "baseline_history_completeness": "complete", "expected_source_record_count": 2,
        "records": [{"source": "events", "record_id": "event-resolved", "evidence_ids": []}],
    }])
    assert len(important) == 1
    assert important[0]["history_completeness"] == "partial_or_unknown"
    assert "does not cancel the recorded need for follow-up" in important[0]["reason"]
    assert clarification == []


def test_repeated_dementia_caregiver_observations_are_reviewed_without_claiming_change():
    result = build_care_attention_assessment(
        records=[
            {
                "id": "care-observation-1", "source": "caregiver_observations",
                "person_id": "person-dementia-1", "observation_category": "dementia.sleep",
                "description": "Woke several times overnight", "epistemic_status": "observed",
                "observed_at": NOW - timedelta(days=3), "recorded_at": NOW - timedelta(days=3),
            },
            {
                "id": "care-observation-2", "source": "caregiver_observations",
                "person_id": "person-dementia-1", "observation_category": "dementia.sleep",
                "description": "Woke several times overnight", "epistemic_status": "reported",
                "observed_at": NOW - timedelta(days=1), "recorded_at": NOW - timedelta(days=1),
            },
        ],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    item = next(
        item for item in result["important_to_address"]
        if item["pattern_type"] == "repeated_caregiver_observation_topic"
    )
    assert item["record_count"] == 2
    assert [record["record_id"] for record in item["records"]] == [
        "care-observation-1", "care-observation-2"
    ]
    assert item["evidence_ids"] == []
    assert "does not establish a change" in item["reason"]


def test_dementia_observation_without_event_time_or_with_uncertainty_is_not_patterned():
    result = build_care_attention_assessment(
        records=[
            {
                "id": "no-event-time", "source": "caregiver_observations",
                "person_id": "person-dementia-2", "observation_category": "dementia.wandering",
                "description": "Went outside", "epistemic_status": "observed",
                "recorded_at": NOW - timedelta(days=1),
            },
            {
                "id": "uncertain-observation", "source": "caregiver_observations",
                "person_id": "person-dementia-2", "observation_category": "dementia.wandering",
                "description": "May have gone outside", "epistemic_status": "uncertain",
                "observed_at": NOW - timedelta(days=2), "recorded_at": NOW - timedelta(days=2),
            },
        ],
        open_loops=[], uncertainties=[], missing_information=[], as_of=NOW,
    )
    assert not any(
        item.get("pattern_type", "").startswith(("repeated_", "multi_attribute_", "explicit_baseline_"))
        for item in result["important_to_address"]
    )
    clarification_ids = {item["record_id"] for item in result["needs_clarification"]}
    assert "uncertain-observation" in clarification_ids
