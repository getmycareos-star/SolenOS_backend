from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import math

from app.core.database import get_db
from app.models.identity import Person as PersonModel
from app.core.evidence_enums import EvidenceSubjectRole
from app.core.daily_capture_enums import MedicationStatus, ScaleAxis, VitalType
from app.models.caregiver import Caregiver as CaregiverModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.services.daily_capture import DailyCaptureService
from app.services.learning import LearningEngine, SavePipelineEvent
from app.services.situation import process_situation_input
from app.schemas.situation import SituationInput, SituationDocument, SituationProvenance


router = APIRouter(prefix="/daily-care", tags=["daily-care"])


class MedicationEntry(BaseModel):
    name: str
    scheduled_time: Optional[str] = None
    # Kept for the existing form, which sends a checkbox. It is not the stored
    # value: a boolean cannot say whether a dose was refused, missed because it
    # was never offered, or given late, and those are three different
    # conversations with a prescriber. status is authoritative when present and
    # taken is only the fallback for older clients.
    taken: bool = True
    status: Optional[MedicationStatus] = None
    administered_at: Optional[str] = None
    dose_or_notes: Optional[str] = None
    photo_evidence_id: Optional[str] = None

    def resolved_status(self) -> MedicationStatus:
        """The status to store, preferring the explicit one.

        When only the checkbox is supplied, the note is read for a refusal
        rather than defaulting every false to "missed", because a caregiver
        writing "she spat it out" has told us something the checkbox cannot.
        """
        if self.status is not None:
            return self.status
        if self.taken:
            return MedicationStatus.GIVEN
        note = (self.dose_or_notes or "").lower()
        refusal_words = (
            "refus", "spat", "spit", "rejected", "would not", "wouldn't",
            "pushed away", "threw", "knocked", "no", "declined", "rejected it",
        )
        if any(word in note for word in refusal_words):
            return MedicationStatus.REFUSED
        return MedicationStatus.MISSED


class DailyCareEntry(BaseModel):
    person_id: str
    caregiver_id: str
    occurred_at: Optional[str] = None
    medication: Optional[List[MedicationEntry]] = None
    mood: Optional[str] = None
    mood_scale: Optional[int] = Field(None, ge=1, le=5)
    agitation_level: Optional[str] = None
    agitation_scale: Optional[int] = Field(None, ge=1, le=5)
    vitals: Optional[Dict[str, Any]] = None
    behavior_notes: Optional[str] = None
    sleep_notes: Optional[str] = None
    eating_notes: Optional[str] = None
    wellbeing_stress: Optional[int] = Field(None, ge=1, le=5)
    wellbeing_notes: Optional[str] = None
    general_notes: Optional[str] = None


class DailyCareResponse(BaseModel):
    ok: bool
    entry_id: str
    person_id: str
    events_created: int
    evidence_created: int
    attention_candidate: bool = False
    follow_up_candidate: bool = False
    message: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(entry: DailyCareEntry) -> str:
    if entry.occurred_at:
        return entry.occurred_at
    return _now_iso()


@router.post("/entries", response_model=DailyCareResponse)
def create_daily_care_entry(entry: DailyCareEntry, db: Session = Depends(get_db)):
    person = db.query(PersonModel).filter(PersonModel.id == entry.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    caregiver = db.query(CaregiverModel).filter(
        CaregiverModel.id == entry.caregiver_id,
        CaregiverModel.person_id == entry.person_id,
    ).first()
    if not caregiver:
        raise HTTPException(status_code=403, detail="Caregiver not authorized for this person")

    # Construct raw input string from the entry
    evidence_texts = []
    if entry.medication:
        for med in entry.medication:
            status = med.resolved_status()
            notes = med.dose_or_notes or ""
            text = f"Medication '{med.name}' {status.value}" + (f" â€” {notes}" if notes else "")
            evidence_texts.append(text)
    if entry.mood or entry.mood_scale is not None:
        scale_text = f" (scale {entry.mood_scale}/5)" if entry.mood_scale else ""
        text = f"Mood: {entry.mood or 'recorded'}{scale_text}"
        evidence_texts.append(text)
    if entry.agitation_level or entry.agitation_scale is not None:
        scale_text = f" (scale {entry.agitation_scale}/5)" if entry.agitation_scale else ""
        text = f"Agitation: {entry.agitation_level or 'recorded'}{scale_text}"
        evidence_texts.append(text)
    if entry.vitals:
        vitals_text = "Vitals: " + ", ".join(f"{k}={v}" for k, v in entry.vitals.items())
        evidence_texts.append(vitals_text)
    if entry.behavior_notes:
        evidence_texts.append(f"Behavior: {entry.behavior_notes}")
    if entry.sleep_notes:
        evidence_texts.append(f"Sleep: {entry.sleep_notes}")
    if entry.eating_notes:
        evidence_texts.append(f"Eating: {entry.eating_notes}")
    if entry.wellbeing_stress is not None or entry.wellbeing_notes:
        stress_text = f"Wellbeing: stress level {entry.wellbeing_stress}/5" if entry.wellbeing_stress is not None else "Wellbeing check recorded"
        if entry.wellbeing_notes:
            stress_text += f" â€” {entry.wellbeing_notes}"
        evidence_texts.append(stress_text)
    if entry.general_notes:
        evidence_texts.append(f"General: {entry.general_notes}")

    raw_input = "\n".join(evidence_texts).strip()
    if not raw_input:
        raw_input = "Daily care entry"

    # Determine timestamp
    timestamp = None
    if entry.occurred_at:
        timestamp = entry.occurred_at

    # Create SituationInput
    situation_input = SituationInput(
        caregiver_id=entry.caregiver_id,
        person_id=entry.person_id,
        raw_input=raw_input,
        documents=None,
        provenance=SituationProvenance(
            input_type="daily_care_entry",
            entry_method="api",
            captured_at=_now_iso(),
            recognition_confidence=1.0,
            transcript_uncertain=False,
        ),
        timestamp=timestamp,
    )

    # Process through situation pipeline
    try:
        situation_result = process_situation_input(db, situation_input)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Situation processing failed: {str(e)}")

    # Record medication entries via the daily capture service
    if entry.medication:
        medication_service = DailyCaptureService(db)
        for med in entry.medication:
            # Use the resolved status method which considers explicit status, taken flag, and refusal notes
            status = med.resolved_status()
            # Note: the daily care entry uses dose_or_notes for notes, not for dose.
            # We'll leave dose as None to let the service default to "the prescribed dose".
            # For the note, we'll use dose_or_notes.
            # Convert string times to datetime if provided.
            administered_at_dt = None
            if med.administered_at:
                administered_at_dt = datetime.fromisoformat(med.administered_at)
            scheduled_for_dt = None
            if med.scheduled_time:
                scheduled_for_dt = datetime.fromisoformat(med.scheduled_time)
            payload = {
                "person_id": entry.person_id,
                "caregiver_id": entry.caregiver_id,
                "medication_name": med.name,
                "status": status,
                "dose": None,  # let the service default to "the prescribed dose"
                "note": med.dose_or_notes,
                "administered_at": administered_at_dt,
                "scheduled_for": scheduled_for_dt,
            }
            # Remove None values
            medication_service.record_medication(payload)
    # Record recognized numeric vitals separately from medication entries.
    if entry.vitals:
        vital_service = DailyCaptureService(db)
        measured_at = (
            datetime.fromisoformat(entry.occurred_at.replace("Z", "+00:00"))
            if entry.occurred_at
            else datetime.now(timezone.utc)
        )
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=timezone.utc)
        for vital_name, raw_value in entry.vitals.items():
            # Form clients have historically sent both enum values ("heart_rate")
            # and enum names ("HEART_RATE"). Normalize both explicitly; preserve
            # unrecognized/free-text readings in the narrative evidence above.
            normalized_name = str(vital_name).strip()
            vital_type = VitalType.__members__.get(normalized_name.upper())
            if vital_type is None:
                try:
                    vital_type = VitalType(normalized_name.lower())
                except ValueError:
                    continue
            try:
                value = float(raw_value)
            except (ValueError, TypeError):
                # Non-numeric values remain in the narrative evidence and are
                # intentionally not coerced into a fabricated numeric reading.
                continue
            vital_service.record_vital({
                "person_id": entry.person_id,
                "caregiver_id": entry.caregiver_id,
                "vital_type": vital_type,
                "value": value,
                "unit": None,
                "measured_at": measured_at,
            })
        # Flush the complete structured write set before returning success.
        db.flush()
    # Record each validated five-point scale as a typed observation.
    if entry.mood_scale is not None or entry.agitation_scale is not None:
        scale_service = DailyCaptureService(db)
        scale_inputs = (
            (ScaleAxis.MOOD, entry.mood_scale),
            (ScaleAxis.AGITATION, entry.agitation_scale),
        )
        for axis, raw_score in scale_inputs:
            if raw_score is None:
                continue
            try:
                score = int(raw_score)
            except (ValueError, TypeError):
                continue
            # DailyCareEntry constrains these values to 1..5. Keep this guard
            # here too so internal callers cannot create an invalid scale row.
            if score < 1 or score > 5:
                continue
            scale_service.record_scale({
                "person_id": entry.person_id,
                "caregiver_id": entry.caregiver_id,
                "axis": axis,
                "score": score,
                "observed_at": measured_at,
            })

     # Map SituationResponse to DailyCareResponse
    return DailyCareResponse(
        ok=situation_result.ok,
        entry_id=situation_result.evidence_id,
        person_id=situation_result.person_id,
        events_created=len(situation_result.events) if situation_result.events else 0,
        evidence_created=1,  # One evidence created
        attention_candidate=situation_result.attention_candidate,
        follow_up_candidate=situation_result.follow_up_candidate,
        message=situation_result.message,
    )



@router.get("/{person_id}", response_model=List[Dict[str, Any]])
def list_daily_care_entries(person_id: str, limit: int = 30, db: Session = Depends(get_db)):
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    events = (
        db.query(CareEventModel)
        .filter(
            CareEventModel.person_id == person_id,
            CareEventModel.tags.contains("daily_care") if hasattr(CareEventModel.tags, "contains") else True,
        )
        .order_by(CareEventModel.occurred_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "title": e.title,
            "description": e.description,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "tags": e.tags or [],
            "evidence_ids": e.evidence_ids or [],
            "significance_verdict": e.significance_verdict,
            "attention_candidate": e.attention_candidate,
            "follow_up_candidate": e.follow_up_candidate,
            "confidence": e.confidence,
        }
        for e in events
    ]




