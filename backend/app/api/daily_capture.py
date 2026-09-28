"""API routes for the structured daily forms."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.daily_capture import (
    AdherenceTrendResponse,
    DailyLogCreate,
    DailyLogResponse,
    MedicationRecordCreate,
    MedicationRecordResponse,
    ScaleObservationCreate,
    ScaleObservationResponse,
    VitalSignCreate,
    VitalSignResponse,
)
from app.services.daily_capture import DailyCaptureService

router = APIRouter(prefix="/daily", tags=["daily"])


def get_service(db: Session = Depends(get_db)) -> DailyCaptureService:
    return DailyCaptureService(db)


@router.post("/log", response_model=DailyLogResponse)
def submit_daily_log(
    payload: DailyLogCreate,
    service: DailyCaptureService = Depends(get_service),
):
    """Record the whole daily form in one write.

    Transactional on purpose. A caregiver who fills in medication, vitals and
    three scales and taps submit once should either get all of it recorded or
    none of it. A half-written day is worse than an unwritten one, because it
    looks like a day where those things did not happen.
    """
    evidence_ids: List[str] = []
    care_event_ids: List[str] = []
    medications = []
    vitals = []
    scales = []

    try:
        for item in payload.medications:
            record = service.record_medication(
                {
                    **item.model_dump(),
                    "photo_evidence_id": item.photo_evidence_id,
                }
            )
            medications.append(record)
            if record.evidence_id:
                evidence_ids.append(record.evidence_id)
            if record.care_event_id:
                care_event_ids.append(record.care_event_id)

        for item in payload.vitals:
            record = service.record_vital(item.model_dump())
            vitals.append(record)
            if record.care_event_id:
                care_event_ids.append(record.care_event_id)

        for item in payload.scales:
            record = service.record_scale(item.model_dump())
            scales.append(record)
            if record.care_event_id:
                care_event_ids.append(record.care_event_id)

        service.db.commit()
    except ValueError as exc:
        service.db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        service.db.rollback()
        raise

    return DailyLogResponse(
        person_id=payload.person_id,
        recorded_at=datetime.now(timezone.utc),
        medications=medications,
        vitals=vitals,
        scales=scales,
        evidence_ids=evidence_ids,
        care_event_ids=care_event_ids,
    )


@router.post("/medication", response_model=MedicationRecordResponse)
def record_medication(
    payload: MedicationRecordCreate,
    service: DailyCaptureService = Depends(get_service),
):
    try:
        record = service.record_medication(payload.model_dump())
        service.db.commit()
    except ValueError as exc:
        service.db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return record


@router.post("/vital", response_model=VitalSignResponse)
def record_vital(
    payload: VitalSignCreate,
    service: DailyCaptureService = Depends(get_service),
):
    try:
        record = service.record_vital(payload.model_dump())
        service.db.commit()
    except ValueError as exc:
        service.db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return record


@router.post("/scale", response_model=ScaleObservationResponse)
def record_scale(
    payload: ScaleObservationCreate,
    service: DailyCaptureService = Depends(get_service),
):
    try:
        record = service.record_scale(payload.model_dump())
        service.db.commit()
    except ValueError as exc:
        service.db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    return record


@router.get("/adherence/{person_id}", response_model=AdherenceTrendResponse)
def adherence(
    person_id: str,
    medication_name: str,
    window_days: int = 14,
    service: DailyCaptureService = Depends(get_service),
):
    """What the record shows about whether doses are being taken.

    Returns a rate, the evidence behind it, and an explicit statement of what is
    not established. It does not return advice, because whether to change a
    medication is a decision for a prescriber.
    """
    try:
        return service.adherence_trend(person_id, medication_name, window_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
