from __future__ import annotations

import hashlib
import json
import logging
import uuid
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from app.services.qwen_gateway import get_ollama_config, qwen_chat
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.care import CareEvent, Caregiver, Evidence
from app.models.care_continuity import CareAssertion, CareDeltaRun, CareRecordRelationship, EvidenceObservation
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion, UncertaintyAssessment
from app.services.care_continuity import has_valid_caregiver_necessity
from app.services.uncertainty_intelligence import analyze_uncertainty, evidence_record_to_uncertainty_item
from app.core.uncertainty_enums import EpistemicState, GapReason, GapLifecycleStatus, Priority
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion
from app.services.claim_provenance import build_claim_provenance


logger = logging.getLogger(__name__)

DELTA_ENGINE_VERSION = "2026.11.20.1"

DELTA_JSON_CONTRACT = {
    "type": "object",
    "required": [
        "event_summary", "medications", "conditions", "symptoms", "tests",
        "follow_ups", "contradictions", "clinical_gaps", "caregiver_summary", "clinician_summary"
    ],
    "properties": {
        "event_summary": {"type": "string", "maxLength": 140},
        "medications": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string", "enum": ["new", "dose_changed", "frequency_changed", "route_changed", "discontinued", "unchanged", "conflicting", "uncertain"]},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string", "maxLength": 160},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "conditions": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string"},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string", "maxLength": 160},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "symptoms": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string"},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string", "maxLength": 160},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "tests": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["name", "status", "ordered_date", "result_present", "evidence_excerpt"],
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "ordered_date": {"type": ["string", "null"]},
                    "result_present": {"type": "boolean"},
                    "evidence_excerpt": {"type": "string", "maxLength": 160}
                }
            }
        },
        "follow_ups": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["action", "status", "due_date", "owner", "evidence_excerpt"],
                "properties": {
                    "action": {"type": "string"},
                    "status": {"type": "string"},
                    "due_date": {"type": ["string", "null"]},
                    "owner": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string", "maxLength": 160}
                }
            }
        },
        "contradictions": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["topic", "reports", "why_conflicting", "evidence_excerpts"],
                "properties": {
                    "topic": {"type": "string"},
                    "reports": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 100}},
                    "why_conflicting": {"type": "string"},
                    "evidence_excerpts": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 120}}
                }
            }
        },
        "clinical_gaps": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["gap", "why_it_matters", "next_question", "evidence_excerpt"],
                "properties": {
                    "gap": {"type": "string"},
                    "why_it_matters": {"type": "string", "maxLength": 120},
                    "next_question": {"type": "string", "maxLength": 120},
                    "evidence_excerpt": {"type": "string", "maxLength": 160}
                }
            }
        },
        "caregiver_summary": {"type": "string", "maxLength": 140},
        "clinician_summary": {"type": "string", "maxLength": 140}
    }
}


def _local_ollama_url() -> tuple[str, str, float]:
    # Delta and every other AI workflow share the same local/private Qwen gateway.
    return get_ollama_config()



def _validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    valid = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }
    allowed = expected if isinstance(expected, list) else [expected]
    if expected and not any(kind in valid and valid[kind](value) for kind in allowed):
        raise HTTPException(status_code=502, detail=f"The local model returned an invalid Delta field at {path}; no delta was persisted.")
    if "enum" in schema and value not in schema["enum"]:
        raise HTTPException(status_code=502, detail=f"The local model returned an unsupported Delta value at {path}; no delta was persisted.")
    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        raise HTTPException(status_code=502, detail=f"The local model exceeded the Delta output length limit at {path}; no delta was persisted.")
    if isinstance(value, list) and "maxItems" in schema and len(value) > schema["maxItems"]:
        raise HTTPException(status_code=502, detail=f"The local model exceeded the Delta item limit at {path}; no delta was persisted.")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise HTTPException(status_code=502, detail=f"The local model omitted required Delta field {path}.{key}; no delta was persisted.")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                _validate_schema(value[key], child, f"{path}.{key}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}[{index}]")


def _parse_model_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # Some local model builds wrap otherwise valid JSON in a short preamble.
        # Decode one object without accepting or persisting arbitrary trailing text.
        start = text.find("{")
        if start >= 0:
            try:
                data, consumed = json.JSONDecoder().raw_decode(text[start:])
                trailing = text[start + consumed:].strip()
                if trailing and trailing != "```":
                    raise ValueError("non-whitespace content follows the JSON object")
            except (json.JSONDecodeError, ValueError):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "delta_invalid_json",
                        "message": "The local model returned incomplete or invalid JSON; no delta was persisted.",
                        "output_length": len(text),
                        "ends_with_object_brace": text.rstrip().endswith("}"),
                        "parse_line": exc.lineno,
                        "parse_column": exc.colno,
                    },
                ) from exc
        else:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "delta_invalid_json",
                    "message": "The local model returned no JSON object; no delta was persisted.",
                    "output_length": len(text),
                    "parse_line": exc.lineno,
                    "parse_column": exc.colno,
                },
            ) from exc
    _validate_schema(data, DELTA_JSON_CONTRACT)
    return data


def _request_delta_json(messages: list[dict[str, str]], *, timeout: float) -> dict[str, Any]:
    """Generate a schema-valid Delta object, retrying one malformed model response.

    The retry is bounded within the request budget and never persists an incomplete
    response. Timeouts and schema violations are not retried as formatting errors.
    """
    for attempt in range(2):
        attempt_messages = list(messages)
        attempt_timeout = min(float(timeout), 120.0) if attempt == 0 else min(float(timeout), 30.0)
        output_budget = 384 if attempt == 0 else 512
        if attempt:
            attempt_messages.append({
                "role": "user",
                "content": (
                    "Your previous response was incomplete or invalid JSON. "
                    "Regenerate from the original care records. Return one complete compact JSON "
                    "object matching the supplied schema; include every required key, use empty "
                    "arrays when no finding is supported, and add no explanation or markdown."
                ),
            })
        raw = qwen_chat(
            attempt_messages,
            timeout=attempt_timeout,
            response_format=DELTA_JSON_CONTRACT,
            options={"temperature": 0, "num_ctx": 4096, "num_predict": output_budget},
        )
        try:
            return _parse_model_json(raw)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if attempt or detail.get("code") != "delta_invalid_json":
                raise
    raise HTTPException(status_code=502, detail="The local model could not produce a complete Delta response; no delta was persisted.")

def _safe_assertion_source_context(value: Any) -> dict[str, Any]:
    """Project only source qualifiers needed to interpret document assertions."""
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in ("kind", "authority", "review_status")
        if isinstance(value.get(key), str)
    }
    candidate = value.get("document_candidate")
    if isinstance(candidate, dict):
        allowed = (
            "source_attribution", "negation_status", "modality", "conditionality",
            "temporal_context", "statement_kind", "field_type", "page_number",
            "event_date", "recorded_date",
        )
        projected = {
            key: candidate[key]
            for key in allowed
            if isinstance(candidate.get(key), (str, int, float, bool))
        }
        if projected:
            result["document_candidate"] = projected
    return result


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _delta_run_matches_current_input(
    saved_provenance: dict[str, Any],
    *,
    source_event_snapshot_sha256: str | None,
    care_state_revision_snapshot: str,
    evidence_ids: list[str],
    input_content_sha256: str,
    event_date: datetime,
) -> bool:
    """Only replay a saved Delta result for the exact input and comparison basis.

    Older runs without an input digest are deliberately not reusable: matching
    source/evidence identifiers alone cannot prove that the submitted content
    is the same observation.
    """
    return (
        saved_provenance.get("engine_version") == DELTA_ENGINE_VERSION
        and saved_provenance.get("source_event_snapshot_sha256") == source_event_snapshot_sha256
        and saved_provenance.get("care_state_revision_snapshot") == care_state_revision_snapshot
        and [str(value) for value in (saved_provenance.get("evidence_ids") or [])]
            == [str(value) for value in evidence_ids]
        and saved_provenance.get("input_content_sha256") == input_content_sha256
        and saved_provenance.get("event_date") == event_date.isoformat()
    )



def evaluate_longitudinal_interpretations(
    computed_changes: list[dict[str, Any]],
    *,
    db: Session | None = None,
    person_id: str | None = None,
    history_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently grade interpretations without changing source evidence.

    When a database session is supplied, each source reference is re-checked
    against the same person-scoped source table before an interpretation can pass.
    This deterministic verifier never calls a model, infers causes, rewrites
    observations, or creates care facts.
    """
    counts = {
        "supported": 0,
        "partially_supported": 0,
        "conflicting": 0,
        "insufficient_evidence": 0,
        "stale_evidence": 0,
        "unsupported": 0,
        "no_change_detected": 0,
    }
    evaluated = []
    for item in computed_changes:
        references = [
            ref for ref in (item.get("source_record_refs") or [])
            if isinstance(ref, dict) and str(ref.get("record_id") or "").strip()
        ]
        verified_references = []
        invalid_reference_ids = []
        stale_source_ids = []
        for ref in references:
            record_id = str(ref["record_id"])
            record_type = str(ref.get("record_type") or "")
            exists = True
            database_stale = False
            if db is not None:
                exists = False
                if person_id:
                    if record_type == "care_event":
                        source = db.query(CareEvent).filter(
                            CareEvent.id == record_id, CareEvent.person_id == person_id
                        ).first()
                        exists = source is not None
                        database_stale = bool(
                            source is not None and str(source.status or "").casefold()
                            in {"stale", "historical", "historical_only", "archived", "superseded"}
                        )
                    elif record_type == "evidence":
                        source = db.query(Evidence).filter(
                            Evidence.id == record_id, Evidence.person_id == person_id
                        ).first()
                        exists = source is not None
                        database_stale = bool(
                            source is not None and str(source.evidence_status or "").casefold()
                            in {"stale", "historical", "historical_only", "archived", "superseded", "needs_confirmation"}
                        )
                    elif record_type == "care_assertion":
                        source = db.query(CareAssertion).filter(
                            CareAssertion.id == record_id, CareAssertion.person_id == person_id
                        ).first()
                        exists = source is not None
                        if source is not None and source.review_after_days is not None and source.observed_at is not None:
                            observed_at = source.observed_at
                            if observed_at.tzinfo is None:
                                observed_at = observed_at.replace(tzinfo=timezone.utc)
                            database_stale = datetime.now(timezone.utc) > observed_at + timedelta(
                                days=max(0, int(source.review_after_days))
                            )
                    elif record_type == "evidence_observation":
                        source = db.query(EvidenceObservation).filter(
                            EvidenceObservation.id == record_id, EvidenceObservation.person_id == person_id
                        ).first()
                        exists = source is not None
                        if source is not None:
                            database_stale = source.supersedes_observation_id is not None or (
                                db.query(EvidenceObservation.id).filter(
                                    EvidenceObservation.person_id == person_id,
                                    EvidenceObservation.supersedes_observation_id == record_id,
                                ).first() is not None
                            )
            if exists:
                verified_references.append(ref)
                if database_stale:
                    stale_source_ids.append(record_id)
            else:
                invalid_reference_ids.append(record_id)

        reference_ids = list(dict.fromkeys(str(ref["record_id"]) for ref in verified_references))
        explicit_stale = item.get("historical_only") is True or bool(stale_source_ids) or any(
            str(ref.get("freshness_status") or ref.get("status") or "").casefold()
            in {"stale", "historical_only", "needs_confirmation"}
            for ref in verified_references
        )
        change_kind = str(item.get("change_type") or item.get("classification") or "").casefold()
        if invalid_reference_ids:
            status = "unsupported"
            justification = "SolenOS could not confirm that all linked notes belong to this care record."
        elif change_kind == "conflicting" or item.get("classification") == "conflicting":
            if len(reference_ids) >= 2:
                status = "conflicting"
                justification = "The reports do not agree. SolenOS keeps both and does not choose which one is correct."
            else:
                status = "insufficient_evidence"
                justification = "There are not two separately confirmed sources to help resolve the difference."
        elif explicit_stale:
            status = "stale_evidence"
            justification = "The linked information is marked as older or still needing confirmation."
        elif not reference_ids:
            status = "insufficient_evidence"
            justification = "No supporting note or document is linked to this explanation."
        elif item.get("evidence_supported") is not True or item.get("source_reference_status") not in (None, "matched"):
            status = "unsupported"
            justification = "The linked information has not been checked enough to support this conclusion."
        elif item.get("claim_supported") is True and item.get("certainty") in {"candidate", "supported"}:
            baseline_value = item.get("baseline_coverage_status")
            current_value = item.get("current_coverage_status")
            baseline = str(getattr(baseline_value, "value", baseline_value) or "").casefold()
            current = str(getattr(current_value, "value", current_value) or "").casefold()
            complete = {"covered", "complete", "full", "sufficient"}
            if (baseline and baseline not in complete) or (current and current not in complete):
                status = "partially_supported"
                justification = "The linked information is available, but it does not cover the full period being compared."
            else:
                status = "supported"
                justification = "The linked information supports this change, but it does not establish a medical diagnosis."
        else:
            status = "partially_supported"
            justification = "Some information supports this change, but there are still details to check."

        item["interpretation_status"] = status
        item["interpretation_caregiver_visible"] = status in {
            "supported", "partially_supported", "conflicting"
        }
        item["interpretation_supporting_evidence_ids"] = (
            reference_ids if status not in {"insufficient_evidence", "unsupported"} else []
        )
        item["interpretation_contradictory_evidence_ids"] = (
            reference_ids if status == "conflicting" else []
        )
        item["interpretation_invalid_source_ids"] = invalid_reference_ids
        item["interpretation_stale_source_ids"] = stale_source_ids
        item["interpretation_uncertainty_justification"] = justification
        counts[status] += 1
        evaluated.append({
            "change_id": str(item.get("authoritative_change_id") or ""),
            "subject": str(item.get("name") or item.get("domain") or "care information"),
            "status": status,
            "supporting_evidence_ids": item["interpretation_supporting_evidence_ids"],
            "contradictory_evidence_ids": item["interpretation_contradictory_evidence_ids"],
            "invalid_source_ids": invalid_reference_ids,
            "uncertainty_justification": justification,
            "caregiver_visible": item["interpretation_caregiver_visible"],
        })

    total = len(evaluated)
    if not total:
        if history_coverage and history_coverage.get("complete") is True:
            overall = "no_change_detected"
            counts["no_change_detected"] = 1
        else:
            overall = "insufficient_evidence"
    elif counts["conflicting"]:
        overall = "conflicting"
    elif counts["stale_evidence"] and counts["supported"] == 0:
        overall = "stale_evidence"
    elif counts["unsupported"] or counts["insufficient_evidence"]:
        overall = "partially_supported" if counts["supported"] or counts["partially_supported"] else "unsupported"
    elif counts["partially_supported"]:
        overall = "partially_supported"
    else:
        overall = "supported"
    return {
        "version": "longitudinal-interpretation-evaluator-v1",
        "evaluator": "deterministic_post_delta_person_scoped_source_and_coverage_check",
        "overall_status": overall,
        "caregiver_visible": any(item["caregiver_visible"] for item in evaluated),
        "source_evidence_mutated": False,
        "creates_competing_care_record": False,
        "counts": counts,
        "evaluations": evaluated,
        "limitations": [
            "A supported result means provenance and existing comparison checks passed; it does not establish clinical truth.",
            "Freshness is marked stale only when the source or comparison already carries an explicit historical or confirmation-required marker; no universal expiry period is assumed.",
            "This evaluator verifies emitted interpretations. It does not prove that the Delta Engine detected every meaningful change.",
        ],
    }


def _medication_change_is_explicit(item: dict[str, Any]) -> bool:
    """Require source wording that actually describes a medication change.

    A medication name/dose appearing in a historical list is not evidence that
    the medicine was started, stopped, or changed. The model's change_type label
    alone is never sufficient authority to promote a current medication state.
    """
    change_type = _normalise(item.get("change_type"))
    excerpt = _normalise(item.get("evidence_excerpt"))
    markers = {
        "new": (
            "started", "start taking", "prescribed", "initiated", "commenced",
            "began taking", "new prescription", "added to the medication",
            "changed medication to", "medication changed to",
        ),
        "dose_changed": (
            "dose increased", "dose decreased", "increased the dose",
            "decreased the dose", "reduced the dose", "raised the dose",
            "dose changed", "changed the dose",
        ),
        "frequency_changed": (
            "frequency changed", "changed frequency", "changed from once",
            "changed from twice", "increased to twice", "reduced to once",
        ),
        "route_changed": (
            "route changed", "changed route", "switched from oral",
            "changed from oral", "route was changed",
        ),
        "discontinued": (
            "stopped", "discontinued", "held", "ceased", "no longer taking",
            "stop taking", "stop the medication",
        ),
    }
    for marker in markers.get(change_type, ()):
        position = excerpt.find(marker)
        if position < 0:
            continue
        prefix = excerpt[max(0, position - 36):position]
        if re.search(r"\b(?:not|never|no|without)\s+(?:(?:been|being|actually|yet)\s+)?$", prefix):
            continue
        return True
    return False


def _apply_incomplete_baseline_guard(item: dict[str, Any], *, baseline_complete: bool) -> None:
    """Prevent a truncated baseline from producing a confident state comparison."""
    if baseline_complete:
        return
    item["certainty"] = "uncertain"
    item["change_type"] = "uncertain"
    item["baseline_incomplete"] = True
    item["comparison_warning"] = (
        "The prior-state assertion set was truncated. A complete comparison cannot be made."
    )


def _match_finding_evidence(persisted_evidence: list[dict[str, Any]], excerpt: str) -> list[dict[str, Any]]:
    """Match a finding to sufficiently specific text in persisted, person-scoped evidence.

    Very short excerpts (for example, a medication name or "yes") are not
    discriminative enough to establish provenance and must not be treated as
    evidence linkage. This intentionally prefers a missed link over attributing
    an unrelated record to a finding.
    """
    normalized_excerpt = _normalise(excerpt)
    excerpt_tokens = [token for token in normalized_excerpt.split() if any(char.isalnum() for char in token)]
    if len(normalized_excerpt) < 16 or len(excerpt_tokens) < 3:
        return []
    return [
        row for row in persisted_evidence
        if normalized_excerpt in _normalise(str(row.get("text") or ""))
    ]


def _match_source_references(
    excerpt: str,
    *,
    new_content: str,
    source_event_id: str | None,
    active_state: list[dict[str, Any]],
    prior_events: list[Any],
    evidence_rows: list[Any],
) -> list[dict[str, str]]:
    """Return exact-excerpt references only; never attach every run-level ID to every finding."""
    normalized_excerpt = _normalise(excerpt)
    excerpt_tokens = [token for token in normalized_excerpt.split() if any(char.isalnum() for char in token)]
    if len(normalized_excerpt) < 16 or len(excerpt_tokens) < 3:
        return []

    references: list[dict[str, str]] = []

    def add(record_type: str, record_id: Any, source_text: Any) -> None:
        if record_id and normalized_excerpt in _normalise(source_text):
            references.append({
                "record_type": record_type,
                "record_id": str(record_id),
                "match_basis": "exact_normalized_excerpt",
            })

    if source_event_id:
        add("care_event", source_event_id, new_content)
    for assertion in active_state:
        add("care_assertion", assertion.get("assertion_id"), assertion.get("value"))
    for event in prior_events:
        add("care_event", getattr(event, "id", None),
            f"{getattr(event, 'title', '') or ''} {getattr(event, 'description', '') or ''}")
    for evidence in evidence_rows:
        add("evidence", getattr(evidence, "id", None), getattr(evidence, "source_text", None))

    unique: dict[tuple[str, str], dict[str, str]] = {}
    for reference in references:
        unique[(reference["record_type"], reference["record_id"])] = reference
    return list(unique.values())


def _source_content_matches_event(content: str, event: CareEvent) -> bool:
    """Return whether reconciliation input is actually present in its saved source event.

    Daily Capture may send a structured projection of the saved JSON payload, so
    exact text equality is not the only valid case. A projection is accepted only
    when every supplied key/value is present unchanged in the persisted payload.
    """
    incoming = content.strip()
    saved_description = str(event.description or "").strip()
    saved_title = str(event.title or "").strip()
    if incoming and incoming in {saved_description, saved_title}:
        return True

    try:
        incoming_payload = json.loads(incoming)
        saved_payload = json.loads(saved_description)
    except (TypeError, ValueError):
        return False

    if not isinstance(incoming_payload, dict) or not isinstance(saved_payload, dict) or not incoming_payload:
        return False
    return all(
        key in saved_payload and saved_payload[key] == value
        for key, value in incoming_payload.items()
    )


def source_event_snapshot_hash(event: CareEvent) -> str:
    """Fingerprint the exact saved event state used by a Delta assessment."""
    occurred_at = getattr(event, "occurred_at", None)
    snapshot = {
        "id": str(getattr(event, "id", "") or ""),
        "person_id": str(getattr(event, "person_id", "") or ""),
        "created_by_caregiver_id": str(getattr(event, "created_by_caregiver_id", "") or ""),
        "status": str(getattr(event, "status", "") or ""),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "time_provenance": str(getattr(event, "time_provenance", "") or ""),
        "occurred_at": occurred_at.isoformat() if hasattr(occurred_at, "isoformat") else None,
        "title": str(getattr(event, "title", "") or ""),
        "description": str(getattr(event, "description", "") or ""),
        "evidence_ids": sorted(str(value) for value in (getattr(event, "evidence_ids", None) or [])),
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _care_state_revision_snapshot(db: Session, person_id: str) -> str:
    """Fingerprint the append-only revision set, including same-transaction rows."""
    from sqlalchemy import func
    from app.models.care_continuity import CareStateRevision

    latest_revision_time = db.query(func.max(CareStateRevision.changed_at)).filter(
        CareStateRevision.person_id == person_id
    ).scalar()
    revision_count = db.query(func.count(CareStateRevision.id)).filter(
        CareStateRevision.person_id == person_id
    ).scalar() or 0
    return f"{revision_count}:{latest_revision_time.isoformat() if latest_revision_time else 'none'}"


def _delta_iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _delta_excerpt(value: Any, limit: int = 280) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] if text else None


def _resolve_delta_source_refs(db: Session, person_id: str, record_ids: list[str]) -> list[dict[str, Any]]:
    from app.models.care_continuity import CareAssertion, EvidenceObservation

    ids = list(dict.fromkeys(str(value) for value in (record_ids or []) if value))
    if not ids:
        return []
    events = db.query(CareEvent).filter(CareEvent.person_id == person_id, CareEvent.id.in_(ids)).all()
    evidence_rows = db.query(Evidence).filter(Evidence.person_id == person_id, Evidence.id.in_(ids)).all()
    assertions = db.query(CareAssertion).filter(CareAssertion.person_id == person_id, CareAssertion.id.in_(ids)).all()
    observations = db.query(EvidenceObservation).filter(
        EvidenceObservation.person_id == person_id,
        EvidenceObservation.id.in_(ids),
    ).all()
    event_by_id = {str(item.id): item for item in events}
    evidence_by_id = {str(item.id): item for item in evidence_rows}
    assertion_by_id = {str(item.id): item for item in assertions}
    observation_by_id = {str(item.id): item for item in observations}

    # A normalized LCR fact can itself be the evidence reference carried by a
    # Change. Resolve it through the same person-scoped path as events and
    # assertions, then include its explicit source evidence where available.
    from sqlalchemy import MetaData, Table, and_, inspect, select
    connection = db.connection()
    inspector = inspect(connection)
    metadata = MetaData()
    fact_by_id: dict[str, Any] = {}
    fact_evidence_by_id: dict[str, list[dict[str, Any]]] = {}
    if "care_facts" in inspector.get_table_names():
        facts_table = Table("care_facts", metadata, autoload_with=connection)
        fact_rows = db.execute(
            select(facts_table).where(
                facts_table.c.person_id == str(person_id),
                facts_table.c.id.in_(ids),
            )
        ).mappings().all()
        fact_by_id = {str(row["id"]): row for row in fact_rows}
        if fact_by_id and "care_fact_evidence" in inspector.get_table_names() and "evidence" in inspector.get_table_names():
            links_table = Table("care_fact_evidence", metadata, autoload_with=connection)
            source_evidence_table = Table("evidence", metadata, autoload_with=connection)
            if {"person_id", "fact_id", "evidence_id"}.issubset(links_table.c.keys()):
                linked_rows = db.execute(
                    select(
                        links_table.c.fact_id,
                        source_evidence_table.c.id.label("evidence_id"),
                        *(
                            [links_table.c.evidence_role]
                            if "evidence_role" in links_table.c else []
                        ),
                        *(
                            [links_table.c.source_span]
                            if "source_span" in links_table.c else []
                        ),
                    ).select_from(
                        links_table.join(
                            source_evidence_table,
                            and_(
                                links_table.c.evidence_id == source_evidence_table.c.id,
                                source_evidence_table.c.person_id == str(person_id),
                            ),
                        )
                    ).where(
                        links_table.c.person_id == str(person_id),
                        links_table.c.fact_id.in_(list(fact_by_id)),
                    )
                ).mappings().all()
                for linked in linked_rows:
                    fact_evidence_by_id.setdefault(str(linked["fact_id"]), []).append({
                        "evidence_id": str(linked["evidence_id"]),
                        "evidence_role": linked.get("evidence_role"),
                        "source_span": linked.get("source_span"),
                    })

    references: list[dict[str, Any]] = []
    for record_id in ids:
        if record_id in event_by_id:
            item = event_by_id[record_id]
            references.append({
                "record_type": "care_event",
                "record_id": record_id,
                "label": str(item.title or item.event_type or "Care update"),
                "source_excerpt": _delta_excerpt(item.description),
                "occurred_at": _delta_iso(item.occurred_at),
                "recorded_at": _delta_iso(getattr(item, "recorded_at", None)),
            })
        elif record_id in evidence_by_id:
            item = evidence_by_id[record_id]
            references.append({
                "record_type": "evidence",
                "record_id": record_id,
                "label": str(getattr(item, "source_label", None) or getattr(item, "type", None) or "Saved care evidence"),
                "source_excerpt": _delta_excerpt(getattr(item, "source_text", None)),
                "recorded_at": _delta_iso(getattr(item, "uploaded_at", None)),
            })
        elif record_id in assertion_by_id:
            item = assertion_by_id[record_id]
            references.append({
                "record_type": "care_assertion",
                "record_id": record_id,
                "label": f"{item.subject_type} {item.subject_id}".strip(),
                "source_excerpt": _delta_excerpt(item.source_text or item.asserted_value),
                "occurred_at": _delta_iso(item.observed_at),
                "recorded_at": _delta_iso(item.recorded_at),
            })
        elif record_id in observation_by_id:
            item = observation_by_id[record_id]
            references.append({
                "record_type": "evidence_observation",
                "record_id": record_id,
                "label": f"{item.subject_label} — {item.attribute}",
                "source_excerpt": _delta_excerpt(item.source_passage),
                "occurred_at": _delta_iso(item.occurred_at),
                "recorded_at": _delta_iso(item.recorded_at),
            })
        elif record_id in fact_by_id:
            item = fact_by_id[record_id]
            references.append({
                "record_type": "care_fact",
                "record_id": record_id,
                "label": str(item.get("fact_type") or "Care record fact"),
                "source_excerpt": _delta_excerpt(item.get("fact_value")),
                "occurred_at": _delta_iso(item.get("effective_at")),
                "recorded_at": _delta_iso(item.get("recorded_at")),
                "epistemic_status": str(item.get("epistemic_status") or "not_recorded"),
                "source_evidence": fact_evidence_by_id.get(record_id, []),
                "source_evidence_status": (
                    "verified_person_scoped_links"
                    if fact_evidence_by_id.get(record_id)
                    else "no_verified_linked_evidence"
                ),
            })
    return references


def _canonical_change_classification(
    change: Any,
    *,
    evidence_complete: bool,
    significance_assessment: Any | None = None,
) -> tuple[str, bool]:
    """Classify only an evidence-grounded significance candidate.

    Change.confidence is intentionally uncalibrated and is not the significance
    score. The active linked SignificanceAssessment is the only accepted source
    for verdict and significance confidence. A candidate remains a candidate;
    it is never promoted to a confirmed clinical state.
    """
    raw_type = str(getattr(change.change_type, "value", change.change_type) or "").lower()
    baseline = str(getattr(change.baseline_coverage_status, "value", change.baseline_coverage_status) or "").lower()
    current = str(getattr(change.current_coverage_status, "value", change.current_coverage_status) or "").lower()
    coverage_complete = baseline in {"covered", "complete", "full", "sufficient"} and current in {
        "covered", "complete", "full", "sufficient"
    }
    if (
        significance_assessment is None
        or str(getattr(significance_assessment, "target_type", "") or "").casefold() != "change"
        or str(getattr(significance_assessment, "target_id", "") or "") != str(getattr(change, "id", "") or "")
    ):
        return "uncertain", False
    verdict_value = getattr(significance_assessment, "verdict", None)
    verdict = str(getattr(verdict_value, "value", verdict_value) or "").lower()
    try:
        confidence = float(getattr(significance_assessment, "significance_confidence", None))
    except (TypeError, ValueError):
        confidence = 0.0
    candidate_is_supported = (
        evidence_complete
        and coverage_complete
        and verdict == "meaningful_candidate"
        and confidence >= 0.65
    )
    if not candidate_is_supported:
        return "uncertain", False

    # Direction is meaningful only for an explicitly recognized adverse
    # measure. Use exact normalized subject labels, not substring matching:
    # "no_pain", "pain_management", or "mobility_with_falls" must not inherit
    # the polarity of the word "pain" or "falls" by accident.
    def normalize_subject_label(value: Any) -> str:
        return "_".join(
            str(value or "").strip().casefold().replace("-", " ").replace("/", " ").split()
        )

    subject_labels = {
        normalize_subject_label(change.subject_type),
        normalize_subject_label(change.subject_id),
    }
    adverse_domains = {
        "fall", "falls", "fall_count", "fall_frequency",
        "night_waking", "night_wakings", "night_waking_frequency",
        "agitation", "agitation_frequency",
        "pain", "pain_level", "pain_score", "pain_severity",
        "confusion", "confusion_frequency",
        "missed_medication", "missed_dose",
        "shortness_of_breath", "incontinence", "wandering",
        "refusal_to_eat",
    }
    adverse = bool(subject_labels.intersection(adverse_domains))
    categories = {
        "emergence": "new",
        "onset": "new",
        "recovery": "potential_improvement",
        "recurrence": "recurring",
        "persistence": "persistent",
        "cessation": "potential_resolution",
        "disappearance": "potential_resolution",
        "stabilization": "unchanged",
    }
    if raw_type == "increase":
        return ("potential_worsening", True) if adverse else ("uncertain", False)
    if raw_type == "decrease":
        return ("potential_improvement", True) if adverse else ("uncertain", False)
    return categories.get(raw_type, "uncertain"), raw_type in categories


def _reconcile_from_change_intelligence(
    db: Session,
    *,
    person_id: str,
    event_date: datetime | None,
    source: str,
    content: str,
    source_event_id: str | None,
    evidence_ids: list[str],
    created_by_caregiver_id: str | None,
    persist: bool,
    source_event_row: CareEvent | None,
    source_event_snapshot_sha256: str | None,
    history_coverage: dict[str, Any],
    existing_unresolved_care_loops: dict[str, Any],
) -> dict[str, Any]:
    from app.models.change import Change
    from app.models.significance import SignificanceAssessment
    from app.models.care_continuity import CareStateRevision
    from sqlalchemy import func
    from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence

    reference_time = event_date or (source_event_row.occurred_at if source_event_row else None) or datetime.now(timezone.utc)
    source_assertions = []
    if source_event_id:
        source_assertions = db.query(CareAssertion).filter(
            CareAssertion.person_id == person_id,
            CareAssertion.source_event_id == source_event_id,
        ).all()
    elif evidence_ids:
        assertion_candidates = db.query(CareAssertion).filter(
            CareAssertion.person_id == person_id,
        ).order_by(CareAssertion.recorded_at.desc()).all()
        incoming_evidence_ids = {str(value) for value in evidence_ids}
        source_assertions = [
            item for item in assertion_candidates
            if incoming_evidence_ids.intersection(str(value) for value in (item.evidence_ids or []))
        ]
    reconciliation_receipt = None
    if persist:
        reconciliation_receipt = reconcile_person_change_intelligence(
            db,
            person_id,
            reference_time=reference_time,
            protected_assertion_ids=[str(item.id) for item in source_assertions],
        )
        db.flush()

    linked_ids = {str(value) for value in evidence_ids if value}
    if source_event_id:
        linked_ids.add(str(source_event_id))
    linked_ids.update(str(item.id) for item in source_assertions)
    if evidence_ids:
        source_observations = db.query(EvidenceObservation).filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id.in_(evidence_ids),
        ).all()
        linked_ids.update(str(item.id) for item in source_observations)
    if not source_event_id and evidence_ids:
        recent_events = db.query(CareEvent).filter(
            CareEvent.person_id == person_id,
        ).order_by(CareEvent.occurred_at.desc()).all()
        for item in recent_events:
            if linked_ids.intersection(str(value) for value in (item.evidence_ids or [])):
                linked_ids.add(str(item.id))

    total_change_count = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
    ).count()
    candidate_changes = db.query(Change).filter(
        Change.person_id == person_id,
        Change.is_active.is_(True),
    ).order_by(Change.created_at.desc()).all()
    linked_changes = [
        item for item in candidate_changes
        if linked_ids.intersection(str(value) for value in (item.evidence_ids or []))
    ]
    # Include the append-only revision count as well as its timestamp. Multiple
    # revisions created in one transaction can share the same changed_at value.
    care_state_revision_snapshot = _care_state_revision_snapshot(db, person_id)

    def validate_source_snapshot() -> None:
        if source_event_id:
            current_event = db.query(CareEvent).filter(
                CareEvent.id == source_event_id,
                CareEvent.person_id == person_id,
            ).with_for_update().first()
            if (
                current_event is None
                or getattr(current_event, "status", None) in {"invalidated", "deleted"}
                or source_event_snapshot_hash(current_event) != source_event_snapshot_sha256
            ):
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="The saved care update changed during reconciliation. Retry using the latest record.",
                )
        current_evidence = (
            db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).with_for_update().all()
            if evidence_ids else []
        )
        if evidence_ids and (
            {str(item.id) for item in current_evidence} != {str(value) for value in evidence_ids}
            or any(item.evidence_status == "deleted_by_caregiver" for item in current_evidence)
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The source evidence changed during reconciliation. Retry using the latest record.",
            )
        current_revision_snapshot = _care_state_revision_snapshot(db, person_id)
        if current_revision_snapshot != care_state_revision_snapshot:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The saved care history changed during comparison. Retry using the latest care record.",
            )

    if persist:
        existing_runs = db.query(CareDeltaRun).filter(
            CareDeltaRun.person_id == person_id,
            CareDeltaRun.source == source,
            CareDeltaRun.source_event_id == source_event_id,
        ).order_by(CareDeltaRun.created_at.desc()).all()
        for existing_run in existing_runs:
            try:
                saved_result = json.loads(existing_run.delta_json or "{}")
            except (TypeError, ValueError):
                continue
            saved_provenance = saved_result.get("provenance") or {}
            if _delta_run_matches_current_input(
                saved_provenance,
                source_event_snapshot_sha256=source_event_snapshot_sha256,
                care_state_revision_snapshot=care_state_revision_snapshot,
                evidence_ids=evidence_ids,
                input_content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                event_date=reference_time,
            ):
                validate_source_snapshot()
                db.commit()
                saved_provenance["delta_run_id"] = str(existing_run.id)
                return saved_result

    history_coverage = dict(history_coverage)
    history_coverage["change_assessments"] = {
        "available": len(candidate_changes),
        "total": total_change_count,
        "omitted": max(0, total_change_count - len(candidate_changes)),
    }
    history_coverage["complete"] = bool(history_coverage.get("complete")) and total_change_count <= len(candidate_changes)

    computed_changes: list[dict[str, Any]] = []
    claim_items: list[dict[str, Any]] = []
    all_refs: dict[tuple[str, str], dict[str, Any]] = {}
    for change in linked_changes:
        change_evidence_ids = [str(value) for value in (change.evidence_ids or []) if value]
        references = _resolve_delta_source_refs(db, person_id, change_evidence_ids)
        reference_ids = {str(item["record_id"]) for item in references}
        evidence_complete = bool(references) and set(change_evidence_ids).issubset(reference_ids)
        significance_assessment = None
        if change.significance_assessment_id:
            significance_assessment = db.query(SignificanceAssessment).filter(
                SignificanceAssessment.id == change.significance_assessment_id,
                SignificanceAssessment.person_id == person_id,
                SignificanceAssessment.target_type == "change",
                SignificanceAssessment.target_id == str(change.id),
                SignificanceAssessment.is_active.is_(True),
            ).first()
        classification, classification_supported = _canonical_change_classification(
            change,
            evidence_complete=evidence_complete,
            significance_assessment=significance_assessment,
        )
        # This projection is an evidence-grounded candidate, not a caregiver-
        # or professional-confirmed clinical state.
        supported = classification_supported and evidence_complete
        raw_type = str(getattr(change.change_type, "value", change.change_type) or "uncertain")
        name = str(change.subject_id or change.subject_type or "Care information")
        previous = change.previous_state
        current = change.current_state
        if previous is None and change.previous_count is not None:
            previous = str(change.previous_count)
        if current is None and change.current_count is not None:
            current = str(change.current_count)
        item = {
            "domain": str(change.subject_type),
            "name": name,
            "change_type": raw_type,
            "classification": classification,
            "authoritative_change_type": raw_type,
            "authoritative_change_id": str(change.id),
            "previous": previous,
            "current": current,
            "certainty": "candidate" if supported else "uncertain",
            "evidence_supported": evidence_complete,
            "claim_supported": supported,
            "significance_verdict": (
                str(getattr(significance_assessment.verdict, "value", significance_assessment.verdict))
                if significance_assessment is not None else None
            ),
            "significance_confidence": (
                float(significance_assessment.significance_confidence)
                if significance_assessment is not None else None
            ),
            "source_reference_status": "matched" if evidence_complete else "partial",
            "source_record_refs": references,
            "comparison_basis": str(change.explanation or "Compared with the saved personal baseline."),
            "historical_only": False,
            "baseline_coverage_status": change.baseline_coverage_status,
            "current_coverage_status": change.current_coverage_status,
            "attention_candidate": bool(change.attention_candidate),
            "follow_up_candidate": bool(change.follow_up_candidate),
            "confidence": (
                float(significance_assessment.significance_confidence)
                if significance_assessment is not None else None
            ),
        }
        computed_changes.append(item)
        for reference in references:
            all_refs[(reference["record_type"], reference["record_id"])] = reference
        claim_items.append({
            "text": f"{name}: {classification}",
            "source_references": references,
            "support_status": "matched_source_and_significance_candidate" if supported else "insufficient_evidence",
            "authoritative_change_id": str(change.id),
        })

    linked_contradictions = []
    for item in existing_unresolved_care_loops.get("contradictions", []):
        contradiction_ids = [str(value) for value in (item.get("evidence_ids") or []) if value]
        if not linked_ids.intersection(contradiction_ids):
            continue
        references = _resolve_delta_source_refs(db, person_id, contradiction_ids)
        if not references:
            continue
        resolved_reference_ids = {str(ref["record_id"]) for ref in references}
        evidence_complete = set(contradiction_ids).issubset(resolved_reference_ids)
        conflict_supported = evidence_complete
        linked_contradictions.append({
            "topic": str(item.get("subject_id") or item.get("field") or "Conflicting reports"),
            "reports": [str(ref.get("source_excerpt") or ref.get("label") or "Saved report") for ref in references],
            "why_conflicting": str(item.get("description") or "The saved reports differ."),
            "source_record_refs": references,
            "source_reference_status": "matched" if evidence_complete else "partial",
        })
        for reference in references:
            all_refs[(reference["record_type"], reference["record_id"])] = reference
        claim_items.append({
            "text": f"Conflicting reports: {item.get('subject_id') or item.get('field') or 'care information'}",
            "source_references": references,
            "support_status": "matched_competing_sources" if conflict_supported else "insufficient_evidence",
            "authoritative_contradiction_id": str(item.get("id")),
        })
        computed_changes.append({
            "domain": str(item.get("subject_type") or "care"),
            "name": str(item.get("subject_id") or item.get("field") or "Conflicting reports"),
            "change_type": "conflicting",
            "classification": "conflicting" if conflict_supported else "uncertain",
            "certainty": "supported" if conflict_supported else "uncertain",
            "evidence_supported": evidence_complete,
            "claim_supported": conflict_supported,
            "source_reference_status": "matched" if evidence_complete else "partial",
            "source_record_refs": references,
            "comparison_basis": str(item.get("description") or "Saved reports differ."),
            "historical_only": False,
            "authoritative_contradiction_id": str(item.get("id")),
        })

    clinical_gaps = []
    for item in existing_unresolved_care_loops.get("gaps", []):
        ids = [str(value) for value in (item.get("evidence_ids") or []) if value]
        if not linked_ids.intersection(ids):
            continue
        references = _resolve_delta_source_refs(db, person_id, ids)
        clinical_gaps.append({
            "subject": f"{item.get('subject_type') or ''} {item.get('subject_id') or ''}".strip(),
            "description": str(item.get("description") or "An open care question needs follow-up."),
            "source_record_refs": references,
        })
        for reference in references:
            all_refs[(reference["record_type"], reference["record_id"])] = reference

    follow_ups = []
    for item in existing_unresolved_care_loops.get("questions", []):
        ids = [str(value) for value in (item.get("evidence_ids") or []) if value]
        if not linked_ids.intersection(ids):
            continue
        references = _resolve_delta_source_refs(db, person_id, ids)
        follow_ups.append({
            "question": str(item.get("question") or "A care question remains open."),
            "status": str(item.get("status") or "open"),
            "source_record_refs": references,
        })
        for reference in references:
            all_refs[(reference["record_type"], reference["record_id"])] = reference

    supported_count = sum(1 for item in computed_changes if item.get("certainty") == "supported")
    if supported_count == 1:
        caregiver_summary = "One care change is supported by saved records."
    elif supported_count > 1:
        caregiver_summary = f"{supported_count} care changes are supported by saved records."
    elif computed_changes:
        caregiver_summary = "Some comparisons need more information before they can be confirmed."
    elif history_coverage.get("complete"):
        caregiver_summary = "The saved records do not establish a change for this update."
    else:
        caregiver_summary = "There is not enough earlier information to confirm a change."
    event_summary = str(source_event_row.title if source_event_row and source_event_row.title else "Daily care update")
    result: dict[str, Any] = {
        "event_summary": event_summary,
        "medications": [],
        "conditions": [],
        "symptoms": [],
        "tests": [],
        "follow_ups": follow_ups,
        "contradictions": linked_contradictions,
        "clinical_gaps": clinical_gaps,
        "caregiver_summary": caregiver_summary,
        "clinician_summary": "Deterministic comparison of persisted care-change assessments; no cause inferred.",
        "computed_changes": computed_changes,
        "claim_provenance": {"claims": claim_items},
        "source_record_refs": list(all_refs.values()),
        "history_coverage": history_coverage,
        "uncertainty_assessments": [
            {
                "subject_type": str(item.get("domain") or "care"),
                "subject_id": str(item.get("name") or "care information"),
                "analysis": str(item.get("comparison_basis") or "The available evidence is incomplete."),
                "evidence_ids": [str(ref["record_id"]) for ref in item.get("source_record_refs", [])],
            }
            for item in computed_changes if item.get("certainty") != "supported"
        ],
        "provenance": {
            "person_id": person_id,
            "created_by_caregiver_id": created_by_caregiver_id,
            "source": source,
            "event_date": reference_time.isoformat(),
            "source_event_id": source_event_id,
            "evidence_ids": evidence_ids,
            "input_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "source_event_snapshot_sha256": source_event_snapshot_sha256,
            "care_state_revision_snapshot": care_state_revision_snapshot,
            "engine_version": DELTA_ENGINE_VERSION,
            "model_provider": "deterministic_change_intelligence",
            "model": "none",
            "persisted": persist,
            "change_reconciliation_status": "completed" if persist else "not_run",
            "authoritative_change_ids": [str(item.id) for item in linked_changes],
            "current_state_authority": "persisted SolenOS change records; no medical conclusion or causal explanation inferred",
        },
    }

    # The default production path is the persisted Change Intelligence
    # reconciliation, not the optional model-enrichment path. Grade the
    # actual caregiver-facing result here as well; otherwise the evaluator
    # would exist in code but be bypassed by the default API workflow.
    result["interpretation_evaluation"] = evaluate_longitudinal_interpretations(
        computed_changes,
        db=db,
        person_id=person_id,
        history_coverage=history_coverage,
    )

    if persist:
        validate_source_snapshot()
        run = CareDeltaRun(
            person_id=person_id,
            source=source,
            event_date=reference_time,
            source_event_id=source_event_id,
            evidence_ids=evidence_ids,
            delta_json=json.dumps(result, ensure_ascii=False, sort_keys=True, default=str),
            model_name="deterministic-change-intelligence",
            created_by_caregiver_id=created_by_caregiver_id,
        )
        db.add(run)
        db.flush()
        result["provenance"]["delta_run_id"] = str(run.id)
        run.delta_json = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        db.commit()
    return result


def _event_time_for_reconciliation(
    event_date: datetime | None,
    source_event_row: CareEvent | None,
) -> datetime:
    """Prefer the saved event time over processing time when callers omit it."""
    if event_date is not None:
        return event_date
    persisted_event_time = getattr(source_event_row, "occurred_at", None) if source_event_row else None
    return persisted_event_time or datetime.now(timezone.utc)


def reconcile_event(db: Session, *, person_id: str, event_date: datetime | None, source: str,
                    content: str, source_event_id: str | None = None,
                    evidence_ids: list[str] | None = None, persist: bool = True,
                    created_by_caregiver_id: str | None = None,
                    model_enrichment: bool = False) -> dict[str, Any]:
    if not content.strip():
        raise HTTPException(status_code=422, detail="Care event content is required.")
    if len(content) > 100_000:
        raise HTTPException(status_code=413, detail="Care event content exceeds the 100,000 character limit.")
    evidence_ids = list(dict.fromkeys(evidence_ids or []))
    if evidence_ids:
        rows = db.query(Evidence.id).filter(Evidence.person_id == person_id, Evidence.id.in_(evidence_ids)).all()
        if {row[0] for row in rows} != set(evidence_ids):
            raise HTTPException(status_code=422, detail="Every evidence ID must belong to this care record.")
    source_event_row = None
    source_event_snapshot_sha256 = None
    if source_event_id:
        source_event_row = db.query(CareEvent).filter(
            CareEvent.id == source_event_id,
            CareEvent.person_id == person_id,
        ).first()
        if source_event_row is None:
            raise HTTPException(status_code=422, detail="source_event_id must belong to this care record.")
        if getattr(source_event_row, "status", None) in {"invalidated", "deleted"}:
            raise HTTPException(status_code=409, detail="The saved care update is no longer active.")
        if not _source_content_matches_event(content, source_event_row):
            raise HTTPException(
                status_code=422,
                detail="The reconciliation text does not match the saved care update.",
            )
        source_event_snapshot_sha256 = source_event_snapshot_hash(source_event_row)

    effective_event_date = _event_time_for_reconciliation(event_date, source_event_row)

    active_assertions_query = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.assertion_kind.in_(["current_state", "time_bound_observation"]),
        CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
    )
    if source_event_id:
        # Exclude assertions created by the incoming Daily Capture event itself;
        # otherwise the "before" state contains the new values and hides the delta.
        active_assertions_query = active_assertions_query.filter(
            or_(
                CareAssertion.source_event_id.is_(None),
                or_(CareAssertion.source_event_id.is_(None), CareAssertion.source_event_id != source_event_id),
            )
        )
    active_assertions_total = active_assertions_query.count()
    active_assertions = active_assertions_query.order_by(CareAssertion.recorded_at.desc()).limit(250).all()
    active_state = [{
        "assertion_id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
        "field": item.field, "value": item.asserted_value, "source": item.source_label,
        "assertion_kind": item.assertion_kind,
        "epistemic_state": item.epistemic_state or "reported",
        "observed_at": item.observed_at.isoformat() if item.observed_at else None,
        "status": item.assertion_status, "evidence_ids": item.evidence_ids or [],
        "source_context": _safe_assertion_source_context(getattr(item, "source_context", None)),
    } for item in active_assertions]

    # Include the existing LCR's real source material as context too. Many older
    # records predate normalized assertions; they are evidence, not automatically
    # promoted into verified current-state facts.
    prior_events_query = db.query(CareEvent).filter(CareEvent.person_id == person_id)
    if source_event_id:
        # Daily Capture may already have saved the event before invoking Delta.
        # Do not let the event being reconciled masquerade as historical baseline.
        prior_events_query = prior_events_query.filter(CareEvent.id != source_event_id)
    prior_events_total = prior_events_query.count()
    prior_events = prior_events_query.order_by(CareEvent.occurred_at.desc()).limit(25).all()
    prior_evidence_query = db.query(Evidence).filter(Evidence.person_id == person_id)
    if evidence_ids:
        # These evidence rows belong to the incoming event; the new-event text is
        # supplied separately and must not be counted again as prior evidence.
        prior_evidence_query = prior_evidence_query.filter(~Evidence.id.in_(evidence_ids))
    prior_evidence_total = prior_evidence_query.count()
    prior_evidence = prior_evidence_query.order_by(Evidence.uploaded_at.desc()).limit(25).all()
    existing_record_context = {
        "events": [{
            "event_id": item.id, "date": item.occurred_at.isoformat() if item.occurred_at else None,
            "type": item.event_type, "title": item.title, "description": item.description,
            "evidence_ids": item.evidence_ids or [],
        } for item in prior_events],
        "evidence": [{
            "evidence_id": item.id, "date": item.uploaded_at.isoformat() if item.uploaded_at else None,
            "type": item.type, "source_text": (item.source_text or "")[:1500],
        } for item in prior_evidence],
    }
    open_gaps_query = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([
            GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED,
            GapLifecycleStatus.PARTIALLY_RESOLVED,
        ]),
    )
    open_gaps_total = open_gaps_query.count()
    open_gaps = open_gaps_query.order_by(InformationGap.created_at.desc()).limit(50).all()
    open_questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id, OpenQuestion.status == "open",
        OpenQuestion.necessity == "necessary",
    ).order_by(OpenQuestion.asked_at.desc()).all()
    open_questions = [
        question for question in open_questions
        if has_valid_caregiver_necessity(question, db, person_id)
    ]
    open_questions_total = len(open_questions)
    open_questions = open_questions[:50]
    unresolved_contradictions_query = db.query(Contradiction).filter(
        Contradiction.person_id == person_id,
        Contradiction.resolution_status.in_(["unresolved", "qualified"]),
    )
    unresolved_contradictions_total = unresolved_contradictions_query.count()
    unresolved_contradictions = unresolved_contradictions_query.order_by(Contradiction.created_at.desc()).limit(50).all()
    existing_unresolved_care_loops = {
        "gaps": [{
            "id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
            "field": item.field, "status": item.lifecycle_status.value if hasattr(item.lifecycle_status, "value") else str(item.lifecycle_status),
            "description": item.description, "expected_by": item.expected_by.isoformat() if item.expected_by else None,
            "evidence_ids": item.evidence_ids or [],
        } for item in open_gaps],
        "questions": [{
            "id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
            "question": item.question, "status": item.status, "priority": item.priority.value if hasattr(item.priority, "value") else str(item.priority),
            "intended_answerer_role": item.intended_answerer_role or "not_known",
            "intended_answerer_label": item.intended_answerer_label,
            "necessity_rationale": item.necessity_rationale,
            "evidence_ids": item.evidence_ids or [],
        } for item in open_questions],
        "contradictions": [{
            "id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
            "field": item.field, "description": item.description, "status": item.resolution_status,
            "evidence_ids": item.evidence_ids or [],
        } for item in unresolved_contradictions],
    }

    history_coverage = {
        "active_assertions": {"available": len(active_state), "total": active_assertions_total, "omitted": max(0, active_assertions_total - len(active_state))},
        "prior_events": {"available": len(prior_events), "total": prior_events_total, "omitted": max(0, prior_events_total - len(prior_events))},
        "prior_evidence": {"available": len(prior_evidence), "total": prior_evidence_total, "omitted": max(0, prior_evidence_total - len(prior_evidence))},
        "open_gaps": {"available": len(open_gaps), "total": open_gaps_total, "omitted": max(0, open_gaps_total - len(open_gaps))},
        "open_questions": {"available": len(open_questions), "total": open_questions_total, "omitted": max(0, open_questions_total - len(open_questions))},
        "unresolved_contradictions": {"available": len(unresolved_contradictions), "total": unresolved_contradictions_total, "omitted": max(0, unresolved_contradictions_total - len(unresolved_contradictions))},
    }
    history_coverage["complete"] = all(
        category["omitted"] == 0 for category in history_coverage.values()
    )

    if not model_enrichment:
        return _reconcile_from_change_intelligence(
            db,
            person_id=person_id,
            event_date=event_date,
            source=source,
            content=content,
            source_event_id=source_event_id,
            evidence_ids=evidence_ids,
            created_by_caregiver_id=created_by_caregiver_id,
            persist=persist,
            source_event_row=source_event_row,
            source_event_snapshot_sha256=source_event_snapshot_sha256,
            history_coverage=history_coverage,
            existing_unresolved_care_loops=existing_unresolved_care_loops,
        )
    base_url, model, timeout = _local_ollama_url()
    system_prompt = (
        "You are the SolenOS Delta Engine, a conservative longitudinal care-information reconciliation component. "
        "Compare NEW_CARE_EVENT against EXISTING_ACTIVE_STATE, EXISTING_RECORD_CONTEXT, and EXISTING_UNRESOLVED_CARE_LOOPS; do not summarize the event in isolation. HISTORY_COVERAGE identifies records omitted by context bounds. If any category is incomplete, never claim omitted history contains no matching event, never label a state unchanged solely because no match appears in the supplied subset, and mark conclusions that depend on absence from omitted history as uncertain. A bounded context is not a complete care history. EXISTING_RECORD_CONTEXT contains prior persisted events/documents and is historical source material, not automatically verified current state. EXISTING_UNRESOLVED_CARE_LOOPS contains previously recorded gaps, questions, and unresolved contradictions that must be reconciled with the new event. Do not silently close a loop because the new event omits it; do not mark a loop verified complete based only on model inference. "
        "Return only one compact JSON object matching the supplied schema. Do not emit markdown or extra keys. "
        "Never invent a diagnosis, medication order, date, test result, follow-up completion, or causal explanation. "
        "Distinguish new information from a meaningful state change. Use unchanged only when the source supports it. "
        "A mention is not an instruction to start/stop/change a medicine unless the source clearly says so. "
        "Do not assume absence from this event means discontinued. Keep competing reports visible; never silently choose a winner. "
        "Mark uncertainty explicitly. A test ordered without a result present in the provided record is an open loop, not a negative result. "
        "For symptoms, compare only when comparable prior observations exist; do not infer trends from incomparable reports. "
        "Clinical gaps and follow-ups must be grounded in supplied text/state. caregiver_summary uses clear plain language; clinician_summary preserves exact clinical terms. "
        "Do not diagnose, recommend treatment, or replace a clinician. Evidence excerpts must be short verbatim snippets from NEW_CARE_EVENT, EXISTING_ACTIVE_STATE, or EXISTING_RECORD_CONTEXT. When citing historical context, do not claim it is current unless its date/status supports that interpretation. "
        "Keep event_summary, caregiver_summary, and clinician_summary to at most 10 words each. Keep evidence excerpts to the shortest exact quote that proves the finding. " +
        "Return up to five distinct findings in each array when each is independently supported; return an empty array when none is supported. Do not collapse multiple medications, symptoms, tests, or follow-ups into one finding. Never repeat one fact across arrays. "
        "Do not speculate about medical risks or possible causes. An undocumented cause is unknown, not evidence of a risk. "
        "Do not create both a follow-up and a clinical gap for the same missing information; do not create either unless the source identifies a concrete unanswered care requirement or explicitly requests follow-up. "
        "A meal or fluid-intake observation is not itself a diagnosis or symptom: do not infer reduced appetite, dehydration, illness, or a cause from an amount eaten or drunk. "
        "Never duplicate one observation as both a condition and a symptom; preserve the source observation in the event summary unless a distinct, explicitly documented clinical finding exists."
    )
    user_payload = {
        "history_coverage": history_coverage,
        "existing_active_state": active_state,
        "existing_record_context": existing_record_context,
        "existing_unresolved_care_loops": existing_unresolved_care_loops,
        "new_care_event": {
            "date": effective_event_date.isoformat(),
            "source": source,
            "content": content,
            "evidence_ids": evidence_ids,
            "source_event_id": source_event_id,
        },
    }
    try:
        result = _request_delta_json(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            timeout=timeout,
        )
    except HTTPException as exc:
        if exc.status_code == 504:
            raise HTTPException(status_code=504, detail="The local SolenOS model timed out. The care event remains unchanged.") from exc
        if exc.status_code == 503:
            raise HTTPException(status_code=503, detail="The local SolenOS model could not complete reconciliation. No fallback model was used and no delta was persisted.") from exc
        raise


    # Medication-list mentions are not prescribing instructions. Fail closed
    # unless the quoted source itself explicitly describes the requested change.
    for medication in result.get("medications", []):
        change_type = _normalise(medication.get("change_type"))
        if change_type in {"new", "dose_changed", "frequency_changed", "route_changed", "discontinued"}:
            if not _medication_change_is_explicit(medication):
                medication["change_type"] = "uncertain"
                medication["certainty"] = "uncertain"
                medication["medication_mention_only"] = True
                medication["promotion_warning"] = (
                    "The source mentions a medication but does not explicitly document "
                    "a start, stop, or change. It was not promoted as current medication state."
                )

    # Verify both the quotation and the claim before allowing a finding to
    # become a normalized assertion. A real quote alone must not legitimize an
    # unrelated or invented value.
    context_text = [str(item.get("title") or "") + " " + str(item.get("description") or "") for item in existing_record_context["events"]]
    context_text += [str(item.get("source_text") or "") for item in existing_record_context["evidence"]]
    source_corpus = "\n".join([content] + [str(item.get("value", "")) for item in active_state] + context_text).casefold()
    active_claim_corpus = "\n".join([content] + [str(item.get("value", "")) for item in active_state]).casefold()
    for domain in ("medications", "conditions", "symptoms", "tests", "follow_ups", "clinical_gaps"):
        for item in result[domain]:
            excerpt = str(item.get("evidence_excerpt") or "").strip()
            item["evidence_supported"] = bool(excerpt and excerpt.casefold() in source_corpus)
            if domain in {"medications", "conditions", "symptoms"}:
                name = str(item.get("name") or "").strip().casefold()
                current = str(item.get("current") or "").strip().casefold()
                change_type = str(item.get("change_type") or "").casefold()
                claim_supported = bool(name and name in active_claim_corpus)
                if current:
                    if domain == "medications" and change_type == "discontinued":
                        claim_supported = claim_supported and any(
                            marker in active_claim_corpus
                            for marker in ("stop ", "stopped ", "discontinue", "discontinued", "hold ", "held ", "no longer", "cease")
                        )
                    else:
                        claim_supported = claim_supported and current in active_claim_corpus
                elif domain == "medications" and change_type == "discontinued":
                    claim_supported = claim_supported and any(
                        marker in active_claim_corpus
                        for marker in ("stop ", "stopped ", "discontinue", "discontinued", "hold ", "held ", "no longer", "cease")
                    )
                item["claim_supported"] = claim_supported
                if not claim_supported:
                    item["certainty"] = "uncertain"
                    item["provenance_warning"] = "The reported value or state change could not be independently matched to the new event or current-state evidence."
            if not item["evidence_supported"]:
                item["provenance_warning"] = "Evidence excerpt could not be verified against supplied source text."
                if domain in {"medications", "conditions", "symptoms"}:
                    item["certainty"] = "uncertain"
    for item in result["contradictions"]:
        excerpts = item.get("evidence_excerpts") or []
        item["evidence_supported"] = bool(excerpts) and all(
            isinstance(excerpt, str) and excerpt.strip() and excerpt.casefold() in source_corpus
            for excerpt in excerpts
        )
        if not item["evidence_supported"]:
            item["provenance_warning"] = "Conflicting-source excerpts require human verification."

    # Add a deterministic comparison annotation without letting a model's label
    # overwrite contradictory source reports or assert that an item is verified.
    computed = []
    for domain in ("medications", "conditions", "symptoms"):
        expected_subject_type = {"medications": "medication", "conditions": "condition", "symptoms": "symptom"}[domain]
        for item in result[domain]:
            name = str(item.get("name", "")).strip()
            if not name:
                item["change_type"] = "uncertain"
                item["certainty"] = "uncertain"
                continue
            change_type = str(item.get("change_type") or "").casefold()
            if domain == "medications" and change_type in {"dose_changed", "frequency_changed", "route_changed"}:
                expected_field = {"dose_changed": "dose", "frequency_changed": "frequency", "route_changed": "route"}[change_type]
            else:
                expected_field = {"medications": "medication_reported_state", "conditions": "condition_reported_state", "symptoms": "symptom_reported_state"}[domain]
            # Preserve the semantic field before the display label is normalized
            # from dose_changed/frequency_changed into the generic "changed".
            item.setdefault("field", expected_field)
            candidates = [
                old for old in active_state
                if _normalise(old["subject_type"]) == expected_subject_type
                and _normalise(old["subject_id"]) == _normalise(name)
                and (domain == "symptoms" or _normalise(old["field"]) == expected_field)
                and (domain != "symptoms" or old.get("assertion_kind") == "time_bound_observation")
            ]
            # A symptom trend compares with the most recent observation, not all
            # historical observations (which may legitimately differ over time).
            if domain == "symptoms" and candidates:
                candidates.sort(key=lambda old: str(old.get("observed_at") or ""), reverse=True)
                candidates = candidates[:1]
            prior_values = list(dict.fromkeys(old["value"] for old in candidates))
            item["existing_assertions"] = [old["assertion_id"] for old in candidates]
            distinct_prior = list(dict.fromkeys(_normalise(value) for value in prior_values))
            _apply_incomplete_baseline_guard(
                item,
                baseline_complete=history_coverage["complete"],
            )
            if item.get("baseline_incomplete"):
                # Do not let the bounded in-memory subset overrule the guard.
                computed.append({
                    "domain": domain,
                    "name": name,
                    "field": item.get("field", expected_field),
                    "change_type": "uncertain",
                    "previous": item.get("previous"),
                    "current": item.get("current"),
                    "certainty": "uncertain",
                    "evidence_supported": item.get("evidence_supported", False),
                    "claim_supported": item.get("claim_supported"),
                    "source_record_refs": list(item.get("source_record_refs") or []),
                    "comparison_basis": "Incomplete prior assertion coverage; no reliable state comparison.",
                    "baseline_assertion_ids": list(item.get("existing_assertions") or []),
                    "baseline_values": list(item.get("prior_state_candidates") or []),
                })
                continue
            if domain != "symptoms" and len(distinct_prior) > 1:
                item["change_type"] = "conflicting"
                item["certainty"] = "uncertain"
                item["prior_state_candidates"] = prior_values
                item["comparison_warning"] = "Existing assertions for this care-state field disagree; no prior value was selected."
            elif prior_values and item.get("current") is not None and _normalise(item.get("current")) == distinct_prior[0]:
                item["change_type"] = "unchanged"
                item["previous"] = prior_values[0]
            elif prior_values and item.get("current") is not None:
                if change_type not in {"discontinued", "conflicting", "uncertain"}:
                    item["change_type"] = "changed" if domain != "symptoms" else item.get("change_type", "changed")
                item["previous"] = prior_values[0]
                item["prior_state_candidates"] = prior_values
            else:
                item["comparison_basis"] = "No matching normalized prior assertion; historical source context is provided separately and is not promoted to verified state."
                if not item.get("change_type"):
                    item["change_type"] = "uncertain"
                    item["certainty"] = "uncertain"
            computed.append({
                "domain": domain,
                "name": name,
                "field": item.get("field", expected_field),
                "change_type": item.get("change_type"),
                "previous": item.get("previous"),
                "current": item.get("current"),
                "certainty": item.get("certainty", "uncertain"),
                "evidence_supported": item.get("evidence_supported", False),
                "claim_supported": item.get("claim_supported"),
                "source_record_refs": list(item.get("source_record_refs") or []),
                "comparison_basis": item.get("comparison_basis"),
                "baseline_assertion_ids": list(item.get("existing_assertions") or []),
                "baseline_values": list(item.get("prior_state_candidates") or ([item.get("previous")] if item.get("previous") is not None else [])),
            })

    # Historical imports may explain what was true then, but must not regress
    # the active state if a later-dated assertion already exists. Annotate every
    # affected finding before persistence gating so the UI still explains why.
    if event_date is not None:
        incoming_day = event_date.date()
        for domain in ("medications", "conditions"):
            subject_type = {"medications": "medication", "conditions": "condition"}[domain]
            for item in result[domain]:
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                change_type = str(item.get("change_type") or "").casefold()
                field = str(item.get("field") or {
                    "dose_changed": "dose", "frequency_changed": "frequency",
                    "route_changed": "route",
                }.get(change_type, "medication_reported_state" if domain == "medications" else "condition_reported_state"))
                subject_id = name.casefold() if domain == "medications" else name
                if any(
                    peer.assertion_kind == "current_state"
                    and peer.subject_type == subject_type
                    and _normalise(peer.subject_id) == _normalise(subject_id)
                    and peer.field == field
                    and peer.observed_at is not None
                    and peer.observed_at.date() > incoming_day
                    and peer.assertion_status in {"reported", "review_required", "accepted"}
                    for peer in active_assertions
                ):
                    item["historical_only"] = True
                    item["promotion_warning"] = "This event predates a later-dated care-state report. It is preserved in reconciliation history but not promoted as current state."
                    for computed_item in computed:
                        if computed_item.get("domain") == domain and _normalise(computed_item.get("name")) == _normalise(name):
                            computed_item["historical_only"] = True

    # Use exact, person-scoped source records for loop provenance. A run-level
    # evidence ID is not automatically support for every model-generated finding.
    source_evidence_rows = list(prior_evidence)
    if evidence_ids:
        source_evidence_rows.extend(db.query(Evidence).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(evidence_ids),
        ).all())
    source_evidence_rows = list({str(row.id): row for row in source_evidence_rows}.values())
    source_evidence_by_id = {str(row.id): row for row in source_evidence_rows}
    prior_event_by_id = {str(row.id): row for row in prior_events}
    active_assertion_by_id = {
        str(item.get("assertion_id")): item
        for item in active_state if item.get("assertion_id")
    }
    caregiver_ids = {
        str(value)
        for value in (
            getattr(source_event_row, "created_by_caregiver_id", None),
            *[getattr(item, "created_by_caregiver_id", None) for item in prior_events],
            *[getattr(item, "uploaded_by_caregiver_id", None) for item in source_evidence_rows],
            *[getattr(item, "reported_by_caregiver_id", None) for item in source_evidence_rows],
        )
        if value
    }
    caregiver_names = {
        str(item.id): item.name
        for item in db.query(Caregiver).filter(
            Caregiver.person_id == person_id,
            Caregiver.id.in_(caregiver_ids or {"__no_delta_caregiver__"}),
        ).all()
    }

    def enrich_delta_source_references(references: list[dict[str, str]]) -> list[dict[str, str]]:
        enriched = []
        for reference in references:
            record_type = reference.get("record_type")
            record_id = str(reference.get("record_id") or "")
            item = dict(reference)
            if record_type == "care_event":
                event = source_event_row if source_event_row and str(source_event_row.id) == record_id else prior_event_by_id.get(record_id)
                if event is not None:
                    item.update({
                        "label": event.title or "Care update",
                        "source_excerpt": " — ".join(part for part in (event.title or "", event.description or "") if part)[:700],
                        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                        "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
                        "entered_by": caregiver_names.get(str(event.created_by_caregiver_id or "")),
                    })
                elif record_id == str(source_event_id or ""):
                    item.update({
                        "label": "New care update",
                        "source_excerpt": content[:700],
                        "occurred_at": event_date.isoformat() if event_date else None,
                    })
            elif record_type == "care_assertion":
                assertion = active_assertion_by_id.get(record_id)
                if assertion:
                    item.update({
                        "label": str(assertion.get("field") or "Care record detail"),
                        "source_excerpt": str(assertion.get("value") or "")[:700],
                        "occurred_at": assertion.get("observed_at"),
                        "recorded_at": assertion.get("recorded_at"),
                    })
            elif record_type == "evidence":
                evidence = source_evidence_by_id.get(record_id)
                if evidence:
                    metadata = evidence.extra_metadata or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except (TypeError, ValueError):
                            metadata = {}
                    item.update({
                        "label": (metadata.get("filename") if isinstance(metadata, dict) else None) or "Original note or document",
                        "source_excerpt": (evidence.source_text or "")[:700],
                        "occurred_at": evidence.occurred_at.isoformat() if evidence.occurred_at else None,
                        "recorded_at": evidence.ingested_at.isoformat() if evidence.ingested_at else (evidence.uploaded_at.isoformat() if evidence.uploaded_at else None),
                        "reported_by": evidence.reported_by_label or evidence.original_author or caregiver_names.get(str(evidence.reported_by_caregiver_id or "")),
                        "entered_by": caregiver_names.get(str(evidence.uploaded_by_caregiver_id or "")),
                    })
            enriched.append(item)
        return enriched

    persisted_evidence = [
        evidence_record_to_uncertainty_item(row)
        for row in source_evidence_rows
    ]

    delta_claims = []
    for domain in ("medications", "conditions", "symptoms", "tests", "follow_ups", "clinical_gaps"):
        for finding_index, item in enumerate(result.get(domain, [])):
            excerpt = str(item.get("evidence_excerpt") or "").strip()
            references = enrich_delta_source_references(_match_source_references(
                excerpt,
                new_content=content,
                source_event_id=source_event_id,
                active_state=active_state,
                prior_events=prior_events,
                evidence_rows=source_evidence_rows,
            ))
            item["source_record_refs"] = references
            item["source_reference_status"] = "matched" if references else "not_matched"
            if not references:
                item["evidence_supported"] = False
                item["provenance_warning"] = (
                    "This finding has no source record that matches its quoted passage. "
                    "It must not be treated as supported."
                )
                if domain in {"medications", "conditions", "symptoms"}:
                    item["claim_supported"] = False
                    item["certainty"] = "uncertain"
            claim_text = " — ".join(
                str(value).strip()
                for value in (
                    item.get("name") or item.get("action") or item.get("question") or item.get("description"),
                    item.get("current"),
                    item.get("status"),
                )
                if value is not None and str(value).strip()
            )
            claim_supported = bool(item.get("evidence_supported")) and (
                item.get("claim_supported") is not False
            ) and bool(references)
            delta_claims.append({
                "claim_id": f"{domain}-{finding_index + 1}",
                "claim_type": domain,
                "text": claim_text or excerpt,
                "support_status": "matched_source_and_state" if claim_supported else "not_matched_to_source",
                "validation_basis": "exact_source_excerpt_and_deterministic_state_checks" if claim_supported else "source_reference_or_state_check_failed",
                "source_references": references,
            })

    for contradiction_index, item in enumerate(result.get("contradictions", [])):
        excerpts = item.get("evidence_excerpts") or []
        references = []
        every_excerpt_linked = bool(excerpts)
        for excerpt in excerpts:
            excerpt_refs = enrich_delta_source_references(_match_source_references(
                str(excerpt),
                new_content=content,
                source_event_id=source_event_id,
                active_state=active_state,
                prior_events=prior_events,
                evidence_rows=source_evidence_rows,
            ))
            if not excerpt_refs:
                every_excerpt_linked = False
            references.extend(excerpt_refs)
        unique_refs = {
            (ref["record_type"], ref["record_id"]): ref for ref in references
        }
        item["source_record_refs"] = list(unique_refs.values())
        item["source_reference_status"] = "matched" if every_excerpt_linked and unique_refs else "not_matched"
        if not every_excerpt_linked or not unique_refs:
            item["evidence_supported"] = False
            item["provenance_warning"] = "Every side of this disagreement must link to its own saved source. One or more source links could not be verified."
        delta_claims.append({
            "claim_id": f"contradictions-{contradiction_index + 1}",
            "claim_type": "contradiction",
            "text": str(item.get("topic") or item.get("why_conflicting") or "Conflicting care reports"),
            "support_status": "matched_competing_sources" if item.get("evidence_supported") and item.get("source_reference_status") == "matched" else "not_matched_to_all_sources",
            "validation_basis": "each_competing_excerpt_matched_to_its_source",
            "source_references": item["source_record_refs"],
        })

    # Keep the deterministic comparison projection in sync with the claim-level
    # source verification performed above. computed_changes is consumed by the
    # caregiver UI and persisted in CareDeltaRun; leaving its references empty
    # would discard the exact provenance already established for each finding.
    for computed_item in computed:
        domain = str(computed_item.get("domain") or "")
        name = _normalise(computed_item.get("name"))
        matching_items = [
            item for item in result.get(domain, [])
            if _normalise(item.get("name")) == name
        ]
        if len(matching_items) != 1:
            computed_item["certainty"] = "uncertain"
            computed_item["evidence_supported"] = False
            computed_item["claim_supported"] = False
            computed_item["source_record_refs"] = []
            computed_item["provenance_warning"] = (
                "The comparison could not be linked to one unambiguous source finding."
            )
            continue
        source_item = matching_items[0]
        computed_item["source_record_refs"] = list(source_item.get("source_record_refs") or [])
        computed_item["evidence_supported"] = source_item.get("evidence_supported") is True
        computed_item["claim_supported"] = source_item.get("claim_supported")
        computed_item["certainty"] = source_item.get("certainty", "uncertain")
        computed_item["source_reference_status"] = source_item.get("source_reference_status", "not_matched")
        computed_item["historical_only"] = source_item.get("historical_only") is True
        if source_item.get("provenance_warning"):
            computed_item["provenance_warning"] = source_item["provenance_warning"]
        if not computed_item["source_record_refs"]:
            computed_item["certainty"] = "uncertain"
            computed_item["evidence_supported"] = False
            computed_item["claim_supported"] = False

    summary_source_records = []
    if source_event_id:
        summary_source_records.append({
            "record_type": "care_event",
            "record_id": str(source_event_id),
            "label": "New care update",
            "source_text": content,
        })
    summary_source_records.extend({
        "record_type": "care_event",
        "record_id": str(event.id),
        "label": getattr(event, "title", None) or "Earlier care update",
        "source_text": " ".join(part for part in (
            getattr(event, "title", "") or "",
            getattr(event, "description", "") or "",
        ) if part),
        "occurred_at": getattr(event, "occurred_at", None).isoformat() if getattr(event, "occurred_at", None) else None,
        "recorded_at": getattr(event, "recorded_at", None).isoformat() if getattr(event, "recorded_at", None) else None,
    } for event in prior_events)
    summary_source_records.extend({
        "record_type": "care_assertion",
        "record_id": str(item.get("assertion_id")),
        "label": str(item.get("field") or "Care record detail"),
        "source_text": str(item.get("value") or ""),
        "occurred_at": item.get("observed_at"),
    } for item in active_state if item.get("assertion_id"))
    summary_source_records.extend({
        "record_type": "evidence",
        "record_id": str(item.id),
        "label": "Original note or document",
        "source_text": item.source_text or "",
        "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
        "recorded_at": item.ingested_at.isoformat() if item.ingested_at else (item.uploaded_at.isoformat() if item.uploaded_at else None),
    } for item in source_evidence_rows)
    for summary_name in ("event_summary", "caregiver_summary", "clinician_summary"):
        summary_text = str(result.get(summary_name) or "").strip()
        if not summary_text:
            continue
        summary_provenance = build_claim_provenance(summary_text, summary_source_records)
        for summary_index, summary_claim in enumerate(summary_provenance.get("claims", [])):
            summary_status = summary_claim.get("support_status")
            summary_refs = enrich_delta_source_references(summary_claim.get("source_references") or [])
            delta_claims.append({
                "claim_id": f"{summary_name}-{summary_index + 1}",
                "claim_type": "summary",
                "summary_field": summary_name,
                "text": summary_claim.get("text") or summary_text,
                "support_status": "matched_exact_source_wording" if summary_status == "matched_saved_wording" and summary_refs else "historical_only" if summary_status == "matched_historical_only" else "not_matched_to_source",
                "validation_basis": "exact_saved_source_wording" if summary_status == "matched_saved_wording" and summary_refs else "historical_wording_only" if summary_status == "matched_historical_only" else "summary_statement_not_exactly_matched",
                "source_references": summary_refs,
            })

    result["claim_provenance"] = {
        "version": "delta-claim-provenance-v1",
        "coverage": "all_findings_linked" if delta_claims and all(
            item["support_status"] in {"matched_source_and_state", "matched_competing_sources", "matched_exact_source_wording"}
            for item in delta_claims
        ) else "some_findings_unmatched" if delta_claims else "no_findings",
        "claims": delta_claims,
        "interpretation_limit": "The quoted wording is linked to its original note. This does not prove that the information is true, comes from a separate source or explains what caused the change.",
    }

    def loop_provenance(item: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
        references = list(item.get("source_record_refs") or [])
        linked_evidence_ids = list(dict.fromkeys(
            ref["record_id"] for ref in references if ref["record_type"] == "evidence"
        ))
        return linked_evidence_ids, references


    canonical_change_reconciliation = {"status": "not_run", "reason": "persistence_disabled"}

    if persist:
        created_assertion_ids: list[str] = []
        # Model inference runs without holding source-row locks. Reacquire and
        # verify the exact event/evidence snapshot immediately before any
        # derived assertions are written, so a correction or deletion during
        # inference cannot produce a stale persisted comparison.
        if source_event_id:
            current_source_event = db.query(CareEvent).filter(
                CareEvent.id == source_event_id,
                CareEvent.person_id == person_id,
            ).with_for_update().first()
            if (
                current_source_event is None
                or current_source_event.status in {"invalidated", "deleted"}
                or source_event_snapshot_hash(current_source_event) != source_event_snapshot_sha256
            ):
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="The saved care update changed during reconciliation. Retry using the latest record.",
                )
        incoming_evidence_rows = (
            db.query(Evidence).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).with_for_update().all()
            if evidence_ids else []
        )
        if evidence_ids and (
            {str(row.id) for row in incoming_evidence_rows} != {str(value) for value in evidence_ids}
            or any(row.evidence_status == "deleted_by_caregiver" for row in incoming_evidence_rows)
        ):
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="The source evidence changed during reconciliation. Retry using the latest record.",
            )
        try:
            incoming_persisted_evidence = [
                evidence_record_to_uncertainty_item(row)
                for row in incoming_evidence_rows
            ]
            for domain in ("medications", "conditions", "symptoms"):
                for item in result[domain]:
                    name = str(item.get("name", "")).strip()
                    excerpt = str(item.get("evidence_excerpt") or "").strip()
                    matching_finding_evidence = _match_finding_evidence(
                        incoming_persisted_evidence, excerpt
                    )
                    finding_evidence_ids = [
                        str(row["id"]) for row in matching_finding_evidence if row.get("id")
                    ]
                    finding_source_supported = bool(finding_evidence_ids) or bool(
                        source_event_id and excerpt and _normalise(excerpt) in _normalise(content)
                    )
                    current = item.get("current")
                    change_type = str(item.get("change_type") or "").casefold()
                    assertion_kind = "time_bound_observation" if domain == "symptoms" else "current_state"
                    # Unchanged current-state reports must not manufacture another
                    # active-state fact. A symptom observation at a distinct time is
                    # still a historical occurrence and must be retained; exact
                    # same-time duplicates are linked to the existing assertion below.
                    if change_type in {"conflicting", "uncertain"} or item.get("historical_only"):
                        continue
                    if change_type == "unchanged" and assertion_kind != "time_bound_observation":
                        continue
                    if domain == "medications" and change_type == "discontinued" and current is None:
                        current = "discontinued"
                    claim_support_required = assertion_kind != "time_bound_observation"
                    if (
                        not name or current is None
                        or item.get("certainty") != "supported"
                        or not item.get("evidence_supported")
                        or (claim_support_required and not item.get("claim_supported"))
                        or not finding_source_supported
                    ):
                        continue
                    if domain == "medications":
                        inferred_field = {
                            "dose_changed": "dose",
                            "frequency_changed": "frequency",
                            "route_changed": "route",
                        }.get(change_type, "medication_reported_state")
                        field = str(item.get("field") or inferred_field)
                        subject_id = name.casefold()
                    elif domain == "conditions":
                        field = str(item.get("field") or "condition_reported_state")
                        subject_id = name
                    else:
                        field = str(item.get("field") or "symptom_reported_state")
                        subject_id = name
                    if assertion_kind == "current_state" and event_date is not None:
                        incoming_day = event_date.date()
                        later_state_exists = any(
                            peer.assertion_kind == "current_state"
                            and peer.subject_type == domain.rstrip("s")
                            and _normalise(peer.subject_id) == _normalise(subject_id)
                            and peer.field == field
                            and peer.observed_at is not None
                            and peer.observed_at.date() > incoming_day
                            and peer.assertion_status in {"reported", "review_required", "accepted"}
                            for peer in active_assertions
                        )
                        if later_state_exists:
                            item["historical_only"] = True
                            item["promotion_warning"] = "This event predates a later-dated care-state report. It is preserved in reconciliation history but not promoted as current state."
                            continue
                    if assertion_kind in {"time_bound_observation", "current_state"} and source_event_id and event_date is not None:
                        # Exact same subject/value at the same observed instant is a
                        # duplicate observation, not a second independent fact. Keep
                        # the new event/evidence intact and link it to the first assertion.
                        def _same_occurrence_instant(left, right):
                            if left is None or right is None:
                                return False
                            left_utc = left.replace(tzinfo=timezone.utc) if left.tzinfo is None else left.astimezone(timezone.utc)
                            right_utc = right.replace(tzinfo=timezone.utc) if right.tzinfo is None else right.astimezone(timezone.utc)
                            return left_utc == right_utc

                        reporter_filter = (
                            or_(
                                CareAssertion.source_label == source,
                                CareAssertion.created_by_caregiver_id == created_by_caregiver_id,
                            )
                            if created_by_caregiver_id
                            else CareAssertion.source_label == source
                        )
                        duplicate_candidates = db.query(
                            CareAssertion, CareEvent.occurred_at
                        ).join(
                            CareEvent,
                            CareEvent.id == CareAssertion.source_event_id,
                        ).filter(
                            CareAssertion.person_id == person_id,
                            CareEvent.person_id == person_id,
                            CareAssertion.subject_type == domain.rstrip("s"),
                            CareAssertion.subject_id == subject_id,
                            CareAssertion.field == field,
                                            CareAssertion.asserted_value == str(current),
                            reporter_filter,
                            CareAssertion.assertion_status != "rejected",
                            CareAssertion.source_event_id != source_event_id,
                        ).order_by(CareAssertion.recorded_at.asc()).all()
                        # Compare normalized UTC instants in Python. This avoids
                        # backend-specific timestamp equality differences while
                        # retaining exact-occurrence semantics.
                        duplicate_assertion = next(
                            (
                                candidate for candidate, source_occurred_at in duplicate_candidates
                                if _same_occurrence_instant(source_occurred_at, event_date)
                            ),
                            None,
                        )
                        if duplicate_assertion is None:
                            # Legacy assertions may have no source event lineage.
                            legacy_candidates = db.query(CareAssertion).filter(
                                CareAssertion.person_id == person_id,
                                CareAssertion.subject_type == domain.rstrip("s"),
                                CareAssertion.subject_id == subject_id,
                                CareAssertion.field == field,
                                CareAssertion.asserted_value == str(current),
                                or_(
                                    CareAssertion.source_event_id.is_(None),
                                    CareAssertion.source_event_id != source_event_id,
                                ),
                                CareAssertion.assertion_status != "rejected",
                                reporter_filter,
                            ).order_by(CareAssertion.recorded_at.asc()).all()
                            duplicate_assertion = next(
                                (
                                    candidate for candidate in legacy_candidates
                                    if _same_occurrence_instant(candidate.observed_at, event_date)
                                ),
                                None,
                            )
                        if duplicate_assertion is None:
                            # A later correction may remove the original assertion
                            # while preserving its source event. Do not promote an exact
                            # duplicate of that event into a second independent fact.
                            incoming_event = db.query(CareEvent).filter(
                                CareEvent.id == source_event_id,
                                CareEvent.person_id == person_id,
                            ).first()
                            duplicate_source_event = None
                            if incoming_event is not None:
                                prior_events = db.query(CareEvent).filter(
                                    CareEvent.person_id == person_id,
                                    CareEvent.id != source_event_id,
                                ).order_by(CareEvent.recorded_at.asc()).all()

                                def _normalized_event_text(value):
                                    return " ".join(str(value or "").casefold().split())

                                duplicate_source_event = next(
                                    (
                                        candidate for candidate in prior_events
                                        if candidate.event_type == incoming_event.event_type
                                        and candidate.created_by_caregiver_id == incoming_event.created_by_caregiver_id
                                        and _same_occurrence_instant(candidate.occurred_at, event_date)
                                        and _normalized_event_text(candidate.title) == _normalized_event_text(incoming_event.title)
                                        and _normalized_event_text(candidate.description) == _normalized_event_text(incoming_event.description)
                                    ),
                                    None,
                                )
                            if duplicate_source_event is not None:
                                existing_event_link = db.query(CareRecordRelationship.id).filter(
                                    CareRecordRelationship.person_id == person_id,
                                    CareRecordRelationship.source_type == "care_event",
                                    CareRecordRelationship.source_id == source_event_id,
                                    CareRecordRelationship.relationship_type == "duplicate_observation_of",
                                    CareRecordRelationship.target_type == "care_event",
                                    CareRecordRelationship.target_id == duplicate_source_event.id,
                                ).first()
                                if existing_event_link is None:
                                    db.add(CareRecordRelationship(
                                        person_id=person_id,
                                        source_type="care_event",
                                        source_id=source_event_id,
                                        relationship_type="duplicate_observation_of",
                                        target_type="care_event",
                                        target_id=duplicate_source_event.id,
                                        provenance=json.dumps({
                                            "match_basis": "exact_source_event_time_and_content",
                                            "evidence_ids": finding_evidence_ids,
                                            "original_source_event_id": duplicate_source_event.id,
                                        }),
                                        created_by_caregiver_id=created_by_caregiver_id,
                                    ))
                                item["duplicate_observation"] = True
                                item["duplicate_of_event_id"] = duplicate_source_event.id
                                continue

                        if duplicate_assertion is not None:
                            existing_duplicate_link = db.query(CareRecordRelationship.id).filter(
                                CareRecordRelationship.person_id == person_id,
                                CareRecordRelationship.source_type == "care_event",
                                CareRecordRelationship.source_id == source_event_id,
                                CareRecordRelationship.relationship_type == "duplicate_observation_of",
                                CareRecordRelationship.target_type == "care_assertion",
                                CareRecordRelationship.target_id == duplicate_assertion.id,
                            ).first()
                            if existing_duplicate_link is None:
                                db.add(CareRecordRelationship(
                                    person_id=person_id,
                                    source_type="care_event",
                                    source_id=source_event_id,
                                    relationship_type="duplicate_observation_of",
                                    target_type="care_assertion",
                                    target_id=duplicate_assertion.id,
                                    provenance=json.dumps({
                                        "match_basis": "exact_subject_value_and_observed_at",
                                        "evidence_ids": finding_evidence_ids,
                                        "original_source_event_id": duplicate_assertion.source_event_id,
                                    }),
                                    created_by_caregiver_id=created_by_caregiver_id,
                                ))
                            item["duplicate_observation"] = True
                            item["duplicate_of_assertion_id"] = duplicate_assertion.id
                            continue
                    if source_event_id:
                        # Daily Capture may already have created a source assertion
                        # for the same field. Reuse that provenance rather than
                        # duplicating the same fact during its Delta pass.
                        same_event_assertion = db.query(CareAssertion.id).filter(
                            CareAssertion.person_id == person_id,
                            CareAssertion.source_event_id == source_event_id,
                            CareAssertion.subject_type == domain.rstrip("s"),
                            CareAssertion.subject_id == subject_id,
                            CareAssertion.field == field,
                            CareAssertion.assertion_kind == assertion_kind,
                            CareAssertion.asserted_value == str(current),
                        ).first()
                        if same_event_assertion:
                            continue
                    created_assertion = create_assertion(db, CareAssertionCreate(
                        person_id=person_id, subject_type=domain.rstrip("s"), subject_id=subject_id,
                        field=field, assertion_kind=assertion_kind,
                        epistemic_state="reported" if assertion_kind == "time_bound_observation" else "inferred",
                        asserted_value=str(current), source_event_id=source_event_id, source_label=source,
                        source_text=str(item.get("evidence_excerpt") or content[:500]),
                        evidence_ids=finding_evidence_ids, observed_at=effective_event_date,
                        created_by_caregiver_id=created_by_caregiver_id,
                        review_after_days=30 if domain != "symptoms" else None,
                    ), commit=False)
                    if created_assertion is not None:
                        created_assertion_ids.append(str(created_assertion.id))

            def add_internal_gap_candidate(
                subject_type: str,
                subject_id: str,
                field: str,
                description: str,
                priority: Priority,
                linked_evidence_ids: list[str],
                source_record_refs: list[dict[str, str]],
                evidence_excerpt: str,
            ) -> None:
                # Delta output is a review-only interpretation. It may enrich
                # the internal LCR, but it cannot independently create a
                # caregiver question or decide that human attention is needed.
                if not source_record_refs or not linked_evidence_ids:
                    return
                gap = db.query(InformationGap).filter(
                    InformationGap.person_id == person_id,
                    InformationGap.subject_type == subject_type,
                    InformationGap.subject_id == subject_id,
                    InformationGap.field == field,
                    InformationGap.lifecycle_status.in_([GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED, GapLifecycleStatus.PARTIALLY_RESOLVED]),
                ).first()
                source_context = {
                    "candidate_source": "delta_engine",
                    "source": source,
                    "source_event_id": source_event_id,
                    "source_record_refs": source_record_refs,
                    "evidence_excerpt": evidence_excerpt,
                    "grounding_status": "source_excerpt_linked; semantic_entailment_not_automatically_verified",
                    "necessity": "not_assessed",
                    "caregiver_attention_required": False,
                }
                if gap is None:
                    db.add(InformationGap(
                        person_id=person_id, subject_type=subject_type, subject_id=subject_id,
                        field=field, epistemic_state=EpistemicState.UNKNOWN, gap_reason=GapReason.ABSENT,
                        priority=priority, lifecycle_status=GapLifecycleStatus.OPEN,
                        description=description, evidence_ids=linked_evidence_ids,
                        context=[source_context],
                    ))
                else:
                    contexts = list(gap.context or [])
                    if source_context not in contexts:
                        contexts.append(source_context)
                        gap.context = contexts
                    gap.evidence_ids = list(dict.fromkeys(
                        list(gap.evidence_ids or []) + linked_evidence_ids
                    ))

            for item in result["tests"]:
                linked_evidence_ids, source_refs = loop_provenance(item)
                if item.get("evidence_supported") and source_refs and item.get("result_present") is False:
                    name = str(item.get("name") or "ordered test").strip()
                    add_internal_gap_candidate(
                        "test", name, "result_missing",
                        f"{name} is referenced but no result is present in the supplied record.",
                        Priority.HIGH, linked_evidence_ids, source_refs,
                        str(item.get("evidence_excerpt") or ""),
                    )
            for item in result["follow_ups"]:
                status = str(item.get("status") or "unknown").casefold()
                linked_evidence_ids, source_refs = loop_provenance(item)
                if item.get("evidence_supported") and source_refs and status not in {"completed", "done", "cancelled", "canceled"}:
                    action = str(item.get("action") or "follow-up action").strip()
                    add_internal_gap_candidate(
                        "follow_up", action, "follow_up_status",
                        f"Follow-up is reported as {status}; completion is not established.",
                        Priority.MEDIUM, linked_evidence_ids, source_refs,
                        str(item.get("evidence_excerpt") or ""),
                    )
            for item in result["clinical_gaps"]:
                linked_evidence_ids, source_refs = loop_provenance(item)
                if item.get("evidence_supported") and source_refs:
                    gap = str(item.get("gap") or "missing care information").strip()
                    add_internal_gap_candidate(
                        "care_gap", gap, "missing_information",
                        str(item.get("why_it_matters") or gap), Priority.MEDIUM,
                        linked_evidence_ids, source_refs, str(item.get("evidence_excerpt") or ""),
                    )

            # Only evidence supplied with this incoming event may support a
            # newly promoted assertion. Older evidence remains available to
            # uncertainty/baseline analysis, but must not be relabelled as support
            # from the event currently being reconciled.
            incoming_evidence_rows = (
                db.query(Evidence).filter(
                    Evidence.person_id == person_id,
                    Evidence.id.in_(evidence_ids),
                ).all()
                if evidence_ids else []
            )
            incoming_persisted_evidence = [
                evidence_record_to_uncertainty_item(row)
                for row in incoming_evidence_rows
            ]

            for finding in computed:
                finding_name = str(finding.get("name") or "").strip()
                if not finding_name:
                    continue
                finding_domain = str(finding.get("domain") or "")
                finding_field = str(next((
                    item.get("field") for item in result.get(finding_domain, [])
                    if _normalise(item.get("name")) == _normalise(finding_name)
                ), "reported_state"))
                finding_subject_type = {
                    "medications": "medication",
                    "conditions": "condition",
                    "symptoms": "symptom",
                }.get(finding_domain, "care_finding")
                source_items = [
                    item for item in result.get(finding_domain, [])
                    if _normalise(item.get("name")) == _normalise(finding_name)
                ]
                source_item = source_items[0] if len(source_items) == 1 else {}
                # Run-level evidence IDs are not automatically evidence for every finding.
                # Link only persisted evidence whose stored text contains the finding's
                # explicit excerpt; otherwise preserve the finding as unsupported/unknown.
                excerpt = str(source_item.get("evidence_excerpt") or "").strip()
                finding_evidence = _match_finding_evidence(incoming_persisted_evidence, excerpt)
                finding_evidence_ids = [str(row["id"]) for row in finding_evidence if row.get("id")]
                # An assertion may cite either exact matching persisted evidence or
                # the incoming persisted event itself. Never inherit every evidence
                # ID attached to the reconciliation request: unrelated documents
                # must not become provenance for this finding.
                finding_source_supported = bool(finding_evidence_ids) or bool(
                    source_event_id and excerpt and _normalise(excerpt) in _normalise(content)
                )
                source_item["finding_source_supported"] = finding_source_supported
                baseline_ids = set(str(value) for value in (source_item.get("existing_assertions") or []))
                baseline_assertions = [
                    assertion for assertion in active_state
                    if str(assertion.get("assertion_id")) in baseline_ids
                ]
                # Prior state is usable as a comparison baseline only when it is
                # an actual persisted assertion for this person/field. Keep its
                # source status explicit; it is not promoted to verified fact.
                baseline_evidence = [{
                    "id": str(assertion["assertion_id"]),
                    "source_type": "care_assertion",
                    "text": str(assertion.get("value") or ""),
                    "verified": assertion.get("status") == "accepted",
                    "observed_at": assertion.get("observed_at"),
                    "role": "baseline",
                    "is_baseline": True,
                } for assertion in baseline_assertions if assertion.get("value") not in (None, "")]
                current_evidence = [{
                    **item,
                    "role": "current",
                    "is_baseline": False,
                } for item in finding_evidence]
                assessment_evidence = baseline_evidence + current_evidence
                analysis = analyze_uncertainty(
                    claim=(str(finding.get("current") or "") or str(finding.get("change_type") or "Change not established")),
                    task="identify_change",
                    evidence=assessment_evidence,
                    unresolved_conflicts=([{
                        "field": finding_field,
                        "description": finding.get("comparison_warning") or "Competing state values were found.",
                        "evidence_ids": finding_evidence_ids,
                    }] if finding.get("change_type") == "conflicting" else []),
                )
                if finding.get("change_type") == "conflicting":
                    epistemic_state = EpistemicState.CONFLICTING
                    gap_reason = GapReason.CONFLICTING
                elif (
                    not finding_evidence
                    or analysis.get("sufficiency") == "insufficient_pending_resolution"
                    or "missing_baseline" in analysis.get("classifications", [])
                ):
                    # A source quote without a comparable persisted baseline does
                    # not establish a change. Preserve the observation, but leave
                    # the change assessment unknown until the missing prerequisite exists.
                    epistemic_state = EpistemicState.UNKNOWN
                    gap_reason = GapReason.INSUFFICIENT_EVIDENCE
                elif finding.get("certainty") != "supported" or finding.get("historical_only"):
                    epistemic_state = EpistemicState.PARTIALLY_KNOWN
                    gap_reason = GapReason.MISSING_CONTEXT if finding.get("historical_only") else GapReason.LOW_QUALITY
                else:
                    # Supported provenance permits a qualified state, not certainty.
                    epistemic_state = EpistemicState.PARTIALLY_KNOWN
                    gap_reason = None
                active_assessments = db.query(UncertaintyAssessment).filter(
                    UncertaintyAssessment.person_id == person_id,
                    UncertaintyAssessment.subject_type == finding_subject_type,
                    UncertaintyAssessment.subject_id == finding_name,
                    UncertaintyAssessment.field == finding_field,
                    UncertaintyAssessment.is_active.is_(True),
                ).all()
                assessment_id = str(uuid.uuid4())
                for active_assessment in active_assessments:
                    active_assessment.is_active = False
                    active_assessment.superseded_by_assessment_id = assessment_id
                db.add(UncertaintyAssessment(
                    id=assessment_id,
                    person_id=person_id,
                    subject_type=finding_subject_type,
                    subject_id=finding_name,
                    field=finding_field,
                    epistemic_state=epistemic_state,
                    confidence=None,
                    known_value=None,
                    gap_reason=gap_reason,
                    context=[{
                        "origin": "delta_engine",
                        "delta_run_pending": True,
                        "analysis": analysis,
                        "finding": finding,
                    }],
                    evidence_ids=finding_evidence_ids or None,
                    assessed_by_caregiver_id=created_by_caregiver_id,
                    is_active=True,
                ))
                result.setdefault("uncertainty_assessments", []).append({
                    "assessment_id": assessment_id,
                    "subject_type": finding_subject_type,
                    "subject_id": finding_name,
                    "field": finding_field,
                    "epistemic_state": epistemic_state.value,
                    "confidence": None,
                    "analysis": analysis,
                })
            db.flush()
            if created_assertion_ids:
                from app.services.change_intelligence_reconciliation import (
                    reconcile_person_change_intelligence,
                )
                reconcile_person_change_intelligence(
                    db,
                    person_id,
                    reference_time=event_date,
                    protected_assertion_ids=created_assertion_ids,
                )
                db.flush()
                canonical_change_reconciliation = {
                    "status": "completed",
                    "engine": "persisted_change_intelligence_reconciliation",
                    "protected_assertion_count": len(created_assertion_ids),
                }
            else:
                canonical_change_reconciliation = {
                    "status": "not_run",
                    "reason": "no_new_assertions_created",
                }
        except Exception as exc:
            # Keep the API response generic, but retain the original database or
            # validation exception in server logs so persistence regressions are
            # diagnosable without exposing care content to the caller.
            logger.exception("Delta reconciliation persistence failed")
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="The reconciliation could not be persisted; the transaction was rolled back.",
            ) from exc

    result["interpretation_evaluation"] = evaluate_longitudinal_interpretations(computed, db=db, person_id=person_id, history_coverage=history_coverage)
    result["computed_changes"] = computed
    result["provenance"] = {
        "person_id": person_id, "created_by_caregiver_id": created_by_caregiver_id, "source": source, "event_date": (event_date or datetime.now(timezone.utc)).isoformat(),
        "source_event_id": source_event_id, "evidence_ids": evidence_ids,
        "source_event_snapshot_sha256": source_event_snapshot_sha256,
        "engine_version": DELTA_ENGINE_VERSION,
        "model_provider": "configured_local_ollama", "model": model,
        "persisted": persist, "current_state_authority": "existing SolenOS care-continuity records; this delta is an interpretation, not a verified clinical fact",
        "canonical_change_reconciliation": canonical_change_reconciliation,
    }
    if persist:
        try:
            # Persist model-detected conflicts with the same source-bound transaction.
            for item in result["contradictions"]:
                if not item.get("evidence_supported"):
                    continue
                topic = str(item.get("topic") or "unspecified conflict").strip()
                description = str(item.get("why_conflicting") or "Competing source reports require human review.")
                description += " Reports: " + " | ".join(str(value) for value in (item.get("reports") or []))
                # Retain the provenance for every competing source, not just the
                # latest event's evidence IDs.
                conflict_refs: list[dict[str, str]] = []
                for excerpt in item.get("evidence_excerpts") or []:
                    conflict_refs.extend(_match_source_references(
                        str(excerpt),
                        new_content=content,
                        source_event_id=source_event_id,
                        active_state=active_state,
                        prior_events=prior_events,
                        evidence_rows=source_evidence_rows,
                    ))
                unique_conflict_refs = {
                    (ref["record_type"], ref["record_id"]): ref
                    for ref in conflict_refs
                }
                conflict_evidence_ids = list(dict.fromkeys(
                    record_id for record_type, record_id in unique_conflict_refs
                    if record_type == "evidence"
                ))
                if not unique_conflict_refs:
                    continue
                existing = db.query(Contradiction).filter(
                    Contradiction.person_id == person_id,
                    Contradiction.subject_type == "delta_engine_report",
                    Contradiction.subject_id == topic,
                    Contradiction.resolution_status == "unresolved",
                ).first()
                if existing is None:
                    db.add(Contradiction(
                        person_id=person_id, subject_type="delta_engine_report", subject_id=topic,
                        field=topic, description=description, evidence_ids=conflict_evidence_ids,
                        source_assertion_ids=[], resolution_status="unresolved",
                    ))
            run = CareDeltaRun(
                person_id=person_id, source=source,
                event_date=effective_event_date,
                source_event_id=source_event_id, evidence_ids=evidence_ids,
                delta_json="{}", model_name=model,
                created_by_caregiver_id=created_by_caregiver_id,
            )
            db.add(run)
            db.flush()
            result["provenance"]["delta_run_id"] = run.id
            assessment_ids = [
                item.get("assessment_id") for item in result.get("uncertainty_assessments", [])
                if item.get("assessment_id")
            ]
            if assessment_ids:
                saved_assessments = db.query(UncertaintyAssessment).filter(
                    UncertaintyAssessment.person_id == person_id,
                    UncertaintyAssessment.id.in_(assessment_ids),
                ).all()
                for saved in saved_assessments:
                    saved_context = list(saved.context or [])
                    if saved_context and isinstance(saved_context[0], dict):
                        saved_context[0]["delta_run_pending"] = False
                        saved_context[0]["delta_run_id"] = run.id
                        saved.context = saved_context
                for assessment in result.get("uncertainty_assessments", []):
                    assessment["delta_run_id"] = run.id
            run.delta_json = json.dumps(result, ensure_ascii=False, default=str)
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="The reconciliation could not be persisted; the transaction was rolled back.")
    return result
