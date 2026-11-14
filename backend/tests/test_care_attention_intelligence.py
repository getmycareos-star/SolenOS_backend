from datetime import datetime, timedelta, timezone

from app.services.care_attention_intelligence import build_care_attention_assessment

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


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
            {"id": "obs-1", "source": "evidence_observations", "subject_id": "person-1",
             "subject_label": "Mom", "attribute": "usual_activity", "observed_value": "walks daily",
             "occurred_at": NOW - timedelta(days=3), "evidence_ids": ["ev-1"]},
            {"id": "obs-2", "source": "evidence_observations", "subject_id": "person-1",
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
            {"id": f"obs-{i}", "source": "evidence_observations", "subject_id": "person-2",
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
            "id": "current-1", "source": "evidence_observations", "subject_id": "person-3",
            "subject_label": "Mom", "attribute": "usual_orientation",
            "observed_value": "confused about location", "occurred_at": NOW - timedelta(days=1),
            "evidence_ids": ["ev-current"],
        }],
        baseline_items=[{
            "id": "baseline-1", "source": "evidence_observations", "subject_id": "person-3",
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
            "id": "current-2", "source": "evidence_observations", "subject_id": "person-4",
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
