from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.care import CareEvent, Evidence
from app.models.care_continuity import CareAssertion, CareDeltaRun
from app.models.uncertainty import Contradiction, InformationGap, OpenQuestion
from app.core.uncertainty_enums import EpistemicState, GapReason, GapLifecycleStatus, Priority
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion


DELTA_JSON_CONTRACT = {
    "type": "object",
    "required": [
        "event_summary", "medications", "conditions", "symptoms", "tests",
        "follow_ups", "contradictions", "clinical_gaps", "caregiver_summary", "clinician_summary"
    ],
    "properties": {
        "event_summary": {"type": "string"},
        "medications": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string", "enum": ["new", "dose_changed", "frequency_changed", "route_changed", "discontinued", "unchanged", "conflicting", "uncertain"]},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string"},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "symptoms": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["change_type", "name", "previous", "current", "evidence_excerpt", "certainty"],
                "properties": {
                    "change_type": {"type": "string"},
                    "name": {"type": "string"},
                    "previous": {"type": ["string", "null"]},
                    "current": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["supported", "uncertain"]}
                }
            }
        },
        "tests": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "status", "ordered_date", "result_present", "evidence_excerpt"],
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "ordered_date": {"type": ["string", "null"]},
                    "result_present": {"type": "boolean"},
                    "evidence_excerpt": {"type": "string"}
                }
            }
        },
        "follow_ups": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action", "status", "due_date", "owner", "evidence_excerpt"],
                "properties": {
                    "action": {"type": "string"},
                    "status": {"type": "string"},
                    "due_date": {"type": ["string", "null"]},
                    "owner": {"type": ["string", "null"]},
                    "evidence_excerpt": {"type": "string"}
                }
            }
        },
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["topic", "reports", "why_conflicting", "evidence_excerpts"],
                "properties": {
                    "topic": {"type": "string"},
                    "reports": {"type": "array", "items": {"type": "string"}},
                    "why_conflicting": {"type": "string"},
                    "evidence_excerpts": {"type": "array", "items": {"type": "string"}}
                }
            }
        },
        "clinical_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["gap", "why_it_matters", "next_question", "evidence_excerpt"],
                "properties": {
                    "gap": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "next_question": {"type": "string"},
                    "evidence_excerpt": {"type": "string"}
                }
            }
        },
        "caregiver_summary": {"type": "string"},
        "clinician_summary": {"type": "string"}
    }
}


def _local_ollama_url() -> tuple[str, str, float]:
    base_url = os.getenv("OLLAMA_BASE_URL", "").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "").strip()
    try:
        timeout = max(5.0, min(float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60")), 180.0))
    except ValueError:
        timeout = 60.0
    if not base_url or not model:
        raise HTTPException(status_code=503, detail="The configured local SolenOS model is unavailable: OLLAMA_BASE_URL and OLLAMA_MODEL must be configured.")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise HTTPException(status_code=503, detail="Local model configuration must use an HTTP(S) service URL.")
    environment = (
        os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or ""
    ).strip().lower()
    if environment in {"production", "prod"}:
        hostname = parsed.hostname.casefold()
        if hostname in {"localhost", "127.0.0.1", "::1"} or not hostname.endswith(".railway.internal"):
            raise HTTPException(status_code=503, detail="Production Delta inference must use the private SolenOS model service; loopback and public model hosts are rejected.")
    return base_url, model, timeout



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
        raise HTTPException(status_code=502, detail="The local model returned invalid JSON; no delta was persisted.") from exc
    _validate_schema(data, DELTA_JSON_CONTRACT)
    return data


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def reconcile_event(db: Session, *, person_id: str, event_date: datetime | None, source: str,
                    content: str, source_event_id: str | None = None,
                    evidence_ids: list[str] | None = None, persist: bool = True,
                    created_by_caregiver_id: str | None = None) -> dict[str, Any]:
    if not content.strip():
        raise HTTPException(status_code=422, detail="Care event content is required.")
    if len(content) > 100_000:
        raise HTTPException(status_code=413, detail="Care event content exceeds the 100,000 character limit.")
    evidence_ids = list(dict.fromkeys(evidence_ids or []))
    if evidence_ids:
        rows = db.query(Evidence.id).filter(Evidence.person_id == person_id, Evidence.id.in_(evidence_ids)).all()
        if {row[0] for row in rows} != set(evidence_ids):
            raise HTTPException(status_code=422, detail="Every evidence ID must belong to this care record.")
    if source_event_id and not db.query(CareEvent.id).filter(CareEvent.id == source_event_id, CareEvent.person_id == person_id).first():
        raise HTTPException(status_code=422, detail="source_event_id must belong to this care record.")

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
                CareAssertion.source_event_id != source_event_id,
            )
        )
    active_assertions = active_assertions_query.order_by(CareAssertion.recorded_at.desc()).limit(250).all()
    active_state = [{
        "assertion_id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
        "field": item.field, "value": item.asserted_value, "source": item.source_label,
        "assertion_kind": item.assertion_kind,
        "observed_at": item.observed_at.isoformat() if item.observed_at else None,
        "status": item.assertion_status, "evidence_ids": item.evidence_ids or [],
    } for item in active_assertions]

    # Include the existing LCR's real source material as context too. Many older
    # records predate normalized assertions; they are evidence, not automatically
    # promoted into verified current-state facts.
    prior_events_query = db.query(CareEvent).filter(CareEvent.person_id == person_id)
    if source_event_id:
        # Daily Capture may already have saved the event before invoking Delta.
        # Do not let the event being reconciled masquerade as historical baseline.
        prior_events_query = prior_events_query.filter(CareEvent.id != source_event_id)
    prior_events = prior_events_query.order_by(CareEvent.occurred_at.desc()).limit(25).all()
    prior_evidence_query = db.query(Evidence).filter(Evidence.person_id == person_id)
    if evidence_ids:
        # These evidence rows belong to the incoming event; the new-event text is
        # supplied separately and must not be counted again as prior evidence.
        prior_evidence_query = prior_evidence_query.filter(~Evidence.id.in_(evidence_ids))
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
    open_gaps = db.query(InformationGap).filter(
        InformationGap.person_id == person_id,
        InformationGap.lifecycle_status.in_([
            GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED,
            GapLifecycleStatus.PARTIALLY_RESOLVED,
        ]),
    ).order_by(InformationGap.created_at.desc()).limit(50).all()
    open_questions = db.query(OpenQuestion).filter(
        OpenQuestion.person_id == person_id, OpenQuestion.status == "open",
    ).order_by(OpenQuestion.asked_at.desc()).limit(50).all()
    unresolved_contradictions = db.query(Contradiction).filter(
        Contradiction.person_id == person_id,
        Contradiction.resolution_status.in_(["unresolved", "qualified"]),
    ).order_by(Contradiction.created_at.desc()).limit(50).all()
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
            "evidence_ids": item.evidence_ids or [],
        } for item in open_questions],
        "contradictions": [{
            "id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
            "field": item.field, "description": item.description, "status": item.resolution_status,
            "evidence_ids": item.evidence_ids or [],
        } for item in unresolved_contradictions],
    }

    base_url, model, timeout = _local_ollama_url()
    system_prompt = (
        "You are the SolenOS Delta Engine, a conservative longitudinal care-information reconciliation component. "
        "Compare NEW_CARE_EVENT against EXISTING_ACTIVE_STATE, EXISTING_RECORD_CONTEXT, and EXISTING_UNRESOLVED_CARE_LOOPS; do not summarize the event in isolation. EXISTING_RECORD_CONTEXT contains prior persisted events/documents and is historical source material, not automatically verified current state. EXISTING_UNRESOLVED_CARE_LOOPS contains previously recorded gaps, questions, and unresolved contradictions that must be reconciled with the new event. Do not silently close a loop because the new event omits it; do not mark a loop verified complete based only on model inference. "
        "Return only one JSON object matching the supplied schema. Do not emit markdown. "
        "Never invent a diagnosis, medication order, date, test result, follow-up completion, or causal explanation. "
        "Distinguish new information from a meaningful state change. Use unchanged only when the source supports it. "
        "A mention is not an instruction to start/stop/change a medicine unless the source clearly says so. "
        "Do not assume absence from this event means discontinued. Keep competing reports visible; never silently choose a winner. "
        "Mark uncertainty explicitly. A test ordered without a result present in the provided record is an open loop, not a negative result. "
        "For symptoms, compare only when comparable prior observations exist; do not infer trends from incomparable reports. "
        "Clinical gaps and follow-ups must be grounded in supplied text/state. caregiver_summary uses clear plain language; clinician_summary preserves exact clinical terms. "
        "Do not diagnose, recommend treatment, or replace a clinician. Evidence excerpts must be short verbatim snippets from NEW_CARE_EVENT, EXISTING_ACTIVE_STATE, or EXISTING_RECORD_CONTEXT. When citing historical context, do not claim it is current unless its date/status supports that interpretation."
    )
    user_payload = {
        "existing_active_state": active_state,
        "existing_record_context": existing_record_context,
        "existing_unresolved_care_loops": existing_unresolved_care_loops,
        "new_care_event": {
            "date": (event_date or datetime.now(timezone.utc)).isoformat(),
            "source": source,
            "content": content,
            "evidence_ids": evidence_ids,
            "source_event_id": source_event_id,
        },
        "json_schema": DELTA_JSON_CONTRACT,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": DELTA_JSON_CONTRACT,
                    "options": {"temperature": 0},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            model_result = response.json()
            content_json = model_result.get("message", {}).get("content")
            if not isinstance(content_json, str):
                raise ValueError("missing message.content")
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="The local SolenOS model timed out. The care event remains unchanged.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="The local SolenOS model could not complete reconciliation. No fallback model was used and no delta was persisted.") from exc

    result = _parse_model_json(content_json)

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
            computed.append({"domain": domain, "name": name, "change_type": item.get("change_type"), "previous": item.get("previous"), "current": item.get("current"), "certainty": item.get("certainty", "uncertain"), "comparison_basis": item.get("comparison_basis")})

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

    if persist:
        try:
            for domain in ("medications", "conditions", "symptoms"):
                for item in result[domain]:
                    name = str(item.get("name", "")).strip()
                    current = item.get("current")
                    change_type = str(item.get("change_type") or "").casefold()
                    # A repeated unchanged report is useful in this run's audit
                    # result but must not manufacture another active-state fact.
                    # Competing/uncertain values remain visible for review and are
                    # never promoted into normalized active state.
                    if change_type in {"unchanged", "conflicting", "uncertain"} or item.get("historical_only"):
                        continue
                    if domain == "medications" and change_type == "discontinued" and current is None:
                        current = "discontinued"
                    if not name or current is None or item.get("certainty") != "supported" or not item.get("evidence_supported") or not item.get("claim_supported"):
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
                    assertion_kind = "time_bound_observation" if domain == "symptoms" else "current_state"
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
                    create_assertion(db, CareAssertionCreate(
                        person_id=person_id, subject_type=domain.rstrip("s"), subject_id=subject_id,
                        field=field, assertion_kind=assertion_kind,
                        asserted_value=str(current), source_event_id=source_event_id, source_label=source,
                        source_text=str(item.get("evidence_excerpt") or content[:500]),
                        evidence_ids=evidence_ids, observed_at=event_date or datetime.now(timezone.utc),
                        created_by_caregiver_id=created_by_caregiver_id,
                        review_after_days=30 if domain != "symptoms" else None,
                    ), commit=False)

            def add_open_loop(subject_type: str, subject_id: str, field: str, description: str, question_text: str, priority: Priority) -> None:
                gap = db.query(InformationGap).filter(
                    InformationGap.person_id == person_id,
                    InformationGap.subject_type == subject_type,
                    InformationGap.subject_id == subject_id,
                    InformationGap.field == field,
                    InformationGap.lifecycle_status.in_([GapLifecycleStatus.OPEN, GapLifecycleStatus.REOPENED, GapLifecycleStatus.PARTIALLY_RESOLVED]),
                ).first()
                if gap is None:
                    db.add(InformationGap(
                        person_id=person_id, subject_type=subject_type, subject_id=subject_id,
                        field=field, epistemic_state=EpistemicState.UNKNOWN, gap_reason=GapReason.ABSENT,
                        priority=priority, lifecycle_status=GapLifecycleStatus.OPEN,
                        description=description, evidence_ids=evidence_ids,
                    ))
                question = db.query(OpenQuestion).filter(
                    OpenQuestion.person_id == person_id, OpenQuestion.subject_type == subject_type,
                    OpenQuestion.subject_id == subject_id, OpenQuestion.question == question_text,
                    OpenQuestion.status == "open",
                ).first()
                if question is None:
                    db.add(OpenQuestion(
                        person_id=person_id, question=question_text, subject_type=subject_type,
                        subject_id=subject_id, status="open", priority=priority, evidence_ids=evidence_ids,
                        context=[{"source": source, "source_event_id": source_event_id}],
                    ))

            for item in result["tests"]:
                if item.get("evidence_supported") and item.get("result_present") is False:
                    name = str(item.get("name") or "ordered test").strip()
                    add_open_loop("test", name, "result_missing",
                        f"{name} is referenced but no result is present in the supplied record.",
                        f"Has the result for {name} been received and reviewed?", Priority.HIGH)
            for item in result["follow_ups"]:
                status = str(item.get("status") or "unknown").casefold()
                if item.get("evidence_supported") and status not in {"completed", "done", "cancelled", "canceled"}:
                    action = str(item.get("action") or "follow-up action").strip()
                    add_open_loop("follow_up", action, "follow_up_status",
                        f"Follow-up is reported as {status}; completion is not established.",
                        f"Has this follow-up been completed: {action}?", Priority.MEDIUM)
            for item in result["clinical_gaps"]:
                if item.get("evidence_supported"):
                    gap = str(item.get("gap") or "missing care information").strip()
                    question_text = str(item.get("next_question") or f"What is known about {gap}?").strip()
                    add_open_loop("care_gap", gap, "missing_information",
                        str(item.get("why_it_matters") or gap), question_text, Priority.MEDIUM)
            db.flush()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="The reconciliation could not be persisted; the transaction was rolled back.")

    result["computed_changes"] = computed
    result["provenance"] = {
        "person_id": person_id, "created_by_caregiver_id": created_by_caregiver_id, "source": source, "event_date": (event_date or datetime.now(timezone.utc)).isoformat(),
        "source_event_id": source_event_id, "evidence_ids": evidence_ids,
        "model_provider": "configured_local_ollama", "model": model,
        "persisted": persist, "current_state_authority": "existing SolenOS care-continuity records; this delta is an interpretation, not a verified clinical fact",
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
                existing = db.query(Contradiction).filter(
                    Contradiction.person_id == person_id,
                    Contradiction.subject_type == "delta_engine_report",
                    Contradiction.subject_id == topic,
                    Contradiction.resolution_status == "unresolved",
                ).first()
                if existing is None:
                    db.add(Contradiction(
                        person_id=person_id, subject_type="delta_engine_report", subject_id=topic,
                        field=topic, description=description, evidence_ids=evidence_ids,
                        source_assertion_ids=[], resolution_status="unresolved",
                    ))
            run = CareDeltaRun(
                person_id=person_id, source=source,
                event_date=event_date or datetime.now(timezone.utc),
                source_event_id=source_event_id, evidence_ids=evidence_ids,
                delta_json="{}", model_name=model,
                created_by_caregiver_id=created_by_caregiver_id,
            )
            db.add(run)
            db.flush()
            result["provenance"]["delta_run_id"] = run.id
            run.delta_json = json.dumps(result, ensure_ascii=False, default=str)
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="The reconciliation could not be persisted; the transaction was rolled back.")
    return result
