from datetime import datetime, timezone
from typing import List, Optional
import json
from sqlalchemy.orm import Session
from app.models.care import Caregiver, Person, Evidence, CareEvent
from app.schemas.situation import SituationInput, SituationResponse, SituationEvent, SituationDocument
from app.services.change_detection import detect_event_changes
from app.services.situation_formation import detect_situations


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def process_situation_input(db: Session, payload: SituationInput) -> SituationResponse:
    caregiver = db.query(Caregiver).filter(Caregiver.id == payload.caregiver_id).first()
    if not caregiver:
        raise ValueError("Caregiver not found")

    person = db.query(Person).filter(Person.id == payload.person_id).first()
    if not person:
        raise ValueError("Person not found")

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
    event_ids: List[str] = []

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
            confidence=1.0 if not doc else (doc.ocr_confidence or 0.5),
            evidence_status="reported",
            source_type="caregiver_observation",
        )
        db.add(evidence)
        db.flush()
        evidence_ids.append(evidence.id)
        return evidence

    if payload.raw_input and payload.raw_input.strip():
        create_evidence(payload.raw_input.strip())

    if payload.documents:
        for doc in payload.documents:
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
        title=payload.raw_input.strip()[:200] if payload.raw_input and payload.raw_input.strip() else "Document observation",
        description=payload.raw_input.strip() if payload.raw_input and payload.raw_input.strip() else None,
        evidence_ids=evidence_ids,
        created_by_caregiver_id=payload.caregiver_id,
    )
    db.add(care_event)
    db.flush()
    event_ids.append(care_event.id)
    db.commit()

    changes: List = []
    situations: List = []
    attention_candidate = False
    follow_up_candidate = False
    what_changed = None

    try:
        changes = detect_event_changes(db, payload.person_id, care_event.event_type, None, occurred_at)
        if changes:
            sig_changes = [c for c in changes if c.significance_verdict and c.significance_verdict.value != "insufficient_evidence"]
            if sig_changes:
                attention_candidate = any(c.attention_candidate for c in sig_changes)
                follow_up_candidate = any(c.follow_up_candidate for c in sig_changes)
                parts = []
                for c in sig_changes:
                    if c.change_type:
                        parts.append(f"{c.change_type.value}: {c.subject_type}")
                if parts:
                    what_changed = "; ".join(parts)

        detected = detect_situations(db, payload.person_id, occurred_at)
        if detected:
            situations = detected
            active_situations = [s for s in detected if s.state.value == "active"]
            if active_situations:
                attention_candidate = attention_candidate or any(s.attention_candidate for s in active_situations)
                follow_up_candidate = follow_up_candidate or any(s.follow_up_candidate for s in active_situations)

        care_event.significance_verdict = sig_changes[0].significance_verdict.value if sig_changes else None
        care_event.attention_candidate = attention_candidate
        care_event.follow_up_candidate = follow_up_candidate
        care_event.confidence = sig_changes[0].confidence if sig_changes else 0.5
        if situations:
            care_event.situation_id = situations[0].id
        db.commit()
        db.refresh(care_event)
    except Exception:
        db.rollback()
        db.add(care_event)
        db.flush()
        db.commit()

    events = [
        SituationEvent(
            id=e.id,
            event_type=e.event_type,
            title=e.title,
            description=e.description,
            occurred_at=e.occurred_at,
            evidence_ids=e.evidence_ids or [],
        )
        for e in db.query(CareEvent).filter(CareEvent.id.in_(event_ids)).all()
    ]

    situation_responses = []
    for s in situations:
        situation_responses.append({
            "id": s.id,
            "title": s.title,
            "description": s.description,
            "situation_type": s.situation_type.value if s.situation_type else None,
            "state": s.state.value if s.state else None,
            "confidence": s.confidence,
            "attention_candidate": s.attention_candidate,
            "follow_up_candidate": s.follow_up_candidate,
        })

    return SituationResponse(
        ok=True,
        situation_id=care_event.id,
        care_key=payload.caregiver_id,
        person_id=payload.person_id,
        evidence_id=evidence_ids[0],
        care_event_id=care_event.id,
        message="Care input recorded successfully.",
        events=events,
        situations=situation_responses or None,
        attention_candidate=attention_candidate,
        follow_up_candidate=follow_up_candidate,
        what_changed=what_changed,
    )
