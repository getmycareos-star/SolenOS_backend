from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
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
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=503, detail="Local model configuration must use an HTTP(S) service URL.")
    if os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() in {"production", "prod"} and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(status_code=503, detail="Production must use the configured private model service, not loopback.")
    return base_url, model, timeout


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
    required = {"event_summary", "medications", "conditions", "symptoms", "tests", "follow_ups", "contradictions", "clinical_gaps", "caregiver_summary", "clinician_summary"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise HTTPException(status_code=502, detail="The local model response did not satisfy the Delta Engine contract; no delta was persisted.")
    for key in required - {"event_summary", "caregiver_summary", "clinician_summary"}:
        if not isinstance(data[key], list):
            raise HTTPException(status_code=502, detail=f"The local model returned an invalid {key} collection; no delta was persisted.")
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

    active_assertions = db.query(CareAssertion).filter(
        CareAssertion.person_id == person_id,
        CareAssertion.assertion_kind == "current_state",
        CareAssertion.assertion_status.in_(["reported", "review_required", "accepted"]),
    ).order_by(CareAssertion.recorded_at.asc()).limit(500).all()
    active_state = [{
        "assertion_id": item.id, "subject_type": item.subject_type, "subject_id": item.subject_id,
        "field": item.field, "value": item.asserted_value, "source": item.source_label,
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
    prior_events = prior_events_query.order_by(CareEvent.occurred_at.desc()).limit(100).all()
    prior_evidence_query = db.query(Evidence).filter(Evidence.person_id == person_id)
    if evidence_ids:
        # These evidence rows belong to the incoming event; the new-event text is
        # supplied separately and must not be counted again as prior evidence.
        prior_evidence_query = prior_evidence_query.filter(~Evidence.id.in_(evidence_ids))
    prior_evidence = prior_evidence_query.order_by(Evidence.uploaded_at.desc()).limit(100).all()
    existing_record_context = {
        "events": [{
            "event_id": item.id, "date": item.occurred_at.isoformat() if item.occurred_at else None,
            "type": item.event_type, "title": item.title, "description": item.description,
            "evidence_ids": item.evidence_ids or [],
        } for item in prior_events],
        "evidence": [{
            "evidence_id": item.id, "date": item.uploaded_at.isoformat() if item.uploaded_at else None,
            "type": item.type, "source_text": (item.source_text or "")[:4000],
        } for item in prior_evidence],
    }

    base_url, model, timeout = _local_ollama_url()
    system_prompt = (
        "You are the SolenOS Delta Engine, a conservative longitudinal care-information reconciliation component. "
        "Compare NEW_CARE_EVENT against EXISTING_ACTIVE_STATE; do not summarize the event in isolation. "
        "Return only one JSON object matching the supplied schema. Do not emit markdown. "
        "Never invent a diagnosis, medication order, date, test result, follow-up completion, or causal explanation. "
        "Distinguish new information from a meaningful state change. Use unchanged only when the source supports it. "
        "A mention is not an instruction to start/stop/change a medicine unless the source clearly says so. "
        "Do not assume absence from this event means discontinued. Keep competing reports visible; never silently choose a winner. "
        "Mark uncertainty explicitly. A test ordered without a result present in the provided record is an open loop, not a negative result. "
        "For symptoms, compare only when comparable prior observations exist; do not infer trends from incomparable reports. "
        "Clinical gaps and follow-ups must be grounded in supplied text/state. caregiver_summary uses clear plain language; clinician_summary preserves exact clinical terms. "
        "Do not diagnose, recommend treatment, or replace a clinician. Evidence excerpts must be short verbatim snippets from NEW_CARE_EVENT or EXISTING_ACTIVE_STATE."
    )
    user_payload = {
        "existing_active_state": active_state,
        "existing_record_context": existing_record_context,
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

    # Verify that quoted evidence exists in the supplied source or active state.
    context_text = [str(item.get("title") or "") + " " + str(item.get("description") or "") for item in existing_record_context["events"]]
    context_text += [str(item.get("source_text") or "") for item in existing_record_context["evidence"]]
    source_corpus = "\n".join([content] + [str(item.get("value", "")) for item in active_state] + context_text).casefold()
    for domain in ("medications", "conditions", "symptoms", "tests", "follow_ups", "clinical_gaps"):
        for item in result[domain]:
            excerpt = str(item.get("evidence_excerpt") or "").strip()
            item["evidence_supported"] = bool(excerpt and excerpt.casefold() in source_corpus)
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
    known = {}
    for item in active_state:
        key = (_normalise(item["subject_type"]), _normalise(item["subject_id"]), _normalise(item["field"]))
        known.setdefault(key, []).append(item)
    computed = []
    for domain in ("medications", "conditions", "symptoms"):
        for item in result[domain]:
            name = str(item.get("name", "")).strip()
            if not name:
                item["change_type"] = "uncertain"
                item["certainty"] = "uncertain"
                continue
            field = f"{domain.rstrip('s')}_reported_state"
            candidates = [old for old in active_state if _normalise(old["subject_id"]) == _normalise(name) or _normalise(old["field"]) == _normalise(name)]
            prior_values = list(dict.fromkeys(old["value"] for old in candidates))
            item["existing_assertions"] = [old["assertion_id"] for old in candidates]
            distinct_prior = list(dict.fromkeys(_normalise(value) for value in prior_values))
            if len(distinct_prior) > 1:
                item["change_type"] = "conflicting"
                item["certainty"] = "uncertain"
                item["prior_state_candidates"] = prior_values
                item["comparison_warning"] = "Existing normalized assertions disagree; no prior value was selected."
            elif prior_values and item.get("current") is not None and _normalise(item.get("current")) == distinct_prior[0]:
                item["change_type"] = "unchanged"
            elif prior_values and item.get("current") is not None:
                if item.get("change_type") not in {"discontinued", "conflicting", "uncertain"}:
                    item["change_type"] = "changed"
                item["prior_state_candidates"] = prior_values
            else:
                item["comparison_basis"] = "No normalized prior assertion; compare against the supplied historical LCR source context."
                if not item.get("change_type"):
                    item["change_type"] = "uncertain"
                    item["certainty"] = "uncertain"
            computed.append({"domain": domain, "name": name, "change_type": item.get("change_type"), "previous": item.get("previous"), "current": item.get("current"), "certainty": item.get("certainty", "uncertain"), "comparison_basis": item.get("comparison_basis")})

    if persist:
        try:
            for domain in ("medications", "conditions", "symptoms"):
                for item in result[domain]:
                    name = str(item.get("name", "")).strip()
                    current = item.get("current")
                    change_type = str(item.get("change_type") or "").casefold()
                    if domain == "medications" and change_type == "discontinued" and current is None:
                        current = "discontinued"
                    if not name or current is None or item.get("certainty") != "supported" or not item.get("evidence_supported"):
                        continue
                    if domain == "medications":
                        field = str(item.get("field") or ("dose" if "dose" in change_type else "medication_reported_state"))
                        subject_id = name.casefold()
                    elif domain == "conditions":
                        field = str(item.get("field") or "condition_reported_state")
                        subject_id = name
                    else:
                        field = str(item.get("field") or "symptom_reported_state")
                        subject_id = name
                    create_assertion(db, CareAssertionCreate(
                        person_id=person_id, subject_type=domain.rstrip("s"), subject_id=subject_id,
                        field=field, assertion_kind="time_bound_observation" if domain == "symptoms" else "current_state",
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
