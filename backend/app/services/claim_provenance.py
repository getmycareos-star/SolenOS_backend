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
    match = re.search(
        r"\b(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:(?:separate|distinct|recorded|reported|documented|saved)\s+)*(?:incident|incidents|event|events|occasion|occasions|episode|episodes)\b",
        statement.casefold(),
    )
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
        bracket_ids = [value.strip() for value in re.findall(r"\[([^\]\r\n]{1,160})\]", text)]
        if bracket_ids:
            record_id = bracket_ids[0]
        record_type = "care_record"
        prefix = text.casefold()
        for marker, kind in (
            ("lcr event evidence", "care_event"),
            ("lcr care event", "care_event"),
            ("lcr evidence", "care_evidence"),
            ("lcr document extraction", "document_extraction"),
            ("lcr appointment evidence", "appointment"),
            ("recorded care event", "care_event"),
            ("medication-related care event", "care_event"),
            ("caregiver-reported medication source evidence", "care_evidence"),
            ("caregiver-provided evidence", "care_evidence"),
            ("persisted extracted document", "document_extraction"),
            ("recorded appointment", "appointment"),
            ("recorded handoff", "care_handoff"),
            ("recorded notification", "notification"),
            ("recorded freshness policy application", "freshness_assessment"),
            ("canonical lcr source", "lcr_record"),
        ):
            if marker in prefix:
                record_type = kind
                break
        record_metadata: dict[str, Any] = {}
        if not record_id:
            try:
                payload = json.loads(text[text.index("{"):])
                if isinstance(payload, dict):
                    for key in ("id", "source_id", "event_id", "evidence_id", "appointment_id", "handoff_id"):
                        if payload.get(key):
                            record_id = str(payload[key])
                            break
                    source_type_map = {
                        "living_record": "living_care_record",
                        "events": "care_event",
                        "evidence": "care_evidence",
                        "document_extractions": "document_extraction",
                        "appointments": "appointment",
                        "handoffs": "care_handoff",
                        "notifications": "notification",
                        "evidence_freshness_assessments": "freshness_assessment",
                        "evidence_completeness_assessments": "completeness_assessment",
                        "evidence_observations": "care_evidence_observation",
                        "assertions": "care_assertion",
                        "facts": "care_fact",
                        "decisions": "care_decision",
                        "decision_outcomes": "care_decision_outcome",
                        "uncertainties": "care_uncertainty",
                        "uncertainty_assessments": "care_uncertainty_assessment",
                        "open_questions": "open_question",
                        "corrections": "care_correction",
                    }
                    raw_source_type = str(payload.get("source_type") or record_type)
                    record_type = source_type_map.get(raw_source_type, raw_source_type)
                    record_payload = payload.get("record") if isinstance(payload.get("record"), dict) else payload
                    if isinstance(record_payload, dict):
                        source_status = (
                            record_payload.get("epistemic_status")
                            or record_payload.get("record_status")
                            or record_payload.get("status")
                        )
                        source_attribution = (
                            record_payload.get("source_attribution")
                            or record_payload.get("time_provenance")
                        )
                        if source_status is not None:
                            record_metadata["record_status"] = str(source_status)
                        if source_attribution is not None:
                            record_metadata["source_attribution"] = str(source_attribution)
                        record_metadata["reported_by"] = (
                            record_payload.get("reported_by_label")
                            or record_payload.get("original_author")
                        )
                        record_metadata["occurred_at"] = (
                            record_payload.get("occurred_at")
                            or record_payload.get("observed_at")
                            or record_payload.get("event_at")
                        )
                        record_metadata["recorded_at"] = (
                            record_payload.get("recorded_at")
                            or record_payload.get("uploaded_at")
                            or record_payload.get("created_at")
                        )
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
                "freshness_assessment": "Review of how current a source is",
                "lcr_record": "Care record detail",
            }.get(record_type, "Care record"),
            "source_text": text,
            "source_excerpt": text[:700],
            "record_status": None,
            "related_evidence_ids": bracket_ids[1:2] if record_type in {"document_extraction", "freshness_assessment"} and len(bracket_ids) > 1 else [],
            **record_metadata,
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
    shared_origin_evidence_pairs: set[frozenset[str]] | None = None,
) -> dict[str, Any]:
    """Count distinct, dated, explicitly tagged event records; never infer from prose alone."""
    if topic not in _EVENT_TOPIC_TERMS:
        return {"status": "unsupported_topic", "topic": topic, "count": None, "source_references": []}
    terms = _EVENT_TOPIC_TERMS[topic]
    eligible_statuses = {"recorded", "completed", "occurred", "resolved", "accepted"}
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
        if status not in eligible_statuses:
            continue
        searchable = " ".join(str(event.get(key) or "") for key in ("event_type", "title", "description", "tags")).casefold()
        if not any(term in searchable for term in terms):
            continue
        candidates.append({**event, "_occurred": occurred, "_searchable": re.sub(r"\s+", " ", searchable).strip()})

    correction_review_events = [
        item for item in candidates if item.get("source_correction_requires_review")
    ]
    if correction_review_events:
        return {
            "status": "source_correction_requires_review",
            "topic": topic,
            "window_label": window_label,
            "count": None,
            "candidate_count": len(candidates),
            "affected_event_count": len(correction_review_events),
            "interpretation_limit": "A source detail linked to one or more matching care updates was later corrected. Those updates need review before a current total can be stated.",
            "source_references": [],
        }

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
    ambiguous_ids = []
    seen_evidence_sets = []
    reviewed_shared_pairs = shared_origin_evidence_pairs or set()
    for event in sorted(candidates, key=lambda item: (item["_occurred"], str(item.get("id") or ""))):
        evidence_ids = {str(value) for value in (event.get("evidence_ids") or []) if value}
        signature = (event["_occurred"].isoformat(), event["_searchable"])
        date_signature = (event["_occurred"].date().isoformat(), event["_searchable"])
        duplicate_of_existing = False
        ambiguous_with_existing = False
        for _, existing_ids, existing_signature, existing_date_signature in seen_evidence_sets:
            shared_origin = any(
                frozenset((left, right)) in reviewed_shared_pairs
                for left in evidence_ids for right in existing_ids if left != right
            )
            if (evidence_ids and existing_ids and evidence_ids.intersection(existing_ids)) or shared_origin or signature == existing_signature:
                duplicate_of_existing = True
                break
            if date_signature == existing_date_signature:
                ambiguous_with_existing = True
        if duplicate_of_existing:
            duplicate_ids.append(str(event.get("id")))
            continue
        if ambiguous_with_existing:
            ambiguous_ids.append(str(event.get("id")))
        unique.append(event)
        seen_evidence_sets.append((event, evidence_ids, signature, date_signature))

    if ambiguous_ids:
        return {
            "status": "ambiguous_duplicate_candidates",
            "topic": topic,
            "window_label": window_label,
            "count": None,
            "candidate_count": len(candidates),
            "duplicate_event_ids": duplicate_ids,
            "ambiguous_event_ids": ambiguous_ids,
            "source_references": [],
            "interpretation_limit": "Some same-day reports have identical wording but no confirmed shared original. They may describe the same incident or separate incidents, so no total is stated until a caregiver reviews them.",
        }

    if not unique:
        return {
            "status": "no_matching_records",
            "topic": topic,
            "window_label": window_label,
            "count": 0,
            "candidate_count": len(candidates),
            "duplicate_event_ids": duplicate_ids,
            "source_references": [],
            "interpretation_limit": "No matching care-event records were found in this time window. This does not establish that no incident occurred.",
        }

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


_SOURCE_ATTRIBUTION_RE = re.compile(
    r"\b(?:according to|reported by|observed by|documented by|"
    r"(?:caregiver|family|patient|professional|clinician)[- ]report|"
    r"(?:caregiver|family|patient|person|professional|clinician|doctor|nurse|staff|"
    r"[A-Z][A-Za-z'’-]{1,30})\s+(?:reported|reports|observed|documented|said|says|noted|recorded|stated))\b",
    re.IGNORECASE,
)
_CLAIM_ATTRIBUTION_RE = re.compile(
    r"\b(?:according to|reported by|observed by|documented by|"
    r"(?:caregiver|family|patient|professional|clinician|document|note|record)[- ](?:reported|observed|documented)|"
    r"(?:was|were|is|are|has been|had been)\s+(?:reported|observed|documented|noted|recorded)|"
    r"(?:caregiver|family|patient|person|professional|clinician|doctor|nurse|staff|document|note|record|"
    r"[A-Z][A-Za-z'’-]{1,30})\s+(?:reported|reports|observed|documented|said|says|noted|recorded|stated))\b",
    re.IGNORECASE,
)
_CLAIM_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|didn't|doesn't|wasn't|weren't|isn't|aren't|"
    r"cannot|can't|denies|denied|negative|neither|nor)\b",
    re.IGNORECASE,
)
_SOURCE_NEGATION_RE = _CLAIM_NEGATION_RE
_CLAIM_UNCERTAINTY_RE = re.compile(
    r"\b(?:may|might|could|possibly|possible|possibly|uncertain|unclear|"
    r"not sure|suspected|suspect|appears|appeared|seems|seemed|perhaps|likely|unlikely)\b",
    re.IGNORECASE,
)
_SOURCE_UNCERTAINTY_RE = _CLAIM_UNCERTAINTY_RE


def _claim_preserves_source_qualifiers(statement: str, source: dict[str, Any], matched_text: str | None = None) -> bool:
    """Do not strip attribution, negation, or uncertainty from a source passage."""
    source_text = str(source.get("source_text") or "")
    status = " ".join(str(source.get(key) or "") for key in (
        "record_status", "epistemic_status", "source_attribution",
    )).casefold()
    related = source.get("related_evidence") or []
    related_statuses = " ".join(
        " ".join(str(item.get(key) or "") for key in (
            "record_status", "epistemic_status", "source_attribution",
        ))
        for item in related if isinstance(item, dict)
    ).casefold()
    attribution_required = bool(
        _SOURCE_ATTRIBUTION_RE.search(source_text)
        or any(term in status for term in ("reported", "observed", "documented", "unverified"))
        or any(term in related_statuses for term in ("reported", "observed", "documented", "unverified"))
        or (source.get("reported_by") and str(source.get("record_status") or "").casefold() not in {"verified", "confirmed"})
    )
    if attribution_required and not _CLAIM_ATTRIBUTION_RE.search(statement):
        return False

    normalized_source = _normalise(source_text)
    normalized_claim = _normalise(matched_text if matched_text is not None else statement)
    qualifier_text = matched_text if matched_text is not None else statement
    search_from = 0
    while normalized_claim:
        position = normalized_source.find(normalized_claim, search_from)
        if position < 0:
            return False
        prefix = normalized_source[:position]
        # Evaluate only the current clause; a contrast such as "but" starts a
        # new proposition and must not inherit an earlier negation or hedge.
        clause_prefix = re.split(
            r"[.!?;]\s*|\b(?:but|however|although|whereas)\b",
            prefix,
        )[-1]
        if _SOURCE_NEGATION_RE.search(clause_prefix) and not _CLAIM_NEGATION_RE.search(qualifier_text):
            search_from = position + 1
            continue
        if _SOURCE_UNCERTAINTY_RE.search(clause_prefix) and not _CLAIM_UNCERTAINTY_RE.search(qualifier_text):
            search_from = position + 1
            continue
        return True
    return False


_ATTRIBUTED_QUOTE_FRAME_RE = re.compile(
    r"^(?:(?:the )?(?:caregiver|family(?: member)?|patient|person|professional|clinician|doctor|nurse|staff|document|note|record)\s+(?:reported|observed|documented|said|noted|recorded|stated|reports|says|describes|mentions)|"
    r"(?:caregiver|family|patient|professional|clinician)[- ](?:reported|observed|documented)|"
    r"according to (?:the )?(?:caregiver|family|patient|professional|clinician|document|note|record))\s*:?$",
    re.IGNORECASE,
)


def _exact_attributed_source_quote(statement: str) -> str | None:
    """Accept a source quote only when the surrounding words are attribution framing."""
    patterns = (
        r'^(?P<prefix>[^“”]{1,120})“(?P<quote>[^“”]{4,500})”\s*[.!?]?$',
        r'^(?P<prefix>[^‘’]{1,120})‘(?P<quote>[^‘’]{4,500})’\s*[.!?]?$',
    )
    for pattern in patterns:
        match = re.match(pattern, (statement or "").strip())
        if not match:
            continue
        prefix = match.group("prefix").strip().rstrip(":").strip()
        quote = match.group("quote").strip()
        if prefix and _ATTRIBUTED_QUOTE_FRAME_RE.fullmatch(prefix):
            return quote
    return None


def build_claim_provenance(reply: str, source_records: list[dict[str, Any]], validated_aggregates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Link only exact source wording or an explicitly attributed exact source quote.

    A match is not an independence assessment and does not establish clinical
    truth. Duplicate records remain separate references and are not counted as
    independent corroboration.
    """
    historical_statuses = {"corrected", "superseded", "withdrawn", "rejected", "retracted", "expired", "deleted", "archived"}
    claims = []
    aggregates = list(validated_aggregates or [])
    for index, statement in enumerate(split_response_claims(reply)):
        normalized_claim = _normalise(statement)
        matches = []
        current_matches = 0
        quoted_matches = 0
        aggregate_match = None
        aggregate_review = None
        statement_number = _claim_number(statement)
        for candidate in aggregates:
            topic = str(candidate.get("topic") or "")
            topic_terms = _EVENT_TOPIC_TERMS.get(topic, ())
            mentions_topic = any(term in normalized_claim for term in topic_terms)
            mentions_incident = bool(re.search(r"\b(?:incident|incidents|event|events|occasion|occasions|episode|episodes)\b", normalized_claim))
            if statement_number is None or not mentions_topic or not mentions_incident:
                continue
            if candidate.get("status") != "validated":
                aggregate_review = candidate
            elif statement_number == candidate.get("count"):
                aggregate_match = candidate
            else:
                aggregate_review = {
                    "status": "count_mismatch",
                    "topic": topic,
                    "window_label": candidate.get("window_label"),
                    "count": candidate.get("count"),
                    "interpretation_limit": "The number in this statement does not match the distinct dated care-event records found in the requested time window.",
                }
            break
        if normalized_claim:
            for source in source_records:
                source_text = str(source.get("source_text") or "")
                normalized_source = _normalise(source_text)
                quoted_passage = _exact_attributed_source_quote(statement)
                exact_match = bool(
                    normalized_claim
                    and normalized_claim in normalized_source
                    and _claim_preserves_source_qualifiers(statement, source)
                )
                quoted_match = bool(
                    quoted_passage
                    and _normalise(quoted_passage) in normalized_source
                    and _claim_preserves_source_qualifiers(statement, source, matched_text=quoted_passage)
                )
                if exact_match or quoted_match:
                    is_historical = str(source.get("record_status") or "").casefold() in historical_statuses
                    matched_passage = quoted_passage if quoted_match and not exact_match else statement
                    if not is_historical:
                        current_matches += 1
                    if quoted_match and not exact_match:
                        quoted_matches += 1
                    matches.append({
                        "match_type": "attributed_exact_quote" if quoted_match and not exact_match else "exact_statement",
                        "record_type": source.get("record_type"),
                        "record_id": str(source.get("record_id")),
                        "label": source.get("label") or "Saved care record",
                        "matched_passage": matched_passage,
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
        elif aggregate_review is not None:
            matches = []
        support_status = (
            "matched_validated_aggregate" if aggregate_match is not None else
            "aggregate_count_mismatch" if aggregate_review and aggregate_review.get("status") == "count_mismatch" else
            "aggregate_not_validated" if aggregate_review is not None else
            "matched_saved_wording" if current_matches else
            "matched_historical_only" if matches else
            "not_matched_to_saved_wording"
        )
        claims.append({
            "claim_id": f"statement-{index + 1}",
            "text": statement,
            "support_status": support_status,
            "validation_basis": "distinct_dated_event_aggregation" if aggregate_match is not None else "aggregate_count_mismatch" if support_status == "aggregate_count_mismatch" else "aggregate_not_validated" if support_status == "aggregate_not_validated" else "exact_attributed_source_quote" if quoted_matches else "exact_saved_wording" if current_matches else "historical_wording_only" if matches else "no_support_match",
            "aggregate": ({
                "topic": aggregate_match.get("topic"),
                "window_label": aggregate_match.get("window_label"),
                "count": aggregate_match.get("count"),
                "candidate_count": aggregate_match.get("candidate_count"),
                "duplicate_event_ids": aggregate_match.get("duplicate_event_ids", []),
            } if aggregate_match is not None else None),
            "aggregate_review": ({
                "status": aggregate_review.get("status"),
                "topic": aggregate_review.get("topic"),
                "window_label": aggregate_review.get("window_label"),
                "count": aggregate_review.get("count"),
                "affected_event_count": aggregate_review.get("affected_event_count"),
                "interpretation_limit": aggregate_review.get("interpretation_limit"),
            } if aggregate_review is not None else None),
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
            "Exact source wording is matched to that source only. A short quotation is accepted only when it is an exact source substring and its framing explicitly preserves attribution. "
            "A numerical incident claim is supported only when it matches a complete, bounded count of distinct, dated, explicitly matching care-event records. "
            "These methods do not establish clinical truth, semantic entailment beyond the matched passage, or independent corroboration. Other paraphrases remain unsupported."
        ),
        "validated_aggregates": [
            {key: value for key, value in item.items() if key != "source_references"}
            for item in (validated_aggregates or [])
        ],
        "claims": claims,
    }

_MATERIAL_CARE_TERMS = re.compile(
    r"\b(?:fall|falls|fell|fallen|stumble|stumbled|unsteady|unsteadiness|"
    r"balance|mobility|walking|walked|dizzy|dizziness|pain|appetite|eating|ate|"
    r"sleep|slept|restless|waking|wake|insomnia|fatigue|tired|"
    r"(?:quieter|restless|poor|rough|good)\s+night|"
    r"confus(?:ed|ion)|memory|behavio[u]?r|symptom|symptoms|"
    r"medication|medicine|dose|prescription|diagnos(?:is|ed)|dementia|"
    r"alzheimer|delirium|infection|fever|blood pressure|glucose|"
    r"appointment|test result|hospital|discharge|treatment|wound|"
    r"seizure|breathing|swelling|hydration|dehydration|weight loss|"
    r"weight gain|blood sugar|oxygen level|urinary tract infection|uti|pneumonia)\b",
    re.IGNORECASE,
)
_MATERIAL_ASSERTION_VERBS = re.compile(
    r"\b(?:is|are|was|were|has|have|had|felt|feels|slept|shows?|indicates?|"
    r"increased|decreased|improved|worsened|stopped|started|missed|takes?|taking|"
    r"needs?|requires?|uses?|reported|reports|observed|documented|scheduled|"
    r"confirmed|completed|occurred|happened|experienced|became|remains?|"
    r"continues?|says?|states?|describes?|mentions?|includes?|contains?)\b",
    re.IGNORECASE,
)

_NON_ASSERTIVE_OPENINGS = re.compile(
    r"^(?:if|when|please|call|ask|contact|seek|consider|you can|you may|"
    r"you should|do not|don't|never|i can|i can't|i cannot|it may help|"
    r"it might help|would you|could you|can you|do you know|is there)\b",
    re.IGNORECASE,
)


def unmatched_material_claims(provenance: dict[str, Any]) -> list[dict[str, Any]]:
    """Return care-related assertions that lack an exact current-source match.

    This is intentionally conservative. It is not semantic entailment: paraphrases
    remain unsupported until a deterministic, source-bound assertion can support
    them. Questions, conditional guidance, and non-assertive support language are
    excluded so they are not mistaken for claims about the person's care.
    """
    unmatched = []
    for claim in provenance.get("claims", []) if isinstance(provenance, dict) else []:
        if not isinstance(claim, dict):
            continue
        statement = str(claim.get("text") or "").strip()
        if not statement or _NON_ASSERTIVE_OPENINGS.search(statement):
            continue
        if statement.endswith("?"):
            continue
        if not _MATERIAL_CARE_TERMS.search(statement):
            continue
        if not _MATERIAL_ASSERTION_VERBS.search(statement):
            continue
        if claim.get("support_status") not in {
            "matched_saved_wording",
            "matched_validated_aggregate",
        }:
            unmatched.append(claim)
    return unmatched

