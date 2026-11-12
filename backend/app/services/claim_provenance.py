"""Claim-level source matching for caregiver-facing Companion responses.

Only exact saved wording is presented as a source match. Paraphrases and claims
that cannot be matched to an exact source passage remain explicitly unsupported.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


def _normalise(value: str) -> str:
    value = (value or "").casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\r\n.,;:!?\"'()[]{}")


def split_response_claims(reply: str) -> list[str]:
    """Split a response into displayable statements without inventing claims."""
    statements = []
    for line in (reply or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", line).strip()
        if not cleaned:
            continue
        statements.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9\"'])", cleaned)
            if part.strip()
        )
    return statements



_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12,
}

_EVENT_TOPIC_TERMS = {
    "unsteadiness": (
        "unsteady", "unsteadiness", "lost balance", "loss of balance",
        "wobbly", "stumbled", "staggered",
    ),
}


def _claim_number(statement: str) -> int | None:
    match = re.search(r"\b(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", statement.casefold())
    if not match:
        return None
    token = match.group(1)
    try:
        return int(token)
    except ValueError:
        return _NUMBER_WORDS.get(token)


def context_lines_to_source_records(lines: list[str]) -> list[dict[str, Any]]:
    """Turn only explicitly identified context records into claim-match candidates."""
    records = []
    seen = set()
    for line in lines or []:
        text = str(line or "").strip()
        if not text:
            continue
        record_id = None
        match = re.search(r"\[([^\]\r\n]{1,160})\]", text)
        if match:
            record_id = match.group(1).strip()
        record_type = "care_record"
        prefix = text.casefold()
        for marker, kind in (
            ("recorded care event", "care_event"),
            ("medication-related care event", "care_event"),
            ("caregiver-reported medication source evidence", "care_evidence"),
            ("caregiver-provided evidence", "care_evidence"),
            ("persisted extracted document", "document_extraction"),
            ("recorded appointment", "appointment"),
            ("recorded handoff", "care_handoff"),
            ("recorded notification", "notification"),
            ("canonical lcr source", "lcr_record"),
        ):
            if marker in prefix:
                record_type = kind
                break
        if not record_id:
            try:
                payload = json.loads(text[text.index("{"):])
                if isinstance(payload, dict):
                    for key in ("source_id", "event_id", "evidence_id", "appointment_id", "handoff_id", "id"):
                        if payload.get(key):
                            record_id = str(payload[key])
                            break
                    record_type = str(payload.get("source_type") or record_type)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        if not record_id:
            continue
        key = (record_type, str(record_id))
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "record_type": record_type,
            "record_id": str(record_id),
            "label": {
                "care_event": "Care update",
                "care_evidence": "Original note or document",
                "document_extraction": "Document text",
                "appointment": "Appointment record",
                "care_handoff": "Care handoff",
                "notification": "Follow-up notice",
                "lcr_record": "Care record detail",
            }.get(record_type, "Care record"),
            "source_text": text,
            "source_excerpt": text[:700],
            "record_status": None,
        })
    return records


def build_distinct_event_aggregate(
    events: list[dict[str, Any]],
    *,
    topic: str,
    start_at: datetime,
    end_at: datetime,
    window_label: str,
    truncated: bool = False,
) -> dict[str, Any]:
    """Count distinct, dated, explicitly tagged event records; never infer from prose alone."""
    if topic not in _EVENT_TOPIC_TERMS:
        return {"status": "unsupported_topic", "topic": topic, "count": None, "source_references": []}
    terms = _EVENT_TOPIC_TERMS[topic]
    excluded_statuses = {"draft", "pending", "cancelled", "canceled", "rejected", "deleted", "superseded", "withdrawn"}
    candidates = []
    start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
    end = end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)
    for event in events or []:
        occurred = event.get("occurred_at")
        if isinstance(occurred, str):
            try:
                occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            except ValueError:
                occurred = None
        if occurred is None:
            continue
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        if not (start <= occurred < end):
            continue
        status = str(event.get("status") or "recorded").casefold()
        if status in excluded_statuses:
            continue
        searchable = " ".join(str(event.get(key) or "") for key in ("event_type", "title", "description", "tags")).casefold()
        if not any(term in searchable for term in terms):
            continue
        candidates.append({**event, "_occurred": occurred, "_searchable": re.sub(r"\s+", " ", searchable).strip()})

    if truncated:
        return {
            "status": "incomplete_source_window",
            "topic": topic,
            "window_label": window_label,
            "count": None,
            "candidate_count": len(candidates),
            "source_references": [],
            "interpretation_limit": "The source search reached its record limit. No total is stated.",
        }

    unique = []
    duplicate_ids = []
    seen_evidence_sets = []
    for event in sorted(candidates, key=lambda item: (item["_occurred"], str(item.get("id") or ""))):
        evidence_ids = {str(value) for value in (event.get("evidence_ids") or []) if value}
        signature = (event["_occurred"].isoformat(), event["_searchable"])
        duplicate_of_existing = any(
            (evidence_ids and existing_ids and evidence_ids.intersection(existing_ids))
            or signature == existing_signature
            for _, existing_ids, existing_signature in seen_evidence_sets
        )
        if duplicate_of_existing:
            duplicate_ids.append(str(event.get("id")))
            continue
        unique.append(event)
        seen_evidence_sets.append((event, evidence_ids, signature))

    references = [{
        "record_type": "care_event",
        "record_id": str(item.get("id")),
        "label": "Care update",
        "occurred_at": item["_occurred"].isoformat(),
        "recorded_at": item.get("recorded_at"),
        "entered_by": item.get("entered_by"),
        "source_excerpt": " — ".join(part for part in (str(item.get("title") or ""), str(item.get("description") or "")) if part)[:700],
        "related_evidence_ids": [str(value) for value in (item.get("evidence_ids") or [])],
    } for item in unique]
    return {
        "status": "validated",
        "topic": topic,
        "window_label": window_label,
        "count": len(unique),
        "candidate_count": len(candidates),
        "duplicate_event_ids": duplicate_ids,
        "source_references": references,
        "interpretation_limit": "This is a count of distinct, dated, matching care-event records in the supplied time window. It is not independent clinical corroboration or proof that the record is complete.",
    }


def build_claim_provenance(reply: str, source_records: list[dict[str, Any]], validated_aggregates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Link only statements whose wording occurs in a saved source.

    A match is not an independence assessment and does not establish clinical
    truth. Duplicate records remain separate references and are not counted as
    independent corroboration.
    """
    historical_statuses = {"corrected", "superseded", "withdrawn", "rejected", "retracted", "expired", "deleted", "archived"}
    claims = []
    aggregates = [item for item in (validated_aggregates or []) if item.get("status") == "validated"]
    for index, statement in enumerate(split_response_claims(reply)):
        normalized_claim = _normalise(statement)
        matches = []
        current_matches = 0
        aggregate_match = None
        statement_number = _claim_number(statement)
        for aggregate in aggregates:
            topic = str(aggregate.get("topic") or "")
            topic_terms = _EVENT_TOPIC_TERMS.get(topic, ())
            mentions_topic = any(term in normalized_claim for term in topic_terms)
            mentions_incident = bool(re.search(r"\b(?:incident|incidents|event|events|occasion|occasions)\b", normalized_claim))
            if statement_number is not None and mentions_topic and mentions_incident and statement_number == aggregate.get("count"):
                aggregate_match = aggregate
                break
        if normalized_claim:
            for source in source_records:
                source_text = str(source.get("source_text") or "")
                normalized_source = _normalise(source_text)
                if normalized_claim and normalized_claim in normalized_source:
                    is_historical = str(source.get("record_status") or "").casefold() in historical_statuses
                    if not is_historical:
                        current_matches += 1
                    matches.append({
                        "record_type": source.get("record_type"),
                        "record_id": str(source.get("record_id")),
                        "label": source.get("label") or "Saved care record",
                        "matched_passage": statement,
                        "source_excerpt": source_text[:700],
                        "occurred_at": source.get("occurred_at"),
                        "recorded_at": source.get("recorded_at"),
                        "reported_by": source.get("reported_by"),
                        "entered_by": source.get("entered_by"),
                        "record_status": source.get("record_status"),
                        "source_independence_status": source.get("source_independence_status", "not_assessed"),
                        "exact_wording_duplicate_count": int(source.get("exact_wording_duplicate_count") or 0),
                        "source_state_label": source.get("source_state_label") or ("Earlier or corrected record; not treated as current" if is_historical else None),
                        "correction_note": source.get("correction_note"),
                        "correction_rationale": source.get("correction_rationale"),
                        "previous_source_excerpt": source.get("previous_source_excerpt"),
                        "related_evidence_ids": list(source.get("related_evidence_ids") or []),
                        "related_evidence": list(source.get("related_evidence") or []),
                    })
        if aggregate_match is not None:
            matches = list(aggregate_match.get("source_references") or [])
        claims.append({
            "claim_id": f"statement-{index + 1}",
            "text": statement,
            "support_status": (
                "matched_validated_aggregate" if aggregate_match is not None else
                "matched_saved_wording" if current_matches else
                "matched_historical_only" if matches else
                "not_matched_to_saved_wording"
            ),
            "validation_basis": "distinct_dated_event_aggregation" if aggregate_match is not None else "exact_saved_wording" if current_matches else "historical_wording_only" if matches else "no_support_match",
            "aggregate": ({
                "topic": aggregate_match.get("topic"),
                "window_label": aggregate_match.get("window_label"),
                "count": aggregate_match.get("count"),
                "candidate_count": aggregate_match.get("candidate_count"),
                "duplicate_event_ids": aggregate_match.get("duplicate_event_ids", []),
            } if aggregate_match is not None else None),
            "source_references": matches,
        })
    unmatched = sum(1 for claim in claims if claim["support_status"] not in {"matched_saved_wording", "matched_validated_aggregate"})
    return {
        "version": "claim-provenance-v1",
        "coverage": "all_statements_matched" if claims and unmatched == 0 else (
            "some_statements_unmatched" if claims else "no_statements"
        ),
        "statement_count": len(claims),
        "unmatched_statement_count": unmatched,
        "matching_limit": (
            "Exact source wording is matched to that source only. A numerical incident claim is supported only when it matches a complete, bounded count of distinct, dated, explicitly matching care-event records. "
            "Neither method establishes clinical truth or independent corroboration. Other paraphrases remain unsupported."
        ),
        "validated_aggregates": [
            {key: value for key, value in item.items() if key != "source_references"}
            for item in (validated_aggregates or [])
        ],
        "claims": claims,
    }
