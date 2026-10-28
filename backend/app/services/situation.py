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

    # Defense in depth: a caregiver must be linked to the target care recipient.
    # Request middleware also binds IDs to the bearer session, but the service
    # must remain safe when called from another route or internal workflow.
    if caregiver.person_id != person.id:
        raise ValueError("Caregiver is not linked to this person")

    timestamp = payload.timestamp
    if timestamp:
        try:
            occurred_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=timezone.utc)
            time_provenance = "caregiver_reported_event_time"
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Invalid timestamp; provide a valid ISO-8601 timestamp or omit it."
            ) from exc
    else:
        occurred_at = _utc_now()
        time_provenance = "entry_time_defaulted"

    evidence_ids: List[str] = []
    event_ids: List[str] = []

    def create_evidence(source_text: str, doc: Optional[SituationDocument] = None) -> Evidence:
        metadata = None
        if doc:
            # Preserve the extractor's actual provenance. Unknown confidence stays
            # unknown; upload time is not evidence of a document's effective date.
            metadata = {
                "document_id": doc.id,
                "document_name": doc.name,
                "mime_type": doc.mime_type,
                "extraction_source": doc.extraction_source,
                "ocr_confidence": doc.ocr_confidence,
                "document_time_status": "not_extracted",
                "captured_at": payload.provenance.captured_at if payload.provenance else None,
                "entry_method": payload.provenance.entry_method if payload.provenance else None,
            }
        evidence = Evidence(
            person_id=payload.person_id,
            type="note" if not doc else "document",
            source_text=source_text,
            original_file_path=doc.name if doc else None,
            extra_metadata=json.dumps(metadata) if metadata else None,
            time_provenance=payload.provenance.captured_at if payload.provenance else None,
            uploaded_by_caregiver_id=payload.caregiver_id,
            confidence=doc.ocr_confidence if doc else 1.0,
            evidence_status="reported",
            source_type=(
                "caregiver_observation"
                if not doc
                else "document_ocr"
                if doc.extraction_source == "ocr"
                else "document_text_extraction"
                if doc.extraction_source in ("text", "tika")
                else "document_extraction_method_unknown"
            ),
        )
        db.add(evidence)
        db.flush()
        evidence_ids.append(evidence.id)
        return evidence

    if payload.raw_input and payload.raw_input.strip():
        create_evidence(payload.raw_input.strip())

    if payload.documents:
        for doc in payload.documents:
            if doc.evidence_id:
                # Reuse the already-persisted original evidence instead of
                # duplicating it. Scope both person and uploader to the
                # authenticated care record; a client-supplied ID is not proof
                # of ownership.
                existing = (
                    db.query(Evidence)
                    .filter(
                        Evidence.id == doc.evidence_id,
                        Evidence.person_id == payload.person_id,
                        Evidence.uploaded_by_caregiver_id == payload.caregiver_id,
                    )
                    .first()
                )
                if existing is None:
                    raise ValueError("Attached evidence does not belong to this care record")
                if existing.id not in evidence_ids:
                    evidence_ids.append(existing.id)
                continue
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
        time_provenance=time_provenance,
        title=payload.raw_input.strip()[:200] if payload.raw_input and payload.raw_input.strip() else "Document observation",
        description=payload.raw_input.strip() if payload.raw_input and payload.raw_input.strip() else None,
        evidence_ids=evidence_ids,
        tags=[f"capture_category:{payload.capture_category}"] if payload.capture_category else [],
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
    sig_changes: List = []
    analysis_status = "complete"
    analysis_warning = None

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
        care_event.confidence = sig_changes[0].confidence if sig_changes else None
        if situations:
            care_event.situation_id = situations[0].id
        db.commit()
        db.refresh(care_event)
    except Exception:
        # The evidence and event were committed before optional derived analysis.
        # Do not pretend the analysis succeeded, and do not re-add/recommit the
        # already-persisted event as if this were a fresh capture.
        db.rollback()
        analysis_status = "partial"
        analysis_warning = (
            "Care input was saved, but change and situation analysis could not "
            "be completed. The recorded evidence remains available."
        )
        changes = []
        sig_changes = []
        situations = []
        attention_candidate = False
        follow_up_candidate = False
        what_changed = None

    events = [
        SituationEvent(
            id=e.id,
            event_type=e.event_type,
            title=e.title,
            description=e.description,
            occurred_at=e.occurred_at,
            time_provenance=e.time_provenance,
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
        care_key=payload.client_care_key or payload.caregiver_id,
        person_id=payload.person_id,
        evidence_id=evidence_ids[0],
        care_event_id=care_event.id,
        message=(
            "Care input recorded successfully."
            if analysis_status == "complete"
            else "Care input recorded; analysis is incomplete."
        ),
        events=events,
        situations=situation_responses or None,
        attention_candidate=attention_candidate,
        follow_up_candidate=follow_up_candidate,
        what_changed=what_changed,
        analysis_status=analysis_status,
        analysis_warning=analysis_warning,
    )
