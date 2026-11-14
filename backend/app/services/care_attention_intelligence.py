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


def _record_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(item.get("source") or "unknown"),
        "record_id": str(item.get("id") or ""),
        "title": str(item.get("title") or item.get("attribute") or item.get("source") or "Care record")[:240],
        "occurred_at": item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"),
        "recorded_at": item.get("recorded_at") or item.get("created_at") or item.get("uploaded_at"),
        "evidence_ids": _evidence_ids(item),
    }


def _open_loop_decisions(open_loops: list[dict[str, Any]], now: datetime):
    immediate, important, deferred, clarification = [], [], [], []
    for loop in open_loops:
        status = _text(loop.get("recorded_status") or loop.get("status"))
        if status in CLOSED_STATUSES or status not in OPEN_STATUSES:
            continue
        due = _datetime(loop.get("due_at") or loop.get("deadline_at"))
        explicit_deadline = _text(loop.get("deadline_source")) in {"explicit_user", "recorded_commitment", "system_policy"}
        overdue = bool(due and explicit_deadline and due <= now)
        deadline_soon = bool(due and explicit_deadline and now < due <= now + timedelta(hours=24))
        reasons = loop.get("priority_reasons") if isinstance(loop.get("priority_reasons"), list) else []
        normalized_reasons = {_text(reason) for reason in reasons}
        critical_delay = _text(loop.get("priority")) in {"high", "critical"} and _text(CRITICAL_DELAY_REASON) in normalized_reasons
        item = {
            **_record_ref(loop), "status": status, "priority_basis": [],
            "owner": loop.get("owner"), "due_at": loop.get("due_at"),
            "review_at": loop.get("review_at"), "deferred_reason": loop.get("deferred_reason"),
            "consequence_of_delay": loop.get("consequence_of_delay"),
        }
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
    for key in ("observed_value", "normalized_value", "value_text", "asserted_value", "current_state", "title"):
        value = _text(item.get(key))
        if value:
            return value
    return ""


def _subject_key(item: dict[str, Any]) -> str:
    return _text(item.get("subject_id")) or _text(item.get("subject_label") or item.get("subject_type"))


def _pattern_reviews(records: list[dict[str, Any]], baseline_items: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    eligible = []
    for item in records:
        if item.get("source") not in {"evidence_observations", "caregiver_observations", "events", "facts"}:
            continue
        if item.get("negated") is True or item.get("supersedes_observation_id"):
            continue
        if _text(item.get("status") or item.get("lifecycle_status")) in CLOSED_STATUSES:
            continue
        attribute = _text(item.get("attribute") or item.get("event_type") or item.get("fact_type"))
        subject = _subject_key(item)
        occurred = _datetime(item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"))
        if not attribute or not subject or not occurred or occurred > now or occurred < now - timedelta(days=MAX_PATTERN_WINDOW_DAYS) or not _evidence_ids(item):
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
        subject = _subject_key(baseline)
        attribute = _text(baseline.get("attribute") or baseline.get("event_type") or baseline.get("fact_type"))
        value = _observation_value(baseline)
        if subject and attribute and value:
            baseline_by_key[(subject, attribute)].append(baseline)
            if baseline.get("id"):
                baseline_ids.add(str(baseline["id"]))

    for (subject, attribute), entries in by_subject_attribute.items():
        baseline_rows = baseline_by_key.get((subject, attribute), [])
        current_entries = [(when, item) for when, item in entries if str(item.get("id") or "") not in baseline_ids]
        baseline_values = {_observation_value(item) for item in baseline_rows if _observation_value(item)}
        changed_entries = [
            (when, item) for when, item in current_entries
            if _observation_value(item) and baseline_values
            and _observation_value(item) not in baseline_values
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
        values = {_observation_value(item) for _, item in entries if _observation_value(item)}
        if (subject, attribute) in emitted or len(distinct) < 2 or len(values) < 2:
            continue
        entries.sort(key=lambda pair: pair[0])
        refs = [_record_ref(item) for _, item in entries]
        reviews.append({
            "id": f"repeated:{subject}:{attribute}", "category": "important_to_address",
            "title": str(entries[-1][1].get("subject_label") or attribute)[:240],
            "reason": "Different values for the same recorded observation were reported more than once within 14 days. Review the sequence against the person's documented baseline; this does not establish cause or clinical severity.",
            "pattern_type": "repeated_attribute_with_changed_values", "record_count": len(distinct),
            "records": refs, "evidence_ids": sorted({e for ref in refs for e in ref["evidence_ids"]}),
            "first_observed_at": refs[0]["occurred_at"], "last_observed_at": refs[-1]["occurred_at"],
        })
        emitted.add((subject, attribute))

    for subject, entries in by_subject.items():
        attributes = {attribute for attribute, _, _ in entries}
        days = {occurred.date() for _, occurred, _ in entries}
        if len(entries) < 3 or len(attributes) < 3 or len(days) < 2 or any((subject, a) in emitted for a in attributes):
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


def build_care_attention_assessment(*, records: list[dict[str, Any]], open_loops: list[dict[str, Any]],
    uncertainties: list[dict[str, Any]], missing_information: list[dict[str, Any]],
    baseline_items: list[dict[str, Any]] | None = None, as_of: Any = None, source_coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    now = _datetime(as_of) or datetime.now(timezone.utc)
    immediate, important, deferred, loop_clarifications = _open_loop_decisions(open_loops, now)
    important.extend(_pattern_reviews(records, baseline_items or [], now))
    clarification = list(loop_clarifications)
    for item in [*uncertainties, *missing_information]:
        if _text(item.get("status") or item.get("lifecycle_status")) in CLOSED_STATUSES:
            continue
        clarification.append({
            **_record_ref(item), "category": "needs_clarification",
            "reason": "A saved uncertainty, conflict, or information gap remains unresolved. The missing or competing information may change what should happen next.",
        })
    immediate.sort(key=lambda item: (0 if item.get("due_at") else 1, str(item.get("due_at") or ""), item["source"], item["record_id"]))
    important.sort(key=lambda item: (str(item.get("last_observed_at") or item.get("due_at") or ""), item.get("source", ""), item.get("record_id", item.get("id", ""))))
    deferred.sort(key=lambda item: (str(item.get("review_at") or ""), item["source"], item["record_id"]))
    clarification.sort(key=lambda item: (item["source"], item["record_id"]))
    return {
        "schema_version": "care-attention.v1", "as_of": now.isoformat(),
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
