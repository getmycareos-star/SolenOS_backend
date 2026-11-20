"""Evidence-grounded care attention assessment.

This module ranks only explicit commitments and source-backed record patterns.
It does not diagnose, infer causation, or turn missing information into reassurance.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

CLOSED_STATUSES = {"resolved", "closed", "completed", "answered", "reconciled", "withdrawn", "retracted", "superseded", "cancelled", "dismissed", "not_applicable", "known"}
OPEN_STATUSES = {"open", "active", "pending", "unresolved", "needs_review", "not_started", "follow_up_pending", "waiting", "deferred", "blocked", "review_abandoned", "pending_closure_verification"}
CRITICAL_DELAY_REASON = "Critical consequence of delay was explicitly recorded"
CARE_ATTENTION_POLICY_VERSION = "care-attention.v2"
MAX_PATTERN_WINDOW_DAYS = 14


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _evidence_ids(item: dict[str, Any]) -> list[str]:
    raw = item.get("evidence_ids")
    return sorted({str(value) for value in raw if value}) if isinstance(raw, list) else []


def _pattern_source_ids(item: dict[str, Any]) -> list[str]:
    """Use linked evidence where present; otherwise identify a caregiver observation by its own record ID."""
    linked = _evidence_ids(item)
    if linked:
        return linked
    if item.get("source") == "caregiver_observations" and item.get("id"):
        return [str(item["id"])]
    return []


def _record_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(item.get("source") or "unknown"),
        "record_id": str(item.get("id") or ""),
        "title": str(item.get("title") or item.get("observation_category") or item.get("attribute") or item.get("source") or "Care record")[:240],
        "occurred_at": item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"),
        "recorded_at": item.get("recorded_at") or item.get("created_at") or item.get("uploaded_at"),
        "evidence_ids": _evidence_ids(item),
    }


def _open_loop_decisions(open_loops: list[dict[str, Any]], now: datetime):
    immediate, important, deferred, clarification = [], [], [], []
    for loop in open_loops:
        status = _text(loop.get("recorded_status") or loop.get("status"))
        if status in CLOSED_STATUSES:
            continue
        item = {
            **_record_ref(loop), "status": status or "unknown", "priority_basis": [],
            "owner": loop.get("owner"), "due_at": loop.get("due_at"),
            "review_at": loop.get("review_at"), "deferred_reason": loop.get("deferred_reason"),
            "consequence_of_delay": loop.get("consequence_of_delay"),
        }
        if status not in OPEN_STATUSES:
            item["category"] = "needs_clarification"
            item["priority_basis"].append(
                "The saved follow-up status is missing or unrecognized; SolenOS cannot safely treat it as open or resolved."
            )
            clarification.append(item)
            continue
        due = _datetime(loop.get("due_at") or loop.get("deadline_at"))
        explicit_deadline = _text(loop.get("deadline_source")) in {"explicit_user", "recorded_commitment", "system_policy"}
        overdue = bool(due and explicit_deadline and due <= now)
        deadline_soon = bool(due and explicit_deadline and now < due <= now + timedelta(hours=24))
        reasons = loop.get("priority_reasons") if isinstance(loop.get("priority_reasons"), list) else []
        normalized_reasons = {_text(reason) for reason in reasons}
        critical_delay = _text(loop.get("priority")) in {"high", "critical"} and _text(CRITICAL_DELAY_REASON) in normalized_reasons
        if overdue:
            item["category"] = "needs_attention_now"
            item["priority_basis"].append("An explicitly sourced deadline has passed.")
            immediate.append(item)
        elif critical_delay:
            item["category"] = "needs_attention_now"
            item["priority_basis"].append(CRITICAL_DELAY_REASON)
            immediate.append(item)
        elif deadline_soon:
            item["category"] = "important_to_address"
            item["priority_basis"].append("An explicitly sourced follow-up deadline is within 24 hours.")
            important.append(item)
        elif status == "deferred":
            review_at = _datetime(loop.get("review_at"))
            if loop.get("deferred_reason") and review_at and review_at > now:
                item["category"] = "can_wait_with_documented_basis"
                item["priority_basis"].append("A postponement reason and future review date are recorded; this is not a safety assessment.")
                deferred.append(item)
            else:
                item["category"] = "needs_clarification"
                item["priority_basis"].append("The item was postponed without a current, future review basis.")
                clarification.append(item)
        elif normalized_reasons.intersection({
            _text("Competing claims remain unresolved"),
            _text("Required information is still missing"),
        }) or "persisted deadline has passed" in normalized_reasons:
            item["category"] = "needs_clarification"
            item["priority_basis"].extend(str(reason) for reason in reasons)
            clarification.append(item)
        elif _text(loop.get("priority")) in {"high", "critical"} and not reasons:
            item["category"] = "needs_clarification"
            item["priority_basis"].append("The saved priority is high, but its explicit escalation basis is missing.")
            clarification.append(item)
        else:
            item["category"] = "important_to_address"
            item["priority_basis"].extend(
                str(reason) for reason in reasons
                if _text(reason) in {
                    _text("A mandatory prerequisite is unresolved"),
                    _text("Inactive item requires human review; this is not closure"),
                    _text("Explicit follow-up deadline is within 24 hours"),
                }
            )
            if not item["priority_basis"]:
                item["priority_basis"].append("An unresolved care commitment is recorded.")
            important.append(item)
    return immediate, important, deferred, clarification

def _observation_value(item: dict[str, Any]) -> str:
    value = ""
    for key in ("observed_value", "normalized_value", "value_text", "fact_value", "asserted_value", "current_state", "description", "observation_content", "title"):
        value = _text(item.get(key))
        if value:
            break
    if not value:
        return ""
    units = _text(item.get("units"))
    return f"{value} [{units}]" if units else value


def _subject_key(item: dict[str, Any]) -> str:
    return _text(item.get("subject_id") or item.get("person_id")) or _text(item.get("subject_label") or item.get("subject_type"))


def _pattern_reviews(records: list[dict[str, Any]], baseline_items: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    # A correction points from the newer observation to the older observation.
    # Exclude the superseded row, not the correcting row.
    superseded_ids = {
        str(item.get("supersedes_observation_id"))
        for item in records
        if item.get("source") == "evidence_observations" and item.get("supersedes_observation_id")
    } | {
        str(item.get("id"))
        for item in records
        if item.get("source") == "facts" and item.get("superseded_by_fact_id") and item.get("id")
    }
    eligible = []
    for item in records:
        source = item.get("source")
        if source not in {"evidence_observations", "facts", "caregiver_observations"}:
            continue
        if item.get("negated") is True:
            continue
        epistemic = _text(item.get("epistemic_status") or item.get("epistemic_state"))
        # Only directly reported, observed, or verified structured records can
        # form a change pattern. Inference, uncertainty, and conflict are not
        # promoted into apparent facts by aggregation.
        if epistemic not in {"reported", "observed", "verified"}:
            continue
        if str(item.get("id") or "") in superseded_ids:
            continue
        if _text(item.get("assertion_status")) in {"rejected", "superseded", "retracted"}:
            continue
        if item.get("hedge_marker") or item.get("ambiguity_note"):
            continue
        if _text(item.get("status") or item.get("lifecycle_status")) in CLOSED_STATUSES:
            continue
        attribute = _text(item.get("attribute") or item.get("observation_category") or item.get("event_type") or item.get("fact_type"))
        subject = _subject_key(item)
        # Do not substitute record time for event time: an observation without
        # an explicit observed_at cannot be placed in a temporal pattern.
        occurred = _datetime(item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"))
        if not attribute or not subject or not occurred or occurred > now or occurred < now - timedelta(days=MAX_PATTERN_WINDOW_DAYS) or not _pattern_source_ids(item):
            continue
        eligible.append((subject, attribute, occurred, item))

    by_subject, by_subject_attribute = defaultdict(list), defaultdict(list)
    for subject, attribute, occurred, item in eligible:
        by_subject[subject].append((attribute, occurred, item))
        by_subject_attribute[(subject, attribute)].append((occurred, item))

    reviews, emitted = [], set()
    baseline_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    baseline_ids = set()
    for baseline in baseline_items:
        # A baseline must be explicitly marked by the persisted LCR projection
        # and itself be a source-attributed, non-inferred record.
        if baseline.get("baseline_basis") != "explicit_persisted_baseline_marker":
            continue
        if _text(baseline.get("epistemic_status") or baseline.get("epistemic_state")) not in {"reported", "observed", "verified"}:
            continue
        if baseline.get("negated") is True or baseline.get("hedge_marker") or baseline.get("ambiguity_note"):
            continue
        subject = _subject_key(baseline)
        attribute = _text(baseline.get("attribute") or baseline.get("event_type") or baseline.get("fact_type"))
        value = _observation_value(baseline)
        if subject and attribute and value and _evidence_ids(baseline):
            baseline_by_key[(subject, attribute)].append(baseline)
            if baseline.get("id"):
                baseline_ids.add(str(baseline["id"]))

    for (subject, attribute), entries in by_subject_attribute.items():
        baseline_rows = baseline_by_key.get((subject, attribute), [])
        current_entries = [(when, item) for when, item in entries if str(item.get("id") or "") not in baseline_ids]
        baseline_values = {_observation_value(item) for item in baseline_rows if _observation_value(item)}
        baseline_evidence_ids = {
            evidence_id for baseline in baseline_rows for evidence_id in _evidence_ids(baseline)
        }
        changed_entries = [
            (when, item) for when, item in current_entries
            if _observation_value(item) and baseline_values
            and _observation_value(item) not in baseline_values
            # Repeated projections of one source note do not establish a new change.
            and bool(set(_evidence_ids(item)) - baseline_evidence_ids)
        ]
        if changed_entries:
            current_refs = [_record_ref(item) for _, item in changed_entries]
            baseline_refs = [_record_ref(item) for item in baseline_rows]
            refs = baseline_refs + current_refs
            reviews.append({
                "id": f"baseline-change:{subject}:{attribute}",
                "category": "important_to_address",
                "title": str(changed_entries[-1][1].get("subject_label") or attribute)[:240],
                "reason": "A recent source-linked observation differs from an explicitly recorded personal baseline. Review the change in context; this does not establish clinical severity or cause.",
                "pattern_type": "explicit_baseline_deviation",
                "record_count": len(current_refs),
                "records": refs,
                "evidence_ids": sorted({e for ref in refs for e in ref["evidence_ids"]}),
                "first_observed_at": current_refs[0]["occurred_at"],
                "last_observed_at": current_refs[-1]["occurred_at"],
            })
            emitted.add((subject, attribute))

    for (subject, attribute), entries in by_subject_attribute.items():
        distinct = {str(item.get("id")): item for _, item in entries if item.get("id")}
        distinct_evidence_ids = {source_id for _, item in entries for source_id in _pattern_source_ids(item)}
        values = {_observation_value(item) for _, item in entries if _observation_value(item)}
        if (subject, attribute) in emitted or len(distinct) < 2 or len(distinct_evidence_ids) < 2:
            continue
        if not values or (len(values) < 2 and not all(item.get("source") == "caregiver_observations" for _, item in entries)):
            continue
        entries.sort(key=lambda pair: pair[0])
        refs = [_record_ref(item) for _, item in entries]
        caregiver_topic_only = all(item.get("source") == "caregiver_observations" for _, item in entries)
        pattern_type = "repeated_caregiver_observation_topic" if caregiver_topic_only and len(values) == 1 else "repeated_attribute_with_changed_values"
        reason = (
            "More than one caregiver observation was recorded for this topic on separate occasions within 14 days. Review the notes together; repetition alone does not establish a change, cause, or clinical significance."
            if pattern_type == "repeated_caregiver_observation_topic"
            else "Different values for the same recorded observation appear in distinct linked source records within 14 days. Review the sequence against the person's documented baseline; this does not establish independent corroboration, cause, or clinical severity."
        )
        reviews.append({
            "id": f"repeated:{subject}:{attribute}", "category": "important_to_address",
            "title": str(entries[-1][1].get("subject_label") or attribute)[:240],
            "reason": reason,
            "pattern_type": pattern_type, "record_count": len(distinct),
            "records": refs, "evidence_ids": sorted({e for ref in refs for e in ref["evidence_ids"]}),
            "first_observed_at": refs[0]["occurred_at"], "last_observed_at": refs[-1]["occurred_at"],
        })
        emitted.add((subject, attribute))

    for subject, entries in by_subject.items():
        attributes = {attribute for attribute, _, _ in entries}
        days = {occurred.date() for _, occurred, _ in entries}
        distinct_evidence_ids = {source_id for _, _, item in entries for source_id in _pattern_source_ids(item)}
        if len(entries) < 3 or len(attributes) < 3 or len(days) < 2 or len(distinct_evidence_ids) < 2 or any((subject, a) in emitted for a in attributes):
            continue
        refs = [_record_ref(item) for _, _, item in sorted(entries, key=lambda row: row[1])]
        reviews.append({
            "id": f"cluster:{subject}", "category": "important_to_address",
            "title": "Several different care observations recorded close together",
            "reason": "Several distinct observations were recorded across at least two days within 14 days. Review them together; their relationship and clinical significance are not established.",
            "pattern_type": "multi_attribute_observation_cluster", "record_count": len(refs),
            "records": refs, "evidence_ids": sorted({e for ref in refs for e in ref["evidence_ids"]}),
            "first_observed_at": refs[0]["occurred_at"], "last_observed_at": refs[-1]["occurred_at"],
        })
    return reviews


def _persisted_change_decisions(changes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    important, clarification = [], []
    for change in changes:
        verdict = _text(change.get("significance_verdict"))
        if verdict not in {"meaningful_candidate", "insufficient_evidence"}:
            continue
        if _text(change.get("confirmation_status")) in {"rejected", "dismissed", "cancelled", "withdrawn", "superseded"}:
            continue
        attention_candidate = change.get("attention_candidate") is True
        follow_up_candidate = change.get("follow_up_candidate") is True
        if not (attention_candidate or follow_up_candidate):
            continue
        records = change.get("records") if isinstance(change.get("records"), list) else []
        evidence_ids = sorted({
            str(evidence_id)
            for record in records if isinstance(record, dict)
            for evidence_id in (record.get("evidence_ids") or []) if evidence_id
        })
        source_record_ids = sorted({
            str(record.get("record_id")) for record in records
            if isinstance(record, dict) and record.get("record_id")
        })
        coverage_complete = (
            change.get("baseline_coverage_status") == "covered"
            and change.get("current_coverage_status") == "covered"
            and change.get("baseline_is_stable") is True
            and change.get("baseline_history_completeness") == "complete"
        )
        expected_source_record_count = int(change.get("expected_source_record_count") or 0)
        source_complete = bool(source_record_ids) and expected_source_record_count > 0 and len(source_record_ids) == expected_source_record_count
        history_complete = coverage_complete and source_complete
        item = {
            "id": str(change.get("id") or ""),
            "source": "persisted_change",
            "record_id": str(change.get("id") or ""),
            "title": str(change.get("title") or "Recorded care change")[:240],
            "change_type": change.get("change_type"),
            "change_direction": change.get("change_direction"),
            "previous_state": change.get("previous_state"),
            "current_state": change.get("current_state"),
            "category": "important_to_address",
            "care_significance": "attention_candidate" if attention_candidate else "follow_up_candidate",
            "significance_verdict": verdict,
            "significance_assessment_id": change.get("significance_assessment_id"),
            "significance_assessment_status": _text(change.get("significance_assessment_status")) or "not_recorded",
            "evidence_status": _text(change.get("evidence_status")) or "unknown",
            "evidence_confidence": change.get("evidence_confidence"),
            "significance_confidence": change.get("significance_confidence"),
            "confidence_interpretation": "uncalibrated_score_not_probability",
            "assessment_dimensions": change.get("assessment_dimensions") if isinstance(change.get("assessment_dimensions"), list) else [],
            "attention_candidate": attention_candidate,
            "follow_up_candidate": follow_up_candidate,
            "action_status": _text(change.get("action_status")) or "not_recorded",
            "linked_follow_up_outcome_status": _text(change.get("linked_follow_up_outcome_status")) or "not_recorded",
            "linked_follow_up_id": change.get("linked_follow_up_id"),
            "history_completeness": "complete" if history_complete else "partial_or_unknown",
            "reason": str(change.get("explanation") or "A saved change assessment needs review.")[:2400],
            "evidence_ids": evidence_ids,
            "source_record_ids": source_record_ids,
            "records": records,
            "record_count": len(records),
            "baseline_coverage_status": change.get("baseline_coverage_status"),
            "current_coverage_status": change.get("current_coverage_status"),
        }
        assessment_status = item["significance_assessment_status"]
        if assessment_status in {"schema_unavailable", "missing_or_inactive", "target_mismatch"}:
            item["reason"] += " The saved review details for this possible change are not available right now. The original care information remains in the record."
        elif assessment_status == "not_recorded":
            item["reason"] += " This possible change has not had a separate importance review yet. It is based on the saved care information."
        if item["action_status"] == "closed" and item["linked_follow_up_outcome_status"] in {"outcome_unknown", "unknown", "not_recorded"}:
            item["reason"] += " The follow-up was closed, but no result was recorded."
        if attention_candidate and not history_complete:
            # A candidate without a complete baseline and source set cannot be
            # elevated as an important change. Keep it visible for clarification.
            item["category"] = "needs_clarification"
            item["care_significance"] = "unverified_attention_candidate"
            item["reason"] += (
                " The baseline or source history is incomplete. Confirm the missing care history "
                "before treating this as an important change."
            )
            clarification.append(item)
            continue
        if not history_complete:
            item["reason"] += (
                " The available history is incomplete, so the size and timing of the change remain uncertain. "
                "The recorded follow-up remains visible."
            )
        else:
            item["reason"] += " This is a review prompt based on the recorded history, not a diagnosis or a causal conclusion."
        important.append(item)
    return important, clarification

def build_care_attention_assessment(*, records: list[dict[str, Any]], open_loops: list[dict[str, Any]],
    uncertainties: list[dict[str, Any]], missing_information: list[dict[str, Any]],
    baseline_items: list[dict[str, Any]] | None = None, persisted_changes: list[dict[str, Any]] | None = None, as_of: Any = None, source_coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _datetime(as_of) or datetime.now(timezone.utc)
    immediate, important, deferred, loop_clarifications = _open_loop_decisions(open_loops, now)
    important.extend(_pattern_reviews(records, baseline_items or [], now))
    persisted_important, persisted_clarification = _persisted_change_decisions(persisted_changes or [])
    coverage = source_coverage or {}
    if coverage.get("persisted_change_source_available") is False:
        persisted_clarification.append({
            "source": "changes",
            "record_id": "source-unavailable",
            "title": "Care-change history could not be checked",
            "category": "needs_clarification",
            "reason": (
                "The saved change-history source is unavailable or its required fields are incomplete. "
                "SolenOS cannot confirm whether there are recorded changes until this source is restored."
            ),
        })
    if coverage.get("persisted_change_history_bounded"):
        # Keep individually persisted care-significance candidates visible.
        # The separate notice prevents this bounded list from being mistaken
        # for a complete review of every active change.
        for item in persisted_important:
            item["history_completeness"] = "bounded"
            item["reason"] = (
                str(item.get("reason") or "")
                + " The overall change list is incomplete; other saved changes may not be shown."
            ).strip()
        persisted_clarification.append({
            "source": "changes",
            "record_id": "bounded-history",
            "title": "Some saved care changes need a wider review",
            "category": "needs_clarification",
            "reason": (
                "More active care-change records exist than this review could examine. "
                "The list may be incomplete; review the full change history before relying on its priorities."
            ),
        })
    important.extend(persisted_important)
    clarification = [*loop_clarifications, *persisted_clarification]
    for item in [*uncertainties, *missing_information]:
        if _text(item.get("status") or item.get("lifecycle_status")) in CLOSED_STATUSES:
            continue
        clarification.append({
            **_record_ref(item), "category": "needs_clarification",
            "reason": "A saved uncertainty, conflict, or information gap remains unresolved. The missing or competing information may change what should happen next.",
        })
    # Keep ambiguous or explicitly uncertain observations visible, but do not
    # let them contribute to a change pattern or escalation.
    for item in records:
        if item.get("source") not in {"evidence_observations", "caregiver_observations"}:
            continue
        epistemic = _text(item.get("epistemic_status") or item.get("epistemic_state"))
        if epistemic not in {"uncertain", "conflicting", "missing", "inferred"} and not item.get("hedge_marker") and not item.get("ambiguity_note"):
            continue
        if _text(item.get("status") or item.get("lifecycle_status")) in CLOSED_STATUSES:
            continue
        clarification.append({
            **_record_ref(item), "category": "needs_clarification",
            "reason": "This observation is marked uncertain, inferred, or ambiguous in the saved record. Check its meaning before treating it as a change in the person's condition.",
        })
    # One source record may appear in more than one uncertainty projection.
    clarification_by_record = {}
    for item in clarification:
        key = (str(item.get("source") or ""), str(item.get("record_id") or ""))
        if key not in clarification_by_record:
            clarification_by_record[key] = item
    clarification = list(clarification_by_record.values())
    immediate.sort(key=lambda item: (0 if item.get("due_at") else 1, str(item.get("due_at") or ""), item["source"], item["record_id"]))
    important.sort(key=lambda item: (str(item.get("last_observed_at") or item.get("due_at") or ""), item.get("source", ""), item.get("record_id", item.get("id", ""))))
    deferred.sort(key=lambda item: (str(item.get("review_at") or ""), item["source"], item["record_id"]))
    clarification.sort(key=lambda item: (item["source"], item["record_id"]))
    return {
        "schema_version": CARE_ATTENTION_POLICY_VERSION, "as_of": now.isoformat(),
        "status": "bounded_evidence_assessment",
        "needs_attention_now": immediate[:100], "important_to_address": important[:100],
        "can_wait_with_documented_basis": deferred[:100], "needs_clarification": clarification[:100],
        "counts": {"needs_attention_now": len(immediate), "important_to_address": len(important),
                   "can_wait_with_documented_basis": len(deferred), "needs_clarification": len(clarification)},
        "source_coverage": source_coverage or {},
        "policy": {
            "urgent_escalation": "Only an explicitly sourced overdue deadline or an explicitly recorded critical consequence can promote an open loop to needs_attention_now.",
            "baseline_and_patterns": "Only repeated, source-linked structured observations are grouped. Patterns prompt review; they do not establish deterioration, diagnosis, or causation.",
            "deferral": "A future review date and recorded postponement reason are required for the documented-deferral category. This does not establish safety.",
            "uncertainty": "Unresolved conflicts and missing information remain visible as clarification needs; absence of evidence is not reassurance.",
            "explanations": "Every item carries source record identifiers, timestamps, and linked evidence identifiers where available.",
        },
        "limitations": ["This is not a clinical diagnosis or validated clinical triage tool.",
            "Source history may be incomplete or bounded; a missing record does not mean an event did not occur.",
            "Critical warning signs still require appropriate emergency or professional guidance outside this ranking."],
    }
