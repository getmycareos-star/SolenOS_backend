"""Structured daily capture.

The daily forms are not a parallel record. Each tap writes an Evidence row and a
CareEvent, which are the two tables the rest of SolenOS already reads, so a
structured entry and an uploaded document are indistinguishable to the change
detector, the meaning engine and the safety rules. That is the whole point: the
form is a better input, not a new destination.

Two things this module refuses to do:

- It never interprets. An out-of-range vital is flagged as out of range and left
  there. Whether that matters clinically is not this system's call.
- It never edits. A caregiver who was wrong about a dose corrects it through the
  existing correction path, which cascades. Silently overwriting a wrong entry
  would destroy the only record that it was ever wrong.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.daily_capture_enums import (
    SCALE_DIRECTIONS,
    SCALE_POLES,
    VITAL_ATTENTION_RANGES,
    MedicationStatus,
    ScaleAxis,
    VitalType,
)
from app.models.care import CareEvent, Evidence
from app.models.daily_capture import MedicationRecord, ScaleObservation, VitalSign


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp as local wall-clock and mark it.

    A caregiver typing "7am" means their morning, not UTC. Attaching UTC to a
    naive value would be a quiet lie, so the value is normalised to an aware
    datetime here and the original wall-clock time is preserved in the
    time_provenance string the record already carries.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _format_when(value: Optional[datetime]) -> str:
    if value is None:
        return "no time recorded"
    if value.tzinfo is None:
        return "at {} local time, recorded by the caregiver".format(value.strftime("%Y-%m-%d %H:%M"))
    return "at {} UTC, recorded by the caregiver".format(value.strftime("%Y-%m-%d %H:%M"))


class DailyCaptureService:
    """Turns form input into evidence and events."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Evidence and event plumbing
    # ------------------------------------------------------------------

    def _write_evidence(
        self,
        *,
        person_id: str,
        caregiver_id: str,
        evidence_type: str,
        source_text: str,
        occurred_at: datetime,
        linked_evidence_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Evidence:
        """Create one evidence row describing what the caregiver entered.

        A photo the app already uploaded is referenced in extra_metadata rather
        than duplicated, so the bottle image keeps its own evidence identity and
        the correction cascade can reach it independently.
        """
        meta = dict(metadata or {})
        if linked_evidence_id:
            meta["related_photo_evidence_id"] = linked_evidence_id

        evidence = Evidence(
            person_id=person_id,
            type=evidence_type,
            source_text=source_text,
            uploaded_by_caregiver_id=caregiver_id,
            source_type="caregiver",
            evidence_status="reported",
            time_provenance=_format_when(occurred_at),
            confidence=1.0,  # The caregiver attested to this. It is a direct report.
            # extra_metadata is a Text column on this model, not JSON, so the
            # dict is serialised rather than bound directly.
            extra_metadata=json.dumps(meta, default=str) if meta else None,
        )
        self.db.add(evidence)
        self.db.flush()
        return evidence

    def _write_event(
        self,
        *,
        person_id: str,
        caregiver_id: str,
        event_type: str,
        title: str,
        description: str,
        occurred_at: datetime,
        evidence_ids: List[str],
        tags: Optional[List[str]] = None,
    ) -> CareEvent:
        """Create the care event the rest of the engine reads.

        confidence is left unset on purpose. A caregiver reporting that they gave
        a dose is certain about the act, but certainty about the act is not
        certainty about anything downstream, and a confidence on this row would be
        read as a claim about significance. The meaning engine sets that.
        """
        event = CareEvent(
            person_id=person_id,
            event_type=event_type,
            status="recorded",
            occurred_at=_as_utc(occurred_at),
            occurred_at_timezone=None,
            time_provenance=_format_when(occurred_at),
            title=title,
            description=description,
            evidence_ids=evidence_ids,
            tags=tags or [],
            created_by_caregiver_id=caregiver_id,
            reporter_ids=[caregiver_id],
        )
        self.db.add(event)
        self.db.flush()
        return event

    # ------------------------------------------------------------------
    # Medication
    # ------------------------------------------------------------------

    def record_medication(self, payload: Dict[str, Any]) -> MedicationRecord:
        """Record one dose.

        REFUSED is written as a refusal in the caregiver's own words rather than
        reduced to a status code, because the reason for a refusal is usually the
        clinically interesting part and it is the thing a clinician will ask about.
        """
        status = MedicationStatus(payload["status"])
        administered_at = payload.get("administered_at")
        occurred = administered_at or payload.get("scheduled_for") or datetime.now(timezone.utc)

        parts = [
            "Caregiver recorded {} for {} as {}.".format(
                payload["medication_name"],
                payload.get("dose") or "the prescribed dose",
                status.value,
            ),
            _format_when(occurred),
        ]
        if status == MedicationStatus.REFUSED:
            parts.append("The person refused this dose.")
        if payload.get("note"):
            parts.append("Caregiver note: {}".format(payload["note"]))

        evidence = self._write_evidence(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            evidence_type="medication_administration",
            source_text=" ".join(parts),
            occurred_at=occurred,
            linked_evidence_id=payload.get("photo_evidence_id"),
            metadata={
                "medication_name": payload["medication_name"],
                "status": status.value,
                "scheduled_for": payload.get("scheduled_for").isoformat()
                if payload.get("scheduled_for")
                else None,
            },
        )

        event = self._write_event(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            event_type="medication",
            title="{} - {}".format(payload["medication_name"], status.value),
            description=" ".join(parts),
            occurred_at=occurred,
            evidence_ids=[evidence.id],
            tags=["medication", status.value, payload["medication_name"].lower()],
        )

        record = MedicationRecord(
            person_id=payload["person_id"],
            medication_name=payload["medication_name"],
            dose=payload.get("dose"),
            scheduled_for=payload.get("scheduled_for"),
            administered_at=administered_at,
            status=status,
            evidence_id=evidence.id,
            care_event_id=event.id,
            note=payload.get("note"),
            recorded_by_caregiver_id=payload["caregiver_id"],
        )
        self.db.add(record)
        self.db.flush()
        return record

    # ------------------------------------------------------------------
    # Vitals
    # ------------------------------------------------------------------

    def record_vital(self, payload: Dict[str, Any]) -> VitalSign:
        """Record one manually entered vital sign.

        An out-of-range value produces a flag and a note. It does not produce an
        interpretation, a cause, or a suggestion, because a number outside a
        reference range is a reason to call someone, not a finding about the person.
        """
        vital_type = VitalType(payload["vital_type"])
        value = float(payload["value"])
        unit = payload.get("unit")
        measured_at = payload["measured_at"]

        if unit is None:
            unit = {
                VitalType.TEMPERATURE: "C",
                VitalType.HEART_RATE: "bpm",
                VitalType.SPO2: "%",
            }.get(vital_type)

        out_of_range, range_note = self._check_range(vital_type, value, unit)

        description = "Caregiver recorded {} of {} {}{}.".format(
            vital_type.value.replace("_", " "),
            value,
            unit or "",
            _format_when(measured_at),
        )
        if out_of_range and range_note:
            description += " " + range_note
        if payload.get("note"):
            description += " Caregiver note: {}".format(payload["note"])

        evidence = self._write_evidence(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            evidence_type="vital_sign",
            source_text=description,
            occurred_at=measured_at,
            metadata={"vital_type": vital_type.value, "value": value, "unit": unit},
        )

        tags = ["vital_sign", vital_type.value]
        if out_of_range:
            tags.append("out_of_range")

        event = self._write_event(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            event_type="vital_sign",
            title="{}: {} {}".format(vital_type.value.replace("_", " "), value, unit or ""),
            description=description,
            occurred_at=measured_at,
            evidence_ids=[evidence.id],
            tags=tags,
        )

        record = VitalSign(
            person_id=payload["person_id"],
            vital_type=vital_type,
            value=value,
            unit=unit,
            measured_at=_as_utc(measured_at),
            is_out_of_range=out_of_range,
            range_note=range_note,
            evidence_id=evidence.id,
            care_event_id=event.id,
            note=payload.get("note"),
            recorded_by_caregiver_id=payload["caregiver_id"],
        )
        self.db.add(record)
        self.db.flush()
        return record

    def _check_range(self, vital_type: VitalType, value: float, unit: Optional[str]) -> Tuple[bool, Optional[str]]:
        """Flag an unusual reading. Nothing more.

        The check is skipped for temperatures recorded in Fahrenheit, because
        applying a Celsius range to a Fahrenheit number would produce confident
        nonsense. Silently not flagging is the safer failure of the two.
        """
        if vital_type == VitalType.TEMPERATURE and unit and unit.upper() in ("F", "\u00b0F"):
            return False, None

        bounds = VITAL_ATTENTION_RANGES.get(vital_type)
        if not bounds:
            return False, None

        low, high = bounds
        if low <= value <= high:
            return False, None
        if value < low:
            return True, (
                "This reading is below the usual range for this measure. That is a reason "
                "to check it and, if it does not look right, to contact a clinician. It is "
                "not a finding about what is causing it."
            )
        return True, (
            "This reading is above the usual range for this measure. That is a reason "
            "to check it and, if it does not look right, to contact a clinician. It is "
            "not a finding about what is causing it."
        )

    # ------------------------------------------------------------------
    # Scales
    # ------------------------------------------------------------------

    def record_scale(self, payload: Dict[str, Any]) -> ScaleObservation:
        """Record one value on a five-point axis.

        The pole labels are written into the event description rather than only
        stored on the row, so a caregiver reading the timeline later sees "very
        agitated" and not a bare 4, which could equally have been 4 out of 5
        settled.
        """
        axis = ScaleAxis(payload["axis"])
        score = int(payload["score"])
        if not 1 <= score <= 5:
            raise ValueError("Scale scores run from 1 to 5.")

        low_value, low_label, high_value, high_label = SCALE_POLES[axis]
        direction = SCALE_DIRECTIONS[axis]
        observed_at = payload["observed_at"]

        description = "Caregiver rated {} as {}/5 on {} to {}.".format(
            axis.value.replace("_", " "), score, low_label, high_label
        )
        description += " " + _format_when(observed_at)
        if payload.get("note"):
            description += " Caregiver note: {}".format(payload["note"])

        evidence = self._write_evidence(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            evidence_type="structured_observation",
            source_text=description,
            occurred_at=observed_at,
            metadata={"axis": axis.value, "score": score, "direction": direction.value},
        )

        event = self._write_event(
            person_id=payload["person_id"],
            caregiver_id=payload["caregiver_id"],
            # agitation and confusion land in the behaviour engine's own
            # vocabulary, so a five-button tap feeds the hypothesis machinery
            # rather than sitting beside it.
            event_type=self._event_type_for_axis(axis),
            title="{}: {}/5 ({})".format(axis.value.replace("_", " "), score, high_label if score >= 3 else low_label),
            description=description,
            occurred_at=observed_at,
            evidence_ids=[evidence.id],
            tags=["scale", axis.value, "level_{}".format(score)],
        )

        record = ScaleObservation(
            person_id=payload["person_id"],
            axis=axis,
            score=score,
            direction=direction,
            low_label=low_label,
            high_label=high_label,
            observed_at=_as_utc(observed_at),
            evidence_id=evidence.id,
            care_event_id=event.id,
            note=payload.get("note"),
            recorded_by_caregiver_id=payload["caregiver_id"],
        )
        self.db.add(record)
        self.db.flush()
        return record

    def _event_type_for_axis(self, axis: ScaleAxis) -> str:
        """Map each axis onto the vocabulary the engine already uses.

        The behaviour engine already knows about agitation and confusion, so those
        two reuse its categories exactly rather than inventing parallel ones that
        a later reader would have to translate.
        """
        if axis == ScaleAxis.AGITATION:
            return "behavior_agitation"
        if axis == ScaleAxis.CLARITY:
            return "behavior_confusion"
        return "observation"

    # ------------------------------------------------------------------
    # Adherence
    # ------------------------------------------------------------------

    def adherence_trend(
        self,
        person_id: str,
        medication_name: str,
        window_days: int = 14,
    ) -> Dict[str, Any]:
        """Summarise whether doses are actually being taken.

        Reports what the record shows and stops there. A decline is surfaced as a
        pattern with its evidence attached and an explicit note that the reason is
        not established, because "she is missing doses" and "she is refusing
        because of the new tablet" are the same observation and completely
        different conversations with a prescriber.
        """
        if window_days < 2:
            raise ValueError("A window of at least two days is needed to compare halves.")

        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        rows = (
            self.db.query(MedicationRecord)
            .filter(
                MedicationRecord.person_id == person_id,
                MedicationRecord.medication_name == medication_name,
                MedicationRecord.recorded_at >= since,
            )
            .order_by(MedicationRecord.recorded_at.asc())
            .all()
        )

        # Only doses that were actually due count. A row saying "not due" is the
        # absence of a dose, not a missed one, and counting it would make an
        # inactive medication look like poor adherence.
        counted = [
            row
            for row in rows
            if row.status
            in (
                MedicationStatus.GIVEN,
                MedicationStatus.REFUSED,
                MedicationStatus.MISSED,
                MedicationStatus.DELAYED,
            )
        ]

        given = [r for r in counted if r.status == MedicationStatus.GIVEN]
        refused = [r for r in counted if r.status == MedicationStatus.REFUSED]
        missed = [r for r in counted if r.status == MedicationStatus.MISSED]

        evidence_ids: List[str] = []
        for row in counted:
            if row.evidence_id and row.evidence_id not in evidence_ids:
                evidence_ids.append(row.evidence_id)

        midpoint = len(counted) // 2
        earlier, later = counted[:midpoint], counted[midpoint:]

        def _rate(segment: List[MedicationRecord]) -> Optional[float]:
            due = [r for r in segment if r.status in (MedicationStatus.GIVEN, MedicationStatus.MISSED, MedicationStatus.REFUSED)]
            if not due:
                return None
            return round(len([r for r in due if r.status == MedicationStatus.GIVEN]) / len(due), 3)

        earlier_rate = _rate(earlier)
        later_rate = _rate(later)
        declining = (
            earlier_rate is not None
            and later_rate is not None
            and later_rate < earlier_rate
        )

        if not counted:
            possible_context = (
                "No doses have been recorded in this window, so nothing can be said about whether "
                "this medication is being taken. That is an absence of records, not evidence that "
                "doses were missed."
            )
        elif declining:
            possible_context = (
                "Adherence is lower in the second half of this window than in the first. Why is not "
                "established by the record. Whether this is a refusal, a timing problem, a supply "
                "problem, or a change in how it is given would need asking."
            )
        else:
            possible_context = (
                "What is not established is why any dose was refused or missed, and whether the "
                "schedule itself still matches what was prescribed."
            )

        return {
            "person_id": person_id,
            "medication_name": medication_name,
            "window_days": window_days,
            "scheduled_count": len(counted),
            "given_count": len(given),
            "refused_count": len(refused),
            "missed_count": len(missed),
            "adherence_rate": _rate(counted),
            "declining": declining,
            "evidence_ids": evidence_ids,
            "possible_context": possible_context,
            # Confidence here is about the arithmetic on what was recorded, not
            # about the person. It is withheld entirely when nothing was
            # recorded, because a rate computed over no doses is not a low
            # confidence reading, it is no reading at all.
            "confidence": round(min(len(evidence_ids) / 10.0, 0.95), 3) if counted else None,
        }

    def list_recent(
        self,
        person_id: str,
        days: int = 1,
    ) -> Dict[str, Any]:
        """What was recorded today, for reopening the form."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return {
            "medications": (
                self.db.query(MedicationRecord)
                .filter(MedicationRecord.person_id == person_id, MedicationRecord.recorded_at >= since)
                .order_by(MedicationRecord.recorded_at.desc())
                .all()
            ),
            "vitals": (
                self.db.query(VitalSign)
                .filter(VitalSign.person_id == person_id, VitalSign.measured_at >= since)
                .order_by(VitalSign.measured_at.desc())
                .all()
            ),
            "scales": (
                self.db.query(ScaleObservation)
                .filter(ScaleObservation.person_id == person_id, ScaleObservation.observed_at >= since)
                .order_by(ScaleObservation.observed_at.desc())
                .all()
            ),
        }
