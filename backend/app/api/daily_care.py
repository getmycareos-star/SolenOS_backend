from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.daily_capture_enums import MedicationStatus, VitalType
from app.models.care import Person as PersonModel, Caregiver as CaregiverModel, Evidence as EvidenceModel, CareEvent as CareEventModel
from app.services.daily_capture import DailyCaptureService
from app.services.learning import LearningEngine, SavePipelineEvent


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

    occurred_at = entry.occurred_at or _now_iso()
    ts = datetime.fromisoformat(occurred_at.replace("Z", "+00:00")) if "T" in occurred_at else datetime.fromisoformat(occurred_at)

    evidence_texts: List[str] = []
    evidence_items: List[EvidenceModel] = []
    tags: List[str] = []
    events_to_create: List[Dict[str, Any]] = []

    # The typed service, used to write the rows the daily-capture endpoints and
    # the adherence tracker read. Declared once here so every section below can
    # reach it, including when no medication was recorded.
    capture = DailyCaptureService(db)

    # --- Medication ---
    if entry.medication:
        for med in entry.medication:
            # The checkbox collapses to two values, but the stored status keeps
            # the distinction a prescriber actually needs.
            status = med.resolved_status()
            notes = med.dose_or_notes or ""
            text = f"Medication '{med.name}' {status.value}" + (f" — {notes}" if notes else "")
            evidence_texts.append(text)
            events_to_create.append({
                "event_type": "medication",
                "title": f"{med.name} — {status.value}",
                "description": f"Scheduled: {med.scheduled_time or 'not specified'}. Notes: {notes}" if notes else f"Scheduled: {med.scheduled_time or 'not specified'}",
                "tags": ["medication", status.value, "daily_care"],
            })

            # Also written through the typed service, so adherence tracking and
            # the daily-capture timeline see this dose rather than having to
            # reconstruct it from tags.
            administered_at = None
            if med.administered_at:
                administered_at = datetime.fromisoformat(
                    med.administered_at.replace("Z", "+00:00")
                )
            capture.record_medication(
                {
                    "person_id": entry.person_id,
                    "caregiver_id": entry.caregiver_id,
                    "medication_name": med.name,
                    "status": status.value,
                    "scheduled_for": None,
                    "administered_at": administered_at,
                    "note": med.dose_or_notes,
                    "photo_evidence_id": med.photo_evidence_id,
                }
            )

    # --- Mood ---
    if entry.mood or entry.mood_scale is not None:
        scale_text = f" (scale {entry.mood_scale}/5)" if entry.mood_scale else ""
        text = f"Mood: {entry.mood or 'recorded'}{scale_text}"
        evidence_texts.append(text)
        tags.append("mood")
        events_to_create.append({
            "event_type": "mood",
            "title": "Daily mood check",
            "description": entry.mood or "",
            "tags": ["mood", "daily_care", f"scale_{entry.mood_scale}" if entry.mood_scale else "scale_recorded"],
        })
        if entry.mood_scale is not None:
            capture.record_scale({
                "person_id": entry.person_id,
                "caregiver_id": entry.caregiver_id,
                "axis": "mood",
                "score": entry.mood_scale,
                "observed_at": ts,
                "note": entry.mood or None,
            })

    # --- Agitation ---
    if entry.agitation_level or entry.agitation_scale is not None:
        scale_text = f" (scale {entry.agitation_scale}/5)" if entry.agitation_scale else ""
        text = f"Agitation: {entry.agitation_level or 'recorded'}{scale_text}"
        evidence_texts.append(text)
        tags.append("agitation")
        events_to_create.append({
            "event_type": "agitation",
            "title": "Agitation check",
            "description": entry.agitation_level or "",
            "tags": ["agitation", "daily_care", f"scale_{entry.agitation_scale}" if entry.agitation_scale else "scale_recorded"],
        })
        if entry.agitation_scale is not None:
            # A five-button agitation scale is exactly what the typed scale
            # table exists for, so the value lands there rather than existing
            # only as a tag on a text event.
            capture.record_scale({
                "person_id": entry.person_id,
                "caregiver_id": entry.caregiver_id,
                "axis": "agitation",
                "score": entry.agitation_scale,
                "observed_at": ts,
                "note": entry.agitation_level or None,
            })

    # --- Vitals ---
    if entry.vitals:
        vitals_text = "Vitals: " + ", ".join(f"{k}={v}" for k, v in entry.vitals.items())
        evidence_texts.append(vitals_text)
        tags.append("vitals")
        events_to_create.append({
            "event_type": "vital_signs",
            "title": "Vital signs",
            "description": vitals_text,
            "tags": ["vitals", "daily_care"] + [f"v_{k}" for k in entry.vitals.keys()],
        })

        # The free-text form above stays for the record's readability, but the
        # values are also written through the typed service so they carry a
        # unit and get range-checked. A string "195" cannot be flagged, and an
        # unusual reading nobody noticed is worse than one that was recorded.
        for key, raw_value in entry.vitals.items():
            try:
                vital_type = VitalType(key)
            except ValueError:
                # The form allows free-text names. An unrecognised vital is
                # still kept in the evidence above rather than being dropped,
                # because losing a reading is worse than not typing it up.
                continue
            try:
                numeric = float(raw_value)
            except (TypeError, ValueError):
                continue
            capture.record_vital(
                {
                    "person_id": entry.person_id,
                    "caregiver_id": entry.caregiver_id,
                    "vital_type": vital_type.value,
                    "value": numeric,
                    "unit": None,
                    "measured_at": ts,
                    "note": None,
                }
            )

    # --- Behavior ---
    if entry.behavior_notes:
        evidence_texts.append(f"Behavior: {entry.behavior_notes}")
        tags.append("behavior")
        events_to_create.append({
            "event_type": "behavior",
            "title": "Behavior observation",
            "description": entry.behavior_notes,
            "tags": ["behavior", "daily_care"],
        })

    # --- Sleep ---
    if entry.sleep_notes:
        evidence_texts.append(f"Sleep: {entry.sleep_notes}")
        tags.append("sleep")
        events_to_create.append({
            "event_type": "sleep",
            "title": "Sleep notes",
            "description": entry.sleep_notes,
            "tags": ["sleep", "daily_care"],
        })

    # --- Eating ---
    if entry.eating_notes:
        evidence_texts.append(f"Eating: {entry.eating_notes}")
        tags.append("eating")
        events_to_create.append({
            "event_type": "eating",
            "title": "Eating/appetite",
            "description": entry.eating_notes,
            "tags": ["eating", "daily_care"],
        })

    # --- Wellbeing ---
    if entry.wellbeing_stress is not None or entry.wellbeing_notes:
        stress_text = f"Wellbeing: stress level {entry.wellbeing_stress}/5" if entry.wellbeing_stress is not None else "Wellbeing check recorded"
        if entry.wellbeing_notes:
            stress_text += f" — {entry.wellbeing_notes}"
        evidence_texts.append(stress_text)
        tags.append("wellbeing")
        events_to_create.append({
            "event_type": "wellbeing",
            "title": "Caregiver wellbeing check",
            "description": entry.wellbeing_notes or "",
            "tags": ["wellbeing", "caregiver_wellbeing", f"stress_{entry.wellbeing_stress}" if entry.wellbeing_stress else "stress_recorded"],
        })

    # --- General notes ---
    if entry.general_notes:
        evidence_texts.append(f"General: {entry.general_notes}")
        events_to_create.append({
            "event_type": "general",
            "title": "General notes",
            "description": entry.general_notes,
            "tags": ["general", "daily_care"],
        })

    combined_text = "\n".join(evidence_texts)
    evidence = EvidenceModel(
        person_id=entry.person_id,
        type="daily_care_entry",
        source_text=combined_text,
        uploaded_by_caregiver_id=entry.caregiver_id,
        confidence=1.0,
        evidence_status="confirmed",
        source_type="caregiver_observation",
    )
    db.add(evidence)
    db.flush()
    evidence_items.append(evidence)

    created_events: List[CareEventModel] = []
    attention = False
    follow_up = False

    for evt in events_to_create:
        event = CareEventModel(
            person_id=entry.person_id,
            event_type=evt["event_type"],
            title=evt["title"],
            description=evt["description"],
            occurred_at=ts,
            occurred_at_timezone=caregiver.timezone,
            evidence_ids=[evidence.id],
            created_by_caregiver_id=entry.caregiver_id,
            tags=evt["tags"],
            status="recorded",
        )
        db.add(event)
        db.flush()
        created_events.append(event)

    db.commit()

    engine = LearningEngine(db)
    engine.log_pipeline_event(SavePipelineEvent(
        step="daily_care_entry",
        person_id=entry.person_id,
        detail=f"Daily care entry: {len(evidence_texts)} entries, {len(created_events)} events",
    ))
    engine.record_learning_event(
        entry.person_id,
        "daily_care_entry",
        f"Recorded daily care: {combined_text[:200]}",
        "daily_care",
        source_id=evidence.id,
        caregiver_id=entry.caregiver_id,
    )

    # Check for attention/follow-up triggers
    if entry.wellbeing_stress is not None and entry.wellbeing_stress >= 4:
        attention = True
    if entry.mood_scale is not None and entry.mood_scale <= 2:
        follow_up = True
    if entry.agitation_level and entry.agitation_level in ("severe", "extreme", "7", "8", "9", "10"):
        attention = True

    return DailyCareResponse(
        ok=True,
        entry_id=evidence.id,
        person_id=entry.person_id,
        events_created=len(created_events),
        evidence_created=len(evidence_items),
        attention_candidate=attention,
        follow_up_candidate=follow_up,
        message=f"Recorded {len(created_events)} care event(s) at {ts.isoformat()}.",
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
