from datetime import datetime, timezone
from math import isfinite
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.models.care import CareEvent
from app.models.change import Baseline, Change
from app.models.care_continuity import CareAssertion
from app.services.observation_coverage import assess_observation_coverage
from app.services.baseline import (
    build_baseline,
    compare_to_baseline,
    get_longitudinal_event_counts,
    _ensure_utc,
)
from app.core.change_enums import ChangeType, ChangeDirection, BaselineConfidence, SignificanceVerdict


def detect_event_changes(
    db: Session,
    person_id: str,
    event_type: str,
    subject_id: Optional[str],
    reference_time: Optional[datetime] = None,
    min_persistence_days: int = 14,
) -> List[Change]:
    now = _ensure_utc(reference_time) or datetime.now(timezone.utc)
    baseline = build_baseline(db, person_id, event_type, subject_id, now)
    if not baseline:
        return []
    counts = get_longitudinal_event_counts(db, person_id, event_type, subject_id, now)
    current_count = counts["current_window_count"]
    comparison = compare_to_baseline(baseline, current_count, 42)
    current_window_start = counts["current_window_start"]
    baseline_coverage = assess_observation_coverage(
        db, person_id, event_type, baseline.window_start, baseline.window_end
    )
    current_coverage = assess_observation_coverage(
        db, person_id, event_type, current_window_start, now
    )
    comparison["baseline_coverage_status"] = baseline_coverage["coverage_status"]
    comparison["current_coverage_status"] = current_coverage["coverage_status"]
    comparison["baseline_coverage_fraction"] = baseline_coverage["coverage_fraction"]
    comparison["current_coverage_fraction"] = current_coverage["coverage_fraction"]
    comparison["baseline_coverage_has_records"] = bool(baseline_coverage["records"])
    comparison["current_coverage_has_records"] = bool(current_coverage["records"])
    # A rate comparison is defensible only when both windows are explicitly
    # covered. Missing coverage is unknown, not a measured zero and not proof
    # that reporting opportunities were equivalent.
    explicit_incomplete_coverage = any(
        has_records and status != "covered"
        for has_records, status in (
            (comparison["baseline_coverage_has_records"], comparison["baseline_coverage_status"]),
            (comparison["current_coverage_has_records"], comparison["current_coverage_status"]),
        )
    )
    changes = []
    # An event can be newly observed in the records without being a new
    # occurrence in real life. Call it an emergence only if the full baseline
    # interval was explicitly observed and no matching event was recorded there.
    if (
        baseline.event_count == 0
        and current_count > 0
        and comparison.get("baseline_coverage_status") == "covered"
    ):
        change = _create_change(
            db=db,
            person_id=person_id,
            subject_type=event_type,
            subject_id=subject_id or "",
            change_type=ChangeType.EMERGENCE,
            change_direction=ChangeDirection.INCREASING,
            previous_state="No comparable prior events were recorded in the baseline window",
            current_state=f"{current_count} event(s) recorded in the current observation window",
            previous_count=0,
            current_count=current_count,
            baseline_id=baseline.id,
            baseline=baseline,
            comparison=comparison,
            events=counts["recent_events"],
            now=now,
        )
        changes.append(change)
    elif (
        comparison.get("baseline_coverage_status") == "covered"
        and comparison.get("current_coverage_status") == "covered"
        and baseline.event_count > 0
        and comparison.get("frequency_delta") is not None
        and comparison["frequency_delta"] > 0
    ):
        # A higher normalized rate is a change claim only when both windows have
        # explicit complete observation coverage. Otherwise fewer/more records
        # may reflect reporting opportunity rather than an occurrence-rate change.
        # Significance assessment has no universal percentage threshold.
        change = _create_change(
            db=db,
            person_id=person_id,
            subject_type=event_type,
            subject_id=subject_id or "",
            change_type=ChangeType.INCREASE,
            change_direction=ChangeDirection.INCREASING,
            previous_state=(
                f"{comparison['baseline_frequency_per_period']:.3f} events per 30 days "
                f"over {comparison['baseline_window_days']} calendar days"
            ),
            current_state=(
                f"{current_count} events over {comparison['current_window_days']} calendar days "
                f"({(current_count / (comparison['current_window_days'] / 30.0)):.3f} per 30 days)"
            ),
            previous_count=baseline.event_count,
            current_count=current_count,
            baseline_id=baseline.id,
            baseline=baseline,
            comparison=comparison,
            events=counts["recent_events"],
            now=now,
        )
        changes.append(change)
    elif (
        baseline.event_count > 0
        and comparison.get("frequency_delta") is not None
        and comparison["frequency_delta"] < 0
        and comparison.get("baseline_coverage_status") == "covered"
        and comparison.get("current_coverage_status") == "covered"
    ):
        # A lower recorded rate is a decrease candidate only when both windows
        # have explicit complete observation coverage. Without this proof, fewer
        # records may reflect less observation rather than an actual decrease.
        current_frequency = current_count / (comparison["current_window_days"] / 30.0)
        change = _create_change(
            db=db,
            person_id=person_id,
            subject_type=event_type,
            subject_id=subject_id or "",
            change_type=ChangeType.DECREASE,
            change_direction=ChangeDirection.DECREASING,
            previous_state=(
                f"{comparison['baseline_frequency_per_period']:.3f} events per 30 days "
                f"over {comparison['baseline_window_days']} calendar days"
            ),
            current_state=(
                f"{current_count} events over {comparison['current_window_days']} calendar days "
                f"({current_frequency:.3f} per 30 days)"
            ),
            previous_count=baseline.event_count,
            current_count=current_count,
            baseline_id=baseline.id,
            baseline=baseline,
            comparison=comparison,
            events=counts["recent_events"],
            now=now,
        )
        changes.append(change)
    # An empty current window is not evidence that a previously observed event
    # stopped. Without explicit observation-coverage data, do not infer cessation.
    if not changes and current_count >= 2:
        recent_events = counts["recent_events"]
        if len(recent_events) >= 2:
            first_event = min(recent_events, key=lambda e: e.occurred_at)
            last_event = max(recent_events, key=lambda e: e.occurred_at)
            span_days = (last_event.occurred_at - first_event.occurred_at).days
            if span_days <= 42:
                change = _create_change(
                    db=db,
                    person_id=person_id,
                    subject_type=event_type,
                    subject_id=subject_id or "",
                    change_type=ChangeType.RECURRENCE,
                    change_direction=ChangeDirection.INCREASING,
                    previous_state=f"{baseline.event_count} events in baseline window",
                    current_state=f"{current_count} events in current window",
                    previous_count=baseline.event_count,
                    current_count=current_count,
                    baseline_id=baseline.id,
                    baseline=baseline,
                    comparison=comparison,
                    events=recent_events,
                    now=now,
                )
                changes.append(change)
    return changes


def _create_change(
    db: Session,
    person_id: str,
    subject_type: str,
    subject_id: str,
    change_type: ChangeType,
                 change_direction: ChangeDirection,
                 previous_state: str,
                 current_state: str,
                 previous_count: Optional[int],
                 current_count: int,
                 baseline_id: Optional[str],
                 baseline: Optional[Baseline],
                 comparison: Dict[str, Any],
                 events: List[CareEvent],
                 now: datetime,
) -> Change:
    if change_type == ChangeType.EMERGENCE:
        explanation = (
            f"{subject_type} was first observed in the available records. "
            f"{current_state}. {previous_state}. "
            "This does not establish that the event was absent before the baseline window."
        )
    else:
        explanation = (
            f"{subject_type} was assessed against its recorded baseline. "
            f"Previous state: {previous_state}. Current state: {current_state}. "
            f"Current count: {current_count}. "
            f"Baseline count: {comparison.get('baseline_event_count', 'unknown')}. "
            "Rates describe recorded events only and do not establish causation. "
            f"Baseline observation coverage: {comparison.get('baseline_coverage_status', 'unknown')}; "
            f"current observation coverage: {comparison.get('current_coverage_status', 'unknown')}. "
            "Unknown or incomplete coverage limits interpretation of the true occurrence rate."
        )
    relative_change = comparison.get("relative_change_percent")
    if isinstance(relative_change, (int, float)) and isfinite(relative_change):
        explanation += f" Relative change: {relative_change:.1f}%."
    elif relative_change is not None:
        explanation += (
            " Relative change is undefined because the baseline rate was zero; "
            "absolute recorded event counts are shown instead."
        )
    # Source observations and the baseline are separate provenance objects.
    # Keep only actual care-event IDs in evidence_ids; baseline_id already has its
    # own foreign-key field. This also makes repeated assessments idempotent.
    evidence_ids = sorted({e.id for e in events if e.id})
    normalized_subject_id = subject_id or ""
    existing = (
        db.query(Change)
        .filter(
            Change.person_id == person_id,
            Change.subject_type == subject_type,
            Change.subject_id == normalized_subject_id,
            Change.change_type == change_type,
            Change.is_active.is_(True),
        )
        .order_by(Change.created_at.desc())
        .all()
    )
    evidence_signature = set(evidence_ids)

    def same_assessment_provenance(candidate: Change) -> bool:
        # Coverage is part of the assessment, not merely explanatory metadata.
        # A later coverage record can change whether the same event counts support
        # a comparable rate. Do not return a stale assessment just because its
        # event IDs and raw count have not changed.
        comparisons = (
            ("baseline_coverage_status", comparison.get("baseline_coverage_status")),
            ("current_coverage_status", comparison.get("current_coverage_status")),
            ("baseline_coverage_fraction", comparison.get("baseline_coverage_fraction")),
            ("current_coverage_fraction", comparison.get("current_coverage_fraction")),
        )
        for field, expected in comparisons:
            actual = getattr(candidate, field, None)
            if isinstance(actual, (float, int)) and isinstance(expected, (float, int)):
                # Window endpoints move with reference_time. Ignore tiny fraction
                # drift caused only by request-time movement; a status transition
                # (unknown -> covered) or meaningful coverage change still rebuilds.
                if abs(float(actual) - float(expected)) > 1e-6:
                    return False
            elif actual != expected:
                return False
        # A newly derived baseline ID alone is not a semantic change. Compare the
        # rendered baseline/current states instead, which include the actual
        # measurements and denominators that explain the assessment.
        return (
            candidate.previous_state == previous_state
            and candidate.current_state == current_state
        )

    for candidate in existing:
        if (
            candidate.current_count == current_count
            and set(candidate.evidence_ids or []) == evidence_signature
            and same_assessment_provenance(candidate)
        ):
            # Identical evidence, measurements, and assessment provenance remain idempotent.
            return candidate
        if (
            not evidence_signature
            and candidate.created_at is not None
            and same_assessment_provenance(candidate)
        ):
            created_at = _ensure_utc(candidate.created_at)
            if created_at is not None and (now - created_at).days < 1:
                return candidate

    magnitude = comparison.get("relative_change_percent")
    if magnitude is not None and magnitude == float("inf"):
        # An unbounded relative change is not a measured finite magnitude.
        magnitude = None
    change = Change(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        change_type=change_type,
        change_direction=change_direction,
        previous_state=previous_state,
        current_state=current_state,
        magnitude=magnitude,
        previous_count=previous_count,
        current_count=current_count,
        baseline_id=baseline_id,
        comparison_window_start=comparison.get("current_window_start"),
        comparison_window_end=comparison.get("current_window_end"),
        confidence=None,
        baseline_coverage_status=comparison.get("baseline_coverage_status"),
        current_coverage_status=comparison.get("current_coverage_status"),
        baseline_coverage_fraction=comparison.get("baseline_coverage_fraction"),
        current_coverage_fraction=comparison.get("current_coverage_fraction"),
        evidence_ids=evidence_ids,
        explanation=explanation,
        time_provenance=f"detected_at:{now.isoformat()}",
    )
    db.add(change)
    db.flush()

    # When new evidence changes an active assessment, retain the previous record
    # as history and explicitly link it to the replacement. Never leave two active
    # assessments for the same person/domain/subject/change type.
    for candidate in existing:
        candidate.is_active = False
        candidate.superseded_by_change_id = change.id
    if existing:
        db.flush()

    from app.services.significance import assess_change
    assess_change(db, change, baseline, comparison, events, now)
    db.flush()
    return change


def _calculate_change_confidence(comparison: Dict[str, Any], event_count: int) -> Optional[float]:
    # Heuristic weights are not a calibrated probability; keep confidence unknown.
    return None

def detect_assertion_changes(
    db: Session,
    person_id: str,
    reference_time: Optional[datetime] = None,
) -> List[Change]:
    """Detect transitions between source-attributed, time-bound structured assertions.

    This deliberately compares persisted assertion values, not free-text keywords.
    It does not infer direction, severity, cause, or clinical significance from
    arbitrary categorical strings.
    """
    now = _ensure_utc(reference_time) or datetime.now(timezone.utc)
    assertions = (
        db.query(CareAssertion)
        .filter(
            CareAssertion.person_id == person_id,
            CareAssertion.assertion_kind == "time_bound_observation",
            CareAssertion.observed_at.is_not(None),
            CareAssertion.assertion_status.in_(("reported", "review_required", "accepted")),
        )
        .order_by(CareAssertion.observed_at.asc(), CareAssertion.recorded_at.asc(), CareAssertion.id.asc())
        .all()
    )

    grouped: Dict[tuple[str, str, str], List[CareAssertion]] = {}
    seen_source_events: set[tuple[str, str, str, str, str]] = set()
    for assertion in assertions:
        observed_at = _ensure_utc(assertion.observed_at)
        if observed_at is None or observed_at > now:
            continue
        if assertion.source_event_id:
            dedupe_key = (
                assertion.source_event_id,
                assertion.subject_type,
                assertion.subject_id,
                assertion.field,
                assertion.asserted_value.strip(),
            )
            if dedupe_key in seen_source_events:
                continue
            seen_source_events.add(dedupe_key)
        key = (assertion.subject_type, assertion.subject_id, assertion.field)
        grouped.setdefault(key, []).append(assertion)

    results: List[Change] = []
    for (subject_type, subject_id, field), items in grouped.items():
        # Multiple incompatible values at the same observed instant are not ordered
        # evidence of change. Leave that case to the contradiction workflow.
        by_time: Dict[datetime, List[CareAssertion]] = {}
        for item in items:
            observed_at = _ensure_utc(item.observed_at)
            if observed_at is not None:
                by_time.setdefault(observed_at, []).append(item)
        ordered: List[CareAssertion] = []
        for observed_at in sorted(by_time):
            at_time = by_time[observed_at]
            distinct_values = {item.asserted_value.strip() for item in at_time}
            if len(distinct_values) == 1:
                ordered.append(at_time[-1])
        if len(ordered) < 2:
            continue

        latest_value = ordered[-1].asserted_value.strip()
        run_start = len(ordered) - 1
        while run_start > 0 and ordered[run_start - 1].asserted_value.strip() == latest_value:
            run_start -= 1
        if run_start == 0:
            continue
        prior = ordered[run_start - 1]
        current_run = ordered[run_start:]
        if prior.asserted_value.strip() == latest_value:
            continue

        # Keep the prior state and every distinct persisted observation in the
        # latest contiguous state run as explicit provenance for this transition.
        evidence_ids = list(dict.fromkeys([prior.id] + [item.id for item in current_run]))
        source_subject_type = f"assertion:{subject_type}.{field}"
        existing = (
            db.query(Change)
            .filter(
                Change.person_id == person_id,
                Change.subject_type == source_subject_type,
                Change.subject_id == subject_id,
                Change.change_type == ChangeType.TRANSITION,
                Change.is_active.is_(True),
            )
            .with_for_update()
            .order_by(Change.created_at.desc())
            .all()
        )
        matching = next(
            (candidate for candidate in existing if set(candidate.evidence_ids or []) == set(evidence_ids)),
            None,
        )
        if matching is not None:
            results.append(matching)
            continue

        prior_at = _ensure_utc(prior.observed_at)
        current_at = _ensure_utc(current_run[0].observed_at)
        current_last_at = _ensure_utc(current_run[-1].observed_at)
        if prior_at is None or current_at is None or current_last_at is None:
            continue
        # Repeated reports bound the observation span; they do not prove continuous persistence between reports.
        change = Change(
            person_id=person_id,
            subject_type=source_subject_type,
            subject_id=subject_id,
            change_type=ChangeType.TRANSITION,
            change_direction=ChangeDirection.UNKNOWN,
            previous_state=prior.asserted_value,
            current_state=latest_value,
            magnitude=None,
            previous_count=None,
            current_count=None,
            baseline_id=None,
            comparison_window_start=prior_at,
            comparison_window_end=current_last_at,
            persistence_days=None,
            is_persistent=None,
            confidence=None,
            evidence_ids=evidence_ids,
            explanation=(
                f"Structured field '{field}' for {subject_type} was reported as "
                f"'{prior.asserted_value}' at {prior_at.isoformat()} and later as "
                f"'{latest_value}' at {current_at.isoformat()}. The latest value appears in "
                f"{len(current_run)} source observation(s) through {current_last_at.isoformat()}; "
                "the interval between observations is not treated as continuous persistence. "
                "This records a "
                "difference between source-attributed observations; it does not "
                "establish cause, direction of clinical change, or severity."
            ),
            time_provenance="observed_at_from_source_assertions",
            significance_verdict=SignificanceVerdict.INSUFFICIENT_EVIDENCE,
            attention_candidate=False,
            follow_up_candidate=False,
            confirmation_status="detected",
            is_active=True,
        )
        db.add(change)
        db.flush()
        for candidate in existing:
            candidate.is_active = False
            candidate.superseded_by_change_id = change.id
        if existing:
            db.flush()
        results.append(change)
    return results

