from datetime import datetime, timezone
from typing import List, Optional
import json

from sqlalchemy.orm import Session

from app.models.care import Caregiver, Person, Evidence, CareEvent
from app.schemas.situation import (
    SituationInput,
    SituationResponse,
    SituationEvent,
    SituationDocument,
)
from app.services.change_detection import detect_event_changes
from app.services.situation_formation import detect_situations
from app.services.capture_analysis import analyze_capture_with_qwen


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def analyze_saved_care_event(db: Session, care_event_id: str) -> dict:
    """Run the shared deterministic change/situation analysis for an already-saved event.

    The original caregiver report is committed before analysis starts. A derived-analysis
    failure is reported explicitly and never presented as a complete analysis.
    """
    care_event = db.query(CareEvent).filter(CareEvent.id == care_event_id).first()
    if care_event is None:
        return {
            "analysis_status": "failed",
            "analysis_error": "The saved care event could not be reloaded for analysis.",
            "what_changed": None,
            "attention_candidate": False,
            "follow_up_candidate": False,
            "situations": [],
        }

    person_id = care_event.person_id
    occurred_at = care_event.occurred_at
    try:
        changes = detect_event_changes(
            db, person_id, care_event.event_type, None, occurred_at
        ) or []
        significant_changes = [
            item for item in changes
            if item.significance_verdict
            and item.significance_verdict.value != "insufficient_evidence"
        ]
        attention_candidate = any(item.attention_candidate for item in significant_changes)
        follow_up_candidate = any(item.follow_up_candidate for item in significant_changes)
        change_parts = [
            f"{item.change_type.value}: {item.subject_type}"
            for item in significant_changes if item.change_type
        ]
        what_changed = "; ".join(change_parts) if change_parts else None

        detected = detect_situations(db, person_id, occurred_at) or []
        active_situations = [item for item in detected if item.state.value == "active"]
        if active_situations:
            attention_candidate = attention_candidate or any(
                item.attention_candidate for item in active_situations
            )
            follow_up_candidate = follow_up_candidate or any(
                item.follow_up_candidate for item in active_situations
            )

        care_event = db.query(CareEvent).filter(CareEvent.id == care_event_id).first()
        if care_event is None:
            raise RuntimeError("Saved event disappeared during analysis")
        care_event.significance_verdict = (
            significant_changes[0].significance_verdict.value
            if significant_changes else None
        )
        care_event.attention_candidate = attention_candidate
        care_event.follow_up_candidate = follow_up_candidate
        care_event.confidence = significant_changes[0].confidence if significant_changes else None
        if detected:
            care_event.situation_id = detected[0].id
        db.commit()
        result = {
            "analysis_status": "complete",
            "analysis_error": None,
            "what_changed": what_changed,
            "attention_candidate": attention_candidate,
            "follow_up_candidate": follow_up_candidate,
            "situations": [
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "situation_type": item.situation_type.value if item.situation_type else None,
                    "state": item.state.value if item.state else None,
                    "confidence": item.confidence,
                    "attention_candidate": item.attention_candidate,
                    "follow_up_candidate": item.follow_up_candidate,
                }
                for item in detected
            ],
        }
        result.update(analyze_capture_with_qwen(db, care_event_id))
        return result
    except Exception:
        # The original event was committed before analysis. Roll back only derived work,
        # reload the source event, and return an explicit partial-success state.
        db.rollback()
        result = {
            "analysis_status": "failed",
            "analysis_error": "The report was saved, but derived analysis could not be completed.",
            "what_changed": None,
            "attention_candidate": False,
            "follow_up_candidate": False,
            "situations": [],
        }
        result.update(analyze_capture_with_qwen(db, care_event_id))
        return result


def process_situation_input(db: Session, payload: SituationInput) -> SituationResponse:
    caregiver = db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first()
    if not caregiver:
        raise ValueError("Caregiver not found")

    person = db.query(Person).filter(Person.id == payload.person_id).first()
    if not person:
        raise ValueError("Person not found")

    # Enforce the relationship even for service callers outside the HTTP route.
    if caregiver.person_id != person.id:
        raise ValueError("Caregiver is not linked to this person")

    timestamp = payload.timestamp
    if timestamp:
        try:
            occurred_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        except ValueError:
            occurred_at = _utc_now()
    else:
        occurred_at = _utc_now()

    evidence_ids: List[str] = []

    def create_evidence(source_text: str, doc: Optional[SituationDocument] = None) -> Evidence:
        metadata = None
        if doc and (doc.ocr_confidence is not None or doc.mime_type):
            metadata = {
                "ocr_confidence": doc.ocr_confidence,
                "mime_type": doc.mime_type,
            }
        evidence = Evidence(
            person_id=payload.person_id,
            type="note" if not doc else "document",
            source_text=source_text,
            original_file_path=doc.name if doc else None,
            extra_metadata=json.dumps(metadata) if metadata else None,
            time_provenance=payload.provenance.captured_at if payload.provenance else None,
            uploaded_by_caregiver_id=payload.caregiver_id,
            confidence=1.0 if not doc else (
                doc.ocr_confidence if doc.ocr_confidence is not None else 0.5
            ),
            evidence_status="reported",
            source_type="caregiver_observation",
        )
        db.add(evidence)
        db.flush()
        evidence_ids.append(evidence.id)
        return evidence

    if payload.raw_input and payload.raw_input.strip():
        create_evidence(payload.raw_input.strip())

    for doc in payload.documents or []:
        if doc.extracted_text and doc.extracted_text.strip():
            create_evidence(doc.extracted_text.strip(), doc)

    if not evidence_ids:
        raise ValueError("No valid evidence provided")

    care_event = CareEvent(
        person_id=payload.person_id,
        event_type="observation",
        status="recorded",
        occurred_at=occurred_at,
        occurred_at_timezone=caregiver.timezone,
        title=(
            payload.raw_input.strip()[:200]
            if payload.raw_input and payload.raw_input.strip()
            else "Document observation"
        ),
        description=payload.raw_input.strip() if payload.raw_input and payload.raw_input.strip() else None,
        evidence_ids=evidence_ids,
        created_by_caregiver_id=payload.caregiver_id,
    )
    db.add(care_event)
    db.commit()
    db.refresh(care_event)

    # Both free-text capture and structured Daily Care now call this shared analysis step.
    analysis = analyze_saved_care_event(db, care_event.id)
    saved_event = db.query(CareEvent).filter(CareEvent.id == care_event.id).first()
    events = [
        SituationEvent(
            id=saved_event.id,
            event_type=saved_event.event_type,
            title=saved_event.title,
            description=saved_event.description,
            occurred_at=saved_event.occurred_at,
            evidence_ids=saved_event.evidence_ids or [],
        )
    ]

    return SituationResponse(
        ok=True,
        situation_id=saved_event.id,
        care_key=payload.caregiver_id,
        person_id=payload.person_id,
        evidence_id=evidence_ids[0],
        care_event_id=saved_event.id,
        message=(
            "Care input recorded successfully."
            if analysis["analysis_status"] == "complete"
            else "Care input recorded; derived analysis is incomplete."
        ),
        events=events,
        situations=analysis["situations"] or None,
        attention_candidate=analysis["attention_candidate"],
        follow_up_candidate=analysis["follow_up_candidate"],
        what_changed=analysis["what_changed"],
        analysis_status=analysis["analysis_status"],
        analysis_error=analysis["analysis_error"],
    )
