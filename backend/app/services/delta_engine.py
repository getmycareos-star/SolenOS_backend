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
from app.models.care_continuity import CareAssertion
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
                    "change_type": {"type": "string},
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
                    "action": {"type": "string},
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
                    "topic": {"type": "string},
                    "reports": {"type": "array", "items": {"type": "string"}},
                    "why_conflicting": {"type": "string},
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
                    "gap": {"type": "string},
                    "why_it_matters": {"type": "string"},
                    "next_question": {"type": "string},
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
                    evidence_ids: list[str] | None = None, persist: bool = True) -> dict[str, Any]:
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
                    "format": "json",
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
            if prior_values and item.get("current") is not None and _normalise(item.get("current")) in {_normalise(v) for v in prior_values}:
                item["change_type"] = "unchanged"
            elif prior_values and item.get("current") is not None and all(_normalise(item.get("current")) != _normalise(v) for v in prior_values):
                item["change_type"] = "changed" if item.get("change_type") not in {"discontinued", "conflicting", "uncertain"} else item["change_type"]
                item["prior_state_candidates"] = prior_values
            elif not prior_values and item.get("current") is not None:
                if item.get("change_type") not in {"conflicting", "uncertain"}:
                    item["change_type"] = "new"
            computed.append({"domain": domain, "name": name, "change_type": item.get("change_type"), "previous": item.get("previous"), "current": item.get("current"), "certainty": item.get("certainty", "uncertain")})

    if persist:
        try:
            for domain in ("medications", "conditions", "symptoms"):
                for item in result[domain]:
                    name = str(item.get("name", "")).strip()
                    current = item.get("current")
                    if not name or current is None or item.get("certainty") != "supported":
                        continue
                    field = f"{domain.rstrip('s')}_reported_state"
                    create_assertion(db, CareAssertionCreate(
                        person_id=person_id, subject_type=domain.rstrip("s"), subject_id=name,
                        field=field, assertion_kind="time_bound_observation" if domain == "symptoms" else "current_state",
                        asserted_value=str(current), source_event_id=source_event_id, source_label=source,
                        source_text=str(item.get("evidence_excerpt") or content[:500]),
                        evidence_ids=evidence_ids, observed_at=event_date or datetime.now(timezone.utc),
                        review_after_days=30 if domain != "symptoms" else None,
                    ), commit=False)
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=500, detail="The reconciliation could not be persisted; the transaction was rolled back.")

    result["computed_changes"] = computed
    result["provenance"] = {
        "person_id": person_id, "source": source, "event_date": (event_date or datetime.now(timezone.utc)).isoformat(),
        "source_event_id": source_event_id, "evidence_ids": evidence_ids,
        "model_provider": "configured_local_ollama", "model": model,
        "persisted": persist, "current_state_authority": "existing SolenOS care-continuity records; this delta is an interpretation, not a verified clinical fact",
    }
    return result
