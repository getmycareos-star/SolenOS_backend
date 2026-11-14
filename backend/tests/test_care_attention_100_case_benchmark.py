"""100 independently labelled care-attention classification cases.

Synthetic policy-contract benchmark only. It does not establish clinical
accuracy, calibration, or real-world caregiver outcomes.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.services.care_attention_intelligence import build_care_attention_assessment

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def test_100_independently_labelled_care_attention_cases():
    open_loops, uncertainties, missing_information, records = [], [], [], []
    expected = {}

    def add_loop(case_id, expected_category, **fields):
        open_loops.append({
            "id": case_id,
            "source": "open_loop_records",
            "title": f"Synthetic follow-up {case_id}",
            "recorded_status": "open",
            **fields,
        })
        expected[case_id] = expected_category

    # 15 overdue commitments: only explicit deadline provenance can make them urgent.
    for i in range(15):
        add_loop(
            f"overdue-{i:02d}", "needs_attention_now",
            deadline_source="recorded_commitment",
            due_at=NOW - timedelta(minutes=i + 1),
        )

    # 10 explicitly recorded critical consequences of delay.
    for i in range(10):
        add_loop(
            f"critical-delay-{i:02d}", "needs_attention_now",
            priority="critical",
            priority_reasons=["Critical consequence of delay was explicitly recorded"],
            consequence_of_delay="A specific consequence was recorded by the caregiver.",
        )

    # 15 sourced deadlines within 24 hours are important, not urgent.
    for i in range(15):
        add_loop(
            f"due-soon-{i:02d}", "important_to_address",
            deadline_source="explicit_user",
            due_at=NOW + timedelta(hours=1 + i),
        )

    # 10 ordinary unresolved commitments remain visible as important.
    for i in range(10):
        add_loop(f"open-commitment-{i:02d}", "important_to_address", priority="normal")

    # 10 postponements can wait only with a reason and a future review date.
    for i in range(10):
        add_loop(
            f"documented-deferral-{i:02d}", "can_wait_with_documented_basis",
            recorded_status="deferred",
            deferred_reason=f"Caregiver-recorded reason {i}",
            review_at=NOW + timedelta(days=i + 1),
        )

    # 10 incomplete deferrals must be clarified, not treated as safe.
    for i in range(10):
        add_loop(
            f"unsupported-deferral-{i:02d}", "needs_clarification",
            recorded_status="deferred", deferred_reason=None, review_at=None,
        )

    # 5 unexplained high-priority flags must not be promoted to urgency.
    for i in range(5):
        add_loop(
            f"unexplained-high-{i:02d}", "needs_clarification",
            priority="high", priority_reasons=[],
        )

    # 5 unresolved conflicts and 5 missing-information gaps need clarification.
    for i in range(5):
        uncertainties.append({
            "id": f"conflict-{i:02d}", "source": "conflicts",
            "title": f"Conflicting reports {i}", "status": "unresolved",
            "evidence_ids": [f"evidence-conflict-{i}"],
        })
        expected[f"conflict-{i:02d}"] = "needs_clarification"
        missing_information.append({
            "id": f"missing-{i:02d}", "source": "missing_information",
            "title": f"Missing care detail {i}", "status": "open",
        })
        expected[f"missing-{i:02d}"] = "needs_clarification"

    # 5 ambiguous observations remain visible for clarification, never patterns.
    for i in range(5):
        records.append({
            "id": f"ambiguous-observation-{i:02d}",
            "source": "evidence_observations",
            "epistemic_status": "uncertain",
            "subject_id": f"subject-ambiguous-{i}",
            "subject_label": "Care recipient",
            "attribute": f"ambiguous-domain-{i}",
            "value_text": "possibly different",
            "occurred_at": NOW - timedelta(hours=2),
            "evidence_ids": [f"evidence-ambiguous-{i}"],
        })
        expected[f"ambiguous-observation-{i:02d}"] = "needs_clarification"

    # 5 closed commitments must not reappear, even with an overdue critical flag.
    for i in range(5):
        add_loop(
            f"closed-{i:02d}", "no_action",
            recorded_status="closed", priority="critical",
            priority_reasons=["Critical consequence of delay was explicitly recorded"],
            deadline_source="recorded_commitment", due_at=NOW - timedelta(days=1),
        )

    # 5 pairs copied from one source must not become repeated-change patterns.
    for i in range(5):
        for version, value in enumerate(("usual", "different")):
            records.append({
                "id": f"same-source-{i:02d}-{version}",
                "source": "evidence_observations",
                "epistemic_status": "reported",
                "subject_id": f"subject-copy-{i}",
                "subject_label": "Care recipient",
                "attribute": f"copied-domain-{i}",
                "value_text": value,
                "occurred_at": NOW - timedelta(days=version + 1),
                "evidence_ids": [f"same-evidence-{i}"],
            })
        expected[f"same-source-{i:02d}"] = "no_action"

    result = build_care_attention_assessment(
        records=records,
        open_loops=open_loops,
        uncertainties=uncertainties,
        missing_information=missing_information,
        as_of=NOW,
    )

    actual = {}
    for category in (
        "needs_attention_now",
        "important_to_address",
        "can_wait_with_documented_basis",
        "needs_clarification",
    ):
        for item in result[category]:
            actual[str(item.get("record_id") or item.get("id"))] = category

    outcomes = [
        {"case": case_id, "expected": category, "actual": actual.get(case_id, "no_action")}
        for case_id, category in expected.items()
    ]
    matrix = Counter((item["expected"], item["actual"]) for item in outcomes)
    correct = sum(
        count for (expected_category, actual_category), count in matrix.items()
        if expected_category == actual_category
    )
    accuracy = correct / len(outcomes)

    print(f"CARE_ATTENTION_100_CASE_ACCURACY={accuracy:.3f}")
    print(f"CARE_ATTENTION_100_CASE_MATRIX={dict(matrix)}")
    print(f"CARE_ATTENTION_100_CASE_MISMATCHES={[item for item in outcomes if item['expected'] != item['actual']]}")

    assert len(outcomes) == 100
    assert accuracy == 1.0, f"Care-attention classification mismatch: {matrix}"
    assert result["policy"]["urgent_escalation"].startswith("Only an explicitly sourced overdue deadline")
    assert any(
        "not a safety assessment" in reason
        for item in result["can_wait_with_documented_basis"]
        for reason in item["priority_basis"]
    )
