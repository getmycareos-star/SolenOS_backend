from typing import Any

from sqlalchemy.orm import Session

from app.models.capture_analysis import CaptureAnalysis


def persist_capture_analysis(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    source_event_id: str | None,
    source_evidence_id: str | None,
    source_text: str,
    extraction_status: str,
    candidates: dict[str, Any] | None,
) -> CaptureAnalysis:
    """Persist the source-linked extraction and review state without writing LCR facts."""
    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=source_event_id,
        source_evidence_id=source_evidence_id,
        source_text=source_text,
        extraction_status=extraction_status,
        candidates=candidates,
        review_history=[],
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis
